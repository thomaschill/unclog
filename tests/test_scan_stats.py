from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from unclog.scan.stats import load_activity_index


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def test_missing_files_yield_empty_index(tmp_path: Path) -> None:
    index = load_activity_index(tmp_path / "stats.json", tmp_path / "history.jsonl")
    assert index.last_active_overall is None
    assert index.first_session_at is None
    assert index.total_sessions == 0
    assert index.total_messages == 0
    assert dict(index.per_project_last_active) == {}
    assert dict(index.slash_command_last_used) == {}
    assert dict(index.at_mention_last_used) == {}


def test_stats_cache_provides_first_session_and_totals(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps(
            {
                "firstSessionDate": "2026-01-03T14:48:11.345Z",
                "lastComputedDate": "2026-04-15",
                "totalSessions": 273,
                "totalMessages": 79372,
                "dailyActivity": [
                    {"date": "2026-04-10", "messageCount": 5},
                    {"date": "2026-04-15", "messageCount": 12},
                ],
            }
        ),
        encoding="utf-8",
    )
    index = load_activity_index(stats, tmp_path / "missing.jsonl")
    assert index.first_session_at == datetime(2026, 1, 3, 14, 48, 11, 345000, tzinfo=UTC)
    assert index.total_sessions == 273
    assert index.total_messages == 79372
    assert index.last_active_overall == datetime(2026, 4, 15, tzinfo=UTC)


def test_history_jsonl_tracks_per_project_and_slash_commands(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    _write_jsonl(
        history,
        [
            {
                "display": "/ship my code",
                "timestamp": 1_700_000_000_000,
                "project": "/Users/tom/draper",
            },
            {
                "display": "look at @code-reviewer please",
                "timestamp": 1_700_001_000_000,
                "project": "/Users/tom/draper",
            },
            {
                "display": "/ship again",
                "timestamp": 1_700_002_000_000,
                "project": "/Users/tom/other",
            },
        ],
    )
    index = load_activity_index(tmp_path / "missing.json", history)
    assert set(index.per_project_last_active) == {"/Users/tom/draper", "/Users/tom/other"}
    draper = index.per_project_last_active["/Users/tom/draper"]
    other = index.per_project_last_active["/Users/tom/other"]
    assert other > draper
    assert "ship" in index.slash_command_last_used
    assert "code-reviewer" in index.at_mention_last_used
    # Most recent slash-command wins when used multiple times.
    assert index.slash_command_last_used["ship"] == datetime.fromtimestamp(
        1_700_002_000_000 / 1000.0, tz=UTC
    )


def test_history_jsonl_is_tolerant_of_garbage_lines(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text(
        "not json\n"
        + json.dumps({"display": "/hi", "timestamp": 1_700_000_000_000, "project": "/x"})
        + "\n{broken\n"
        + json.dumps({"display": "ignored no ts", "project": "/x"})
        + "\n",
        encoding="utf-8",
    )
    index = load_activity_index(tmp_path / "missing.json", history)
    assert "hi" in index.slash_command_last_used
    assert index.per_project_last_active["/x"] is not None


def test_history_wins_over_stats_cache_for_last_overall(tmp_path: Path) -> None:
    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps({"dailyActivity": [{"date": "2026-01-01"}]}), encoding="utf-8"
    )
    history = tmp_path / "history.jsonl"
    _write_jsonl(
        history,
        [{"display": "hi", "timestamp": 1_800_000_000_000, "project": "/x"}],
    )
    index = load_activity_index(stats, history)
    assert index.last_active_overall is not None
    assert index.last_active_overall.year >= 2027


def test_age_days_returns_none_for_missing(tmp_path: Path) -> None:
    index = load_activity_index(tmp_path / "s.json", tmp_path / "h.jsonl")
    now = datetime(2026, 4, 17, tzinfo=UTC)
    assert index.age_days(now, of=index.last_active_overall) is None


def test_age_days_computes_positive_days(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    _write_jsonl(
        history,
        [{"display": "hi", "timestamp": 1_700_000_000_000, "project": "/x"}],
    )
    index = load_activity_index(tmp_path / "missing.json", history)
    # 1_700_000_000 s ~ 2023-11-14; pick a much later "now".
    now = datetime(2026, 4, 17, tzinfo=UTC)
    age = index.age_days(now, of=index.last_active_overall)
    assert age is not None and age > 700
