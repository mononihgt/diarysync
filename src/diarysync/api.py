"""Public Python API.

Everything the command line does is available as a plain function or through
the :class:`DiarySync` facade, so the same code can be driven from a script:

    import diarysync

    sync = diarysync.DiarySync(vault="/path/to/vault")
    report = sync.exercise(since="2026-09-01")
    for record in report.inserted:
        print(record.schedule_line())

    report = sync.work(since="2026-08-25", source="codex")

The lower-level pieces stay importable too, for callers that want to compose
their own pipeline:

    settings = diarysync.load_settings(vault="/path/to/vault")
    records  = diarysync.collect_activities(settings, since=..., until=...)
    report   = diarysync.run(records, settings, dry_run=True)
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Settings, find_vault, load_settings, prompt_mfa, prompt_password
from .models import Activity, Record, WorkEntry
from .sync import SyncReport, run

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_WORK_WINDOW_DAYS",
    "DiarySync",
    "build_summarizer",
    "collect_activities",
    "collect_work",
    "parse_day",
    "resolve_window",
]

#: Default look-back for ``diarysync exercise``.
DEFAULT_WINDOW_DAYS = 30
#: Default look-back for ``diarysync work``.
DEFAULT_WORK_WINDOW_DAYS = 14

Summarizer = Callable[[str], str]


# ---------------------------------------------------------------------------
# date helpers
# ---------------------------------------------------------------------------


def parse_day(value) -> date:
    """Coerce a ``date``, ``datetime`` or ``"YYYY-MM-DD"`` string to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {value!r} (expected YYYY-MM-DD)")


