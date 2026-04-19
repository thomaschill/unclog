"""Centralised display-mode resolution (spec §10, §11.9).

Every rendering layer — welcome frame, hero, countdown — asks the
same question: *can I show colour?* and *can I move?* Rather than each
layer re-deriving the answer from ``NO_COLOR`` + ``isatty`` + CLI flags,
we resolve it once at the CLI entry point and pass a :class:`DisplayOptions`
down.

Precedence (strongest first):

1. ``--json`` or ``--plain`` → plain, no colour, no animation.
2. ``NO_COLOR`` env var → plain, no colour, no animation.
3. Non-TTY stdout → plain, no colour, no animation.
4. ``--report`` or ``--no-animation`` → rich + colour, no animation.
5. Otherwise → rich + colour + animation (default interactive).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayOptions:
    """Rendering capabilities available for this invocation.

    - ``plain``: route output through :func:`render_plain` (ASCII, no colour).
    - ``colour``: emit Rich style codes. Implied by ``not plain``.
    - ``animate``: run post-apply countdown. Requires ``colour``.
    - ``show_wordmark``: render the full product frame (welcome panel +
      bottom hint bar). Suppressed for ``--report``/``--json``/``--plain``
      per spec §11.4. Flag name is retained for historical reasons —
      semantically it means "show chrome, not just the report body".
    - ``verbose``: show the full pre-picker chrome (scan-meta block in
      the welcome panel, persistent tips, "also running" footer). Off
      by default — the trimmed view is enough once the user has seen
      the safety messaging once. Plain/JSON paths ignore this flag.
    """

    plain: bool
    colour: bool
    animate: bool
    show_wordmark: bool
    verbose: bool

    @classmethod
    def resolve(
        cls,
        *,
        as_json: bool,
        plain_flag: bool,
        report_only: bool,
        no_animation_flag: bool,
        verbose_flag: bool = False,
        is_tty: bool | None = None,
        env: dict[str, str] | None = None,
    ) -> DisplayOptions:
        """Compute the display mode for one ``unclog`` invocation.

        ``is_tty`` / ``env`` default to real process state but can be
        injected for deterministic tests.
        """
        tty = is_tty if is_tty is not None else _stdout_is_tty()
        environ = env if env is not None else os.environ
        no_color = bool(environ.get("NO_COLOR"))

        if as_json or plain_flag or no_color or not tty:
            return cls(
                plain=True, colour=False, animate=False, show_wordmark=False, verbose=False
            )
        if report_only:
            return cls(
                plain=False,
                colour=True,
                animate=False,
                show_wordmark=False,
                verbose=verbose_flag,
            )
        if no_animation_flag:
            return cls(
                plain=False,
                colour=True,
                animate=False,
                show_wordmark=True,
                verbose=verbose_flag,
            )
        return cls(
            plain=False,
            colour=True,
            animate=True,
            show_wordmark=True,
            verbose=verbose_flag,
        )


def _stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = ["DisplayOptions"]
