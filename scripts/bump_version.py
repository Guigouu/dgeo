# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bump the version for a release: python scripts/bump_version.py minor|patch|major|X.Y.Z

- feature (feat) -> minor, bug fix (fix) -> patch; docs, tests and CI alone do not need a release.
- Updates pyproject.toml and dgeo/__init__.py, and turns the "## Unreleased" section of CHANGELOG.md
  into "## X.Y.Z — <date>" (or adds an empty section to fill in).

Merging the resulting PR into main publishes the GitHub Release automatically (see .github/workflows/release.yml).
"""
import datetime
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
pyproject = root / "pyproject.toml"
cur = re.search(r'(?m)^version = "(\d+)\.(\d+)\.(\d+)"', pyproject.read_text())
if len(sys.argv) != 2 or not cur:
    sys.exit(__doc__)
major, minor, patch = map(int, cur.groups())
arg = sys.argv[1]
if arg == "major":
    new = f"{major + 1}.0.0"
elif arg == "minor":
    new = f"{major}.{minor + 1}.0"
elif arg == "patch":
    new = f"{major}.{minor}.{patch + 1}"
elif re.fullmatch(r"\d+\.\d+\.\d+", arg):
    new = arg
else:
    sys.exit(__doc__)

pyproject.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{new}"', pyproject.read_text(), count=1))
p = root / "dgeo/__init__.py"
p.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', p.read_text(), count=1))
p = root / "CHANGELOG.md"
s = p.read_text()
today = datetime.date.today().isoformat()
if re.search(rf"(?m)^## {re.escape(new)}\b", s):
    pass
elif re.search(r"(?m)^## Unreleased\s*$", s):
    s = re.sub(r"(?m)^## Unreleased\s*$", f"## {new} — {today}", s, count=1)
else:
    s = s.replace("# Changelog\n", f"# Changelog\n\n## {new} — {today}\n\n- TODO: describe the changes.\n", 1)
p.write_text(s)
print(f"version {'.'.join(cur.groups())} -> {new}; check CHANGELOG.md and open a PR")