def resolve_window(
    *,
    since=None,
    until=None,
    days: int | None = None,
    default_days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[date, date]:
    """Turn ``since``/``until``/``days`` into an inclusive ``(start, end)`` window.

    ``days`` is counted back from ``until`` (today by default), so
    ``days=1`` means "today only".
    """
    end = parse_day(until) if until else date.today()
    if since is not None:
        start = parse_day(since)
    else:
        # ``days=0`` must be rejected, so do not use ``days or default``.
        span = default_days if days is None else days
        if span < 1:
            raise ValueError("days must be >= 1")
        start = end - timedelta(days=span - 1)
    if start > end:
        raise ValueError(f"since ({start}) is after until ({end})")
    return start, end


# ---------------------------------------------------------------------------
# collectors
# ---------------------------------------------------------------------------


def collect_activities(
    settings: Settings,
    *,
    since,
    until,
    csv_path: str | Path | None = None,
    mfa_prompt: Callable[[], str] | None = None,
    client=None,
) -> list[Activity]:
    """Return Garmin activities in ``[since, until]``.

    With ``csv_path`` the file is parsed offline; otherwise the Garmin Connect
    API is queried using ``settings``.  Both paths produce identical records.
    """
    start, end = parse_day(since), parse_day(until)

    if csv_path is not None:
        from .sources.garmin_csv import load_csv

        return [a for a in load_csv(Path(csv_path).expanduser()) if start <= a.day <= end]

    from .sources.garmin import fetch_activities

    return fetch_activities(
        settings.garmin_email or "",
        settings.garmin_password or "",
        start=start,
        end=end,
        is_cn=settings.garmin_is_cn,
        token_store=settings.token_store,
        mfa_prompt=mfa_prompt,
        client=client,
    )


def build_summarizer(command: str | None, *, timeout: int = 120) -> Summarizer | None:
    """Wrap an external command as a ``prompt -> one-line summary`` callable.

    The command receives the raw prompt on stdin and must print the summary on
    stdout.  A failing command degrades to ``""``, which makes
    :func:`collect_work` fall back to the built-in heuristic.
    """
    if not command:
        return None
    argv = shlex.split(command)

    def summarizer(prompt: str) -> str:
        try:
            completed = subprocess.run(
                argv, input=prompt, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if completed.returncode != 0:
            return ""
        for line in completed.stdout.splitlines():
            if line.strip():
                return line.strip()
        return ""

    return summarizer


def collect_work(
    settings: Settings,
    *,
    since,
    until,
    source: str = "all",
    summarizer: Summarizer | None = None,
    min_minutes: int = 0,
    summary_limit: int = 80,
    roots: dict[str, Path] | None = None,
) -> list[WorkEntry]:
    """Return work blocks reconstructed from local agent transcripts.

    ``source`` is ``"all"``, ``"codex"`` or ``"dsh"``.  Only threads a human
    drove are considered; spawned sub-agents and automatic reviews are skipped.
    """
    from .sources import sessions as session_sources

    start, end = parse_day(since), parse_day(until)
    available = session_sources.default_session_roots() if roots is None else roots
    selected = available if source == "all" else {k: v for k, v in available.items() if k == source}
    if not selected:
        return []

    events: list = []
    for origin, root in selected.items():
        if origin == "codex":
            events.extend(session_sources.read_codex_events(root))
        elif origin == "dsh":
            events.extend(session_sources.read_dsh_events(root))

    return list(
        session_sources.collect_work_entries(
            events,
            start=start,
            end=end,
            summarizer=summarizer,
            summary_limit=summary_limit,
            min_minutes=min_minutes,
        )
    )


# ---------------------------------------------------------------------------
# facade
# ---------------------------------------------------------------------------


@dataclass
class DiarySync:
    """Convenience facade over one Obsidian vault.

    Configuration is resolved once in :meth:`__post_init__`, so a single
    instance can run several syncs:

        sync = DiarySync(vault="/vault", email="me@example.com")
        sync.exercise(days=30)
        sync.work(since="2026-08-01")
    """

    vault: str | Path | None = None
    email: str | None = None
    password: str | None = None
    is_cn: bool | None = None
    token_store: str | Path | None = None
    #: Override the diary location; defaults to ``<vault>/diary``.
    diary_path: str | Path | None = None
    overlap_ratio: float | None = None
    dry_run: bool = False
    force: bool = False
    #: Interactive MFA callback; ``None`` disables the prompt.
    mfa_prompt: Callable[[], str] | None = None
    #: Ask for the Garmin password with getpass when none was supplied.
    prompt_for_password: bool = False
    #: External summarizer command for :meth:`work`.
    summarizer_cmd: str | None = None

    settings: Settings = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.settings = load_settings(
            vault=self.vault,
            diary_dir=self.diary_path,
            email=self.email,
            password=self.password,
            is_cn=self.is_cn,
            token_store=self.token_store,
            overlap_ratio=self.overlap_ratio,
            summarizer_cmd=self.summarizer_cmd,
        )

    # -- construction helpers ---------------------------------------------

    @classmethod
    def open(cls, vault: str | Path | None = None, **overrides) -> DiarySync:
        """Build a facade, discovering the vault from the working directory."""
        return cls(vault=vault or find_vault(), **overrides)

    @property
    def vault_path(self) -> Path:
        return self.settings.vault

    @property
    def diary_dir(self) -> Path:
        return self.settings.diary_dir

    def configure(self, **overrides) -> DiarySync:
        """Return a copy with overrides applied."""
        return replace(self, **overrides)

    # -- authentication ----------------------------------------------------

    def login(self) -> str:
        """Verify the Garmin credentials and cache tokens."""
        from .sources.garmin import probe_login

        self._require_credentials()
        return probe_login(
            self.settings.garmin_email or "",
            self.settings.garmin_password or "",
            is_cn=self.settings.garmin_is_cn,
            token_store=self.settings.token_store,
        )

    def _require_credentials(self) -> None:
        """Resolve the Garmin password, prompting only when actually needed.

        Constructing a facade is deliberately free of side effects: a script
        that only writes offline CSV records never sees a password prompt.
        """
        if self.settings.garmin_password:
            return
        if self.prompt_for_password:
            self.settings = replace(self.settings, garmin_password=prompt_password())
            return
        raise ValueError(
            "No Garmin password available. Pass password=..., set "
            "DIARYSYNC_GARMIN_PASSWORD, or enable prompt_for_password."
        )

    def _mfa(self) -> Callable[[], str] | None:
        return self.mfa_prompt if self.mfa_prompt is not None else prompt_mfa

    # -- collection --------------------------------------------------------

    def activities(
        self,
        *,
        since=None,
        until=None,
        days: int | None = None,
        csv: str | Path | None = None,
    ) -> list[Activity]:
        start, end = resolve_window(
            since=since, until=until, days=days, default_days=DEFAULT_WINDOW_DAYS
        )
        if csv is None:
            self._require_credentials()
        return collect_activities(
            self.settings, since=start, until=end, csv_path=csv, mfa_prompt=self._mfa()
        )

    def work_entries(
        self,
        *,
        since=None,
        until=None,
        days: int | None = None,
        source: str = "all",
        min_minutes: int = 0,
        summary_limit: int = 80,
    ) -> list[WorkEntry]:
        start, end = resolve_window(
            since=since, until=until, days=days, default_days=DEFAULT_WORK_WINDOW_DAYS
        )
        return collect_work(
            self.settings,
            since=start,
            until=end,
            source=source,
            summarizer=build_summarizer(self.settings.summarizer_cmd),
            min_minutes=min_minutes,
            summary_limit=summary_limit,
        )

    # -- writing -----------------------------------------------------------

    def apply(self, records: Sequence[Record], *, dry_run: bool | None = None) -> SyncReport:
        """Write records into the diary, skipping anything already recorded."""
        return run(
            list(records),
            self.settings,
            dry_run=self.dry_run if dry_run is None else dry_run,
            force=self.force,
        )

    def exercise(
        self, *, csv: str | Path | None = None, dry_run: bool | None = None, **window
    ) -> SyncReport:
        """Collect Garmin activities and write them into the diary.

        ``window`` accepts ``since`` / ``until`` / ``days``.
        """
        return self.apply(self.activities(csv=csv, **window), dry_run=dry_run)

    def work(
        self,
        *,
        dry_run: bool | None = None,
        source: str = "all",
        min_minutes: int = 0,
        summary_limit: int = 80,
        **window,
    ) -> SyncReport:
        """Collect agent work blocks and write them into the diary."""
        entries = self.work_entries(
            source=source, min_minutes=min_minutes, summary_limit=summary_limit, **window
        )
        return self.apply(entries, dry_run=dry_run)

    def sync(
        self,
        *,
        csv: str | Path | None = None,
        dry_run: bool | None = None,
        source: str = "all",
        min_minutes: int = 0,
        summary_limit: int = 80,
        **window,
    ) -> SyncReport:
        """Collect both kinds of record and write them in one pass."""
        records: list[Record] = list(self.activities(csv=csv, **window))
        records.extend(
            self.work_entries(
                source=source, min_minutes=min_minutes, summary_limit=summary_limit, **window
            )
        )
        return self.apply(records, dry_run=dry_run)
