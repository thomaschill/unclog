# unclog — v0.1 Spec

## 1. Vision

A single-command CLI that audits a Claude Code installation, surfaces bloat that is clogging the context window, and offers interactive, reversible cleanup. Local-only, zero telemetry, fast enough to run on whim. Beautiful enough to want to run.

Primary user: a developer whose `~/.claude/` has accumulated skills, agents, MCP servers, and CLAUDE.md rules they no longer use, slowing Claude Code and inflating cost.

Installation target:
```
uv tool install unclog
unclog
```

## 2. Non-goals for v0.1

- Any network call by default (except optional `--accurate` token counting via Anthropic API).
- Support for LLM tools other than Claude Code. Architecture must allow extension later; v0.1 ships Claude Code only.
- LLM-powered CLAUDE.md rewriting.
- Secrets scanning in transcripts.
- Permissions-list pruning, hook cleanup, model-selection advice.
- Cross-developer team audits.
- A web UI, VS Code extension, or scheduled cron.

## 3. User experience

### 3.1 The flow

```
$ unclog

 ▁▂▃  unclog
      v0.1.0  ·  local-only audit

 [custom flow spinner]  Reading config…
 [custom flow spinner]  Parsing last session per project…
 [custom flow spinner]  Measuring CLAUDE.md composition…

 ┌─ Claude installation ────────────────────────────────────────┐
 │                                                              │
 │   42,180  tokens baseline · clogged                          │
 │                                                              │
 │   ████████ ████ ██████ ███ ██ █                              │
 │   github  notion CLAUDE skills other                         │
 │                                                              │
 │   • github-mcp              18,402  ⚠ never invoked          │
 │   • notion-mcp              11,220                           │
 │   • ~/.claude/CLAUDE.md      2,341                           │
 │   • skill descriptions ×47   6,188                           │
 │   • project CLAUDE.md        3,104                           │
 │   • other                      925                           │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘

 Found 8 issues that could save ~12,400 tokens.

 [global]         Remove skill  fashion-trend-analyst   187 tok · 0 uses 120d
 [global]         Disable MCP   chinese-marketing-mcp   7,840   · 0 uses 94d
 [global]         Remove 3 dead file refs in CLAUDE.md  —       · paths missing
 [project draper] Remove duplicate section from CLAUDE.md 412   · exists globally
 [global→draper]  Move rule "use yarn" to project scope  340    · only used here
 …

 Fix these?  [y/N]
```

If `y`:

```
 Select fixes to apply:

 [x] Remove skill  fashion-trend-analyst         187 tok
 [x] Remove 3 dead file refs in global CLAUDE.md  —
 [x] Remove duplicate section in draper CLAUDE.md 412 tok
 [ ] Disable MCP  chinese-marketing-mcp         7,840 tok
 [ ] Uninstall plugin foo-bar  (stale 127d)        —
 [ ] Move rule "use yarn" to draper scope          340 tok
 …

 space toggle · a all · n none · enter confirm · esc cancel
```

After confirm:

```
 Apply 3 changes? [y/N] y

 ✓ Snapshot  ~/.claude/.unclog/snapshots/2026-04-17-1842
 ✓ Applied 3 changes

   Baseline: 42,180 → [animated countdown] → 41,589
   Saved 591 tokens.

 Undo:  unclog restore latest
```

### 3.2 Safety principles

1. Every `[y/N]` prompt defaults to No. Mashing enter exits cleanly.
2. Destructive actions run only after a snapshot exists on disk.
3. Snapshots are full file copies, not diffs. Restore is a `cp` back.
4. Only conservatively-safe findings are pre-checked. Anything touching MCPs, plugins, or cross-scope moves requires explicit user selection.
5. Items flagged as "broken" (e.g. MCP configured but failed to load) are never auto-selected — user might be mid-fix.

## 4. Command surface

