import shutil
import subprocess

import pytest
from conftest import git_init, make_project, write

from mekhen.core.config import ConfigError, find_root, load_config, mekhen_home
from mekhen.core.store import ensure_store, git_ignores, project_key, store_dir


def test_root_is_nearest_marker(project_dir):
    nested = project_dir / "src" / "pkg"
    nested.mkdir(parents=True)
    assert find_root(nested) == project_dir.resolve()


def test_default_store_is_outside_the_project(project_dir):
    cfg = load_config(project_dir)
    assert cfg.store == "home"
    assert store_dir(cfg) == mekhen_home() / "projects" / project_key(project_dir)


def test_repo_store_is_dot_diagrams(project_dir):
    assert store_dir(load_config(project_dir, store="repo")) == project_dir.resolve() / ".diagrams"


def test_precedence_flag_env_project_user(project_dir, monkeypatch):
    write(mekhen_home() / "config.toml", 'store = "home"\n')
    assert load_config(project_dir).store == "home"  # user config

    write(project_dir / "mekhen.toml", 'store = "repo"\n')
    assert load_config(project_dir).store == "repo"  # project beats user

    monkeypatch.setenv("MEKHEN_STORE", "home")
    assert load_config(project_dir).store == "home"  # env beats project

    assert load_config(project_dir, store="repo").store == "repo"  # flag beats env
    cfg = load_config(project_dir, dir="docs/diagrams")
    assert cfg.store == "dir" and cfg.explicit_dir == project_dir.resolve() / "docs" / "diagrams"


def test_pyproject_section_and_mekhen_toml_merge(project_dir):
    write(project_dir / "pyproject.toml",
          '[project]\nname = "p"\n[tool.mekhen]\ndir = "docs/d"\n[tool.mekhen.gen.imports]\ndepth = 2\n')
    write(project_dir / "mekhen.toml", "[gen.imports]\ndirection = \"TB\"\n")
    cfg = load_config(project_dir)
    assert cfg.store == "dir"
    assert cfg.gen["imports"] == {"depth": 2, "direction": "TB"}


def test_bad_store_value(project_dir):
    write(project_dir / "mekhen.toml", 'store = "cloud"\n')
    with pytest.raises(ConfigError):
        load_config(project_dir)


def test_home_store_key_is_stable_and_distinct(tmp_path):
    a = tmp_path / "one" / "app"
    b = tmp_path / "two" / "app"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert project_key(a) == project_key(a)
    assert project_key(a) != project_key(b)
    assert project_key(a).startswith("app-")


def test_home_store_leaves_project_untouched(project_dir):
    project = make_project(project_dir, store="home")
    project.new("flow")
    assert sorted(p.name for p in project_dir.iterdir()) == ["pyproject.toml"]
    assert (project.store / "project.json").is_file()
    assert (project.store / "baseline" / "flow.mmd").is_file()


def test_repo_store_ignores_itself(project_dir):
    cfg = load_config(project_dir, store="repo")
    ensure_store(cfg)
    assert (store_dir(cfg) / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_repo_store_is_invisible_to_git(project_dir):
    git_init(project_dir)
    project = make_project(project_dir, store="repo")
    project.new("flow")
    status = subprocess.run(["git", "-C", str(project_dir), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    assert ".diagrams" not in status
    assert git_ignores(project.store, project.root) is True
    assert project.store_issues() == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_unignored_store_is_reported(project_dir):
    git_init(project_dir)
    write(project_dir / ".diagrams" / "baseline" / "x.mmd", "%% mekhen:hand-written\nflowchart TD\n  a\n")
    project = make_project(project_dir, store="repo")
    assert [i.code for i in project.store_issues()] == ["store-not-ignored"]


def test_user_project_tables_and_command_safety(project_dir):
    root = project_dir.resolve().as_posix()
    write(mekhen_home() / "config.toml",
          f'max_nodes = 40\n[projects."{root}"]\nprovider = "command"\n'
          'providers.command = { command = "echo user" }\n'
          '[projects."/somewhere/else/*"]\nmax_nodes = 5\n')
    write(project_dir / "mekhen.toml", 'max_nodes = 80\n[providers.command]\ncommand = "rm -rf /"\n')
    cfg = load_config(project_dir)
    assert cfg.get("provider") == "command"
    assert cfg.get("providers")["command"] == {"command": "echo user"}  # the repo's command is ignored
    assert cfg.get("max_nodes") == 80  # project settings beat user ones
