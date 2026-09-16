"""Read and minimally-edit Obsidian diary markdown files.

The diary files are hand-written prose, so this module is deliberately
conservative: it only ever touches the ``# 日程`` and ``# 打卡`` sections,
leaves frontmatter and every other section byte-identical, and never reorders
existing lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECTION_SCHEDULE = "日程"
SECTION_CHECKIN = "打卡"

# A section header is a level-1 heading: "# 日程".  Level-2 headings such as
# "## 组会记录" are ordinary content and must not split sections.
_HEADER_RE = re.compile(r"^#\s+(?P<title>\S.*?)\s*$")
_ITEM_RE = re.compile(r"^(?P<indent>\s*)[-*]\s*\[(?P<mark>[ xX])\]\s*(?P<body>.*?)\s*$")
_TIME_RE = re.compile(
    r"(?<!\d)(?P<h1>[01]?\d|2[0-3]):(?P<m1>[0-5]\d)\s*[-–—~至]\s*"
    r"(?P<h2>[01]?\d|2[0-3]):(?P<m2>[0-5]\d)(?!\d)"
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)


@dataclass
class ScheduleEntry:
    """One ``- [x] HH:MM - HH:MM #tag text`` line already in the diary."""

    line_no: int
    text: str
    checked: bool
    start_min: int | None
    end_min: int | None
    body: str

    @property
    def has_window(self) -> bool:
        return self.start_min is not None and self.end_min is not None

    def window(self) -> tuple[int, int] | None:
        if self.start_min is None or self.end_min is None:
            return None
        start, end = self.start_min, self.end_min
        # Windows that appear to cross midnight are normalised so overlap
        # arithmetic stays monotonic.
        if end < start:
            end += 24 * 60
        return start, end


def parse_item(line: str) -> tuple[bool, str, int | None, int | None] | None:
    """Parse a task-list line, returning ``(checked, body, start, end)``."""
    match = _ITEM_RE.match(line)
    if not match:
        return None
    body = match.group("body")
    checked = match.group("mark").lower() == "x"
    start_min = end_min = None
    time_match = _TIME_RE.search(body)
    if time_match:
        start_min = int(time_match.group("h1")) * 60 + int(time_match.group("m1"))
        end_min = int(time_match.group("h2")) * 60 + int(time_match.group("m2"))
    return checked, body, start_min, end_min


