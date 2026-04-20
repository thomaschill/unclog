"""Apply a list of findings. No snapshot — irreversible by design in 0.2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from unclog.apply.primitives import ApplyError, apply_action
from unclog.findings.base import Finding


@dataclass
class ApplyResult:
    succeeded: list[Finding] = field(default_factory=list)
    failed: list[tuple[Finding, str]] = field(default_factory=list)

    @property
    def token_savings(self) -> int:
        return sum(f.token_savings or 0 for f in self.succeeded)


def apply_findings(findings: list[Finding], *, claude_home: Path) -> ApplyResult:
    """Apply each finding in order. One failure doesn't abort the batch."""
    result = ApplyResult()
    for finding in findings:
        try:
            apply_action(finding, claude_home=claude_home)
        except ApplyError as exc:
            result.failed.append((finding, str(exc)))
        except Exception as exc:
            result.failed.append((finding, f"{type(exc).__name__}: {exc}"))
        else:
            result.succeeded.append(finding)
    return result


__all__ = ["ApplyResult", "apply_findings"]
