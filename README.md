# unclog

[![PyPI](https://img.shields.io/pypi/v/unclog.svg)](https://pypi.org/project/unclog/)

**Every agent, skill, and MCP server you've installed occupies your Claude Code context window on every turn.** `unclog` scans your install, shows you what's costing tokens, and lets you handpick what to delete.

```
# install
uv tool install unclog

# run
unclog
```

![unclog scan output](https://raw.githubusercontent.com/thomaschill/unclog/main/screenshot.png)

## What it shows

- Every **agent** in `~/.claude/agents/`, with its token cost.
- Every **skill** in `~/.claude/skills/`, with its token cost.
- Every **MCP server** declared in `~/.claude.json` (global and per-project), with its measured session-token cost when available.
- A **baseline** — the total tokens loaded before you type a single message.

## Fixing things

After the scan, one sectioned picker opens. Tick the agents / skills / MCPs you want to remove, hit `enter`, confirm once, done. A post-apply line shows how many tokens you saved and the new baseline.

Picker keys: `↑↓` move · `space` toggle · `a` / `A` check section / all · `n` / `N` clear section / all · `enter` apply · `q` quit.

**Actions are destructive.** Deleted files are removed from disk. Commented-out MCPs are renamed to `__unclog_disabled__<name>` in your config (easy to restore by hand, but there is no undo command).

## Guarantees

- **Local-only.** All measurement runs in-process via `tiktoken`. No telemetry, no accounts, no network.
- **No background processes.** `unclog` runs, prints, optionally applies, exits.

## Requirements

Python 3.11+, Claude Code ≥ 2.1.90, macOS or Linux.

## Development

```
uv sync --all-extras --dev
uv run pytest
uv run ruff check src tests
uv run mypy src/unclog
```

## License

MIT. See [LICENSE](LICENSE).
