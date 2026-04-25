# unclog

[![PyPI](https://img.shields.io/pypi/v/unclog.svg)](https://pypi.org/project/unclog/)
[![GitHub stars](https://img.shields.io/github/stars/thomaschill/unclog?style=flat&logo=github)](https://github.com/thomaschill/unclog)

**Every agent, skill, and MCP server you've installed occupies your Claude Code context window on every turn.** `unclog` scans your install, shows you what's costing tokens, and lets you handpick what to delete — locally, with no telemetry, no accounts, and no network calls.

```
# install
uv tool install unclog

# run
unclog
```

![unclog scan output](https://raw.githubusercontent.com/thomaschill/unclog/main/media/screenshot.png)

## What it shows

- Every **agent** in `~/.claude/agents/`, with its token cost.
- Every **skill** in `~/.claude/skills/`, with its token cost.
- Every **slash command** in `~/.claude/commands/`, with its token cost.
- Every **MCP server** declared in `~/.claude.json` (global and per-project). Per-MCP token cost isn't available from session data, so the picker shows `— tok`; this is honest, not a bug.
- A **baseline** — the total tokens loaded before you type a single message.

## Remove what's costing you

![unclog interactive curate flow](https://raw.githubusercontent.com/thomaschill/unclog/main/media/demo.gif)

After the scan, one sectioned picker opens. Tick the agents / skills / commands / MCPs you want to remove, hit `enter`, confirm once, done. A post-apply line shows how many tokens you saved and the new baseline.

Picker keys: `↑↓` move · `space` toggle · `a` / `A` check section / all · `n` / `N` clear section / all · `enter` apply · `q` quit.

**Actions are destructive.** Deleted files are removed from disk. MCP servers are deleted from `~/.claude.json`. There is no undo and unclog keeps no snapshot — if you want to revive something, restore it from your own backup or reinstall.

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