class Diary:
    """A single ``diary/YYYY-MM-DD.md`` file held in memory."""

    def __init__(self, path: Path, text: str, *, existed: bool) -> None:
        self.path = path
        self._lines: list[str] = text.splitlines()
        self.existed = existed
        self._dirty = False

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Diary:
        if path.exists():
            return cls(path, path.read_text(encoding="utf-8"), existed=True)
        return cls(path, "", existed=False)

    @property
    def lines(self) -> list[str]:
        return self._lines

    @property
    def dirty(self) -> bool:
        return self._dirty

    def render(self) -> str:
        text = "\n".join(self._lines)
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    # -- structure ---------------------------------------------------------

    def frontmatter(self) -> str:
        match = _FRONTMATTER_RE.match(self.render())
        return match.group(0) if match else ""

    def sections(self) -> list[tuple[str, int, int]]:
        """``(title, header_index, end_index_exclusive)`` for each ``# `` heading."""
        headers: list[tuple[str, int]] = []
        for index, line in enumerate(self._lines):
            match = _HEADER_RE.match(line)
            if match:
                headers.append((match.group("title"), index))
        result: list[tuple[str, int, int]] = []
        for position, (title, index) in enumerate(headers):
            end = headers[position + 1][1] if position + 1 < len(headers) else len(self._lines)
            result.append((title, index, end))
        return result

    def find_section(self, title: str) -> tuple[int, int] | None:
        target = _normalise_heading(title)
        for name, start, end in self.sections():
            if _normalise_heading(name) == target:
                return start, end
        return None

    # -- queries -----------------------------------------------------------

    def schedule_entries(self) -> list[ScheduleEntry]:
        section = self.find_section(SECTION_SCHEDULE)
        if section is None:
            return []
        start, end = section
        entries: list[ScheduleEntry] = []
        for index in range(start + 1, end):
            parsed = parse_item(self._lines[index])
            if parsed is None:
                continue
            checked, body, start_min, end_min = parsed
            entries.append(
                ScheduleEntry(
                    line_no=index,
                    text=self._lines[index],
                    checked=checked,
                    start_min=start_min,
                    end_min=end_min,
                    body=body,
                )
            )
        return entries

    def checkin_entries(self) -> list[ScheduleEntry]:
        section = self.find_section(SECTION_CHECKIN)
        if section is None:
            return []
        start, end = section
        entries: list[ScheduleEntry] = []
        for index in range(start + 1, end):
            parsed = parse_item(self._lines[index])
            if parsed is None:
                continue
            entries.append(
                ScheduleEntry(
                    line_no=index,
                    text=self._lines[index],
                    checked=parsed[0],
                    start_min=parsed[2],
                    end_min=parsed[3],
                    body=parsed[1],
                )
            )
        return entries

    def has_checkin(self, label: str) -> bool:
        return any(label in entry.body for entry in self.checkin_entries())

    # -- mutation ----------------------------------------------------------

    def _ensure_section(self, title: str) -> tuple[int, int]:
        """Return ``(header_index, end_index)``, creating the section if absent."""
        found = self.find_section(title)
        if found is not None:
            return found

        if self._lines:
            if self._lines[-1].strip():
                self._lines.append("")
            self._lines.append(f"# {title}")
            self._lines.append("")
        else:
            # Brand new file.  The vault's canonical layout puts "# 打卡"
            # before "# 日程"; callers create 打卡 first.
            self._lines = [f"# {title}", ""]
        self._dirty = True
        created = self.find_section(title)
        assert created is not None
        return created

    def _item_indices(self, start: int, end: int) -> list[int]:
        return [index for index in range(start + 1, end) if parse_item(self._lines[index]) is not None]

    def add_checkin(self, label: str = "运动", *, complete_unchecked: bool = True) -> bool:
        """Ensure ``- [x] <label>`` exists under ``# 打卡``.

        Returns ``True`` when the file was changed.
        """
        section = self.find_section(SECTION_CHECKIN)
        if section is not None:
            for entry in self.checkin_entries():
                if label not in entry.body:
                    continue
                if not entry.checked and complete_unchecked:
                    self._lines[entry.line_no] = f"- [x] {entry.body}"
                    self._dirty = True
                    return True
                return False

        start, end = self._ensure_section(SECTION_CHECKIN)
        self._insert_item(SECTION_CHECKIN, f"- [x] {label}", new_start_min=None)
        return True

    def add_schedule_lines(self, lines: list[str]) -> list[str]:
        """Insert rendered schedule lines in time order.  Returns those inserted."""
        if not lines:
            return []
        self._ensure_section(SECTION_SCHEDULE)
        for line in lines:
            parsed = parse_item(line)
            new_start = parsed[2] if parsed else None
            self._insert_item(SECTION_SCHEDULE, line, new_start_min=new_start)
        return list(lines)

    def _insert_item(self, title: str, line: str, *, new_start_min: int | None) -> None:
        start, end = self.find_section(title)  # type: ignore[misc]
        # Guarantee exactly one blank line between the heading and its items.
        if start + 1 >= len(self._lines) or self._lines[start + 1].strip():
            self._lines.insert(start + 1, "")
            self._dirty = True
        start, end = self.find_section(title)  # type: ignore[misc]

        items = self._item_indices(start, end)
        if items and new_start_min is not None:
            for index in items:
                parsed = parse_item(self._lines[index])
                assert parsed is not None
                if parsed[2] is not None and parsed[2] > new_start_min:
                    self._lines.insert(index, line)
                    self._dirty = True
                    return

        if items:
            self._lines.insert(items[-1] + 1, line)
        else:
            self._lines.insert(start + 2, line)
        self._dirty = True

    def save(self, *, dry_run: bool = False) -> None:
        if dry_run or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(), encoding="utf-8")


def _normalise_heading(text: str) -> str:
    return text.replace("#", "").replace(" ", "").replace("\u3000", "").strip()