```
unclog                       # scan → report → interactive fix flow
unclog --report              # scan → report, exit (no prompts)
unclog --json                # structured output to stdout, no color/animation
unclog --plain               # ASCII-only, no color, no animation (for CI)
unclog --dry-run             # prompts run, no files written, no snapshot
unclog --yes                 # apply pre-checked safe findings, skip all prompts
unclog --project PATH        # narrow the audit to a single project
unclog --no-animation        # disable post-apply countdown; keeps color

unclog restore               # list snapshots
unclog restore <id>          # restore a specific snapshot
unclog restore latest        # restore the most recent snapshot

unclog --help
unclog --version
```

Rules:

- `--dry-run` and `--yes` are mutually exclusive; `--report` cannot combine with either. `--report | --json` imply no animation and `--plain`-compatible styling.
- `--json` emits a single JSON document, no logs to stdout; diagnostics go to stderr.
- `NO_COLOR=1` or non-TTY stdout auto-forces `--plain --no-animation`.
- Default scope is global + every project registered in `~/.claude.json` + the CWD if it looks project-like but isn't registered. `--project PATH` narrows to a single project.

## 5. Data sources

All reads are local, read-only until the user confirms apply. No file is opened for write outside the apply phase.

### 5.1 Always read

| Path | Purpose |
|---|---|
| `~/.claude.json` | `mcpServers`, `projects{}` (for enumeration), ignore `oauthAccount` |
| `~/.claude/settings.json` | permissions, enabled plugins |
| `~/.claude/CLAUDE.md` | size + content for lint |
| `~/.claude/CLAUDE.local.md` | same |
| `~/.claude/skills/*/SKILL.md` | frontmatter + body size |
| `~/.claude/agents/*.md` | existence + frontmatter |
| `~/.claude/commands/*.md` | existence |
| `~/.claude/plugins/installed_plugins.json` | version, installedAt |
| `~/.claude/stats-cache.json` | aggregate token use, activity |
| `~/.claude/history.jsonl` | per-project last-activity, prompt stream |

### 5.2 Read per audited project

| Path | Purpose |
|---|---|
| `<project>/CLAUDE.md`, `CLAUDE.local.md` | size + lint |
| `<project>/.claude/settings.json`, `settings.local.json` | scope overrides |
| `<project>/.claude/skills/*/SKILL.md` | project skills |
| `<project>/.claude/agents/*.md` | project agents |
| `<project>/.claude/commands/*.md` | project commands |
| `<project>/.mcp.json` | shared MCP config |
| `<project>/.claudeignore` | existence + size check |
| `~/.claude/projects/<encoded-path>/<latest>.jsonl` | system block only (first message) — ground-truth cost of what was actually injected last session |

### 5.3 Explicitly NOT read in v0.1

- Full session JSONL (only the system block of the latest session per project).
- Plan markdowns, todos, file-history, shell-snapshots, debug logs.
- Marketplace caches (parked for v0.2 update checks).

### 5.4 Session JSONL system-block parsing

This is the single load-bearing technical read. From `~/.claude/projects/<encoded>/<latest>.jsonl`, parse only the first message's system content block and decompose it into source contributions:

- Global CLAUDE.md content → byte/token count
- Project CLAUDE.md content → byte/token count
- Each skill description loaded → attributed to its SKILL.md
- Each MCP server's tool schemas → attributed to its `mcpServers` entry
- Everything else → "other"

Rules:

- If no session JSONL exists for a project, mark MCP + skill costs as `unknown` and fall back to estimating from config (count skill descriptions, flag MCPs as "size not measured").
- Never spawn MCP servers to measure them in v0.1.
- Parser must tolerate unknown fields and malformed lines. Skip, don't crash.

## 6. Finding types

Each finding is a typed record with: id, scope, title, reason, evidence, token savings estimate, action, auto-check default.

