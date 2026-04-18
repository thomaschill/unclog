# unclog

**Every MCP, skill, hook, and CLAUDE.md line you've installed is charging you on every turn.** `unclog` scans your Claude Code install, measures the bloat, and offers a reversible fix.

```
uv tool install unclog
unclog
```

<!-- TODO: screenshot of `unclog` finding bloat — include probed MCPs, auto-memory, and hook findings -->

## What it finds

| Category | What unclog surfaces |
|---|---|
| **CLAUDE.md** | oversized sections, cross-project duplicates, scope mismatches, dead file refs, broken `@`-import chains (depth ≤5) |
| **Auto-memory** | per-project `MEMORY.md` files auto-injected into every turn |
| **Hooks** | every-turn handlers whose stdout silently joins your context |
| **MCP servers** | configured-but-dead, loaded-but-never-called, and — with `--probe-mcps` — live tools-schema token counts |
| **Skills / agents / commands** | zero-invocation entries older than your `unused_days` threshold |
| **Plugins** | stale installs and disabled-but-still-on-disk residue |
| **Project hygiene** | missing `.claudeignore` in repos that bundle `node_modules/`, `venv/`, etc. |
| **Baseline** | total tokens consumed before you type a message, tiered *lean* / *typical* / *clogged* |

## Fixing things

`unclog` never writes without a confirmed prompt. Conservative-safe findings are pre-checked; everything else defaults unchecked. A full snapshot is written to `~/.claude/.unclog/snapshots/<id>/` before any change, and every `y/N` prompt defaults to **No**.

```
unclog restore               # restore the most recent snapshot
unclog restore <id>          # restore a specific one
unclog restore --list        # enumerate every snapshot
```

<!-- TODO: screenshot of the fix selector -->

## Usage

```
unclog                       # scan global + every known project, report, fix
unclog --project <path>      # narrow the audit to a single project
unclog --report              # scan + report, skip the fix flow
unclog --json                # structured output (schema unclog.v0.1)
unclog --probe-mcps          # opt-in: spawn configured MCP servers to measure their tools schema
unclog --list-claude-md      # diagnostic: list every auto-injected context file with token counts
unclog --yes                 # apply every auto-checked finding
unclog --dry-run             # walk the fix flow without writing
unclog --plain               # ASCII-only, CI-safe
```

`NO_COLOR=1` or a non-TTY pipe auto-enables `--plain`.

## Guarantees

- **Local-only.** Every measurement runs in-process via `tiktoken`. No telemetry, no accounts, no network.
- **Reversible.** Every apply pass writes a full snapshot first.
- **No background processes.** `unclog` runs, prints, optionally applies, exits.

## Requirements

Python 3.11+, Claude Code ≥ 2.1.90, macOS or Linux. (Windows: v0.2.)

## Development

```
uv sync --all-extras --dev
uv run pytest
uv run ruff check src tests
uv run mypy src/unclog
```

See [SPEC.md](SPEC.md) for the full data model, detector list, and config schema.

## License

MIT. See [LICENSE](LICENSE).
