# Release validation — 2026-09-09

- Python: 92 tests passed using `TMPDIR=/private/tmp python3 -B -m unittest discover -s tests -p "test_*.py" -v`.
- JavaScript format: 26 checks passed using `node tests/test_format.js`.
- Tests include local HTTP and detached process start/status/stop with temporary synthetic logs.
- Initial sandbox run could not bind local sockets and encountered macOS temporary-path alias assertions; the run above passed with loopback access and a canonical temporary path.
- Browser interaction tests and real-log UI acceptance were not rerun for this release.
- Packaged source excludes local state, credentials, databases, logs and historical outputs. MIT license retained.
