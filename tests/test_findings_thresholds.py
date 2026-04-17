from __future__ import annotations

from pathlib import Path

from unclog.findings.thresholds import (
    DEFAULT_PROMOTE_MIN_PROJECTS,
    DEFAULT_STALE_PLUGIN_DAYS,
    DEFAULT_UNUSED_DAYS,
    load_thresholds,
)


def test_missing_config_returns_defaults(tmp_path: Path) -> None:
    thresholds = load_thresholds(tmp_path / "none.toml")
    assert thresholds.unused_days == DEFAULT_UNUSED_DAYS
    assert thresholds.stale_plugin_days == DEFAULT_STALE_PLUGIN_DAYS
    assert thresholds.promote_min_projects == DEFAULT_PROMOTE_MIN_PROJECTS


def test_valid_config_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[thresholds]\n"
        "unused_days = 30\n"
        "stale_plugin_days = 60\n"
        "promote_min_projects = 5\n",
        encoding="utf-8",
    )
    thresholds = load_thresholds(path)
    assert thresholds.unused_days == 30
    assert thresholds.stale_plugin_days == 60
    assert thresholds.promote_min_projects == 5


def test_non_positive_values_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[thresholds]\n"
        "unused_days = 0\n"
        "stale_plugin_days = -10\n"
        "promote_min_projects = 'three'\n",
        encoding="utf-8",
    )
    thresholds = load_thresholds(path)
    assert thresholds.unused_days == DEFAULT_UNUSED_DAYS
    assert thresholds.stale_plugin_days == DEFAULT_STALE_PLUGIN_DAYS
    assert thresholds.promote_min_projects == DEFAULT_PROMOTE_MIN_PROJECTS


def test_malformed_toml_warns_and_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[thresholds\nbroken", encoding="utf-8")
    warnings: list[str] = []
    thresholds = load_thresholds(path, warnings)
    assert thresholds.unused_days == DEFAULT_UNUSED_DAYS
    assert any("Could not parse" in w for w in warnings)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[thresholds]\nunused_days = 42\nfuture_key = 'ok'\n",
        encoding="utf-8",
    )
    thresholds = load_thresholds(path)
    assert thresholds.unused_days == 42
