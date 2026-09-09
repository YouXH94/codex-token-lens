# Codex Token Lens · 0.2.0

[简体中文](README.md) | English

**A standalone Codex usage dashboard that reads local logs without modifying them.** It uses a light, multi-column layout, opens to Threads by default, and displays numbers using Chinese units. It requires no MCP, API key, model calls, or Codex configuration changes. This English README does not change the application's Chinese interface.

> This release contains Python source code, not a signed installer. Running the application reads local logs.

## Starting and stopping

Requires **Python 3.10+**. At runtime, only the Python standard library and your system browser are needed—no pip, Node, npm, or resource downloads.

Extract into a separate directory such as `~/Applications/codex-token-lens`. **Do not place it inside `~/.codex`, `CODEX_HOME`, or any directory being scanned.** Run these commands from the project directory:

```bash
# macOS / Linux: start a detached process and open the browser
python3 -B service.py start
python3 -B service.py status
python3 -B service.py open
python3 -B service.py stop
```

On Windows, use `py -3 -B service.py start` (and the same other actions), or double-click `start-windows.bat`. On macOS, run `bash start-macos.command`; on Linux, run `bash start-linux.sh`. Opening the macOS launcher through Finder may require granting the file executable permission; do not disable system security protections.

`start` creates a detached process that can keep running after the terminal closes. `stop` sends a shutdown request only to an authenticated Lens instance; it does not terminate Codex. No login item, system service, or shell configuration is installed. Restart Lens after rebooting. If a hosting environment or sandbox reaps all its child processes, run `start` from a regular system terminal instead of changing the Codex sandbox or installing a system service.

The default listener is `127.0.0.1:8765` only. Use the authenticated URL printed on startup; run `service.py open` to reopen it later. Use `--port 8766` if the default port is occupied. **Do not share authenticated URLs, `runtime.json`, or `service.log`.**

See [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) for a local deployment prompt in Chinese. It describes installation, not querying usage through Codex.

## Main views

| View | Features |
| --- | --- |
| Daily / hourly | Date selection, 24-hour input/output bars, hover details, and hour selection to filter Threads |
| Period | Calendar week, calendar month, or a custom date range; daily trends and heatmaps; select a date to drill into hours |
| Threads | Search; project, model, and source filters; raw-value sorting; pagination; parent/child grouping; details, event ledger, and exports |

The header shows today's usage, usage for the selected period, estimated Credits where available, and the cache ratio. Details show cached tokens within input and reasoning tokens within output to avoid double counting. Unknown values appear as `—`; incomplete coverage is marked explicitly.

Thread aliases and display preferences are saved only in **the current browser's local storage**, without modifying Codex sessions. When real logs lack titles, the UI uses the project and a short ID; it does not read prompts to generate titles. Descriptive demo titles are synthetic. Clearing site data removes aliases; switching browsers or ports does not synchronize them.

### Chinese number formatting

| Raw value | Default display | Optional 万/亿 style |
| ---: | ---: | ---: |
| 1,234 | 1.23千 | 1.23千 |
| 12,345 | 1.23万 | 1.23万 |
| 124,532 | 1.25十万 | 12.45万 |
| 1,862,450 | 1.86百万 | 186.25万 |

Units include 千 (thousand), 万 (10 thousand), 十万 (100 thousand), 百万 (million), 千万 (10 million), 亿 (100 million), and 万亿 (trillion). Hover or open the exact-value dialog to see integers. CSV and JSON exports retain full precision. Abbreviations do not affect sorting, totals, or cost calculations.

## Data sources and write boundaries

```text
Original Codex sessions / archived_sessions (read-only)
                         ↓
              Standalone Python Lens process
                         ↓
    ~/.codex-token-lens/usage.sqlite3 (Lens-owned database)
                         ↓
              Local browser at 127.0.0.1
```

Lens reads `CODEX_HOME` by default, falling back to `~/.codex`. You can specify multiple sources:

```bash
python3 -B service.py start --home "$HOME/.codex" --home "/path/to/other/codex-home"
```

Stop an existing Lens instance before changing its arguments. `start` reuses a running instance without reconfiguring it.

Alternatively, copy `config.example.json`, remove unused sample paths, and pass `--config /path/to/lens-roots.json`. This JSON configures Lens sources; it is **not Codex's config.toml**. For mixed WSL/Windows environments, supply accessible log paths explicitly. Lens does not guess cross-platform paths or modify Codex configuration.

