"""Where providers read a project's files from: the working tree, or a git revision (normally HEAD).

Paths are always relative to the project root, with forward slashes. The project root may be a
subfolder of the git repository (a monorepo package); git commands are scoped to it.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "build", "dist", "__pycache__",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".diagrams",
    ".idea", ".vscode", "target", ".next", ".cache",
}


class SourceError(Exception):
    """A source cannot be read (git missing, no commits yet, ...)."""


def _git() -> str | None:
    return shutil.which("git")


def run_git(root: Path, *args: str, check: bool = True, input: bytes | None = None) -> bytes:
    git = _git()
    if not git:
        raise SourceError("git is not installed")
    try:
        result = subprocess.run([git, "-C", str(root), *args], input=input, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise SourceError(f"git {args[0]}: {e}") from e
    if check and result.returncode != 0:
        raise SourceError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _z(data: bytes) -> list[str]:
    return [p for p in data.decode("utf-8", "surrogateescape").split("\0") if p]


def in_git(root: Path) -> bool:
    """True when `root` is inside a git work tree."""
    if not _git():
        return False
    try:
        return run_git(root, "rev-parse", "--is-inside-work-tree", check=False).strip() == b"true"
    except SourceError:
        return False


def head_commit(root: Path) -> str | None:
    """The commit id HEAD points to, or None (not a repo, or no commits yet)."""
    if not _git():
        return None
    try:
        out = run_git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False).strip()
    except SourceError:
        return None
    return out.decode("ascii", "replace") or None


def changed_paths(root: Path) -> set[str]:
    """Files that differ from HEAD (modified, added, deleted, untracked), relative to `root`."""
    out = set(_z(run_git(root, "diff", "--name-only", "-z", "--relative", "HEAD")))
    out |= set(_z(run_git(root, "ls-files", "-z", "--others", "--exclude-standard")))
    return out


def _skipped(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in rel.split("/")[:-1])


class WorkingTree:
    """The files on disk. In a git repo, ignored files are left out (git ls-files)."""

    kind = "worktree"

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git = in_git(root)

    def files(self) -> list[str]:
        if self._git:
            listed = _z(run_git(self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"))
            return sorted(p for p in set(listed) if not _skipped(p) and (self.root / p).is_file())
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            base = Path(dirpath).relative_to(self.root).as_posix()
            for name in filenames:
                out.append(name if base == "." else f"{base}/{name}")
        return sorted(out)

    def read(self, paths: list[str]) -> dict[str, bytes]:
        out = {}
        for rel in paths:
            try:
                out[rel] = (self.root / rel).read_bytes()
            except OSError:
                continue
        return out

    def stat(self, rel: str) -> tuple[int, int] | None:
        try:
            st = (self.root / rel).stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def key(self, paths: list[str]) -> tuple:
        """Changes whenever any of `paths` changes on disk (or the list itself changes)."""
        return ("worktree", tuple((p, self.stat(p)) for p in paths))


class GitRevision:
    """The files as committed at `rev` (default HEAD), read without touching the working tree."""

    kind = "git"

    def __init__(self, root: Path, rev: str = "HEAD") -> None:
        self.root = root
        commit = head_commit(root) if rev == "HEAD" else rev
        if not commit:
            raise SourceError("no commits yet")
        self.commit = commit
        self.prefix = run_git(root, "rev-parse", "--show-prefix").decode("utf-8", "surrogateescape").strip()
        # ls-tree and archive filter by the current folder; run them from the top so they don't.
        self.top = Path(run_git(root, "rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip())
        self._files: list[str] | None = None

    def _spec(self, rel: str = "") -> str:
        return f"{self.commit}:{self.prefix}{rel}"

    def files(self) -> list[str]:
        if self._files is None:
            listed = _z(run_git(self.top, "ls-tree", "-r", "-z", "--name-only", self._spec()))
            self._files = sorted(p for p in listed if not _skipped(p))
        return list(self._files)

    def read(self, paths: list[str]) -> dict[str, bytes]:
        """Reads many files with one `git cat-file --batch`."""
        wanted = [p for p in paths if "\n" not in p]
        if not wanted:
            return {}
        data = run_git(self.root, "cat-file", "--batch",
                       input="".join(self._spec(p) + "\n" for p in wanted).encode("utf-8", "surrogateescape"))
        out: dict[str, bytes] = {}
        pos = 0
        for rel in wanted:
            nl = data.index(b"\n", pos)
            line = data[pos:nl]
            pos = nl + 1
            if line.endswith(b" missing") or line.endswith(b" ambiguous"):
                continue  # "<object> missing": no content follows
            _, kind, size = line.rsplit(b" ", 2)
            if kind == b"blob":
                out[rel] = data[pos:pos + int(size)]
            pos += int(size) + 1  # content, then a newline
        return out

    def stat(self, rel: str) -> tuple[int, int] | None:
        return None

    def key(self, paths: list[str]) -> tuple:
        return ("git", self.commit)

    def export(self, dest: Path) -> Path:
        """Write the revision's files under `dest` (for tools that need a real folder)."""
        data = run_git(self.top, "archive", "--format=tar", self._spec())
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            members = [m for m in tar.getmembers()
                       if (m.isfile() or m.isdir()) and not m.name.startswith(("/", "..")) and ".." not in m.name.split("/")]
            tar.extractall(dest, members=members)  # noqa: S202 - members filtered above
        return dest
