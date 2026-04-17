"""User-configurable detector thresholds (spec §6, §21).

Location: ``<claude_home>/.unclog/config.toml``. The file is optional —
defaults match the spec. Malformed or out-of-range values are replaced
with the default and recorded as a warning; unknown keys are silently
ignored (forward-compatibility with v0.2+ additions).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# Spec §6 defaults.
DEFAULT_UNUSED_DAYS = 90
DEFAULT_STALE_PLUGIN_DAYS = 90
DEFAULT_PROMOTE_MIN_PROJECTS = 3


@dataclass(frozen=True)
class Thresholds:
    """Numeric thresholds that gate every detector's auto-check decision."""

    unused_days: int = DEFAULT_UNUSED_DAYS
    stale_plugin_days: int = DEFAULT_STALE_PLUGIN_DAYS
    promote_min_projects: int = DEFAULT_PROMOTE_MIN_PROJECTS


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):  # bool subclasses int — reject explicitly
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def load_thresholds(config_path: Path, warnings: list[str] | None = None) -> Thresholds:
    """Read ``config.toml`` and return validated thresholds.

    ``warnings`` (when given) collects human-readable notes about any
    fallbacks taken so the main scan can surface them in its output.
    """
    if not config_path.is_file():
        return Thresholds()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        if warnings is not None:
            warnings.append(f"Could not parse {config_path}: {exc}. Using defaults.")
        return Thresholds()

    section = data.get("thresholds") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return Thresholds()

    return Thresholds(
        unused_days=_coerce_positive_int(section.get("unused_days"), DEFAULT_UNUSED_DAYS),
        stale_plugin_days=_coerce_positive_int(
            section.get("stale_plugin_days"), DEFAULT_STALE_PLUGIN_DAYS
        ),
        promote_min_projects=_coerce_positive_int(
            section.get("promote_min_projects"), DEFAULT_PROMOTE_MIN_PROJECTS
        ),
    )
