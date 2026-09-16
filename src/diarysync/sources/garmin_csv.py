"""Offline path: parse an activity CSV exported from Garmin Connect.

The CN site's "导出为csv文献" button produces a UTF-8 (BOM) CSV whose headers
are Chinese; an English-locale export uses the English names.  Both are
accepted, and unknown extra columns are ignored.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from ..activity_types import canonical_type_key, type_label
from ..models import Activity

# Header aliases: logical field -> candidate column names (CN first, then EN).
_HEADERS: dict[str, tuple[str, ...]] = {
    "type": ("活动类型", "Activity Type", "运动类型"),
    "start": ("日期", "Date", "开始时间"),
    "title": ("标题", "Title", "活动名称"),
    "distance": ("距离", "Distance"),
    "calories": ("热量消耗", "Calories", "卡路里"),
    "duration": ("时间", "Time", "时长"),
    "elapsed": ("全程耗时", "Elapsed Time"),
    "moving": ("移动时间", "Moving Time"),
}


class CsvFormatError(ValueError):
    """The file does not look like a Garmin activity export."""


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    available = {name.strip(): name for name in fieldnames if name}
    lowered = {name.lower(): name for name in available}
    for logical, candidates in _HEADERS.items():
        for candidate in candidates:
            if candidate in available:
                mapping[logical] = available[candidate]
                break
            if candidate.lower() in lowered:
                mapping[logical] = lowered[candidate.lower()]
                break
    return mapping


def parse_duration(text: str) -> float | None:
    """``HH:MM:SS`` / ``HH:MM:SS.s`` / ``MM:SS`` -> seconds.

    Garmin writes ``--:--:--`` when a value is missing.
    """
    raw = (text or "").strip().strip('"')
    if not raw or set(raw) <= {"-", ":", " "}:
        return None
    parts = raw.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 1:
        return numbers[0]
    return None


def parse_distance_km(text: str) -> float | None:
    raw = (text or "").strip().strip('"').replace(",", "")
    if not raw or set(raw) <= {"-", ".", " "}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_number(text: str) -> float | None:
    raw = (text or "").strip().strip('"').replace(",", "")
    if not raw or set(raw) <= {"-", ".", " "}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_start(text: str) -> datetime | None:
    raw = (text or "").strip().strip('"')
    for fmt, length in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y/%m/%d %H:%M:%S", 19),
        ("%Y-%m-%dT%H:%M:%S", 19),
    ):
        try:
            return datetime.strptime(raw[:length], fmt)
        except ValueError:
            continue
    return None


def load_csv(path: str | Path) -> list[Activity]:
    """Parse an exported activities CSV into diary records."""
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise CsvFormatError(f"{path}: empty CSV")
        columns = _resolve_columns(list(reader.fieldnames))
        missing = [field for field in ("type", "start", "duration") if field not in columns]
        if missing:
            raise CsvFormatError(
                f"{path}: not a Garmin activity export — missing column(s) {missing}. "
                f"Found headers: {reader.fieldnames}"
            )

        activities: list[Activity] = []
        for row in reader:
            start = _parse_start(row.get(columns["start"], ""))
            duration = parse_duration(row.get(columns["duration"], ""))
            if start is None or duration is None:
                continue
            start_floor = start.replace(second=0, microsecond=0)
            end_floor = (start + timedelta(seconds=duration)).replace(second=0, microsecond=0)
            if end_floor <= start_floor:
                end_floor = start_floor + timedelta(minutes=1)
            raw_type = row.get(columns["type"], "")
            distance_km = None
            if "distance" in columns:
                distance_km = parse_distance_km(row.get(columns["distance"], ""))
            calories = None
            if "calories" in columns:
                calories = _parse_number(row.get(columns["calories"], ""))
            activities.append(
                Activity.build(
                    day=start_floor.date(),
                    start=start_floor,
                    end=end_floor,
                    type_key=canonical_type_key(raw_type),
                    type_label=type_label(raw_type),
                    title=(row.get(columns["title"], "") or "").strip().strip('"'),
                    source_id=None,
                    distance_m=None if distance_km is None else distance_km * 1000.0,
                    calories=calories,
                    source="garmin-csv",
                )
            )
    return activities


def date_span(activities: list[Activity]) -> tuple[date, date] | None:
    if not activities:
        return None
    days = [activity.day for activity in activities]
    return min(days), max(days)
