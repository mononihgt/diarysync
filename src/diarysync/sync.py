"""Orchestration: records in, diary edits out.

The report returned by :func:`run` is the single place the CLI reads from, so
``--dry-run`` and a real run share one code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Settings
from .dedup import ACTION_INSERT, Decision, DedupPolicy, plan
from .diary import Diary
from .ledger import Ledger
from .models import CATEGORY_EXERCISE, Record


@dataclass
class SyncReport:
    inserted: list[Record] = field(default_factory=list)
    skipped: list[tuple[Record, str]] = field(default_factory=list)
    files_written: list[Path] = field(default_factory=list)
    files_created: list[Path] = field(default_factory=list)
    checkins_added: list[date] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total(self) -> int:
        return len(self.inserted) + len(self.skipped)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total": self.total,
            "inserted": [
                {
                    "day": record.day.isoformat(),
                    "line": record.schedule_line(),
                    "category": record.category,
                    "source": record.extra.get("source"),
                }
                for record in self.inserted
            ],
            "skipped": [
                {
                    "day": record.day.isoformat(),
                    "line": record.schedule_line(),
                    "reason": reason,
                }
                for record, reason in self.skipped
            ],
            "files_written": [str(path) for path in self.files_written],
            "files_created": [str(path) for path in self.files_created],
            "checkins_added": [day.isoformat() for day in self.checkins_added],
        }


def diary_path_for(settings: Settings, day: date) -> Path:
    return settings.diary_dir / f"{day:%Y-%m-%d}.md"


def run(
    records: list[Record],
    settings: Settings,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> SyncReport:
    """Apply ``records`` to the diary, skipping anything already recorded."""
    report = SyncReport(dry_run=dry_run)
    if not records:
        return report

    ledger = Ledger(settings.ledger_path).load()
    policy = DedupPolicy(
        overlap_ratio=settings.overlap_ratio,
        use_ledger=settings.use_ledger,
        force=force,
    )

    diaries: dict[date, Diary] = {}
    existed: dict[date, bool] = {}
    for record in records:
        if record.day not in diaries:
            path = diary_path_for(settings, record.day)
            diary = Diary.load(path)
            diaries[record.day] = diary
            existed[record.day] = diary.existed

    # Any exercise record for a day proves the exercise check-in, even when the
    # schedule line itself is later suppressed as a duplicate.  Check-ins are
    # applied first so that a brand-new file lands in the vault's canonical
    # "# 打卡" then "# 日程" order.
    exercise_days = sorted(
        {record.day for record in records if record.category == CATEGORY_EXERCISE}
    )
    for day in exercise_days:
        diary = diaries[day]
        if diary.add_checkin(
            settings.checkin_label,
            complete_unchecked=settings.complete_unchecked_checkin,
        ):
            report.checkins_added.append(day)

    # plan() inserts the surviving schedule lines into the same Diary objects.
    decisions: list[Decision] = plan(records, diaries, ledger, policy)

    for decision in decisions:
        if decision.insert:
            report.inserted.append(decision.record)
        else:
            report.skipped.append((decision.record, decision.reason))

    for day in sorted(diaries):
        diary = diaries[day]
        if not diary.dirty:
            continue
        diary.save(dry_run=dry_run)
        report.files_written.append(diary.path)
        if not existed[day]:
            report.files_created.append(diary.path)

    if not dry_run:
        for decision in decisions:
            if not decision.insert:
                continue
            record = decision.record
            ledger.remember(
                record.identity_keys(),
                day=record.day.isoformat(),
                line=record.schedule_line(),
                source=str(record.extra.get("source", "")),
            )
        ledger.save(dry_run=dry_run)

    return report


def ensure_diary_dir(settings: Settings) -> None:
    settings.diary_dir.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ACTION_INSERT",
    "SyncReport",
    "diary_path_for",
    "ensure_diary_dir",
    "run",
]
