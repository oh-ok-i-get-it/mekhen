"""Bump the pinned Mermaid version: python scripts/update_mermaid.py X.Y.Z

Downloads dist/mermaid.min.js for that version from jsDelivr, computes its SRI hash and rewrites
src/mekhen/mermaid_pin.py. Warns when the release is less than 14 days old. Afterwards: run the
e2e tests (pytest tests/e2e), read the Mermaid changelog for flowchart changes, and bump mekhen's
minor version.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PIN = Path(__file__).resolve().parent.parent / "src" / "mekhen" / "mermaid_pin.py"


def main(version: str) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print("usage: update_mermaid.py X.Y.Z (an exact version)", file=sys.stderr)
        return 2
    with urllib.request.urlopen("https://registry.npmjs.org/mermaid", timeout=60) as r:
        registry = json.load(r)
    released = registry.get("time", {}).get(version)
    if not released:
        print(f"mermaid {version} is not on npm", file=sys.stderr)
        return 1
    age = datetime.now(timezone.utc) - datetime.fromisoformat(released.replace("Z", "+00:00"))
    url = f"https://cdn.jsdelivr.net/npm/mermaid@{version}/dist/mermaid.min.js"
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    sri = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")

    text = PIN.read_text(encoding="utf-8")
    text = re.sub(r'MERMAID_VERSION = "[^"]*"', f'MERMAID_VERSION = "{version}"', text)
    text = re.sub(r'MERMAID_SRI = "[^"]*"', f'MERMAID_SRI = "{sri}"', text)
    PIN.write_text(text, encoding="utf-8", newline="\n")

    print(f"pinned mermaid {version} ({len(data):,} bytes, released {released[:10]})")
    print(f"  {sri}")
    if age.days < 14:
        print(f"WARNING: released only {age.days} day(s) ago; prefer a release at least 14 days old")
    print("next: pytest tests/e2e, read the release notes, bump mekhen's minor version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
