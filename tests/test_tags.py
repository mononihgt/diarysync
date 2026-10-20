from pathlib import Path

from diarysync.tags import vault_tags


def test_vault_tags_counts_inline_tags_and_ignores_code(tmp_path: Path):
    diary = tmp_path / "diary"
    diary.mkdir()
    (diary / "2026-10-20.md").write_text(
        "# 日程\n"
        "\n"
        "- [x] 09:00 - 10:00 #科研 分析数据\n"
        "- [ ] #实验室工作 写代码\n"
        "链接 https://example.com/#anchor\n"
        "行内 `#inline` 不算\n",
        encoding="utf-8",
    )
    (tmp_path / "note.md").write_text(
        "```md\n#代码块里的不算\n```\n"
        "~~~\n#另一个代码块\n~~~\n"
        "#组会 讨论\n",
        encoding="utf-8",
    )
    hidden = tmp_path / ".obsidian"
    hidden.mkdir()
    (hidden / "x.md").write_text("#配置 不应统计\n", encoding="utf-8")

    counts = vault_tags(tmp_path)

    assert counts["科研"] == 1
    assert counts["实验室工作"] == 1
    assert counts["组会"] == 1
    assert "anchor" not in counts
    assert "inline" not in counts
    assert "代码块里的不算" not in counts
    assert "另一个代码块" not in counts
    assert "配置" not in counts


def test_vault_tags_missing_root_is_empty(tmp_path: Path):
    assert not vault_tags(tmp_path / "missing")
