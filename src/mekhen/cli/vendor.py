"""The optional offline copy of Mermaid, kept in the user cache (never in a project or the wheel)."""

from __future__ import annotations

import base64
import hashlib
import urllib.request
from pathlib import Path

from mekhen.core.config import cache_dir
from mekhen.mermaid_pin import MERMAID_SRI, MERMAID_URL, MERMAID_VERSION


def sri(data: bytes, algorithm: str = "sha384") -> str:
    return f"{algorithm}-" + base64.b64encode(hashlib.new(algorithm, data).digest()).decode("ascii")


def cached_path() -> Path:
    return cache_dir() / "mermaid" / MERMAID_VERSION / "mermaid.min.js"


def offline_copy() -> Path | None:
    """The cached file when present and matching the pinned hash, else None."""
    path = cached_path()
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return path if sri(data, MERMAID_SRI.split("-", 1)[0]) == MERMAID_SRI else None


def download(force: bool = False) -> Path:
    path = cached_path()
    if not force and offline_copy():
        return path
    with urllib.request.urlopen(MERMAID_URL, timeout=60) as response:
        data = response.read()
    got = sri(data, MERMAID_SRI.split("-", 1)[0])
    if got != MERMAID_SRI:
        raise RuntimeError(f"downloaded Mermaid does not match the pinned hash ({got} != {MERMAID_SRI})")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
