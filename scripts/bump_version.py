# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bump the version: python scripts/bump_version.py 0.2.0

Updates pyproject.toml and dgeo/__init__.py and adds an empty CHANGELOG section to fill in.
Merging the resulting PR into main publishes the release automatically.
"""
import re
import sys
from pathlib import Path

if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
    sys.exit("usage: python scripts/bump_version.py X.Y.Z")
new = sys.argv[1]
root = Path(__file__).resolve().parents[1]
p = root / "pyproject.toml"
p.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{new}"', p.read_text(), count=1))
p = root / "dgeo/__init__.py"
p.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', p.read_text(), count=1))
p = root / "CHANGELOG.md"
s = p.read_text()
if f"## {new}" not in s:
    s = s.replace("# Changelog\n", f"# Changelog\n\n## {new}\n\n- TODO: describe the changes.\n", 1)
    p.write_text(s)
print(f"version -> {new}; now fill in CHANGELOG.md and open a PR")
