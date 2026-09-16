"""Decide whether a record is already represented in the diary.

Three independent guards, checked in order:

1. **Ledger** — this exact record was written by a previous run.
2. **Exact line** — an identical (whitespace-normalised) schedule line exists.
3. **Window overlap** — an existing ``#运动`` line covers most of the new
   record's time window, which is how a hand-written entry
   (``- [x] 14:30 - 16:00 #运动 有氧``) suppresses an auto-inserted one
   (``- [x] 14:42 - 15:36 #运动 跑步机``).

Overlap uses ``min(len(a), len(b))`` as the denominator, so a short record
nested inside a long hand-written block is a duplicate, while two adjacent
records on the same day (``16:11-16:16`` and ``16:18-16:53``) are not.

Overlap only compares records that would render with the same ``#tag``: an
exercise record is measured against ``#运动`` lines, a work record against
lines carrying its own tag.  A ``#运动`` line therefore never suppresses a
``#科研`` line that happened to run at the same time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .diary import Diary, ScheduleEntry
from .ledger import Ledger
from .models import CATEGORY_EXERCISE, Record

ACTION_INSERT = "insert"
ACTION_SKIP = "skip"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class Decision:
    record: Record
    action: str
    reason: str

    @property
    def insert(self) -> bool:
        return self.action == ACTION_INSERT


@dataclass
class DedupPolicy:
    #: Lines whose start times differ by less than this are not automatically
    #: merged; only real window overlap counts.  Kept for reporting/tuning.
    tolerance_min: int = 15
    #: Fraction of the shorter window that must be shared to call it a duplicate.
    overlap_ratio: float = 0.6
    #: Set False to ignore the persistent ledger (content matching only).
    use_ledger: bool = True
    #: Set True to disable every guard and insert unconditionally.
    force: bool = False


def normalise_line(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip())


def window_overlap(
    a: tuple[int, int], b: tuple[int, int]
) -> float:
    """Shared fraction of the shorter of two ``(start, end)`` minute windows."""
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    overlap = max(0, end - start)
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        # Zero-length windows (identical start and end) still count as a match
        # when they coincide exactly.
        return 1.0 if overlap >= 0 and a[0] == b[0] else 0.0
    return overlap / shorter


def decide(
    record: Record,
    diary: Diary,
    ledger: Ledger,
    policy: DedupPolicy,
) -> Decision:
    if policy.force:
        return Decision(record, ACTION_INSERT, "forced (--force)")

    keys = record.identity_keys()
    if policy.use_ledger:
        hit = ledger.knows(keys)
        if hit is not None:
            return Decision(record, ACTION_SKIP, f"already synced (ledger {hit})")

    candidate = normalise_line(record.schedule_line())
    entries = diary.schedule_entries() if diary is not None else []
    for entry in entries:
        if normalise_line(entry.text) == candidate:
            return Decision(record, ACTION_SKIP, "identical schedule line already present")

    reason = _tag_overlap_reason(record, entries, policy)
    if reason is None:
        reason = _twin_window_reason(record, entries)
    if reason is not None:
        return Decision(record, ACTION_SKIP, reason)

    return Decision(record, ACTION_INSERT, "new record")


def _record_window(record: Record) -> tuple[int, int]:
    start = record.start.hour * 60 + record.start.minute
    end = record.end.hour * 60 + record.end.minute
    if end < start:
        end += 24 * 60
    return start, end


def _same_kind(body: str, category: str) -> bool:
    """True when a diary line describes a record of the same kind.

    Exercise and work never suppress one another: a ``#科研`` session and a run
    can overlap in wall-clock time and are still two different records.
    """
    is_exercise_line = "#运动" in body
    return is_exercise_line if category == CATEGORY_EXERCISE else not is_exercise_line


def _tag_overlap_reason(
    record: Record, entries: list[ScheduleEntry], policy: DedupPolicy
) -> str | None:
    """Report an existing same-kind, same-tag line whose window covers ``record``."""
    marker = "#运动" if record.category == CATEGORY_EXERCISE else f"#{record.tag}"
    new_window = _record_window(record)
    for entry in entries:
        if not entry.has_window or marker not in entry.body:
            continue
        existing = entry.window()
        if existing is None:  # pragma: no cover - has_window guarantees this
            continue
        ratio = window_overlap(existing, new_window)
        if ratio >= policy.overlap_ratio:
            return (
                f"time window already covered by existing line "
                f"({ratio:.0%} overlap): {entry.text.strip()}"
            )
    return None


#: Two same-kind windows this similar are the same record even if the tags
#: were inferred differently by a human and by :func:`infer_tag`.
TWIN_OVERLAP = 0.9
TWIN_LENGTH_RATIO = 1.6


def _twin_window_reason(record: Record, entries: list[ScheduleEntry]) -> str | None:
    """Catch a near-identical window written under a different tag.

    The duration test is what keeps an all-day ``#实验室工作 08:35 - 23:59``
    block from swallowing every short session inside it.
    """
    new_window = _record_window(record)
    new_length = new_window[1] - new_window[0]
    for entry in entries:
        if not entry.has_window or not _same_kind(entry.body, record.category):
            continue
        existing = entry.window()
        if existing is None:  # pragma: no cover
            continue
        length = existing[1] - existing[0]
        shorter, longer = sorted((length, new_length))
        if shorter <= 0 or longer / shorter > TWIN_LENGTH_RATIO:
            continue
        if window_overlap(existing, new_window) >= TWIN_OVERLAP:
            return (
                f"near-identical time window already recorded "
                f"({existing[0] // 60:02d}:{existing[0] % 60:02d}-"
                f"{existing[1] // 60:02d}:{existing[1] % 60:02d}): {entry.text.strip()}"
            )
    return None


def plan(
    records: list[Record],
    diaries: dict,
    ledger: Ledger,
    policy: DedupPolicy,
) -> list[Decision]:
    """Run :func:`decide` for every record, newest-last, with self-dedup.

    Two records from the *same* batch can also collide (for example the online
    API and a CSV export both supplied the same activity).  Decisions are
    applied to an in-memory copy of the target day so later records see earlier
    ones.
    """
    decisions: list[Decision] = []
    for record in sorted(records, key=lambda r: (r.day, r.start)):
        diary = diaries.get(record.day)
        decision = decide(record, diary, ledger, policy)
        decisions.append(decision)
        if decision.insert and diary is not None:
            diary.add_schedule_lines([record.schedule_line()])
    return decisions
