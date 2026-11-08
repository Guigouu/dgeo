## What does this change?

<!-- Short description and motivation. Link the issue if any (Fixes #…). -->

## Checklist

- [ ] `pytest -q` passes and `dgeo demo` still recovers the synthetic position (< 150 m)
- [ ] Physics / scoring changes are covered by a test on synthetic data
- [ ] New source files start with `# SPDX-License-Identifier: AGPL-3.0-or-later`
- [ ] Docs updated (`README.md`, `docs/`) if behaviour changed, entry added to `CHANGELOG.md`
- [ ] Release: feature → `python scripts/bump_version.py minor`, fix → `patch`, docs/tests/CI → none
- [ ] No real recording, ADS-B capture, coordinates or timestamps that could identify a person's location
- [ ] I agree that my contribution is licensed under AGPL-3.0-or-later
