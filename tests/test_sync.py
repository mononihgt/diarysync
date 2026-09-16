"""End-to-end: records in, diary edits out, twice is the same as once."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from diarysync.config import load_settings
from diarysync.ledger import Ledger
from diarysync.models import Activity, WorkEntry
from diarysync.sync import run


def make_activity(day: date, start: str, end: str, label: str = "跑步", title: str = "") -> Activity:
    start_dt = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    end_dt = datetime.combine(day, datetime.strptime(end, "%H:%M").time())
    return Activity.build(
        day=day,
        start=start_dt,
        end=end_dt,
        type_key="running",
        type_label=label,
        title=title,
    )


def make_work(day: date, start: str, end: str, summary: str = "写脚本") -> WorkEntry:
    return WorkEntry.build(
        day=day,
        start=datetime.combine(day, datetime.strptime(start, "%H:%M").time()),
        end=datetime.combine(day, datetime.strptime(end, "%H:%M").time()),
        tag="开发",
        summary=summary,
        session_id="s1",
    )


def test_first_run_creates_the_diary_file(vault: Path):
    settings = load_settings(vault=vault)
    day = date(2026, 9, 14)
    records = [make_activity(day, "19:46", "20:19", "跑步", "杭州市 - 基础训练")]

    report = run(records, settings)

    assert len(report.inserted) == 1
    assert report.skipped == []
    path = vault / "diary" / "2026-09-14.md"
    assert path in report.files_created
    assert path.read_text(encoding="utf-8") == (
        "# 打卡\n\n- [x] 运动\n\n# 日程\n\n- [x] 19:46 - 20:19 #运动 跑步 杭州市 - 基础训练\n"
    )


def test_second_run_is_a_noop(vault: Path):
    settings = load_settings(vault=vault)
    day = date(2026, 9, 14)
    records = [make_activity(day, "19:46", "20:19", "跑步", "杭州市 - 基础训练")]

    run(records, settings)
    path = vault / "diary" / "2026-09-14.md"
    after_first = path.read_text(encoding="utf-8")

    second = run(records, settings)

    assert second.inserted == []
    assert len(second.skipped) == 1
    assert "ledger" in second.skipped[0][1]
    assert path.read_text(encoding="utf-8") == after_first


def test_dry_run_touches_nothing(vault: Path):
    settings = load_settings(vault=vault)
    day = date(2026, 9, 14)
    records = [make_activity(day, "19:46", "20:19")]

    report = run(records, settings, dry_run=True)

    assert len(report.inserted) == 1
    assert not (vault / "diary" / "2026-09-14.md").exists()
    assert not (vault / ".diarysync" / "ledger.json").exists()
    assert report.files_written  # reported as "would write"


def test_checkin_is_added_even_when_the_schedule_line_is_a_duplicate(vault: Path):
    day = date(2026, 9, 14)
    path = vault / "diary" / "2026-09-14.md"
    path.write_text(
        "---\nweather: 晴\n---\n\n# 日志\n\n上午：组会\n\n# 日程\n\n"
        "- [x] 19:45 - 20:20 #运动 有氧\n",
        encoding="utf-8",
    )
    settings = load_settings(vault=vault)
    report = run([make_activity(day, "19:46", "20:19")], settings)

    assert report.inserted == []
    assert report.checkins_added == [day]
    text = path.read_text(encoding="utf-8")
    assert text.count("# 日程") == 1
    assert text.count("- [x] 运动") == 1
    assert "上午：组会" in text


def test_work_and_exercise_share_one_schedule_section(vault: Path):
    day = date(2026, 7, 28)
    settings = load_settings(vault=vault)
    records = [
        make_activity(day, "11:08", "11:15", "跑步机"),
        make_work(day, "13:04", "13:11", "用数据集 A/B/C 解释模型拟合"),
        make_activity(day, "19:23", "19:56", "跑步", "杭州市 跑步"),
    ]

    report = run(records, settings)

    assert len(report.inserted) == 3
    lines = [
        line
        for line in (vault / "diary" / "2026-07-28.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("- [x]")
    ]
    assert lines == [
        "- [x] 运动",
        "- [x] 11:08 - 11:15 #运动 跑步机",
        "- [x] 13:04 - 13:11 #开发 用数据集 A/B/C 解释模型拟合",
        "- [x] 19:23 - 19:56 #运动 跑步 杭州市 跑步",
    ]


def test_work_records_do_not_create_a_checkin(vault: Path):
    day = date(2026, 7, 28)
    settings = load_settings(vault=vault)
    run([make_work(day, "13:04", "13:11")], settings)
    text = (vault / "diary" / "2026-07-28.md").read_text(encoding="utf-8")
    assert "# 打卡" not in text
    assert "# 日程" in text


def test_ledger_can_be_disabled(vault: Path):
    day = date(2026, 9, 14)
    settings = load_settings(vault=vault)
    records = [make_activity(day, "19:46", "20:19", "跑步", "杭州市 - 基础训练")]
    run(records, settings)

    path = vault / "diary" / "2026-09-14.md"
    path.write_text("# 打卡\n\n- [x] 运动\n\n# 日程\n\n", encoding="utf-8")
    settings.use_ledger = False

    report = run(records, settings)
    assert len(report.inserted) == 1


def test_force_writes_even_when_present(vault: Path):
    day = date(2026, 9, 14)
    settings = load_settings(vault=vault)
    records = [make_activity(day, "19:46", "20:19")]
    run(records, settings)
    report = run(records, settings, force=True)
    assert len(report.inserted) == 1
    text = (vault / "diary" / "2026-09-14.md").read_text(encoding="utf-8")
    assert text.count("19:46 - 20:19") == 2


def test_ledger_file_is_written_atomically_and_reads_back(vault: Path):
    settings = load_settings(vault=vault)
    run([make_activity(date(2026, 9, 14), "19:46", "20:19")], settings)
    ledger = Ledger(settings.ledger_path).load()
    assert len(ledger) >= 1
    assert all(entry["day"] == "2026-09-14" for entry in ledger.entries().values())


def test_records_from_two_sources_for_one_activity_collapse(vault: Path):
    """API record and CSV record for the same run must not both be written."""
    day = date(2026, 9, 14)
    settings = load_settings(vault=vault)
    api = make_activity(day, "19:46", "20:19", "跑步", "杭州市 - 基础训练")
    csv = make_activity(day, "19:46", "20:19", "跑步", "杭州市 - 基础训练")
    report = run([api, csv], settings)
    assert len(report.inserted) == 1
    assert len(report.skipped) == 1
