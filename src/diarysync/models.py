"""Value objects shared by every source, dedup rule and writer."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

# A record is "exercise" (gets the 运动 check-in) or "work" (does not).
CATEGORY_EXERCISE = "exercise"
CATEGORY_WORK = "work"


@dataclass(frozen=True)
class Record:
    """One thing that happened, on one day, over one time window.

    ``Record`` is the only type the dedup and writer layers understand.  Every
    source converts its native payload into records, which keeps the
    source-specific quirks (Garmin CSV columns, JSONL transcripts) at the edge.
    """

    day: date
    start: datetime
    end: datetime
    tag: str
    summary: str
    category: str = CATEGORY_WORK
    # Strong, source-assigned identifier (Garmin ``activityId``).  Optional.
    source_id: str | None = None
    # Free-form provenance, e.g. ``{"source": "garmin", "distance_km": 6.55}``.
    extra: dict = field(default_factory=dict)

    # -- rendering ---------------------------------------------------------

    @property
    def body(self) -> str:
        """Everything after the checkbox marker, e.g. ``#运动 跑步 杭州市 - 基础训练``."""
        parts = [f"#{self.tag}"]
        if self.summary:
            parts.append(self.summary)
        return " ".join(parts)

    def schedule_line(self) -> str:
        return f"- [x] {self.start:%H:%M} - {self.end:%H:%M} {self.body}"

    # -- identity ----------------------------------------------------------

    @property
    def content_key(self) -> str:
        """Source-independent identity.

        Built from the rounded time window plus the tag, so a record fetched
        from the Garmin API and the same record parsed from an exported CSV
        collapse onto one key.  Seconds are deliberately dropped: the CSV only
        carries whole seconds and its duration column is coarser than the API's
        float seconds.
        """
        raw = f"{self.day.isoformat()}|{self.start:%H:%M}|{self.end:%H:%M}|{self.tag}|{self.summary}"
        return "c:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def identity_keys(self) -> list[str]:
        """All keys that mean "this exact record", strongest first."""
        keys: list[str] = []
        if self.source_id:
            keys.append(f"id:{self.source_id}")
        keys.append(self.content_key)
        return keys


@dataclass(frozen=True)
class Activity(Record):
    """A Garmin activity.  Kept as a name for readability at the call site."""

    type_key: str = ""
    type_label: str = ""
    title: str = ""
    distance_m: float | None = None
    calories: float | None = None

    @classmethod
    def build(
        cls,
        *,
        day: date,
        start: datetime,
        end: datetime,
        type_key: str,
        type_label: str,
        title: str = "",
        source_id: str | None = None,
        distance_m: float | None = None,
        calories: float | None = None,
        source: str = "garmin",
    ) -> Activity:
        summary = _join_label(type_label, title)
        return cls(
            day=day,
            start=start,
            end=end,
            tag="运动",
            summary=summary,
            category=CATEGORY_EXERCISE,
            source_id=source_id,
            extra={"source": source, "type_key": type_key},
            type_key=type_key,
            type_label=type_label,
            title=title,
            distance_m=distance_m,
            calories=calories,
        )


@dataclass(frozen=True)
class WorkEntry(Record):
    """A block of work reconstructed from an agent session transcript."""

    session_id: str = ""
    prompt: str = ""

    @classmethod
    def build(
        cls,
        *,
        day: date,
        start: datetime,
        end: datetime,
        tag: str,
        summary: str,
        session_id: str,
        prompt: str = "",
        source: str = "codex",
    ) -> WorkEntry:
        return cls(
            day=day,
            start=start,
            end=end,
            tag=tag,
            summary=summary,
            category=CATEGORY_WORK,
            source_id=f"{source}:{session_id}" if session_id else None,
            extra={"source": source, "session_id": session_id},
            session_id=session_id,
            prompt=prompt,
        )


def _join_label(type_label: str, title: str) -> str:
    """``跑步`` + ``杭州市 - 基础训练`` -> ``跑步 杭州市 - 基础训练``.

    A title that is itself a bare activity-type name ("有氧运动" on an indoor
    cardio activity, "跑步机" on a treadmill run) carries no extra information
    and would otherwise be duplicated in the diary line.
    """
    from .activity_types import is_known_type

    label = (type_label or "").strip()
    title = (title or "").strip()
    if not title or title == label or is_known_type(title):
        return label
    return f"{label} {title}"


@runtime_checkable
class Source(Protocol):
    """Anything that can produce records for a date window."""

    name: str

    def fetch(self, start: date, end: date) -> list[Record]:  # pragma: no cover
        ...