| ID | Title | Scope | Auto-check |
|---|---|---|---|
| `unused_skill` | Remove skill never invoked | global/project | Yes if 0 uses ≥ 90d |
| `unused_agent` | Remove agent never invoked | global/project | Yes if 0 uses ≥ 90d |
| `unused_command` | Remove slash command never invoked | global/project | Yes if 0 uses ≥ 90d |
| `dead_mcp` | MCP configured but failed to load | global/project | **No** — user may be mid-fix |
| `unused_mcp` | MCP loaded but never invoked | global/project | **No** — user opts in |
| `stale_plugin` | Plugin enabled, unused ≥ threshold, or > 90d since install | global | **No** |
| `disabled_plugin_residue` | Plugin fully disabled but still installed on disk | global | **No** — flag only |
| `claude_md_dead_ref` | File path in CLAUDE.md no longer exists | global/project | Yes |
| `claude_md_duplicate` | Exact-match section in two scopes | global + project | Yes (remove from global) |
| `claude_md_oversized` | Section > 1,000 tokens | global/project | **No** — open in editor |
| `scope_mismatch_global_to_project` | Global rule only references one project | global → project | **No** |
| `scope_mismatch_project_to_global` | Identical section across ≥ 3 projects | project → global | **No** |
| `missing_claudeignore` | No `.claudeignore` in a project with `node_modules`/`.venv` | project | **No** — flag only |

"Uses" are derived from `stats-cache.json` aggregates + `history.jsonl` timestamps in v0.1. Per-tool invocation counts land in v0.2 when we parse full session JSONL.

**Configurable thresholds** (see §22 for config file). The following have documented defaults that users can override:

| Threshold | Default | Key |
|---|---|---|
| Never-used window | 90 days | `thresholds.unused_days` |
| Stale plugin window | 90 days | `thresholds.stale_plugin_days` |
| Scope-mismatch promotion minimum projects | 3 | `thresholds.promote_min_projects` |

Non-configurable in v0.1 (requires code change): oversized-section threshold (`1,000 tokens`), baseline tier thresholds (`20k` / `50k`).

### 6.1 Actions

Each finding maps to exactly one of these action primitives:

- `delete_file(path)` — skill/agent/command file removal
- `comment_out_mcp(server_name)` — edits `~/.claude.json` or `.mcp.json`, preserves config via comment marker
- `remove_claude_md_section(path, heading)` — strips a named section
- `remove_claude_md_lines(path, line_numbers)` — used for dead-ref lines
- `move_claude_md_section(from_path, to_path, heading)` — cross-scope move
- `disable_plugin(name_with_marketplace)` — sets the plugin's entry in `settings.json`'s `enabledPlugins` map to `false`. Files under `~/.claude/plugins/cache/<name>/` are untouched. This is the primary plugin-cleanup action: it stops the plugin's skills/agents/commands/MCPs from being loaded on the next session, eliminating their context cost. Fully reversible by flipping the entry back to `true` (snapshot records the prior value).
- `uninstall_plugin(name)` — removes the entry from `installed_plugins.json` AND deletes the cache dir. Only offered as a separate action for plugins already disabled for `≥ 90d` with `disabled_plugin_residue` finding. Not offered on live plugins.
- `open_in_editor(path, line_hint)` — no auto-edit; opens `$EDITOR`
- `flag_only` — purely informational, no apply action

Every action has a matching `undo` recorded in the snapshot manifest.

## 7. CLAUDE.md handling (deep dive)

CLAUDE.md is the highest-leverage target. Approach:

1. **Measurement**: from the session JSONL system block, attribute exact token cost to each CLAUDE.md file loaded. Don't trust file-size-on-disk — Claude Code may truncate, dedupe, or partially load.
2. **Section parsing**: parse markdown into a section tree by heading level. Each section has a path (heading chain), byte range, and token cost.
3. **Lint passes**:
   - Dead file refs: regex out anything that looks like a path, stat it. Lines that only consist of a dead ref get flagged for removal. Lines with a dead ref mixed with prose get flagged for manual edit.
   - Exact duplicate sections across global/project: hash section body, match.
   - Oversized section: `> 1,000` tokens → flag with "open in editor" action.
   - Single-project relevance: if section body mentions only paths under one project, suggest moving.
4. **No LLM rewriting in v0.1**. Suggestions that require judgment get `open_in_editor` actions, not auto-edits.

Deferred to v0.2:

