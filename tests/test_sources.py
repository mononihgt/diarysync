"""Sources: Garmin CSV, Garmin API payloads, and agent sessions."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from diarysync.activity_types import canonical_type_key, is_known_type, type_label
from diarysync.models import WorkEntry
from diarysync.sources.garmin import _activity_from_payload
from diarysync.sources.garmin_csv import CsvFormatError, load_csv, parse_duration
from diarysync.sources.sessions import (
    SessionEvent,
    collect_work_entries,
    infer_tag,
    summarise_prompt,
)

CSV_TEXT = """活动类型,日期,我的最爱,标题,距离,热量消耗,时间,平均心率,最大心率,有氧效果,平均步频,最高步频,平均速度,最佳配速,累计爬升,累计下降,平均步长,平均垂直步幅比,平均垂直摆动,平均触地时间,平均坡度调整配速,Normalized Power\u00ae (NP\u00ae),Training Stress Score\u00ae,平均功率,最大功率,步数,体能消耗,减压,最佳单圈时间,圈数,移动时间,全程耗时,最低海拔,最高海拔
跑步,2026-08-22 16:14:45,false,"衢州市 - 基础训练","6.55","364","00:33:02","155","172","3.5","172","180","5:03","3:54","9","7","1.15","8.3","9.6","250","5:02","261","0.0","264","350","5,660","'-9","否","00:00:02.3","8","00:33:00","00:38:47","92","102"
跑步机,2026-08-14 16:11:56,false,"跑步机","0.71","37","00:05:00.6","121","138","0.6","167","176","7:03","5:41","--","--","0.96","9.3","8.9","267","--","217","0.0","210","230","848","'-1","否","00:05:00.6","1","00:05:00","00:05:00.6","--","--"
室内骑行,2026-07-29 19:52:35,false,"室内骑行","0.00","40","00:09:21.4","98","120","0.2","--","--","--","--","--","--","--","--","--","--","--","--","0.0","--","--","--","'-1","否","00:09:21.4","1","00:00:00","00:09:21.4","--","--"
骑行,2026-08-13 19:08:44,false,"衢州市 骑行","11.45","137","00:57:42","81","99","0.1","--","--","11.9","21.3","32","28","--","--","--","--","--","--","0.0","--","--","--","'-4","否","00:07:14.8","3","00:54:58","00:57:42","92","107"
"""


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "Activities.csv"
    path.write_text(CSV_TEXT, encoding="utf-8-sig")
    return path


def test_parse_duration_variants():
    assert parse_duration("00:33:02") == 1982.0
    assert parse_duration("00:05:00.6") == pytest.approx(300.6)
    assert parse_duration("--:--:--") is None
    assert parse_duration("") is None


def test_load_csv_maps_every_row(csv_file: Path):
    activities = load_csv(csv_file)
    assert len(activities) == 4
    first = activities[0]
    assert first.day == date(2026, 8, 22)
    assert first.start.strftime("%H:%M") == "16:14"
    assert first.end.strftime("%H:%M") == "16:47"  # 16:14:45 + 33:02, floored
    assert first.type_label == "跑步"
    assert first.summary == "跑步 衢州市 - 基础训练"
    assert first.distance_m == pytest.approx(6550.0)
    assert first.calories == 364.0


def test_load_csv_handles_type_only_titles(csv_file: Path):
    activities = {a.type_key: a for a in load_csv(csv_file)}
    indoor = activities["indoor_cycling"]
    assert indoor.summary == "室内骑行"


def test_load_csv_rejects_a_foreign_csv(tmp_path: Path):
    path = tmp_path / "notes.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(CsvFormatError):
        load_csv(path)


def test_api_payload_maps_to_the_same_record_as_the_csv(csv_file: Path):
    csv_activity = next(a for a in load_csv(csv_file) if a.day == date(2026, 8, 22))
    payload = {
        "activityId": 639555832,
        "activityName": "衢州市 - 基础训练",
        "startTimeLocal": "2026-08-22 16:14:45",
        "duration": 1982.0,
        "distance": 6550.0,
        "calories": 364.0,
        "activityType": {"typeKey": "running"},
    }
    api_activity = _activity_from_payload(payload)
    assert api_activity is not None
    # The whole point: the CSV and the API must agree on content identity so a
    # later CSV sync does not duplicate an API-synced activity.
    assert api_activity.content_key == csv_activity.content_key
    assert api_activity.identity_keys()[0] == "id:garmin:639555832"


def test_api_payload_without_start_is_ignored():
    assert _activity_from_payload({"duration": 100}) is None
    assert _activity_from_payload({"startTimeLocal": "2026-08-22 16:14:45"}) is None


def test_explain_login_error_names_only_the_missing_field():
    from diarysync.sources.garmin import explain_login_error

    err = explain_login_error(
        Exception("Username and password are required"),
        email=None,
        password="pw",
        token_store="~/.garminconnect",
    )
    assert err.needs_credentials is True
    assert "邮箱" in str(err)
    assert "密码（" not in str(err)


def test_explain_login_error_flags_a_stale_token_cache():
    from diarysync.sources.garmin import explain_login_error

    err = explain_login_error(
        Exception("Failed to retrieve social profile"),
        email="me@example.com",
        password="pw",
        token_store="~/.garminconnect",
    )
    assert err.needs_credentials is False
    assert "token" in str(err)


def test_explain_login_error_passes_through_other_failures():
    from diarysync.sources.garmin import explain_login_error

    err = explain_login_error(Exception("boom"), email="a@b.c", password="p", token_store=None)
    assert "boom" in str(err)
    assert err.needs_credentials is False


def test_type_vocabulary():
    assert canonical_type_key("treadmill_running") == "treadmill_running"
    assert canonical_type_key("跑步机") == "treadmill_running"
    assert type_label("indoor_cycling") == "室内骑行"
    assert type_label("室内骑行") == "室内骑行"
    assert is_known_type("有氧运动")
    assert not is_known_type("基础训练")


def test_summarise_prompt_prefers_a_goal_block():
    prompt = "## background\n读文件\n\n## goal\n用脚本一次性开启训练\n\n## tips\n1. 不要同步"
    assert summarise_prompt(prompt) == "用脚本一次性开启训练"


def test_summarise_prompt_strips_code_and_truncates():
    prompt = "```txt\nA: 就是一开始你就要训练那个模型\n```\n请检查一下当前模型是否支持"
    summary = summarise_prompt(prompt, limit=20)
    assert len(summary) <= 20
    assert "```" not in summary


def test_summarise_prompt_skips_a_short_heading():
    prompt = "Purpose\n\nThe interface_v2 platform needs a full refactor."
    assert summarise_prompt(prompt, 40) == "The interface_v2 platform needs a full…"


def test_summarise_prompt_keeps_a_short_prompt():
    assert summarise_prompt("hello", 40) == "hello"


def test_infer_tag():
    assert infer_tag("ssh sr665-4 连不上了") == "服务器"
    assert infer_tag("dsh 卡死了，如何 kill 进程") == "故障"
    assert infer_tag("请 review 一下这个改动") == "审查"
    assert infer_tag("安装一下这个插件") == "配置"
    assert infer_tag("随便聊聊") == "工作"


def test_collect_work_entries_groups_by_session_and_day():
    events = [
        SessionEvent("s1", datetime(2026, 7, 28, 13, 4), "user", "看一下模型训练参数", "codex"),
        SessionEvent("s1", datetime(2026, 7, 28, 14, 36), "assistant", "已完成", "codex"),
        SessionEvent("s1", datetime(2026, 7, 29, 15, 6), "user", "继续核对 checkpoint", "codex"),
    ]
    entries = collect_work_entries(events, start=date(2026, 7, 28), end=date(2026, 7, 29))
    assert len(entries) == 2
    first = entries[0]
    assert first.day == date(2026, 7, 28)
    assert first.start.strftime("%H:%M") == "13:04"
    assert first.end.strftime("%H:%M") == "14:37"
    assert first.summary == "看一下模型训练参数"
    assert first.tag == "科研"
    assert entries[1].day == date(2026, 7, 29)


def test_collect_work_entries_respects_the_window_and_min_minutes():
    events = [
        SessionEvent("s1", datetime(2026, 7, 1, 10, 0), "user", "旧会话", "codex"),
        SessionEvent("s2", datetime(2026, 7, 28, 10, 0), "user", "很短", "codex"),
        SessionEvent("s2", datetime(2026, 7, 28, 10, 2), "assistant", "完成", "codex"),
    ]
    entries = collect_work_entries(
        events, start=date(2026, 7, 28), end=date(2026, 7, 29), min_minutes=10
    )
    assert entries == []


def test_work_entry_line_format():
    events = [
        SessionEvent("s1", datetime(2026, 7, 28, 13, 4), "user", "对照原始三狼代码检查评估", "codex"),
        SessionEvent("s1", datetime(2026, 7, 28, 14, 36), "assistant", "完成", "codex"),
    ]
    entry = collect_work_entries(events, start=date(2026, 7, 28), end=date(2026, 7, 28))[0]
    assert entry.schedule_line() == (
        "- [x] 13:04 - 14:37 #科研 对照原始三狼代码检查评估"
    )


# ---------------------------------------------------------------------------
# transcript readers
# ---------------------------------------------------------------------------


def _rollout_line(ts: str, role: str, text: str) -> str:
    import json

    return json.dumps(
        {
            "timestamp": ts,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": text}],
            },
        },
        ensure_ascii=False,
    )


def _write_rollout(root: Path, name: str, lines: list[str]) -> Path:
    day_dir = root / "sessions" / "2026" / "07" / "28"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_codex_reader_keeps_real_sessions_and_drops_boilerplate(tmp_path: Path):
    from diarysync.sources.sessions import read_codex_events

    _write_rollout(
        tmp_path,
        "rollout-2026-07-28T13-00-00-019fa746-4d47-7322-baf3-92c8abbb3f5c.jsonl",
        [
            _rollout_line("2026-07-28T13:04:00", "user", "<permissions instructions>\nsandbox"),
            _rollout_line("2026-07-28T13:04:01", "user", "看一下模型训练参数"),
            _rollout_line("2026-07-28T13:11:00", "assistant", "已完成"),
        ],
    )
    events = list(read_codex_events(tmp_path))
    assert [event.text for event in events] == ["看一下模型训练参数", "已完成"]
    assert events[0].session_id == "019fa746-4d47-7322-baf3-92c8abbb3f5c"


def test_codex_reader_drops_approval_review_rollouts(tmp_path: Path):
    from diarysync.sources.sessions import read_codex_events

    _write_rollout(
        tmp_path,
        "rollout-2026-07-28T14-00-00-019fa76d-0b1b-7f42-ab79-aacbc131a270.jsonl",
        [
            _rollout_line(
                "2026-07-28T14:00:00",
                "user",
                "The following is the Codex agent history whose request action you are assessing",
            ),
            _rollout_line("2026-07-28T14:01:00", "user", "TRANSCRIPT START\nsome tool call"),
            _rollout_line("2026-07-28T14:02:00", "assistant", '{"outcome":"allow"}'),
        ],
    )
    assert list(read_codex_events(tmp_path)) == []


def test_dsh_reader_reads_zstd_transcripts(tmp_path: Path):
    zstandard = pytest.importorskip("zstandard")
    import json

    session_dir = tmp_path / "sessions" / "--vault--" / "session-abc"
    session_dir.mkdir(parents=True)
    lines = [
        {"type": "session", "createdAt": 1, "cwd": "/vault"},
        {
            "type": "user/message",
            "time": 1785218640000,
            "data": {
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "安装一下这个插件"}],
            },
        },
        {
            "type": "user/message",
            "time": 1785218641000,
            "data": {
                "role": "user",
                "source": {"kind": "injected"},
                "content": [{"type": "text", "text": "<system-reminder>x</system-reminder>"}],
            },
        },
    ]
    payload = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"
    (session_dir / "session.jsonl.zstd").write_bytes(
        zstandard.ZstdCompressor().compress(payload.encode("utf-8"))
    )

    from diarysync.sources.sessions import read_dsh_events

    events = list(read_dsh_events(tmp_path))
    assert len(events) == 1
    assert events[0].text == "安装一下这个插件"
    assert events[0].session_id == "session-abc"


def _session_meta_line(ts: str, thread_source: str | None, *, subagent: bool = False) -> str:
    import json

    body: dict = {"session_id": "parent", "cwd": "/vault"}
    if thread_source is not None:
        body["thread_source"] = thread_source
    body["source"] = (
        {"subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}}
        if subagent
        else "cli"
    )
    return json.dumps({"timestamp": ts, "type": "session_meta", "payload": body})


@pytest.mark.parametrize(
    "thread_source, subagent",
    [("subagent", True), ("guardian_review", False)],
)
def test_codex_reader_drops_non_user_threads(tmp_path: Path, thread_source, subagent):
    """Spawned workers and automatic reviews are not the human's work."""
    from diarysync.sources.sessions import read_codex_events

    _write_rollout(
        tmp_path,
        "rollout-2026-07-03T00-11-16-019f2399-4ae5-79c3-a85c-22489b49f353.jsonl",
        [
            _session_meta_line("2026-07-03T00:11:00", thread_source, subagent=subagent),
            _rollout_line(
                "2026-07-03T00:11:16",
                "user",
                "You are a Senior Code Reviewer doing the final whole-branch review",
            ),
            _rollout_line("2026-07-03T00:11:30", "assistant", "findings"),
        ],
    )
    assert list(read_codex_events(tmp_path)) == []


