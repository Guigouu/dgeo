# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that the version is consistent across pyproject.toml, dgeo/__init__.py and CHANGELOG.md."""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
v = re.search(r'(?m)^version = "([^"]+)"', (root / "pyproject.toml").read_text()).group(1)  # no tomllib on 3.10
init = re.search(r'__version__ = "([^"]+)"', (root / "dgeo/__init__.py").read_text()).group(1)
changelog = (root / "CHANGELOG.md").read_text()
errors = []
if init != v:
    errors.append(f"dgeo/__init__.py has {init}, pyproject.toml has {v}")
if not re.search(rf"^## {re.escape(v)}\b", changelog, re.M):
    errors.append(f"CHANGELOG.md has no '## {v}' section")
if not re.fullmatch(r"\d+\.\d+\.\d+", v):
    errors.append(f"version {v!r} is not X.Y.Z")
for e in errors:
    print(f"::error::{e}")
sys.exit(1 if errors else 0)
