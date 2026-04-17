from __future__ import annotations

from pathlib import Path

from unclog.findings.base import Action, Finding, Scope


def test_scope_json_includes_project_path_when_present() -> None:
    assert Scope(kind="global").to_json() == {"kind": "global"}
    payload = Scope(kind="project", project_path=Path("/p")).to_json()
    assert payload == {"kind": "project", "project_path": "/p"}


def test_action_json_omits_unset_optional_fields() -> None:
    assert Action(primitive="flag_only").to_json() == {"primitive": "flag_only"}
    payload = Action(
        primitive="delete_file",
        path=Path("/x.md"),
        server_name="github",
        plugin_key="plugin@market",
        heading="Hello",
        line_numbers=(1, 2),
    ).to_json()
    assert payload == {
        "primitive": "delete_file",
        "path": "/x.md",
        "server_name": "github",
        "plugin_key": "plugin@market",
        "heading": "Hello",
        "line_numbers": [1, 2],
    }


def test_finding_json_roundtrip_has_stable_keys() -> None:
    finding = Finding(
        id="unused_command:ship",
        type="unused_command",
        title="Remove /ship",
        reason="0 uses in 120d",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=Path("/x/ship.md")),
        auto_checked=True,
        token_savings=42,
        evidence={"age_days": 120},
    )
    payload = finding.to_json()
    assert payload["id"] == "unused_command:ship"
    assert payload["type"] == "unused_command"
    assert payload["action"]["primitive"] == "delete_file"
    assert payload["scope"]["kind"] == "global"
    assert payload["auto_checked"] is True
    assert payload["token_savings"] == 42
    assert payload["evidence"] == {"age_days": 120}
