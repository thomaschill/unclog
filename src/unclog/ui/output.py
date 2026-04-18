"""Render an :class:`InstallationState` to stdout in the requested format.

M2 swaps the byte-count estimates used in M1 for real tiktoken
measurements. Every composition entry now reports a concrete token
count. MCP servers are attributed from the ``tools`` array of the
most recent session JSONL across all known projects — we measure what
Claude Code actually injected rather than what might theoretically
load.

When no session JSONL is available anywhere (fresh installs, or a home
that has never opened a project), MCP entries stay marked
``unmeasured`` and the baseline degrades to the sum of file-backed
sources. Tokens for CLAUDE.md, skills, and agents are always measured
(we have the source bytes on disk).

``--json`` output keeps the stable ``unclog.v0.1`` schema shape. Field
names that used to advertise ``bytes_estimate`` now read ``tiktoken``
(or ``unmeasured`` for MCP without a session).
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.text import Text

from unclog import __version__
from unclog.findings import detect as detect_findings
from unclog.findings import load_thresholds
from unclog.findings.base import Finding
from unclog.scan.session import SessionSystemBlock
from unclog.scan.tokens import TiktokenCounter, TokenCounter
from unclog.state import InstallationState
from unclog.ui.hero import render_hero, render_treemap
from unclog.ui.theme import ACCENT, DIM, SEVERITY_OK
from unclog.ui.wordmark import wordmark
from unclog.util.paths import ClaudePaths

SCHEMA_ID = "unclog.v0.1"

# Token naming convention for MCP tools in Claude Code sessions:
# ``mcp__<server>__<tool>``. Built-in tools (Read, Write, Bash, etc.)
# do not carry this prefix.
_MCP_TOOL_PREFIX = "mcp__"

# Category colours — deliberately mirror the picker's badge palette so
# the user sees the same colour for "skill" in the scan summary and in
# the picker, and their eye learns the taxonomy across screens.
_INVENTORY_CHIP_COLOUR: dict[str, str] = {
    "skills": "#22c55e",
    "agents": "#38bdf8",
    "commands": "#a78bfa",
    "plugins": "#e879f9",
    "mcp": "#fb923c",
    "hooks": "#f43f5e",
    "projects": "#a3a3a3",
}

# Hook events that fire on every prompt — the highest-cost ones to leave
# unaudited. ``PreToolUse`` / ``PostToolUse`` fire per tool invocation
# which is usually per turn in practice but we only surface the
# guaranteed-every-turn events so the "heavy" signal stays truthful.
_EVERY_TURN_HOOK_EVENTS: frozenset[str] = frozenset({"SessionStart", "UserPromptSubmit"})


def _mcp_attribution(session: SessionSystemBlock, counter: TokenCounter) -> dict[str, int]:
    """Group session tools by MCP server name and sum per-tool token counts.

    Tools that don't match the ``mcp__<server>__`` convention (built-in
    Claude Code tools like ``Read`` / ``Bash``) are excluded.
    """
    per_server: dict[str, int] = {}
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server_name, _, _ = remainder.partition("__")
        if not server_name:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server_name] = per_server.get(server_name, 0) + counter.count(blob)
    return per_server


def _mcp_entry(
    name: str,
    measured: int | None,
    *,
    scope: str,
    probed: int | None = None,
    probe_failed: bool = False,
    extra_note: str | None = None,
) -> dict[str, Any]:
    """Build a composition row for a single MCP server.

    Three token sources, ranked by authority:

    - ``probed``: ``--probe-mcps`` spawned the server and counted its
      tools schema directly. Most accurate; render as ``probe+tiktoken``.
    - ``measured``: per-server cost derived from the latest session's
      tools array. Current Claude Code session JSONLs rarely carry
      schemas, so this is usually ``None`` in practice.
    - Neither: ``unmeasured``. Fallback.

    ``probe_failed`` documents that the probe ran but the server
    couldn't start — the composition still renders as ``unmeasured``
    (there's no schema to measure) but the note makes it clear the
    server is broken, not merely unseen.
    """
    if probed is not None:
        note = extra_note
        tokens_source = "probe+tiktoken"
        tokens: int | None = probed
    elif measured is not None:
        note = extra_note
        tokens_source = "session+tiktoken"
        tokens = measured
    else:
        if probe_failed:
            base_note = "probe failed — see findings for stderr"
        else:
            base_note = (
                "schema not in session JSONL; run with --probe-mcps to measure live"
            )
        note = f"{base_note}; {extra_note}" if extra_note else base_note
        tokens_source = "unmeasured"
        tokens = None
    return {
        "source": f"mcp:{name}",
        "scope": scope,
        "bytes": None,
        "tokens": tokens,
        "tokens_source": tokens_source,
        "note": note,
    }


def build_composition(state: InstallationState, counter: TokenCounter) -> list[dict[str, Any]]:
    """Return the composition breakdown, largest first."""
    entries: list[dict[str, Any]] = []
    gs = state.global_scope

    if gs.claude_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.md",
                "scope": "global",
                "bytes": gs.claude_md_bytes,
                "tokens": counter.count(gs.claude_md_text),
                "tokens_source": "tiktoken",
            }
        )
    if gs.claude_local_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.local.md",
                "scope": "global",
                "bytes": gs.claude_local_md_bytes,
                "tokens": counter.count(gs.claude_local_md_text),
                "tokens_source": "tiktoken",
            }
        )

    if gs.skills:
        skill_tokens = sum(counter.count(f"{s.name}: {s.description or ''}") for s in gs.skills)
        skill_bytes = sum(s.frontmatter_bytes for s in gs.skills)
        entries.append(
            {
                "source": f"skills:descriptions (n={len(gs.skills)})",
                "scope": "global",
                "bytes": skill_bytes,
                "tokens": skill_tokens,
                "tokens_source": "tiktoken",
                "note": "name + description per skill, loaded on every session",
            }
        )

    if gs.agents:
        agent_tokens = sum(counter.count(f"{a.name}: {a.description or ''}") for a in gs.agents)
        agent_bytes = sum(a.frontmatter_bytes for a in gs.agents)
        entries.append(
            {
                "source": f"agents:descriptions (n={len(gs.agents)})",
                "scope": "global",
                "bytes": agent_bytes,
                "tokens": agent_tokens,
                "tokens_source": "tiktoken",
            }
        )

    for content in gs.plugin_content:
        if not content.skills and not content.agents:
            continue
        plugin_tokens = sum(
            counter.count(f"{s.name}: {s.description or ''}") for s in content.skills
        ) + sum(
            counter.count(f"{a.name}: {a.description or ''}") for a in content.agents
        )
        plugin_bytes = sum(s.frontmatter_bytes for s in content.skills) + sum(
            a.frontmatter_bytes for a in content.agents
        )
        breakdown = f"n_skills={len(content.skills)}, n_agents={len(content.agents)}"
        entries.append(
            {
                "source": f"plugin:{content.plugin_key}:bundled ({breakdown})",
                "scope": "global",
                "bytes": plugin_bytes,
                "tokens": plugin_tokens,
                "tokens_source": "tiktoken",
                "note": "bundled by plugin; disable plugin in settings.json to skip",
            }
        )

    memory_projects = [p for p in state.project_scopes if p.memory_md_text]
    if memory_projects:
        memory_tokens = sum(counter.count(p.memory_md_text) for p in memory_projects)
        memory_bytes = sum(p.memory_md_bytes for p in memory_projects)
        entries.append(
            {
                "source": f"auto-memory (n={len(memory_projects)})",
                "scope": "project",
                "bytes": memory_bytes,
                "tokens": memory_tokens,
                "tokens_source": "tiktoken",
                "note": (
                    "~/.claude/projects/<encoded>/memory/MEMORY.md files — "
                    "auto-injected into every session prompt, truncated past ~200 lines"
                ),
            }
        )

    session = gs.latest_session
    attribution = _mcp_attribution(session, counter) if session else {}

    # Global MCPs: load unconditionally into every session.
    global_mcp = gs.config.mcp_servers if gs.config else {}
    probes = gs.mcp_probes
    for name in sorted(global_mcp):
        measured = attribution.get(name)
        probe = probes.get(name) if probes else None
        probed = probe.tools_tokens if probe is not None and probe.ok else None
        probe_failed = probe is not None and not probe.ok
        entries.append(
            _mcp_entry(
                name,
                measured,
                scope="global",
                probed=probed,
                probe_failed=probe_failed,
            )
        )

    # Project-scoped MCPs: only load when that project is open, but
    # they still bloat the baseline for users who spend most of their
    # time in a single project. Collapse identical configs across
    # projects (name + command + args) so a shared MCP appears once
    # with the list of projects that declare it.
    project_groups: dict[tuple[str, str | None, tuple[str, ...]], list[str]] = {}
    project_attribution: dict[tuple[str, str | None, tuple[str, ...]], int] = {}
    if gs.config:
        for project in gs.config.projects.values():
            for srv_name, srv in project.mcp_servers.items():
                key = (srv_name, srv.command, srv.args)
                project_groups.setdefault(key, []).append(str(project.path))
                m = attribution.get(srv_name)
                if m is not None and key not in project_attribution:
                    project_attribution[key] = m
    for (srv_name, _cmd, _args), project_paths in sorted(project_groups.items()):
        measured = project_attribution.get((srv_name, _cmd, _args))
        probe = probes.get(srv_name) if probes else None
        probed = probe.tools_tokens if probe is not None and probe.ok else None
        probe_failed = probe is not None and not probe.ok
        scope_label = (
            f"project:{project_paths[0]}"
            if len(project_paths) == 1
            else f"project:{len(project_paths)} projects"
        )
        entries.append(
            _mcp_entry(
                srv_name,
                measured,
                scope=scope_label,
                probed=probed,
                probe_failed=probe_failed,
                extra_note=(
                    None
                    if len(project_paths) == 1
                    else f"declared in {len(project_paths)} projects"
                ),
            )
        )

    def _rank(entry: dict[str, Any]) -> int:
        tokens = entry.get("tokens")
        return tokens if isinstance(tokens, int) else -1

    entries.sort(key=_rank, reverse=True)
    return entries


def _mcp_label(inv: dict[str, Any]) -> str:
    """Render the MCP-server count with project-scope breakdown when relevant.

    Project-scoped MCPs live in ``~/.claude.json``'s per-project sections
    and only load when that project is open. Surfacing them separately
    prevents the "0 MCP servers" confusion for users whose entire MCP
    footprint is project-scoped.
    """
    total = inv["mcp_servers"]
    project_scoped = inv.get("mcp_servers_project", 0)
    if total == 0:
        return "0 MCP servers"
    if project_scoped and project_scoped == total:
        return f"{total} MCP servers (project-scoped)"
    if project_scoped:
        return f"{total} MCP servers ({project_scoped} project-scoped)"
    return f"{total} MCP servers"


def _hooks_label(inv: dict[str, Any]) -> str:
    """Render the hooks count with project-scope breakdown when relevant.

    Claude Code fires hooks silently on every session; every hook's
    stdout is injected into context, so users routinely underestimate
    what is running on their behalf. Always surface the count, even
    zero, so the absence is explicit.
    """
    total = inv.get("hooks", 0)
    project_scoped = inv.get("hooks_project", 0)
    if total == 0:
        return "0 hooks"
    if project_scoped and project_scoped == total:
        return f"{total} hooks (project-scoped)"
    if project_scoped:
        return f"{total} hooks ({project_scoped} project-scoped)"
    return f"{total} hooks"


def _inventory(state: InstallationState) -> dict[str, int]:
    gs = state.global_scope
    global_mcp = len(gs.config.mcp_servers) if gs.config else 0
    project_mcp = 0
    if gs.config:
        for project in gs.config.projects.values():
            project_mcp += len(project.mcp_servers)
    global_hooks = len(gs.settings.hooks) if gs.settings else 0
    project_hooks = sum(len(p.hooks) for p in state.project_scopes)
    return {
        "skills": len(gs.skills),
        "agents": len(gs.agents),
        "commands": len(gs.commands),
        "plugins": len(gs.installed_plugins),
        "mcp_servers": global_mcp + project_mcp,
        "mcp_servers_global": global_mcp,
        "mcp_servers_project": project_mcp,
        "hooks": global_hooks + project_hooks,
        "hooks_global": global_hooks,
        "hooks_project": project_hooks,
        "projects_known": len(gs.config.projects) if gs.config else 0,
    }


def _baseline(
    composition: list[dict[str, Any]], session: SessionSystemBlock | None
) -> dict[str, Any]:
    attributed_tokens = sum(e["tokens"] for e in composition if isinstance(e.get("tokens"), int))
    unmeasured_sources = sum(1 for e in composition if e["tokens_source"] == "unmeasured")

    if session is not None:
        total = session.total_tokens
        tokens_source = "session+tiktoken"
    else:
        total = attributed_tokens
        tokens_source = "tiktoken" if attributed_tokens else "empty"

    return {
        "estimated_tokens": total,
        "attributed_tokens": attributed_tokens,
        "tokens_source": tokens_source,
        "unmeasured_sources": unmeasured_sources,
        "session_path": str(session.session_path) if session else None,
    }


def _load_findings(state: InstallationState) -> list[Finding]:
    """Run all detectors against ``state`` using the user's thresholds.

    The threshold config is loaded relative to the scanned ``claude_home``
    so the same state can be reported from different CWDs without
    picking up ambient config.
    """
    paths = ClaudePaths(home=state.claude_home)
    thresholds = load_thresholds(paths.config_toml)
    return detect_findings(
        state,
        state.global_scope.activity,
        thresholds,
        now=state.generated_at,
    )


def build_report(state: InstallationState) -> dict[str, Any]:
    """Return the full machine-readable report as a plain dict."""
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    findings = _load_findings(state)
    report: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "unclog_version": __version__,
        "generated_at": state.generated_at.isoformat().replace("+00:00", "Z"),
        "claude_home": str(state.claude_home),
        "baseline": _baseline(composition, session),
        "inventory": _inventory(state),
        "composition": composition,
        "findings": [f.to_json() for f in findings],
        "warnings": list(state.warnings),
        "projects_audited": _projects_audited(state),
    }
    probes = state.global_scope.mcp_probes
    if probes:
        report["mcp_probes"] = [probes[name].to_json() for name in sorted(probes)]
    return report


def _projects_audited(state: InstallationState) -> list[dict[str, Any]]:
    """Summarise the project scopes the scan actually read.

    Used by the JSON schema and the rich/plain renderers so users see
    which CLAUDE.md files the cross-scope detectors had access to —
    the difference between "no findings because nothing's wrong" and
    "no findings because we didn't scan any projects".
    """
    counter = TiktokenCounter()
    return [
        {
            "path": str(project.path),
            "name": project.name,
            "exists": project.exists,
            "claude_md_bytes": project.claude_md_bytes,
            "claude_md_tokens": (
                counter.count(project.claude_md_text) if project.claude_md_text else 0
            ),
            "claude_local_md_bytes": project.claude_local_md_bytes,
            "claude_local_md_tokens": (
                counter.count(project.claude_local_md_text)
                if project.claude_local_md_text
                else 0
            ),
            "memory_md_bytes": project.memory_md_bytes,
            "memory_md_tokens": (
                counter.count(project.memory_md_text) if project.memory_md_text else 0
            ),
            "has_claudeignore": project.has_claudeignore,
        }
        for project in state.project_scopes
    ]


def _claude_md_rows(state: InstallationState) -> list[dict[str, Any]]:
    """Flat per-file rows for the CLAUDE.md + auto-memory listing.

    Includes the global ``CLAUDE.md`` / ``CLAUDE.local.md``, per-project
    CLAUDE.md pairs, and the per-project auto-memory index file Claude
    Code persists at ``~/.claude/projects/<encoded>/memory/MEMORY.md``.
    Missing files are reported as rows with ``tokens=None`` and a
    ``status`` string so users can distinguish "file not present" from
    "project path missing on disk".
    """
    counter = TiktokenCounter()
    rows: list[dict[str, Any]] = []
    gs = state.global_scope

    rows.append(
        {
            "scope": "global",
            "name": "CLAUDE.md",
            "path": str(gs.claude_home / "CLAUDE.md"),
            "tokens": counter.count(gs.claude_md_text) if gs.claude_md_text else None,
            "bytes": gs.claude_md_bytes,
            "status": "present" if gs.claude_md_text else "absent",
        }
    )
    rows.append(
        {
            "scope": "global",
            "name": "CLAUDE.local.md",
            "path": str(gs.claude_home / "CLAUDE.local.md"),
            "tokens": (
                counter.count(gs.claude_local_md_text) if gs.claude_local_md_text else None
            ),
            "bytes": gs.claude_local_md_bytes,
            "status": "present" if gs.claude_local_md_text else "absent",
        }
    )

    for project in state.project_scopes:
        if not project.exists:
            rows.append(
                {
                    "scope": "project",
                    "name": project.name,
                    "path": str(project.claude_md_path),
                    "tokens": None,
                    "bytes": 0,
                    "status": "path_missing",
                }
            )
            continue
        rows.append(
            {
                "scope": "project",
                "name": project.name,
                "path": str(project.claude_md_path),
                "tokens": (
                    counter.count(project.claude_md_text) if project.claude_md_text else None
                ),
                "bytes": project.claude_md_bytes,
                "status": "present" if project.claude_md_text else "absent",
            }
        )
        if project.claude_local_md_text:
            rows.append(
                {
                    "scope": "project",
                    "name": f"{project.name} (CLAUDE.local.md)",
                    "path": str(project.claude_local_md_path),
                    "tokens": counter.count(project.claude_local_md_text),
                    "bytes": project.claude_local_md_bytes,
                    "status": "present",
                }
            )

    for project in state.project_scopes:
        if not project.memory_md_text:
            continue
        rows.append(
            {
                "scope": "memory",
                "name": project.name,
                "path": str(project.memory_md_path),
                "tokens": counter.count(project.memory_md_text),
                "bytes": project.memory_md_bytes,
                "status": "present",
            }
        )
    return rows


def _claude_md_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate counts for the listing footer."""
    present = [r for r in rows if r["status"] == "present"]
    project_rows = [r for r in rows if r["scope"] == "project"]
    return {
        "files_present": len(present),
        "tokens_total": sum(r["tokens"] or 0 for r in present),
        "projects_scanned": len(project_rows),
        "projects_missing": sum(1 for r in rows if r["status"] == "path_missing"),
        "projects_empty": sum(
            1 for r in project_rows if r["status"] == "absent"
        ),
    }


def render_claude_md_listing_plain(state: InstallationState) -> str:
    """Plain-text listing of every auto-injected context file unclog sees.

    Diagnostic output for ``--list-claude-md`` — confirms the scan is
    actually finding every CLAUDE.md and auto-memory file before the
    user starts tuning thresholds or chasing missing-finding bugs.
    """
    rows = _claude_md_rows(state)
    totals = _claude_md_totals(rows)
    lines: list[str] = []
    lines.append("Auto-injected context files found:")
    lines.append("")
    lines.append("global CLAUDE.md:")
    for r in rows:
        if r["scope"] != "global":
            continue
        tokens = "—" if r["tokens"] is None else f"{r['tokens']:>8,} tok"
        status = "" if r["status"] == "present" else f"  ({r['status']})"
        lines.append(f"  {tokens}  {r['name']}{status}  {r['path']}")
    lines.append("")
    lines.append("project CLAUDE.md:")
    project_rows = [r for r in rows if r["scope"] == "project"]
    content_rows = [r for r in project_rows if r["status"] == "present"]
    empty = totals["projects_empty"]
    missing = totals["projects_missing"]
    hidden: list[str] = []
    if empty:
        hidden.append(f"{empty} without a CLAUDE.md")
    if missing:
        hidden.append(f"{missing} path missing")
    if not project_rows:
        lines.append("  (no projects scanned)")
    elif not content_rows:
        lines.append("  (none of the known projects have a CLAUDE.md)")
        if hidden:
            lines.append(f"  (hidden: {' · '.join(hidden)})")
    else:
        for r in content_rows:
            tokens = f"{r['tokens']:>8,} tok"
            lines.append(f"  {tokens}  {r['name']}  {r['path']}")
        if hidden:
            lines.append(f"  (hidden: {' · '.join(hidden)})")
    lines.append("")
    lines.append("auto-memory (MEMORY.md):")
    memory_rows = [r for r in rows if r["scope"] == "memory"]
    if not memory_rows:
        lines.append("  (no auto-memory files found)")
    else:
        for r in memory_rows:
            tokens = f"{r['tokens']:>8,} tok"
            lines.append(f"  {tokens}  {r['name']}  {r['path']}")
    lines.append("")
    lines.append(
        f"totals: {totals['files_present']} file(s) present  ·  "
        f"{totals['tokens_total']:,} tokens total  ·  "
        f"{totals['projects_scanned']} project(s) scanned"
        + (f"  ·  {totals['projects_missing']} missing" if totals["projects_missing"] else "")
    )
    return "\n".join(lines) + "\n"


def render_claude_md_listing_rich(state: InstallationState, console: Console) -> None:
    """Rich TTY rendering of the auto-injected context listing.

    Uses the same per-category colouring as the inventory chips so the
    diagnostic output reads as part of the same product surface.
    """
    rows = _claude_md_rows(state)
    totals = _claude_md_totals(rows)
    console.print(Text("Auto-injected context files found:", style=f"bold {ACCENT}"))
    console.print("")
    console.print(Text("global CLAUDE.md", style=DIM))
    for r in rows:
        if r["scope"] != "global":
            continue
        _print_claude_md_row(console, r)
    console.print("")
    project_rows = [r for r in rows if r["scope"] == "project"]
    content_rows = [r for r in project_rows if r["status"] == "present"]
    empty = totals["projects_empty"]
    missing = totals["projects_missing"]
    hidden: list[str] = []
    if empty:
        hidden.append(f"{empty} without a CLAUDE.md")
    if missing:
        hidden.append(f"{missing} path missing")
    console.print(
        Text(
            f"project CLAUDE.md ({len(content_rows)}/{len(project_rows)} with content)",
            style=DIM,
        )
    )
    if not project_rows:
        console.print("  [dim](no projects scanned)[/dim]")
    elif not content_rows:
        console.print("  [dim](none of the known projects have a CLAUDE.md)[/dim]")
        if hidden:
            console.print(f"  [dim](hidden: {' · '.join(hidden)})[/dim]")
    else:
        for r in content_rows:
            _print_claude_md_row(console, r)
        if hidden:
            console.print(f"  [dim](hidden: {' · '.join(hidden)})[/dim]")
    console.print("")
    memory_rows = [r for r in rows if r["scope"] == "memory"]
    console.print(Text(f"auto-memory MEMORY.md ({len(memory_rows)})", style=DIM))
    if not memory_rows:
        console.print("  [dim](no auto-memory files found)[/dim]")
    else:
        for r in memory_rows:
            _print_claude_md_row(console, r)
    console.print("")
    footer = Text()
    footer.append(f"{totals['files_present']} file(s) present", style=f"bold {ACCENT}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{totals['tokens_total']:,} tokens total", style=f"bold {ACCENT}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{totals['projects_scanned']} project(s) scanned", style=DIM)
    if totals["projects_missing"]:
        footer.append("  ·  ", style=DIM)
        footer.append(f"{totals['projects_missing']} missing", style="#eab308")
    console.print(footer)


def _print_claude_md_row(console: Console, row: dict[str, Any]) -> None:
    line = Text("  ")
    if row["tokens"] is None:
        line.append(f"{'—':>8}    ", style=DIM)
    else:
        line.append(f"{row['tokens']:>8,} tok", style=f"bold {ACCENT}")
    line.append("  ")
    line.append(row["name"])
    if row["status"] == "path_missing":
        line.append("  (path missing)", style="#eab308")
    elif row["status"] == "absent":
        line.append("  (no CLAUDE.md)", style=DIM)
    line.append("  ")
    line.append(row["path"], style=DIM)
    console.print(line)


def baseline_tokens(state: InstallationState) -> int:
    """Compute the current baseline-token total for ``state``.

    Used by the interactive flow to seed the post-apply countdown (spec
    §11.8). Separate from :func:`build_report` to keep the countdown
    path light — no findings detection needed.
    """
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    return int(_baseline(composition, session)["estimated_tokens"] or 0)


def render_json(state: InstallationState) -> str:
    """Render the state as a single JSON document."""
    return json.dumps(build_report(state), indent=2, sort_keys=False)


def render_plain(state: InstallationState) -> str:
    """ASCII-only, colour-free text render. Used for ``--plain`` and non-TTY."""
    report = build_report(state)
    lines: list[str] = []
    lines.append(f"unclog {report['unclog_version']}")
    lines.append(f"claude_home: {report['claude_home']}")
    lines.append("")
    baseline = report["baseline"]
    lines.append(
        f"baseline: ~{baseline['estimated_tokens']:,} tokens  "
        f"({baseline['tokens_source']})"
    )
    if baseline.get("unmeasured_sources"):
        lines.append(
            f"  ({baseline['unmeasured_sources']} MCP source(s) unmeasured - "
            f"open them in Claude Code to record a session)"
        )
    lines.append("")
    inv = report["inventory"]
    lines.append(
        "inventory: "
        f"{inv['skills']} skills | {inv['agents']} agents | "
        f"{inv['commands']} commands | {inv['plugins']} plugins | "
        f"{_mcp_label(inv)} | {_hooks_label(inv)} | "
        f"{inv['projects_known']} known projects"
    )
    if report["composition"]:
        lines.append("")
        lines.append("composition (largest first):")
        for entry in report["composition"]:
            tokens = entry.get("tokens")
            size = "unmeasured" if tokens is None else f"{tokens:>8,} tok"
            scope = entry.get("scope")
            scope_suffix = (
                f"  [{scope}]" if isinstance(scope, str) and scope.startswith("project:") else ""
            )
            lines.append(f"  {size}  {entry['source']}{scope_suffix}")
    if report["findings"]:
        lines.append("")
        auto = sum(1 for f in report["findings"] if f.get("auto_checked"))
        info = sum(
            1
            for f in report["findings"]
            if f.get("action", {}).get("primitive") == "flag_only"
        )
        opt_in = len(report["findings"]) - auto - info
        parts = [f"{auto} auto-fix"]
        if opt_in:
            parts.append(f"{opt_in} opt-in")
        if info:
            parts.append(f"{info} informational")
        lines.append(
            f"findings: {len(report['findings'])} ({', '.join(parts)})"
        )
        for f in report["findings"]:
            primitive = f.get("action", {}).get("primitive")
            if f.get("auto_checked"):
                marker = "[x]"
            elif primitive == "flag_only":
                marker = "[i]"
            else:
                marker = "[ ]"
            scope = f["scope"].get("kind", "global")
            lines.append(f"  {marker} [{scope:>7}] {f['title']} - {f['reason']}")
    else:
        lines.append("")
        lines.append("findings: none")
    if report["warnings"]:
        lines.append("")
        lines.append("warnings:")
        for w in report["warnings"]:
            lines.append(f"  ! {w}")
    return "\n".join(lines) + "\n"


def render_rich(
    state: InstallationState,
    console: Console,
    *,
    show_wordmark: bool = True,
) -> None:
    """Pretty TTY render: wordmark, hero, treemap, inventory.

    Spec §11.4 suppresses the wordmark in ``--report``/``--json``/``--plain``.
    The CLI resolves :class:`~unclog.ui.display.DisplayOptions` and passes
    ``show_wordmark`` so this renderer stays ignorant of mode flags.
    """
    report = build_report(state)
    baseline = report["baseline"]
    inv = report["inventory"]

    if show_wordmark:
        console.print(wordmark())
    console.print(render_hero(baseline))
    if report["composition"]:
        console.print("")
        console.print(render_treemap(report["composition"]))
    console.print("")
    console.print(_render_inventory_chips(inv))
    _render_findings_rich(report["findings"], console)
    if report["warnings"]:
        console.print("")
        for warning in report["warnings"]:
            console.print(f"[#eab308]![/#eab308] [dim]{warning}[/dim]")


def _render_inventory_chips(inv: dict[str, int]) -> Text:
    """Render the inventory line as coloured category chips.

    Each chip is ``N label`` with the count in the category colour and
    the label dim. Shares the picker's palette so the user's eye learns
    the taxonomy once and carries it everywhere. Zero-valued categories
    are dropped to keep the line compact on narrow terminals.
    """
    mcp_label = "MCP"
    if inv["mcp_servers"] and inv.get("mcp_servers_project") == inv["mcp_servers"]:
        mcp_label = "MCP (project-scoped)"
    elif inv.get("mcp_servers_project"):
        mcp_label = f"MCP ({inv['mcp_servers_project']} project-scoped)"

    hooks_total = inv.get("hooks", 0)
    hooks_label = "hooks"
    if hooks_total and inv.get("hooks_project") == hooks_total:
        hooks_label = "hooks (project-scoped)"
    elif inv.get("hooks_project"):
        hooks_label = f"hooks ({inv['hooks_project']} project-scoped)"

    chips: list[tuple[str, str, int]] = [
        ("skills", "skills", inv["skills"]),
        ("agents", "agents", inv["agents"]),
        ("commands", "commands", inv["commands"]),
        ("plugins", "plugins", inv["plugins"]),
        ("mcp", mcp_label, inv["mcp_servers"]),
        ("hooks", hooks_label, hooks_total),
        ("projects", "projects", inv["projects_known"]),
    ]

    text = Text()
    first = True
    for key, label, value in chips:
        if value == 0 and key != "plugins":
            # Skip zero categories to save space; plugins=0 is still
            # worth surfacing so users know their baseline isn't hiding
            # bundled skills/agents they forgot about.
            continue
        colour = _INVENTORY_CHIP_COLOUR.get(key, DIM)
        if not first:
            text.append("  ·  ", style=DIM)
        first = False
        text.append(f"{value} ", style=f"bold {colour}")
        text.append(label, style=DIM)
    return text


_INFORMATIONAL_GROUP_LABEL: dict[str, str] = {
    "missing_claudeignore": "missing .claudeignore",
    "disabled_plugin_residue": "recently-disabled plugin residue",
    "claude_md_dead_ref": "CLAUDE.md dead references",
    "heavy_hook": "every-turn hooks",
}


def _render_findings_rich(findings: list[dict[str, Any]], console: Console) -> None:
    """Print a one-line summary + grouped informational hints.

    The picker is the real findings UI — it shows every removable item
    with a live selection total. This block exists to (a) give users a
    count before the picker opens and (b) surface the flag_only items
    the picker can't show, grouped by type so we don't emit nine nearly
    identical lines when every project is missing a ``.claudeignore``.

    ``--report``/``--plain`` paths route through :func:`render_plain`
    instead and never hit this renderer.
    """
    console.print("")
    if not findings:
        console.print(f"[{SEVERITY_OK}]✓[/{SEVERITY_OK}] [dim]No issues found.[/dim]")
        return

    removable = [f for f in findings if f.get("action", {}).get("primitive") != "flag_only"]
    informational = [f for f in findings if f.get("action", {}).get("primitive") == "flag_only"]
    removable_tokens = sum(f.get("token_savings") or 0 for f in removable)

    summary = Text()
    summary.append(f"{len(findings)}", style=f"bold {ACCENT}")
    summary.append(" issue(s)", style=DIM)
    if removable:
        summary.append("  ·  ", style=DIM)
        summary.append(f"{len(removable)}", style=f"bold {SEVERITY_OK}")
        summary.append(" removable", style=DIM)
        if removable_tokens:
            summary.append(f" (~{removable_tokens:,} tok)", style=DIM)
    if informational:
        summary.append("  ·  ", style=DIM)
        summary.append(f"{len(informational)}", style=DIM)
        summary.append(" informational", style=DIM)
    console.print(summary)

    if not informational:
        return

    groups: dict[str, list[dict[str, Any]]] = {}
    for f in informational:
        groups.setdefault(f["type"], []).append(f)
    for ftype, group in groups.items():
        label = _INFORMATIONAL_GROUP_LABEL.get(ftype, ftype.replace("_", " "))
        names = [_short_name(f) for f in group]
        shown = names[:4]
        more = f" +{len(names) - 4} more" if len(names) > len(shown) else ""
        row = Text()
        row.append("  · ", style=DIM)
        row.append(f"{len(group)}", style=f"bold {DIM}")
        row.append(f" {label}: ", style=DIM)
        row.append(", ".join(shown), style="default")
        if more:
            row.append(more, style=DIM)
        console.print(row)


def _short_name(finding: dict[str, Any]) -> str:
    """Extract a terse identifier for a finding used in grouped lists."""
    from pathlib import Path as _P

    scope = finding.get("scope", {})
    project_path = scope.get("project_path")
    if project_path:
        return _P(project_path).name
    action = finding.get("action", {})
    plugin_key = action.get("plugin_key")
    if plugin_key:
        return str(plugin_key)
    path = action.get("path")
    if path:
        return _P(path).name
    title = finding.get("title", "")
    return title.split()[-1] if title else "?"