The default state directory, `~/.codex-token-lens/`, holds SQLite and its auxiliary files, `runtime.json`, and `service.log`. Browser exports go to your chosen download location. Command-line exports must target a location outside Codex directories. See [SECURITY.md](SECURITY.md) for the full boundaries (Chinese).

Lens does not invoke the Codex CLI or modify `config.toml`, `auth.json`, original JSONL files, skills, hooks, or AGENTS.md. It does not enable MCP or telemetry. New database and export destinations are checked; protected directories and unsafe link targets are rejected. **Deployment requires no changes to Codex files.**

## Counting rules and limitations

- Total tokens = input + output. Cached tokens are included in input; reasoning tokens are included in output. Counts do not imply access to hidden chain-of-thought text.
- Cumulative snapshots are converted to deltas. Duplicate notifications and identifiable archive/multi-agent replays are filtered. Records that cannot be reliably attributed go into diagnostics instead of being silently included in exact totals.
- A period is a reporting interval, not an official subscription allowance. Credits are estimates based on bundled model/tier rates, **not dollars, actual charges, or remaining account quota**. Unknown models, tiers, or fields reduce estimate coverage; prices are not invented.
- Quota information is a timestamped snapshot already present in logs and may be stale. Lens does not query the account backend for it.
- Coverage is limited to scanned local logs. Remote devices, cloud tasks, and missing or unwritten usage cannot be filled in automatically. Aggregated `codex exec --json` output is treated diagnostically, not mixed with standard rollout records.
- The browser supports local time, Beijing, New York, and UTC. Calendar weeks run Monday through Sunday. Hours are assigned by the recorded event timestamp. During daylight-saving fall-back, repeated civil hours are combined into one hourly bucket; this is not a second-by-second measure of model activity.

## Performance and offline use

By default, Lens checks logs every 15 seconds, reads appended bytes, and updates its own SQLite database. Unchanged reports are reused; the frontend uses ETags and pauses polling when the page is hidden. **This still involves directory enumeration and file-state checks, not native filesystem watching.** Initial scans and large active histories can increase CPU, disk, and memory use. This package does not provide benchmarks for your device.

The program makes no model/API requests and does not download prices. It serves the dashboard and manages its own process over loopback. The static UI uses no CDN, remote fonts, or analytics scripts. Review and update `rates.json` manually when needed.

## Other run modes

```bash
# Foreground; press Ctrl+C to stop
python3 -B lens.py

# Scan once and export interactive HTML outside Codex directories
python3 -B lens.py --once --export "$HOME/Desktop/lens-report.html"

# Set the polling interval explicitly
python3 -B service.py start --interval 15
```

Backend `local` and `UTC` timezones need no third-party timezone package; other IANA zones require a system timezone database. Browser-side Beijing and New York formatting uses browser timezone support and requires no Python timezone package.

Before upgrading, stop the old process **after confirming it belongs to Lens**. Version 0.2.0 keeps the existing SQLite schema. For important statistics, back up Lens's own data directory after stopping the old instance. Do not delete Codex logs to clear statistics. Rebuilding the cache means handling only Lens-owned SQLite and auxiliary files while Lens is stopped.

## Tests and release validation

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
# Optional development checks, not runtime dependencies:
node tests/test_format.js
python3 -B demo.py  # Generate synthetic preview.html first
python3 -B tests/browser_smoke.py
```

See [TEST_RESULTS.md](TEST_RESULTS.md) for this release's validation results.

`tests/browser_smoke.py` requires development installations of Playwright and Chromium. Set `LENS_CHROMIUM` to choose a browser. These dependencies are not needed for everyday use.

This is an independent local usage tool, not an official OpenAI client or billing system.

## SwiftBar menu bar plugins

Companion project: [swiftbar-codex-usage](https://github.com/YouXH94/swiftbar-codex-usage), with an [English README](https://github.com/YouXH94/swiftbar-codex-usage/blob/main/README.en.md). The daily-token plugin reads this service's `/api/report`; the remaining-quota plugin runs independently.

Starting Lens with the default commands above creates `~/.codex-token-lens/runtime.json`, which the companion plugin reads by default. If you customize `--state-dir`, set the plugin's `TOKEN_LENS_STATE` accordingly. Do not share the state file or authenticated dashboard URLs.
