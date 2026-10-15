from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mekhen.core.config import load_config
from mekhen.core.project import Project

FIXTURES = Path(__file__).parent / "fixtures" / "projects"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path_factory, monkeypatch):
    """Keep tests away from the real ~/.mekhen, user cache and any MEKHEN_STORE in the shell."""
    home = tmp_path_factory.mktemp("mekhen-home")
    monkeypatch.setenv("MEKHEN_HOME", str(home / "dot-mekhen"))
    monkeypatch.setenv("MEKHEN_CACHE_DIR", str(home / "cache"))
    monkeypatch.delenv("MEKHEN_STORE", raising=False)
    return home


@pytest.fixture
def repo_store(monkeypatch):
    """Diagrams in <project>/.diagrams/ (the `repo` store) instead of the default home store."""
    monkeypatch.setenv("MEKHEN_STORE", "repo")


@pytest.fixture
def project_dir(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "proj"\n', encoding="utf-8")
    return root


@pytest.fixture
def sample_dir(tmp_path) -> Path:
    root = tmp_path / "sample"
    shutil.copytree(FIXTURES / "sample", root)
    return root


def git_init(root: Path) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    subprocess.run([git, "init", "-q", str(root)], check=True)
    return True


def git_commit(root: Path, message: str = "commit") -> None:
    """Stage everything and commit (initialising the repo first if needed)."""
    git = shutil.which("git")
    if not (root / ".git").exists():
        subprocess.run([git, "init", "-q", str(root)], check=True)
    subprocess.run([git, "-C", str(root), "add", "-A"], check=True)
    subprocess.run([git, "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", message], check=True)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make_project(root: Path, **kwargs) -> Project:
    return Project(load_config(root, **kwargs))
