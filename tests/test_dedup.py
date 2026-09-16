"""Dedup: the same activity must never be written twice."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from diarysync.dedup import ACTION_INSERT, ACTION_SKIP, DedupPolicy, decide, window_overlap
from diarysync.diary import Diary
from diarysync.ledger import Ledger
from diarysync.models import Activity, WorkEntry


def make_activity(day: date, start: str, end: str, label: str = "跑步", **kwargs) -> Activity:
    start_dt = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    end_dt = datetime.combine(day, datetime.strptime(end, "%H:%M").time())
    kwargs.setdefault("title", "")
    return Activity.build(
        day=day,
        start=start_dt,
        end=end_dt,
        type_key="running",
        type_label=label,
        **kwargs,
    )


def write_diary(tmp_path: Path, body: str) -> Diary:
    path = tmp_path / "2026-08-14.md"
    path.write_text(body, encoding="utf-8")
    return Diary.load(path)


def test_window_overlap_uses_the_shorter_window():
    assert window_overlap((600, 780), (660, 720)) == 1.0
    assert window_overlap((600, 660), (660, 720)) == 0.0
    assert window_overlap((600, 660), (630, 720)) == 0.5


def test_identical_line_is_skipped(tmp_path: Path):
    diary = write_diary(
        tmp_path,
        "# 日程\n\n- [x] 16:18 - 16:53 #运动 跑步机 乳酸阈值\n",
    )
    activity = make_activity(date(2026, 8, 14), "16:18", "16:53", "跑步机", title="乳酸阈值")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy())
    assert decision.action == ACTION_SKIP
    assert "identical" in decision.reason


def test_hand_written_block_suppresses_the_activity(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 14:30 - 16:00 #运动 有氧\n")
    activity = make_activity(date(2026, 8, 14), "14:42", "15:36", "跑步机")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy())
    assert decision.action == ACTION_SKIP
    assert "overlap" in decision.reason


def test_adjacent_activities_are_both_kept(tmp_path: Path):
    """16:11-16:16 and 16:18-16:53 are two separate activities, not a duplicate."""
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 16:11 - 16:16 #运动 跑步机\n")
    activity = make_activity(date(2026, 8, 14), "16:18", "16:53", "跑步机")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy())
    assert decision.action == ACTION_INSERT


def test_a_non_exercise_line_does_not_block(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 17:05 - 19:25 散步+洗澡\n")
    activity = make_activity(date(2026, 8, 14), "17:30", "18:00")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy())
    assert decision.action == ACTION_INSERT


def test_untimed_exercise_lines_do_not_block(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] #运动 跑步\n")
    activity = make_activity(date(2026, 8, 14), "17:30", "18:00")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy())
    assert decision.action == ACTION_INSERT


def test_ledger_makes_repeat_runs_idempotent(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n")
    activity = make_activity(date(2026, 8, 14), "17:30", "18:00")
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.remember(activity.identity_keys(), day="2026-08-14", line="x", source="garmin")

    decision = decide(activity, diary, ledger, DedupPolicy())
    assert decision.action == ACTION_SKIP
    assert "ledger" in decision.reason

    without_ledger = decide(activity, diary, ledger, DedupPolicy(use_ledger=False))
    assert without_ledger.action == ACTION_INSERT


def test_force_bypasses_every_guard(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 17:30 - 18:00 #运动 跑步\n")
    activity = make_activity(date(2026, 8, 14), "17:30", "18:00")
    decision = decide(activity, diary, Ledger(tmp_path / "ledger.json"), DedupPolicy(force=True))
    assert decision.action == ACTION_INSERT


def test_overlap_ratio_can_be_relaxed(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 17:00 - 18:00 #运动 有氧\n")
    activity = make_activity(date(2026, 8, 14), "17:30", "18:30")
    strict = decide(activity, diary, Ledger(tmp_path / "l.json"), DedupPolicy(overlap_ratio=0.9))
    loose = decide(activity, diary, Ledger(tmp_path / "l.json"), DedupPolicy(overlap_ratio=0.4))
    assert strict.action == ACTION_INSERT
    assert loose.action == ACTION_SKIP


def test_garmin_id_and_content_hash_both_identify_a_record():
    activity = make_activity(
        date(2026, 8, 14), "17:30", "18:00", source_id="garmin:639555832"
    )
    keys = activity.identity_keys()
    assert keys[0] == "id:garmin:639555832"
    assert len(keys) == 2


def test_content_key_is_stable_across_seconds():
    """The API reports float seconds, the CSV whole seconds; keys must match."""
    day = date(2026, 8, 22)
    coarse = make_activity(day, "16:14", "16:47")
    finer = make_activity(day, "16:14", "16:47")
    assert coarse.content_key == finer.content_key


# ---------------------------------------------------------------------------
# work records
# ---------------------------------------------------------------------------


def make_work(day: date, start: str, end: str, tag: str, summary: str = "做点事") -> WorkEntry:
    return WorkEntry.build(
        day=day,
        start=datetime.combine(day, datetime.strptime(start, "%H:%M").time()),
        end=datetime.combine(day, datetime.strptime(end, "%H:%M").time()),
        tag=tag,
        summary=summary,
        session_id="s1",
    )


def test_work_record_is_suppressed_by_a_same_tag_line(tmp_path: Path):
    diary = write_diary(
        tmp_path,
        "# 日程\n\n- [x] 13:53 - 14:36 #科研 对照原始三狼代码核查两狼 RS 评估\n",
    )
    record = make_work(date(2026, 8, 14), "13:53", "18:43", "科研", "chat:")
    decision = decide(record, diary, Ledger(tmp_path / "l.json"), DedupPolicy())
    assert decision.action == ACTION_SKIP
    assert "overlap" in decision.reason


def test_twin_window_is_suppressed_even_when_the_tag_differs(tmp_path: Path):
    """A human writes #审查, the tagger guesses #科研 — same window, same work."""
    diary = write_diary(
        tmp_path,
        "# 日程\n\n- [x] 17:44 - 18:05 #审查 复核两狼 shared model 与 RS 改动\n",
    )
    record = make_work(date(2026, 8, 14), "17:44", "18:06", "科研", "review changes")
    decision = decide(record, diary, Ledger(tmp_path / "l.json"), DedupPolicy())
    assert decision.action == ACTION_SKIP
    assert "near-identical" in decision.reason


def test_all_day_block_does_not_swallow_short_sessions(tmp_path: Path):
    diary = write_diary(
        tmp_path,
        "# 日程\n\n- [x] 08:35 - 23:59 #实验室工作 huawei\n",
    )
    record = make_work(date(2026, 8, 14), "10:23", "11:50", "开发", "find experiment settings")
    decision = decide(record, diary, Ledger(tmp_path / "l.json"), DedupPolicy())
    assert decision.action == ACTION_INSERT


def test_exercise_and_work_never_suppress_each_other(tmp_path: Path):
    diary = write_diary(tmp_path, "# 日程\n\n- [x] 19:00 - 20:10 #运动 有氧\n")

    work = make_work(date(2026, 8, 14), "19:05", "20:05", "科研", "看论文")
    assert decide(work, diary, Ledger(tmp_path / "l.json"), DedupPolicy()).action == ACTION_INSERT

    diary2 = write_diary(tmp_path, "# 日程\n\n- [x] 19:00 - 20:10 #科研 看论文\n")
    activity = make_activity(date(2026, 8, 14), "19:05", "20:05")
    assert (
        decide(activity, diary2, Ledger(tmp_path / "l2.json"), DedupPolicy()).action
        == ACTION_INSERT
    )
