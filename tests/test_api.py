"""The public Python API: import, compose, and call the tool from a script."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

import diarysync
from diarysync import DiarySync
from diarysync.api import collect_work, resolve_window

# ---------------------------------------------------------------------------
# package surface
# ---------------------------------------------------------------------------


def test_package_exports_the_public_api():
    for name in ("DiarySync", "Activity", "WorkEntry", "SyncReport", "Settings", "run"):
        assert hasattr(diarysync, name), name
    assert diarysync.__version__


def test_readme_example_shapes_are_callable():
    """Mirror the docstring examples so they cannot silently rot."""
    assert callable(diarysync.DiarySync)
    assert callable(diarysync.load_settings)
    assert callable(diarysync.collect_activities)
    assert callable(diarysync.run)
    assert callable(diarysync.load_csv)


# ---------------------------------------------------------------------------
# date windows
# ---------------------------------------------------------------------------


def test_parse_day_accepts_several_types():
    assert diarysync.parse_day("2026-09-14") == date(2026, 9, 14)
    assert diarysync.parse_day(date(2026, 9, 14)) == date(2026, 9, 14)
    assert diarysync.parse_day(datetime(2026, 9, 14, 19, 46)) == date(2026, 9, 14)
    with pytest.raises(ValueError):
        diarysync.parse_day("not-a-date")


def test_resolve_window_counts_days_back_from_until():
    since, until = resolve_window(until="2026-09-16", days=7)
    assert (since, until) == (date(2026, 9, 10), date(2026, 9, 16))


def test_resolve_window_default_and_validation():
    since, until = resolve_window(until=date(2026, 9, 16), default_days=30)
    assert (until - since).days == 29
    with pytest.raises(ValueError):
        resolve_window(since="2026-09-16", until="2026-09-01")
    with pytest.raises(ValueError):
        resolve_window(until="2026-09-16", days=0)


# ---------------------------------------------------------------------------
# facade
# ---------------------------------------------------------------------------


def test_facade_resolves_the_vault_and_diary_dir(vault):
    sync = DiarySync(vault=vault)
    assert sync.vault_path == vault.resolve()
    assert sync.diary_dir == vault.resolve() / "diary"


def test_facade_open_discovers_the_vault_from_cwd(vault, monkeypatch):
    diary = vault / "diary" / "2026-09-14.md"
    diary.write_text("# 打卡\n\n- [x] 运动\n", encoding="utf-8")
    nested = vault / "diary"
    monkeypatch.chdir(nested)
    sync = DiarySync.open()
    assert sync.vault_path == vault.resolve()


def test_facade_reads_the_ledger_location(vault):
    sync = DiarySync(vault=vault)
    assert sync.settings.ledger_path == vault.resolve() / ".diarysync" / "ledger.json"


def test_configure_returns_an_independent_copy(vault):
    sync = DiarySync(vault=vault)
    other = sync.configure(dry_run=True, overlap_ratio=0.9)
    assert other.dry_run is True
    assert other.settings.overlap_ratio == 0.9
    assert sync.dry_run is False
    assert other.settings.vault == sync.settings.vault


def test_login_reuses_the_token_cache_without_any_credentials(vault, monkeypatch):
    """A valid token store means email and password are not needed at all."""
    seen = {}

    def fake_probe_login(email, password, **kwargs):
        seen["email"], seen["password"] = email, password
        return "login ok against connect.garmin.cn"

    monkeypatch.setattr("diarysync.sources.garmin.probe_login", fake_probe_login)

    sync = DiarySync(vault=vault, email=None, password=None)
    assert sync.login() == "login ok against connect.garmin.cn"
    assert seen == {"email": None, "password": None}


def test_missing_credentials_are_prompted_for_then_retried(vault, monkeypatch):
    """The prompt must ask for the email too, not just the password."""
    import diarysync.api as api
    from diarysync.sources.garmin import GarminAuthError

    attempts = []

    def fake_probe_login(email, password, **kwargs):
        attempts.append((email, password))
        if len(attempts) == 1:
            raise GarminAuthError("no usable token", needs_credentials=True)
        return "ok"

    monkeypatch.setattr("diarysync.sources.garmin.probe_login", fake_probe_login)
    monkeypatch.setattr(api, "prompt_email", lambda *a, **k: "me@example.com")
    monkeypatch.setattr(api, "prompt_password", lambda *a, **k: "secret")

    sync = DiarySync(vault=vault, prompt_for_password=True)
    assert sync.login() == "ok"
    assert attempts == [(None, None), ("me@example.com", "secret")]
    assert sync.settings.garmin_email == "me@example.com"


def test_credentials_are_not_prompted_for_when_disabled(vault, monkeypatch):
    from diarysync.sources.garmin import GarminAuthError

    def boom(*args, **kwargs):
        raise GarminAuthError("no usable token", needs_credentials=True)

    monkeypatch.setattr("diarysync.sources.garmin.probe_login", boom)
    sync = DiarySync(vault=vault, prompt_for_password=False)
    with pytest.raises(GarminAuthError):
        sync.login()


def test_activities_prompts_once_then_retries(vault, monkeypatch):
    import diarysync.api as api
    from diarysync.sources.garmin import GarminAuthError

    calls = []

    def fake_collect(settings, **kwargs):
        calls.append((settings.garmin_email, settings.garmin_password))
        if len(calls) == 1:
            raise GarminAuthError("no usable token", needs_credentials=True)
        return []

    monkeypatch.setattr(api, "collect_activities", fake_collect)
    monkeypatch.setattr(api, "prompt_email", lambda *a, **k: "me@example.com")
    monkeypatch.setattr(api, "prompt_password", lambda *a, **k: "secret")

    sync = DiarySync(vault=vault, prompt_for_password=True)
    assert sync.activities(days=1) == []
    assert calls == [(None, None), ("me@example.com", "secret")]


def test_offline_activities_never_touch_credentials(vault, csv_file, monkeypatch):
    import diarysync.api as api

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("offline mode must not resolve credentials")

    monkeypatch.setattr(api, "prompt_email", explode)
    monkeypatch.setattr(api, "prompt_password", explode)

    sync = DiarySync(vault=vault, prompt_for_password=True)
    activities = sync.activities(since="2026-09-01", until="2026-09-16", csv=csv_file)
    assert [a.day for a in activities] == [date(2026, 9, 14), date(2026, 9, 15)]


# ---------------------------------------------------------------------------
# offline collection + writing
# ---------------------------------------------------------------------------

CSV_TEXT = (
    "活动类型,日期,标题,距离,热量消耗,时间\n"
    '跑步,2026-09-14 19:46:56,"杭州市 - 基础训练","6.79","380","00:33:02"\n'
    '跑步机,2026-09-15 16:14:23,"基础训练","6.10","313","00:33:02"\n'
    '跑步,2026-08-01 07:00:00,"太早了","5.00","300","00:24:00"\n'
)


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "Activities.csv"
    path.write_text(CSV_TEXT, encoding="utf-8-sig")
    return path


def test_collect_activities_from_csv_filters_the_window(vault, csv_file):
    settings = diarysync.load_settings(vault=vault)
    activities = diarysync.collect_activities(
        settings, since="2026-09-01", until="2026-09-16", csv_path=csv_file
    )
    assert [a.day for a in activities] == [date(2026, 9, 14), date(2026, 9, 15)]


def test_facade_exercise_writes_to_the_diary(vault, csv_file):
    sync = DiarySync(vault=vault)
    report = sync.exercise(csv=csv_file, since="2026-09-01", until="2026-09-16")

    assert len(report.inserted) == 2
    text = (vault / "diary" / "2026-09-14.md").read_text(encoding="utf-8")
    assert "- [x] 19:46 - 20:19 #运动 跑步 杭州市 - 基础训练" in text
    assert "- [x] 运动" in text


def test_facade_exercise_is_idempotent(vault, csv_file):
    sync = DiarySync(vault=vault)
    first = sync.exercise(csv=csv_file, since="2026-09-01", until="2026-09-16")
    second = sync.exercise(csv=csv_file, since="2026-09-01", until="2026-09-16")

    assert len(first.inserted) == 2
    assert second.inserted == []
    assert len(second.skipped) == 2


def test_facade_honours_the_instance_dry_run_flag(vault, csv_file):
    sync = DiarySync(vault=vault, dry_run=True)
    report = sync.exercise(csv=csv_file, since="2026-09-01", until="2026-09-16")
    assert len(report.inserted) == 2
    assert not (vault / "diary" / "2026-09-14.md").exists()


def test_force_flag_can_be_set_on_the_facade(vault, csv_file):
    sync = DiarySync(vault=vault, force=True)
    sync.exercise(csv=csv_file, since="2026-09-14", until="2026-09-14")
    report = sync.exercise(csv=csv_file, since="2026-09-14", until="2026-09-14")
    assert len(report.inserted) == 1


def test_facade_work_reads_supplied_transcripts(vault, tmp_path, monkeypatch):
    """A script can point the work collector at its own transcript roots."""
    import diarysync.api as api
    from diarysync.sources.sessions import SessionEvent, collect_work_entries

    events = [
        SessionEvent("s1", datetime(2026, 9, 14, 9, 0), "user", "实现一个命令行工具", "codex"),
        SessionEvent("s1", datetime(2026, 9, 14, 10, 30), "assistant", "完成", "codex"),
    ]
    monkeypatch.setattr(
        api, "collect_work", lambda settings, **kwargs: collect_work_entries(
            events, start=kwargs["since"], end=kwargs["until"]
        )
    )
    sync = DiarySync(vault=vault)
    report = sync.work(since="2026-09-14", until="2026-09-14")

    assert len(report.inserted) == 1
    line = report.inserted[0].schedule_line()
    assert line == "- [x] 09:00 - 10:31 #开发 实现一个命令行工具"


def test_collect_work_filters_by_source(vault, monkeypatch):
    from diarysync.sources import sessions as session_sources

    monkeypatch.setattr(session_sources, "default_session_roots", lambda: {})
    settings = diarysync.load_settings(vault=vault)
    assert collect_work(settings, since="2026-09-01", until="2026-09-16", source="codex") == []


def test_compose_low_level_pieces(vault, csv_file):
    """The documented low-level pipeline works end to end."""
    settings = diarysync.load_settings(vault=vault)
    records = diarysync.collect_activities(
        settings, since="2026-09-01", until="2026-09-16", csv_path=csv_file
    )
    report = diarysync.run(records, settings, dry_run=True)
    assert report.dry_run is True
    assert len(report.inserted) == 2
    assert report.as_dict()["total"] == 2


# ---------------------------------------------------------------------------
# summarizer hook
# ---------------------------------------------------------------------------


def test_build_summarizer_uses_the_command_stdout():
    summarizer = diarysync.build_summarizer("tr a-z A-Z")
    assert summarizer is not None
    assert summarizer("hello world") == "HELLO WORLD"


def test_build_summarizer_degrades_on_failure():
    summarizer = diarysync.build_summarizer("false")
    assert summarizer is not None
    assert summarizer("anything") == ""


def test_build_summarizer_is_none_without_a_command():
    assert diarysync.build_summarizer(None) is None
