"""Project root detection and configuration.

Precedence for where diagrams live (first match wins):
  1. --dir / --store flags
  2. MEKHEN_STORE environment variable
  3. project config: mekhen.toml (top level) or [tool.mekhen] in pyproject.toml
  4. user config: ~/.mekhen/config.toml
  5. the default, store = "repo" (<root>/.diagrams/)
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PROJECT_MARKERS = ("pyproject.toml", "mekhen.toml", ".git")
STORES = ("repo", "home")
DEFAULT_STORE = "repo"


class ConfigError(Exception):
    """A config file or flag has a value mekhen cannot use."""


@dataclass
class Config:
    root: Path
    store: str  # "repo", "home" or "dir" (an explicit path)
    explicit_dir: Path | None = None
    gen: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)  # config files that were read


def mekhen_home(env: Mapping[str, str] | None = None) -> Path:
    """~/.mekhen, or MEKHEN_HOME when set (tests use this)."""
    env = os.environ if env is None else env
    if env.get("MEKHEN_HOME"):
        return Path(env["MEKHEN_HOME"]).expanduser()
    return Path.home() / ".mekhen"


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Per-user cache: running-server registry and the offline Mermaid copy."""
    env = os.environ if env is None else env
    if env.get("MEKHEN_CACHE_DIR"):
        return Path(env["MEKHEN_CACHE_DIR"]).expanduser()
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA")
        return Path(base) / "mekhen" if base else Path.home() / "AppData" / "Local" / "mekhen"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "mekhen"
    base = env.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "mekhen"


def find_root(start: Path) -> Path:
    """Walk up from `start` to the nearest directory holding a project marker."""
    start = start.resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return start


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _check_store(value: Any, where: str) -> str:
    if value not in STORES:
        raise ConfigError(f"{where}: store must be one of {', '.join(STORES)}, got {value!r}")
    return value


def load_config(
    path: Path | str | None = None,
    *,
    store: str | None = None,
    dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    env = os.environ if env is None else env
    root = find_root(Path(path) if path else Path.cwd())
    sources: list[str] = []

    project: dict[str, Any] = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        section = _read_toml(pyproject).get("tool", {}).get("mekhen")
        if isinstance(section, dict):
            project = _merge(project, section)
            sources.append(str(pyproject))
    mekhen_toml = root / "mekhen.toml"
    if mekhen_toml.is_file():
        project = _merge(project, _read_toml(mekhen_toml))
        sources.append(str(mekhen_toml))

    user: dict[str, Any] = {}
    user_file = mekhen_home(env) / "config.toml"
    if user_file.is_file():
        user = _read_toml(user_file)
        sources.append(str(user_file))

    gen = _merge(user.get("gen", {}) or {}, project.get("gen", {}) or {})

    def explicit(value: Path | str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else root / p

    if dir:
        return Config(root, "dir", explicit(dir), gen, sources)
    if store:
        return Config(root, _check_store(store, "--store"), None, gen, sources)
    if env.get("MEKHEN_STORE"):
        return Config(root, _check_store(env["MEKHEN_STORE"], "MEKHEN_STORE"), None, gen, sources)
    if project.get("dir"):
        return Config(root, "dir", explicit(project["dir"]), gen, sources)
    if project.get("store"):
        return Config(root, _check_store(project["store"], "project config"), None, gen, sources)
    if user.get("store"):
        return Config(root, _check_store(user["store"], str(user_file)), None, gen, sources)
    return Config(root, DEFAULT_STORE, None, gen, sources)
