"""Diary parsing and writing must be surgical."""

from __future__ import annotations

from pathlib import Path

from diarysync.diary import Diary, parse_item


def test_parse_item_reads_window_and_body():
    parsed = parse_item("- [x] 16:14 - 16:47 #运动 跑步 衢州市 - 基础训练")
    assert parsed is not None
    checked, body, start, end = parsed
    assert checked is True
    assert start == 16 * 60 + 14
    assert end == 16 * 60 + 47
    # ``body`` is everything after the checkbox, window included.
    assert body == "16:14 - 16:47 #运动 跑步 衢州市 - 基础训练"


def test_parse_item_without_window():
    parsed = parse_item("- [x] astrbot配置")
    assert parsed == (True, "astrbot配置", None, None)


def test_parse_item_ignores_level_two_headings():
    assert parse_item("## 组会记录") is None


def test_sections_ignore_level_two_headings(tmp_path: Path):
    path = tmp_path / "2026-09-14.md"
    path.write_text(
        "---\nweather: 晴\n---\n\n# 日志\n\n## 组会记录\n\n正文\n",
        encoding="utf-8",
    )
    diary = Diary.load(path)
    titles = [title for title, _, _ in diary.sections()]
    assert titles == ["日志"]


def test_frontmatter_is_preserved(tmp_path: Path):
    path = tmp_path / "2026-09-14.md"
    path.write_text("---\nweather: 晴\n---\n\n# 日志\n\n上午：组会\n", encoding="utf-8")
    diary = Diary.load(path)
    diary.add_schedule_lines(["- [x] 19:46 - 20:19 #运动 跑步"])
    diary.add_checkin("运动")
    text = diary.render()
    assert text.startswith("---\nweather: 晴\n---\n")
    assert "上午：组会" in text
    assert "- [x] 19:46 - 20:19 #运动 跑步" in text
    assert "- [x] 运动" in text


def test_schedule_lines_are_inserted_in_time_order(tmp_path: Path, vault: Path):
    path = vault / "diary" / "2026-08-14.md"
    path.write_text(
        "---\nweather: 晴\n---\n\n# 日程\n\n"
        "- [x] 09:00 - 11:20 #读文献 学习\n"
        "- [x] 15:30 - 17:25 #运动 有氧+力量\n\n"
        "# 打卡\n\n- [x] 运动\n",
        encoding="utf-8",
    )
    diary = Diary.load(path)
    diary.add_schedule_lines(
        [
            "- [x] 16:11 - 16:16 #运动 跑步机",
            "- [x] 16:18 - 16:53 #运动 跑步机 乳酸阈值",
        ]
    )
    lines = [line for line in diary.render().splitlines() if line.startswith("- [x]")]
    assert lines == [
        "- [x] 09:00 - 11:20 #读文献 学习",
        "- [x] 15:30 - 17:25 #运动 有氧+力量",
        "- [x] 16:11 - 16:16 #运动 跑步机",
        "- [x] 16:18 - 16:53 #运动 跑步机 乳酸阈值",
        "- [x] 运动",
    ]


def test_untimed_lines_keep_their_position(vault: Path):
    path = vault / "diary" / "2026-08-13.md"
    path.write_text(
        "# 日程\n\n- [x] astrbot配置\n- [x] huawei例会\n- [x] 19:09 - 20:06 #运动 骑行\n",
        encoding="utf-8",
    )
    diary = Diary.load(path)
    diary.add_schedule_lines(["- [x] 08:00 - 08:30 #运动 跑步"])
    lines = [line for line in diary.render().splitlines() if line.startswith("- ")]
    assert lines == [
        "- [x] astrbot配置",
        "- [x] huawei例会",
        "- [x] 08:00 - 08:30 #运动 跑步",
        "- [x] 19:09 - 20:06 #运动 骑行",
    ]


def test_missing_sections_are_appended(vault: Path):
    path = vault / "diary" / "2026-09-14.md"
    path.write_text("---\nweather: 晴\n---\n\n# 日志\n\n上午：组会\n", encoding="utf-8")
    diary = Diary.load(path)
    diary.add_checkin("运动")
    diary.add_schedule_lines(["- [x] 19:46 - 20:19 #运动 跑步 杭州市 - 基础训练"])
    diary.save()
    text = path.read_text(encoding="utf-8")
    assert "# 打卡" in text
    assert "# 日程" in text
    assert text.index("# 日志") < text.index("# 打卡") < text.index("# 日程")
    assert text.count("- [x] 运动") == 1


def test_new_file_uses_canonical_layout(vault: Path):
    path = vault / "diary" / "2026-08-12.md"
    diary = Diary.load(path)
    assert diary.existed is False
    diary.add_checkin("运动")
    diary.add_schedule_lines(["- [x] 15:25 - 15:55 #运动 跑步机 基础训练"])
    diary.save()
    assert path.read_text(encoding="utf-8") == (
        "# 打卡\n\n- [x] 运动\n\n# 日程\n\n- [x] 15:25 - 15:55 #运动 跑步机 基础训练\n"
    )


def test_unchecked_checkin_is_completed_not_duplicated(vault: Path):
    path = vault / "diary" / "2026-08-12.md"
    path.write_text("# 打卡\n\n- [ ] 运动\n", encoding="utf-8")
    diary = Diary.load(path)
    assert diary.add_checkin("运动") is True
    assert diary.add_checkin("运动") is False
    text = diary.render()
    assert text.count("运动") == 1
    assert "- [x] 运动" in text


def test_saving_an_untouched_diary_is_a_noop(vault: Path):
    path = vault / "diary" / "2026-08-12.md"
    original = "# 打卡\n\n- [x] 运动\n"
    path.write_text(original, encoding="utf-8")
    diary = Diary.load(path)
    diary.add_checkin("运动")
    diary.save()
    assert path.read_text(encoding="utf-8") == original
