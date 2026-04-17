"""Detect MCP servers loaded in the session but never invoked.

**v0.1 is intentionally a no-op detector.** Distinguishing "loaded"
from "invoked" requires parsing tool_use records out of the full
session JSONL, which ships in v0.2 (spec §5.3 and §19). For v0.1 we
cannot tell from the system block alone whether a given MCP was ever
actually called by Claude.

The module exists so the top-level detector map lines up with the spec
§6 table, and so the v0.2 upgrade is a drop-in replacement rather than
a new wire-up.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Finding
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    return []
