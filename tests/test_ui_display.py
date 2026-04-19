from __future__ import annotations

from unclog.ui.display import DisplayOptions


def test_json_forces_plain_no_colour_no_animation() -> None:
    d = DisplayOptions.resolve(
        as_json=True,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        is_tty=True,
        env={},
    )
    assert d.plain is True
    assert d.colour is False
    assert d.animate is False
    assert d.show_wordmark is False


def test_plain_flag_forces_plain() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=True,
        report_only=False,
        no_animation_flag=False,
        is_tty=True,
        env={},
    )
    assert d == DisplayOptions(
        plain=True, colour=False, animate=False, show_wordmark=False, verbose=False
    )


def test_no_color_env_forces_plain() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        is_tty=True,
        env={"NO_COLOR": "1"},
    )
    assert d.plain is True


def test_non_tty_auto_plain() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        is_tty=False,
        env={},
    )
    assert d.plain is True


def test_report_keeps_colour_but_kills_animation_and_wordmark() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=True,
        no_animation_flag=False,
        is_tty=True,
        env={},
    )
    assert d.plain is False
    assert d.colour is True
    assert d.animate is False
    assert d.show_wordmark is False


def test_no_animation_keeps_colour_and_wordmark() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=False,
        no_animation_flag=True,
        is_tty=True,
        env={},
    )
    assert d.plain is False
    assert d.colour is True
    assert d.animate is False
    assert d.show_wordmark is True


def test_default_interactive_enables_everything() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        is_tty=True,
        env={},
    )
    assert d == DisplayOptions(
        plain=False, colour=True, animate=True, show_wordmark=True, verbose=False
    )


def test_verbose_flag_propagates_when_chrome_visible() -> None:
    d = DisplayOptions.resolve(
        as_json=False,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        verbose_flag=True,
        is_tty=True,
        env={},
    )
    assert d.verbose is True


def test_verbose_flag_dropped_when_plain() -> None:
    """Plain/JSON paths render the same minimal text either way; verbose
    is meaningless without the rich-panel chrome it controls."""
    d = DisplayOptions.resolve(
        as_json=True,
        plain_flag=False,
        report_only=False,
        no_animation_flag=False,
        verbose_flag=True,
        is_tty=True,
        env={},
    )
    assert d.verbose is False