- LLM-powered shrinking (user's own API key, opt-in command).
- Fuzzy duplicate detection (near-match sections).
- Suggesting sections that should become skills.

## 8. Scope model

Every finding is tagged with one of:

- `[global]` — lives in `~/.claude/`
- `[project <name>]` — lives in `<project>/.claude/` or `<project>/CLAUDE.md`
- `[global → project <name>]` — recommended migration global → project
- `[project <name> → global]` — recommended promotion project → global

Project name is derived from the last path segment unless a project config provides a name.

### 8.1 Which projects get audited

- Default: global + every key in `~/.claude.json`'s `projects{}` + the CWD if it looks project-like (`.claude/` or `CLAUDE.md` present) but isn't already registered. Paths that no longer exist on disk are reported as stale project warnings.
- `--project PATH`: narrow the audit to exactly that path (global still scanned).

### 8.2 Cross-scope findings

- Global → project mismatch requires at least one concrete path from the section that resolves only within one project.
- Project → global promotion requires the exact same section body hash in `N ≥ 3` projects.

## 9. Snapshots & restore

### 9.1 Layout

```
~/.claude/.unclog/
  snapshots/
    2026-04-17-1842/
      manifest.json
      files/
        home/
          .claude/
            CLAUDE.md
            skills/fashion-trend-analyst/SKILL.md
        projects/
          draper/
            CLAUDE.md
  cache/
    tokens/           # tiktoken results keyed by content hash
    session-meta/     # (v0.2) indexed session JSONL reads
```

### 9.2 Manifest

```json
{
  "id": "2026-04-17-1842",
  "created_at": "2026-04-17T18:42:03Z",
  "unclog_version": "0.1.0",
  "actions": [
    {
      "finding_id": "unused_skill:fashion-trend-analyst",
      "action": "delete_file",
      "original_path": "/Users/tom/.claude/skills/fashion-trend-analyst/SKILL.md",
      "snapshot_path": "files/home/.claude/skills/fashion-trend-analyst/SKILL.md"
    }
  ]
}
```

### 9.3 Restore

`unclog restore <id>` reads the manifest and copies each `snapshot_path` back to its `original_path`. For directory-level deletions (e.g. plugin uninstall), the snapshot stores the entire directory tree.

### 9.4 Retention

v0.1: keep all snapshots. Add a one-line message when snapshot count > 20 suggesting `unclog snapshots prune` (v0.2).

## 10. Output modes

| Mode | TTY required | Color | Animation | Audience |
|---|---|---|---|---|
| default | yes | yes | yes | human, interactive |
| `--report` | no | auto | no | human, no prompts |
| `--plain` | no | no | no | CI logs, piped |
| `--json` | no | no | no | scripts |

Spec for `--json`:

```json
{
  "version": "0.1.0",
  "generated_at": "...",
  "baseline_tokens": 42180,
  "baseline_tier": "clogged",
  "projects_audited": ["/Users/tom/projects/draper"],
  "composition": [
    {"source": "mcp:github-mcp", "tokens": 18402, "scope": "global"},
    ...
  ],
  "findings": [
    {
      "id": "unused_skill:fashion-trend-analyst",
      "type": "unused_skill",
      "scope": {"kind": "global"},
      "title": "Remove skill never invoked",
      "reason": "0 uses in 120d",
      "evidence": {"last_used": null, "age_days": 120},
      "token_savings": 187,
      "action": {"primitive": "delete_file", "path": "..."},
      "auto_checked": true
    }
  ]
}
```

## 11. Visual design system

### 11.1 Aesthetic reference

Astral (uv, ruff): restrained monochrome with one accent, typography-first, generous whitespace. Identity layered on top via motion and the treemap.

### 11.2 Color palette

| Role | Color | Usage |
|---|---|---|
| Accent | teal `#14b8a6` | product name, primary CTAs, baseline highlight |
| Severity green | `#22c55e` | "lean" baseline, successful apply |
| Severity amber | `#eab308` | "typical" baseline, stale-plugin warnings |
| Severity red | `#ef4444` | "clogged" baseline, broken MCPs, dead refs |
| Foreground | default terminal | body text |
| Dim | `#6b7280` | timestamps, paths, metadata |

Accent is used sparingly — the product name, the hero number accent, and the active prompt. Severity colors only on status columns and severity badges. Everything else is default / dim.

### 11.3 Typography

- Bold: headings, baseline number, finding titles.
- Dim: timestamps, paths, byte counts, hints.
- Italic: rarely — only short finding explanations.
- Never combine bold + color on the same span.

### 11.4 ASCII wordmark

Two-line, small, fixed. Drawn by hand — not pyfiglet. Rendered in teal. Below it, a subtitle line in dim.

```
 ▁▂▃  unclog
      v0.1.0  ·  local-only audit
```

Appears only on the default interactive flow. Suppressed in `--report`, `--json`, `--plain`.

### 11.5 Hero baseline number

Rendered with `rich.text` at a larger style, comma-formatted, colored by tier:

- `< 20,000` → green, subtitle "lean"
- `20,000 – 50,000` → amber, subtitle "typical"
- `> 50,000` → red, subtitle "clogged"

### 11.6 Composition treemap

Horizontal stacked bar, one segment per top-level composition source, width proportional to token share. Segments colored on a cool gradient (teal → slate → deeper teal). Inline labels inside segments ≥ 12% width, else omitted. Legend below with exact token counts, sorted descending.

Width target: terminal width minus 4, capped at 80.

### 11.7 Custom spinner

Frame set (cycles left to right, evokes debris clearing a pipe):

```
"·   ", "··  ", "·•• ", "••• ", " ••·", "  •·", "   ·"
```

Paired with a live status line. Spinner holds an extra ~150ms after the phase completes before snapping to `✓`, so the rhythm feels intentional.

### 11.8 Motion budget

Only two places move:

1. Scan phase spinner + status line.
2. Post-apply baseline countdown (`42,180 → 41,993 → 41,589` over ~400ms via `rich.live`).

No other animation. Quiet movement feels designed; a busy TUI feels desperate.

### 11.9 Accessibility

- `NO_COLOR=1` disables all color.
- `--plain` enforces ASCII and no color.
- `--no-animation` disables spinner and countdown (static frames instead).
- Non-TTY stdout auto-enables plain + no-animation.
- Minimum terminal width: degrade treemap to single line if width < 60.

## 12. Technical architecture

```
unclog/
  __init__.py
  __main__.py              # entry point
  cli.py                   # argparse / click / typer setup
  app.py                   # top-level orchestration
  scan/
    config.py              # parse ~/.claude.json, settings.json
    filesystem.py          # skills/agents/commands/plugins enumeration
    session.py             # session JSONL system-block parsing
    claude_md.py           # markdown section parsing + lint
    stats.py               # stats-cache.json + history.jsonl aggregation
    tokens.py              # tiktoken + optional Anthropic API
  findings/
    base.py                # Finding dataclass + action primitives
    detectors/             # one module per finding type
      unused_skill.py
      unused_agent.py
      unused_command.py
      dead_mcp.py
      unused_mcp.py
      stale_plugin.py
      claude_md_dead_ref.py
      claude_md_duplicate.py
      claude_md_oversized.py
      scope_mismatch.py
      missing_claudeignore.py
  apply/
    snapshot.py            # snapshot creation + manifest
    actions.py             # action primitive implementations
    restore.py
  ui/
    wordmark.py
    hero.py                # baseline number + treemap
    findings_view.py       # findings list rendering
    selector.py            # questionary checkbox flow
    spinner.py             # custom flow spinner
    theme.py               # palette, styles, symbols
    output.py              # mode dispatch (interactive / report / json / plain)
  cache.py                 # content-hash keyed token cache
  util/
    paths.py               # CLAUDE_CONFIG_DIR resolution, project encoding
    markdown.py             # section tree parser
tests/
  fixtures/
    minimal/               # a hand-built ~/.claude/ and project tree
    clogged/               # a pathological one
  scan/
  findings/
  apply/
  ui/
pyproject.toml
README.md
```

### 12.1 Data flow

```
cli.py
  → app.run()
    → scan.*  ────────────► InstallationState
    → findings.detect(state) ───► list[Finding]
    → ui.output.render(state, findings, mode)
      (interactive mode) → ui.selector.select(findings)
        → apply.snapshot(selected)
        → apply.actions(selected)
        → ui.output.render_applied(before, after)
```

`InstallationState` is an immutable dataclass carrying the full parsed world. Findings detectors are pure functions of it. Apply actions are the only code that writes.

### 12.2 CLAUDE_CONFIG_DIR

Resolve once at startup. Fall back to `~/.claude/`. All path construction goes through `util.paths.claude_home()`. Never hardcode.

## 13. Tech stack

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | ecosystem fit, token libs, uv distribution |
| CLI framework | `typer` | type-driven args, clean help output |
| Rendering | `rich` | tables, trees, panels, live, spinners |
| Interactive prompts | `questionary` | checkbox flow matches Claude Code UX |
| Token counting | `tiktoken` default, `anthropic` for `--accurate` | fast default, accurate opt-in |
| Markdown parsing | `mistune` or built-in splitter | section tree extraction |
| Packaging | `uv` / `hatch` | `uv tool install unclog` |
| Testing | `pytest` + `pytest-snapshot` | fixture-driven |
| Lint/format | `ruff` | fast, standard |
| Type check | `mypy --strict` on `unclog/` | catches parser drift early |

No other runtime deps. No `click`, no `colorama`, no `halo`. Keep the install small.

## 14. Privacy posture

Stated loudly in README and in the first-run welcome:

- All reads are local. No network call is made by default.
- No telemetry, ever, in v0.1.
- `--accurate` calls Anthropic's count-tokens endpoint with the user's own `ANTHROPIC_API_KEY`, sending only the text being measured. Documented explicitly.
- Snapshots contain file copies of your local Claude config, nothing else, written only under `~/.claude/.unclog/`.

No `~/.unclog/` directory is created anywhere else.

## 15. Performance

Targets for a typical installation (10 skills, 3 MCPs, 5 projects):

- Cold scan: < 2 seconds.
- Warm scan (cache hit on tokenization): < 1 second.

Caching:

- Token counts keyed by SHA256 of counted text, stored at `~/.claude/.unclog/cache/tokens/`. Evict on version bump.
- Session JSONL parse results keyed by file path + mtime + size.

Session JSONL reads use streaming JSON — never load the full file. For v0.1 we only need the first message, so stop after it.

## 16. Error handling & degradation

The core operating principle: **never crash on malformed input, always produce a useful report.**

- Unknown JSON fields → ignored.
- Malformed JSONL lines → skipped, counted in `--verbose` output.
- Missing files that "should" exist → treated as empty.
- Unreachable session JSONL → MCP/skill costs marked `unknown`, report still renders.
- Token counting failure → fall back to byte-length / 4 estimate with a visible "estimate" marker.
- Restore failure mid-way → partial restore logged, remaining actions recorded so user can complete manually.

Errors that should crash loudly: unreadable `~/.claude.json` (everything depends on it), write failures during snapshot creation, apply action that would escape the snapshot's tracked set.

## 17. Testing strategy

- **Fixture installations** under `tests/fixtures/` — minimal, typical, clogged, broken. Each fixture is a full `~/.claude/` tree + one or two project trees.
- **Scan tests**: run scan against each fixture, snapshot the `InstallationState` JSON.
- **Detector tests**: pure function tests with hand-built states.
- **Apply tests**: run apply in a temp directory, assert file changes, assert snapshot manifest correctness, assert restore round-trips to identical bytes.
- **UI tests**: render to a captured terminal via `rich`'s record mode, snapshot the output text.
- **CLI tests**: invoke via subprocess, assert exit codes, stdout JSON schema.

No live `~/.claude/` is touched by the test suite. Ever.

## 18. Distribution

- PyPI package `unclog`.
- `uv tool install unclog` as the documented install.
- `pipx install unclog` also works.
- A minimal Homebrew tap is parked for v0.2.
- No Docker image, no installer script, no curl-bash in v0.1.

## 19. Deferred to v0.2+ (parking lot)

- Full session JSONL parsing → per-tool invocation counts, error rates, co-occurrence.
- Weekly scheduled audit (`launchd`/cron).
- Shell statusline integration showing live baseline.
- `--apply` auto-fix with git-style diffs.
- LLM-powered CLAUDE.md rewrites (opt-in, user's own API key).
- Secrets scanner.
- Marketplace freshness checks.
- Cross-developer team audit.
- Snapshots prune/list subcommand.
- Fuzzy duplicate detection across scopes.
- Support for additional LLM tools (Cursor, Cline, etc.). Architecture already supports it.
- Model-mismatch advice (Opus-for-everything cost warnings).
- `unclog diff` between snapshots.
- `unclog benchmark` — empirical context cost test.
- Web dashboard (`unclog serve`).

## 20. Decisions (locked for v0.1)

1. **Minimum supported Claude Code version: 2.1.90.** Skills were operational by 2.1.90; `enabledPlugins` and the plugin system have been stable and backward-compatible through the 2.1.x series (current at time of spec: 2.1.112). Older installations: parsers still run, but a one-line `[!] Claude Code older than 2.1.90 detected — some features may misreport` note is printed above the report, and plugin-related findings are suppressed.
2. **Windows: v0.2.** macOS + Linux only for v0.1. Path encoding for `~/.claude/projects/<encoded>/` and the `CLAUDE_CONFIG_DIR` semantics differ enough on Windows to warrant a deliberate second pass. Document as unsupported in README.
3. **Plugin-bundled content is read-only.** Never edit files under `~/.claude/plugins/cache/`. The primary cleanup action is `disable_plugin` — flipping the `enabledPlugins` map entry in `settings.json` to `false`. Disabled plugins don't load their skills/agents/commands/MCPs, so they stop consuming context on the next session. `uninstall_plugin` (which removes the entry from `installed_plugins.json` and deletes the cache dir) is offered only for plugins disabled for ≥ 90 days (finding `disabled_plugin_residue`).
4. **Oversized-section threshold: 1,000 tokens, fixed.** Not configurable in v0.1. Revisit after seeing real installations post-ship.
5. **"Never used" threshold: 90 days, configurable** via `~/.claude/.unclog/config.toml`. See §22.

## 21. User config

Location: `~/.claude/.unclog/config.toml`. Auto-created on first run with documented defaults and commented-out examples. Never required — defaults are usable.

```toml
# unclog v0.1 user config
# All keys are optional. Delete this file to reset to defaults.

[thresholds]
# A skill/agent/command/MCP counts as "unused" if it has 0 invocations
# in this many days. Default: 90.
unused_days = 90

# A plugin is flagged as stale if enabled but unused for this many days
# AND installed ≥ 90 days ago. Default: 90.
stale_plugin_days = 90

# Scope-mismatch promotion (project → global) requires an identical section
# body present in at least this many projects. Default: 3.
promote_min_projects = 3

[tokenizer]
# "tiktoken" (default) or "anthropic". "anthropic" requires ANTHROPIC_API_KEY
# and sends only the text being measured.
default = "tiktoken"

[ui]
# Disable all motion globally. CLI --no-animation also respected.
animation = true
```

Malformed config falls back to defaults with a one-line warning. Unknown keys are ignored silently. Version-gate via a `[meta] version = "0.1"` key if we need to migrate formats later.

## 22. Milestones

| Milestone | Scope |
|---|---|
| M1 — scaffold | pyproject, typer entry, config + filesystem scan, `--json` output with baseline from byte counts |
| M2 — ground-truth measurement | session JSONL system-block parser, token accounting, treemap, hero number |
| M3 — findings | detectors for unused_*, dead_mcp, unused_mcp, stale_plugin, missing_claudeignore |
| M4 — CLAUDE.md | section parser, dead_ref, duplicate, oversized, scope_mismatch detectors |
| M5 — interactive | questionary selector, apply primitives, snapshot + manifest, restore |
| M6 — polish | wordmark, custom spinner, countdown animation, --plain/--no-animation/--report, NO_COLOR |
| M7 — ship | readme, privacy statement, shareable post-apply stat, PyPI release, `uv tool install unclog` verified on macOS + Linux |
