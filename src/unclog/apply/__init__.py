"""Apply layer — delete selected items from disk / config."""

from __future__ import annotations

from unclog.apply.primitives import ApplyError, apply_action
from unclog.apply.runner import ApplyResult, apply_findings

__all__ = ["ApplyError", "ApplyResult", "apply_action", "apply_findings"]
