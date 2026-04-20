from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from unclog.findings.base import Action, Finding, Scope


def test_scope_is_frozen_and_carries_optional_project_path() -> None:
    global_scope = Scope(kind="global")
    assert global_scope.project_path is None
    scoped = Scope(kind="project", project_path=Path("/p"))
    assert scoped.project_path == Path("/p")
    with pytest.raises(dataclasses.FrozenInstanceError):
        scoped.kind = "global"  # type: ignore[misc]


def test_action_defaults_allow_primitive_only() -> None:
    action = Action(primitive="delete_file")
    assert action.path is None
    assert action.server_name is None


def test_action_carries_path_and_server_name() -> None:
    action = Action(
        primitive="comment_out_mcp",
        path=Path("/x.md"),
        server_name="github",
    )
    assert action.path == Path("/x.md")
    assert action.server_name == "github"


def test_finding_token_savings_optional() -> None:
    finding = Finding(
        id="agent:ship",
        type="agent_inventory",
        title="curate ship",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=Path("/x/ship.md")),
    )
    assert finding.token_savings is None
    measured = Finding(
        id="mcp:ship",
        type="mcp_inventory",
        title="curate ship",
        scope=Scope(kind="global"),
        action=Action(primitive="comment_out_mcp", server_name="ship"),
        token_savings=4200,
    )
    assert measured.token_savings == 4200
