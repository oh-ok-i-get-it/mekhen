"""Where a project's diagram files live, and creating that folder.

repo: <root>/.diagrams/, which carries its own `.gitignore` containing `*`, so git ignores the
      whole folder without any change to the project's own ignore files.
home: ~/.mekhen/projects/<repo-name>-<hash8>/, which leaves nothing in the project.
dir:  an explicit path from --dir or `dir = ...` in project config; never auto-ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from mekhen.core.config import Config, mekhen_home

REPO_DIRNAME = ".diagrams"
SIDES = ("baseline", "proposed")


def project_key(root: Path) -> str:
    """Stable key for a project root: folder name plus a short hash of its absolute path."""
    norm = os.path.normcase(str(root.resolve()))
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "project"
    return f"{name}-{digest}"


def store_dir(cfg: Config) -> Path:
    if cfg.store == "dir":
        assert cfg.explicit_dir is not None
        return cfg.explicit_dir
    if cfg.store == "home":
        return mekhen_home() / "projects" / project_key(cfg.root)
    return cfg.root / REPO_DIRNAME


def describe(cfg: Config) -> str:
    path = store_dir(cfg)
    try:
        shown = path.relative_to(cfg.root).as_posix() + "/"
    except ValueError:
        shown = str(path)
    note = {"repo": "inside the project, ignored by git", "home": "outside the project", "dir": "set by config or --dir"}
    return f"{shown} ({note[cfg.store]})"


def ensure_store(cfg: Config) -> Path:
    """Create the store and its side folders; idempotent."""
    path = store_dir(cfg)
    for side in SIDES:
        (path / side).mkdir(parents=True, exist_ok=True)
    if cfg.store == "repo":
        ignore = path / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Created by mekhen: keeps this folder out of git.\n*\n", encoding="utf-8")
    elif cfg.store == "home":
        meta = path / "project.json"
        if not meta.exists():
            meta.write_text(json.dumps({"schema": 1, "root": str(cfg.root)}, indent=2) + "\n", encoding="utf-8")
    return path


def git_ignores(path: Path, root: Path) -> bool | None:
    """True or False when git can answer; None when git is missing or `root` is not a repo."""
    git = shutil.which("git")
    if not git or not (root / ".git").exists():
        return None
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None  # outside the repo: nothing to ignore
    # Probe a file inside: a `*` in the folder's own .gitignore matches its contents, not the folder.
    probe = (rel / "baseline" / "probe.mmd").as_posix()
    try:
        result = subprocess.run(
            [git, "-C", str(root), "check-ignore", "-q", "--no-index", probe],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode in (0, 1):
        return result.returncode == 0
    return None