def test_codex_reader_keeps_user_threads(tmp_path: Path):
    from diarysync.sources.sessions import read_codex_events

    _write_rollout(
        tmp_path,
        "rollout-2026-07-28T13-50-30-019fa746-4d47-7322-baf3-92c8abbb3f5c.jsonl",
        [
            _session_meta_line("2026-07-28T13:50:30", "user"),
            _rollout_line("2026-07-28T13:53:30", "user", "看一下模型训练参数"),
        ],
    )
    events = list(read_codex_events(tmp_path))
    assert [event.text for event in events] == ["看一下模型训练参数"]


def test_codex_reader_without_metadata_falls_back_to_text_filters(tmp_path: Path):
    """Old transcripts have no thread_source; the review marker still catches them."""
    from diarysync.sources.sessions import read_codex_events

    _write_rollout(
        tmp_path,
        "rollout-2026-07-03T01-00-00-019f2400-0000-7000-8000-000000000000.jsonl",
        [
            _rollout_line(
                "2026-07-03T01:00:00",
                "user",
                "The following is the Codex agent history whose request action you are assessing",
            ),
            _rollout_line("2026-07-03T01:00:30", "user", "TRANSCRIPT START"),
        ],
    )
    assert list(read_codex_events(tmp_path)) == []


def test_merge_overlapping_collapses_contained_windows():
    from diarysync.sources.sessions import merge_overlapping

    outer = WorkEntry.build(
        day=date(2026, 8, 27),
        start=datetime(2026, 8, 27, 10, 27),
        end=datetime(2026, 8, 27, 14, 9),
        tag="工作",
        summary="修改参数再次进行model evaluation",
        session_id="a",
    )
    inner = WorkEntry.build(
        day=date(2026, 8, 27),
        start=datetime(2026, 8, 27, 10, 51),
        end=datetime(2026, 8, 27, 10, 53),
        tag="工作",
        summary="修改参数再次进行model evaluation",
        session_id="b",
    )
    merged = merge_overlapping([inner, outer])
    assert len(merged) == 1
    assert merged[0].start == datetime(2026, 8, 27, 10, 27)
    assert merged[0].end == datetime(2026, 8, 27, 14, 9)


