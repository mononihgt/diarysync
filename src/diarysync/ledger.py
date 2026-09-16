"""Persistent record of what has already been written to the diary.

The ledger is the only reason ``diarysync`` stays idempotent when the user
edits a diary afterwards: content matching can be defeated by a reworded line,
but a ledger key survives.  Nothing secret is stored here — only record
identities and the exact line that was written.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LEDGER_VERSION = 1
DEFAULT_LEDGER_NAME = ".diarysync/ledger.json"


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: dict[str, dict] = {}
        self._loaded = False

    # -- io ----------------------------------------------------------------

    def load(self) -> Ledger:
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("records"), dict):
                self._records = payload["records"]
        self._loaded = True
        return self

    def save(self, *, dry_run: bool = False) -> None:
        if dry_run:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": LEDGER_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "records": dict(sorted(self._records.items())),
        }
        # Atomic replace so an interrupted run never truncates the ledger.
        handle, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".ledger-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # -- queries -----------------------------------------------------------

    def knows(self, keys: list[str]) -> str | None:
        for key in keys:
            if key in self._records:
                return key
        return None

    def remember(self, keys: list[str], *, day: str, line: str, source: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key in keys:
            self._records[key] = {"day": day, "line": line, "source": source, "synced_at": stamp}

    def forget(self, keys: list[str]) -> int:
        removed = 0
        for key in keys:
            if self._records.pop(key, None) is not None:
                removed += 1
        return removed

    def entries(self) -> dict[str, dict]:
        return dict(self._records)

    def __len__(self) -> int:
        return len(self._records)
