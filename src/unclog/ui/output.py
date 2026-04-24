"""Render the pre-picker frame: welcome panel + baseline panel.

All token-cost math lives on :class:`~unclog.findings.base.Finding` —
``build_composition`` is a thin reduction over the findings list, not a
parallel scan of :class:`~unclog.state.InstallationState`.
"""

from __future__ import annotations

from rich.console import Console

from unclog.findings.base import Finding
from unclog.state import InstallationState
from unclog.ui.hero import CompositionKind, CompositionRow, render_baseline_panel
from unclog.ui.welcome import welcome_panel

_AGGREGATE_KINDS: dict[str, CompositionKind] = {
    "agent_inventory": "agents",
    "skill_inventory": "skills",
    "command_inventory": "commands",
}


def build_composition(findings: list[Finding]) -> list[CompositionRow]:
    """Reduce ``findings`` into one composition row per top-contributor.

    Agents, skills, and commands each roll up to a single aggregate row
    labelled ``"N agents"``/``"N skills"``/``"N commands"``. MCPs stay
    one-row-per-server so users can see *which* server costs them.

    Findings whose ``token_savings`` is ``None`` or zero are dropped —
    the hero needs something to display; ``—`` placeholders crowd out
    real contributors.
    """
    aggregate_tokens: dict[CompositionKind, int] = {"agents": 0, "skills": 0, "commands": 0}
    aggregate_counts: dict[CompositionKind, int] = {"agents": 0, "skills": 0, "commands": 0}
    rows: list[CompositionRow] = []

    for finding in findings:
        tokens = finding.token_savings or 0
        kind = _AGGREGATE_KINDS.get(finding.type)
        if kind is not None:
            aggregate_counts[kind] += 1
            aggregate_tokens[kind] += tokens
            continue
        if finding.type == "mcp_inventory" and tokens > 0:
            scope_label = (
                f"project:{finding.scope.project_path}"
                if finding.scope.kind == "project" and finding.scope.project_path is not None
                else None
            )
            rows.append(
                CompositionRow(
                    kind="mcp",
                    label=finding.title,
                    tokens=tokens,
                    scope_label=scope_label,
                )
            )

    for aggregate_kind, total in aggregate_tokens.items():
        if total > 0:
            rows.append(
                CompositionRow(
                    kind=aggregate_kind,
                    label=f"{aggregate_counts[aggregate_kind]} {aggregate_kind}",
                    tokens=total,
                )
            )

    rows.sort(key=lambda r: -r.tokens)
    return rows


def baseline_tokens(findings: list[Finding]) -> int:
    """Sum every measurable finding — powers the post-apply ``baseline now`` line."""
    return sum(f.token_savings or 0 for f in findings)


def render_header(
    state: InstallationState, findings: list[Finding], console: Console
) -> None:
    """Print the welcome panel + baseline panel, then a blank line."""
    console.print(welcome_panel(state))
    console.print("")
    console.print(render_baseline_panel(baseline_tokens(findings), build_composition(findings)))
    console.print("")


__all__ = ["baseline_tokens", "build_composition", "render_header"]