def test_merge_keeps_distinct_summaries_apart():
    from diarysync.sources.sessions import merge_overlapping

    first = WorkEntry.build(
        day=date(2026, 8, 27),
        start=datetime(2026, 8, 27, 10, 0),
        end=datetime(2026, 8, 27, 11, 0),
        tag="工作",
        summary="甲",
        session_id="a",
    )
    second = WorkEntry.build(
        day=date(2026, 8, 27),
        start=datetime(2026, 8, 27, 10, 30),
        end=datetime(2026, 8, 27, 11, 30),
        tag="工作",
        summary="乙",
        session_id="b",
    )
    assert len(merge_overlapping([first, second])) == 2


# ---------------------------------------------------------------------------
# timestamp parsing must not depend on datetime.fromisoformat's version quirks
# ---------------------------------------------------------------------------


def test_parse_timestamp_normalises_offsets_and_fractions():
    from diarysync.sources.sessions import _parse_timestamp as parse

    base = parse("2026-07-28T05:53:30Z")
    assert base is not None
    # Every spelling of the same instant must land on the same local time.
    assert parse("2026-07-28T05:53:30+00:00") == base
    assert parse("2026-07-28T05:53:30+0000") == base
    assert parse("2026-07-28T13:53:30+08:00") == base
    assert parse("2026-07-28 05:53:30Z") == base
    # Short and long fractional seconds, which fromisoformat rejected < 3.11.
    assert parse("2026-07-28T05:53:30.380Z") is not None
    assert parse("2026-07-28T05:53:30.380Z").microsecond == 380000
    assert parse("2026-07-28T05:53:30.38Z").microsecond == 380000
    assert parse("2026-07-28T05:53:30.380123456Z").microsecond == 380123
    # A stamp without a zone is taken as already-local.
    assert parse("2026-07-28T05:53:30") == datetime(2026, 7, 28, 5, 53, 30)


def test_parse_timestamp_handles_epochs_and_rejects_junk():
    from diarysync.sources.sessions import _parse_timestamp as parse

    millis = parse(1785218010380)
    assert millis is not None and millis.tzinfo is None
    assert parse(1785218010) == millis.replace(microsecond=0)
    assert parse(None) is None
    assert parse("nonsense") is None
    assert parse("2026-13-45T99:99:99Z") is None
