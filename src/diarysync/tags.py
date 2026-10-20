from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

#: Obsidian 的 inline tag：字母、数字、下划线、连字符、斜杠，中文由 \w 覆盖。
#: 负向后顾排除 URL 锚点（``/#anchor``）和 ``C#`` 这类正文。
_TAG_RE = re.compile(r"(?<![\w/])#([\w/-]+)", re.UNICODE)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def vault_tags(vault: str | Path) -> Counter[str]:
    """统计 vault 里每个既有 inline tag 出现的次数。"""
    root = Path(vault)
    counts: Counter[str] = Counter()
    if not root.is_dir():
        return counts
    for path in sorted(root.rglob("*.md")):
        # Obsidian 不索引隐藏目录与隐藏文件，扫描 tag 时保持同样的范围。
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        counts.update(_file_tags(path))
    return counts


def _file_tags(path: Path) -> list[str]:
    """读取一个 Markdown 文件里的 tag，跳过 fenced code 与 inline code。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    tags: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            continue
        if match:
            fence = match.group(1)
            continue
        tags.extend(_TAG_RE.findall(_INLINE_CODE_RE.sub("", line)))
    return tags
