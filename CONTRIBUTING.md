# Contributing

Thanks for helping. Issues and pull requests are welcome in English or Spanish.

## The most useful contribution: a failing file

If `verify-print` missed a bug that ruined a print, or flagged something that printed fine, open an issue with:

1. The STL or 3MF (or a minimal version that still reproduces it).
2. The exact command and its `--json` output.
3. What actually happened on the printer (photo welcome), printer model and slicer version.

Real failing files are what turn a heuristic into a validated check — the [thin-pocket design note](docs/thin-pocket-detection.md) is waiting on exactly this.

## Code

```bash
pip install -e ".[dev]"
python -m pytest
```

- Keep checks deterministic: same input, same verdict. Random sampling must take a seed.
- Every new check needs a synthetic fixture that reproduces the bug class and a test that fails without the check.
- Document a known false negative as an `xfail` test instead of hiding it.
- Tests must not need a slicer binary: unit-test parsing and command building instead.

## Good first issues

- Add a `--slicer-bin` flag (and Linux/Windows default paths) so slicing works off macOS.
- Targeted thin-pocket detection ([design note](docs/thin-pocket-detection.md)).
- Resolve simple dict aliasing in the parameter linter (the current `xfail`).
