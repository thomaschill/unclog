"""Render an :class:`InstallationState` to stdout in the requested format.

M1 ships two modes:

- ``default``: terse plain-text summary — will be replaced by the
  hero/treemap report in M6.
- ``json``: stable machine-readable schema ``unclog.v0.1``.

Token counts are estimated as ``bytes // 4`` while M1 lacks a tokenizer.
Each composition entry advertises its ``tokens_source`` so consumers know
whether the number is a real tokenizer measurement or a byte estimate.
M2 replaces byte estimates with tiktoken / anthropic counts and adds
``mcp:*`` entries from the session JSONL system block.
"""

from __future__ import annotations

import json
from typing import Any

from unclog import __version__
from unclog.state import InstallationState, tier_for_baseline

SCHEMA_ID = "unclog.v0.1"
BYTES_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(byte_count: int) -> int:
    return byte_count // BYTES_PER_TOKEN_ESTIMATE


def build_composition(state: InstallationState) -> list[dict[str, Any]]:
    """Return the composition breakdown, largest first."""
    entries: list[dict[str, Any]] = []
    gs = state.global_scope

    if gs.claude_md_bytes:
        entries.append(
            {
                "source": "global:CLAUDE.md",
                "scope": "global",
                "bytes": gs.claude_md_bytes,
                "estimated_tokens": _estimate_tokens(gs.claude_md_bytes),
                "tokens_source": "bytes_estimate",
            }
        )
    if gs.claude_local_md_bytes:
        entries.append(
            {
                "source": "global:CLAUDE.local.md",
                "scope": "global",
                "bytes": gs.claude_local_md_bytes,
                "estimated_tokens": _estimate_tokens(gs.claude_local_md_bytes),
                "tokens_source": "bytes_estimate",
            }
        )

    skills_bytes = sum(s.frontmatter_bytes for s in gs.skills)
    if skills_bytes:
        entries.append(
            {
                "source": f"skills:descriptions (n={len(gs.skills)})",
                "scope": "global",
                "bytes": skills_bytes,
                "estimated_tokens": _estimate_tokens(skills_bytes),
                "tokens_source": "bytes_estimate",
                "note": "frontmatter bytes - skill descriptions loaded on every session",
            }
        )

    agents_bytes = sum(a.frontmatter_bytes for a in gs.agents)
    if agents_bytes:
        entries.append(
            {
                "source": f"agents:descriptions (n={len(gs.agents)})",
                "scope": "global",
                "bytes": agents_bytes,
                "estimated_tokens": _estimate_tokens(agents_bytes),
                "tokens_source": "bytes_estimate",
            }
        )

    mcp_servers = gs.config.mcp_servers if gs.config else {}
    for name in sorted(mcp_servers):
        entries.append(
            {
                "source": f"mcp:{name}",
                "scope": "global",
                "bytes": None,
                "estimated_tokens": None,
                "tokens_source": "unmeasured",
                "note": "schema cost requires session JSONL parse (M2)",
            }
        )

    entries.sort(key=lambda e: e.get("bytes") or -1, reverse=True)
    return entries


def _inventory(state: InstallationState) -> dict[str, int]:
    gs = state.global_scope
    return {
        "skills": len(gs.skills),
        "agents": len(gs.agents),
        "commands": len(gs.commands),
        "plugins": len(gs.installed_plugins),
        "mcp_servers": len(gs.config.mcp_servers) if gs.config else 0,
        "projects_known": len(gs.config.projects) if gs.config else 0,
    }


def build_report(state: InstallationState) -> dict[str, Any]:
    """Return the full machine-readable report as a plain dict."""
    composition = build_composition(state)
    measured_bytes = sum(e["bytes"] or 0 for e in composition)
    baseline_tokens = _estimate_tokens(measured_bytes)

    return {
        "schema": SCHEMA_ID,
        "unclog_version": __version__,
        "generated_at": state.generated_at.isoformat().replace("+00:00", "Z"),
        "claude_home": str(state.claude_home),
        "baseline": {
            "estimated_tokens": baseline_tokens,
            "tokens_source": "bytes_estimate",
            "tier": tier_for_baseline(baseline_tokens),
            "measured_bytes": measured_bytes,
            "unmeasured_sources": sum(1 for e in composition if e["tokens_source"] == "unmeasured"),
        },
        "inventory": _inventory(state),
        "composition": composition,
        "findings": [],
        "warnings": list(state.warnings),
        "projects_audited": [],
    }


def render_json(state: InstallationState) -> str:
    """Render the state as a single JSON document."""
    return json.dumps(build_report(state), indent=2, sort_keys=False)


def render_default(state: InstallationState) -> str:
    """Plain-text summary until M6 delivers the hero report."""
    report = build_report(state)
    lines: list[str] = []
    lines.append(f"unclog {report['unclog_version']}  ·  schema {report['schema']}")
    lines.append(f"claude_home: {report['claude_home']}")
    lines.append("")
    baseline = report["baseline"]
    lines.append(
        f"baseline (byte estimate): ~{baseline['estimated_tokens']:,} tokens  [{baseline['tier']}]"
    )
    if baseline["unmeasured_sources"]:
        lines.append(
            f"  ({baseline['unmeasured_sources']} sources unmeasured — "
            f"session JSONL parse arrives in M2)"
        )
    lines.append("")
    inv = report["inventory"]
    lines.append(
        "inventory: "
        f"{inv['skills']} skills · {inv['agents']} agents · "
        f"{inv['commands']} commands · {inv['plugins']} plugins · "
        f"{inv['mcp_servers']} MCP servers · "
        f"{inv['projects_known']} known projects"
    )
    if report["composition"]:
        lines.append("")
        lines.append("composition (largest first):")
        for entry in report["composition"]:
            if entry["bytes"] is None:
                size = "unmeasured"
            else:
                size = f"{entry['bytes']:>8,} B"
            lines.append(f"  {size}  {entry['source']}")
    if report["warnings"]:
        lines.append("")
        lines.append("warnings:")
        for w in report["warnings"]:
            lines.append(f"  ! {w}")
    return "\n".join(lines) + "\n"
