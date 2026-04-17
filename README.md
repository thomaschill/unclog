# unclog

**Every MCP, skill, and CLAUDE.md line you've installed is charging you on every turn.**
`unclog` scans your Claude Code setup and shows you exactly what's costing
what. One command. No network. Fully reversible.

```
uv tool install unclog
unclog
```

---

## What it does

`unclog` reads your `~/.claude` directory, the system block of your most
recent session JSONL — the ground-truth record of what Claude Code actually
loaded — and the `tool_use` records in each project's latest session to see
which MCPs are actually getting called. It produces a token-cost breakdown
by source. No servers spawned. No estimates when real measurements are
available.

By default, `unclog` audits your global install **and every project** listed
in `~/.claude.json`. That's the only way cross-project CLAUDE.md duplicates
and scope mismatches actually surface. Narrow to a single project with
`--project <path>`.

Then it offers an interactive fix selector so you can act on what you find,
with a snapshot written before any change so every action is reversible.

## What it finds

- **CLAUDE.md sections** — global and project-level, ranked by token weight.
  Identical sections repeated across projects are flagged as duplicates;
  project-only advice living in global (and vice versa) surfaces as a scope
  mismatch; sections over ~1,000 tokens are flagged for consolidation.
- **MCP servers** — every definition loaded into context per turn. Servers
  configured but never loaded in the latest session show up as *dead*; ones
  that load every session but never get invoked show up as *unused* with
  their per-session token cost attached.
- **Skills, agents, commands** — accumulated entries silently present in
  every session, scored against invocation history
- **Plugins** — stale installs, disabled-but-still-on-disk residue
- **Project hygiene** — missing `.claudeignore` in repos that bundle
  `node_modules/`, `venv/`, or other large trees
- **Session baseline** — total tokens consumed before you type a single
  message, tiered as *lean* / *typical* / *clogged*

## Fixing things

`unclog` does not change anything without your say-so.

When it finds bloat, it presents a fix selector. Conservative-safe items are
pre-checked. Everything else defaults to unchecked. Every y/N prompt defaults
to **No** — mashing enter exits cleanly.

Before any change is applied, a full snapshot is written to:

```
~/.claude/.unclog/snapshots/<id>/
```

Each snapshot includes a `manifest.json` and a `files/` tree mirroring the
originals. Restore any snapshot at any time:

```
unclog restore              # restore the most recent snapshot
unclog restore <id>         # restore a specific one
unclog restore --list       # enumerate every snapshot on disk
```

## What it doesn't do

- **No telemetry.** Nothing leaves your machine.
- **No network calls.** Every measurement is local — `tiktoken` runs
  in-process, no API is called.
- **No accounts.** Nothing to sign up for.
- **No background processes.** `unclog` runs, prints, optionally applies,
  exits. It does not install hooks, daemons, or watchers.
- **No writes outside your home.** Only `~/.claude/` and explicitly audited
  project directories. Plugin cache dirs are read-only.

## Install

```
uv tool install unclog
```

Requires Python 3.11+ and Claude Code ≥ 2.1.90. macOS and Linux only for
v0.1 — Windows support is tracked for v0.2.

## Usage

```
unclog                       # scan global + every known project, report, fix
unclog --project <path>      # narrow the audit to a single project
unclog --report              # scan + report, skip the fix flow
unclog --json                # structured output to stdout (schema unclog.v0.1)
unclog --plain               # ASCII-only, no colour, no animation (CI-safe)
unclog --yes                 # apply every auto-checked finding without prompts
unclog --dry-run             # walk the fix flow without writing anything
unclog --no-animation        # static frames only; keeps colour

unclog restore               # restore the most recent snapshot
unclog restore <id>          # restore a specific snapshot
unclog restore --list        # list every snapshot on disk
```

`NO_COLOR=1` and piping to a non-TTY both auto-enable `--plain`.

## How it works

1. Reads `~/.claude.json`, `settings.json`, and the global `CLAUDE.md`.
2. Enumerates skills, agents, commands, and plugin installs.
3. Reads every registered project's `CLAUDE.md` / `CLAUDE.local.md` so
   cross-scope duplicates and mismatches can be detected.
4. Locates the most recent session JSONL across all known projects and
   parses its system block — that's what Claude Code actually loaded.
5. Aggregates `tool_use` counts across each project's latest session so
   MCPs that load but never get called can be flagged.
6. Measures every text source with `tiktoken` (GPT-4 encoding) for a
   reproducible, local token count.
7. Runs detectors against the parsed state and produces a `Finding` list.
8. Optionally renders the fix selector and applies selected actions, each
   captured in a snapshot first.

`unclog` is built from pure data transforms end-to-end — the scan produces
an immutable state, detectors are pure functions of it, and the apply layer
is the only code that writes. You can `unclog --json` any time to inspect
the full parsed world.

## Config

`unclog` reads `~/.claude/.unclog/config.toml` if present. Every key is
optional and defaults are sensible. See `SPEC.md §21` for the full list.
Typical overrides:

```toml
[thresholds]
# How many days of zero use counts as "unused". Default 90.
unused_days = 90

# Promote a CLAUDE.md section to global only if it appears identically in
# at least this many projects. Default 3.
promote_min_projects = 3

[ui]
# Disable all motion. CLI --no-animation also respected.
animation = true
```

## Development

```
uv sync --all-extras --dev
uv run pytest
uv run ruff check src tests
uv run mypy src/unclog
```

## License

MIT. See [LICENSE](LICENSE).
