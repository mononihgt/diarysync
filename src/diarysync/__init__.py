"""diarysync — write Garmin activities and agent work logs into an Obsidian diary.

The package is built around three ideas:

1. **Sources** produce :class:`~diarysync.models.Record` objects.  A Garmin
   activity and a Codex work session are both records.
2. **Dedup** decides whether a record is already represented in the diary.
   It combines a content fingerprint, a time-window overlap test against
   hand-written schedule lines, and a persistent ledger.
3. **Writer** inserts surviving records into the right ``# 日程`` / ``# 打卡``
   section of ``diary/YYYY-MM-DD.md`` without disturbing anything else.

Typical use::

    import diarysync

    sync = diarysync.DiarySync(vault="/path/to/vault")
    report = sync.sync(since="2026-09-01")
    print(report.inserted, report.skipped)

Or drive the pieces directly::

    settings = diarysync.load_settings(vault="/path/to/vault")
    records = diarysync.collect_activities(settings, since="2026-09-01", until="2026-09-16")
    diarysync.run(records, settings, dry_run=True)
"""

from __future__ import annotations

__version__ = "0.2.0"

from .api import (
    DEFAULT_WINDOW_DAYS,
    DEFAULT_WORK_WINDOW_DAYS,
    DiarySync,
    build_summarizer,
    collect_activities,
    collect_work,
    parse_day,
    resolve_window,
)
from .config import Settings, find_vault, load_settings
from .dedup import Decision, DedupPolicy, decide
from .diary import Diary
from .ledger import Ledger
from .models import Activity, Record, WorkEntry
from .sources.garmin import GarminAuthError
from .sources.garmin_csv import CsvFormatError, load_csv
from .sync import SyncReport, run

__all__ = [
    "Activity",
    "CsvFormatError",
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_WORK_WINDOW_DAYS",
    "Decision",
    "DedupPolicy",
    "Diary",
    "DiarySync",
    "GarminAuthError",
    "Ledger",
    "Record",
    "Settings",
    "SyncReport",
    "WorkEntry",
    "__version__",
    "build_summarizer",
    "collect_activities",
    "collect_work",
    "decide",
    "find_vault",
    "load_csv",
    "load_settings",
    "parse_day",
    "resolve_window",
    "run",
]
