"""Configuration and path resolution.

Precedence, highest first:

1. explicit CLI arguments
2. environment variables (``DIARYSYNC_*``, then bare ``GARMIN_*``)
3. ``<vault>/.diarysync/config.toml``
4. ``~/.config/diarysync/config.toml``
5. built-in defaults

Secrets are never written by this tool.  ``garmin_password`` is accepted in a
config file for convenience but the environment variable or the interactive
prompt is the recommended path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

try:  # Python >= 3.11
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as _toml  # type: ignore

CONFIG_DIR_NAME = ".diarysync"
CONFIG_FILE_NAME = "config.toml"
LEDGER_FILE_NAME = "ledger.json"

DEFAULT_TOKEN_STORE = "~/.garminconnect"
DEFAULT_DIARY_DIR = "diary"


@dataclass
class Settings:
    vault: Path
    diary_dir: Path
    ledger_path: Path
    garmin_email: str | None = None
    garmin_password: str | None = None
    garmin_is_cn: bool = True
    token_store: Path = field(default_factory=lambda: Path(DEFAULT_TOKEN_STORE).expanduser())
    overlap_ratio: float = 0.6
    use_ledger: bool = True
    checkin_label: str = "运动"
    complete_unchecked_checkin: bool = True
    summarizer_cmd: str | None = None
    work_sources: tuple[str, ...] = ("codex", "dsh")

    @property
    def config_path(self) -> Path:
        return self.vault / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def find_vault(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for a directory that holds ``diary/``.

    Falls back to ``start`` itself when nothing matches.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / DEFAULT_DIARY_DIR).is_dir():
            return candidate
    return current


def _read_config_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as stream:
            data = _toml.load(stream)
    except Exception:  # noqa: BLE001 - a broken config must not abort the run
        return {}
    return data if isinstance(data, dict) else {}


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def load_settings(
    *,
    vault: str | Path | None = None,
    diary_dir: str | Path | None = None,
    email: str | None = None,
    password: str | None = None,
    is_cn: bool | None = None,
    token_store: str | Path | None = None,
    ledger_path: str | Path | None = None,
    overlap_ratio: float | None = None,
    summarizer_cmd: str | None = None,
    cwd: Path | None = None,
) -> Settings:
    env_vault = _first_env("DIARYSYNC_VAULT")
    resolved_vault = Path(vault or env_vault).expanduser() if (vault or env_vault) else find_vault(cwd)
    resolved_vault = resolved_vault.resolve()

    config = {}
    config.update(_read_config_file(Path.home() / ".config" / "diarysync" / CONFIG_FILE_NAME))
    config.update(_read_config_file(resolved_vault / CONFIG_DIR_NAME / CONFIG_FILE_NAME))

    diary_setting = diary_dir or config.get("diary_dir") or DEFAULT_DIARY_DIR
    diary_path = Path(diary_setting)
    if not diary_path.is_absolute():
        diary_path = resolved_vault / diary_path

    ledger_setting = ledger_path or config.get("ledger_path")
    if ledger_setting:
        ledger = Path(ledger_setting).expanduser()
        if not ledger.is_absolute():
            ledger = resolved_vault / ledger
    else:
        ledger = resolved_vault / CONFIG_DIR_NAME / LEDGER_FILE_NAME

    token_setting = token_store or config.get("token_store") or DEFAULT_TOKEN_STORE

    settings = Settings(
        vault=resolved_vault,
        diary_dir=diary_path,
        ledger_path=ledger,
        garmin_email=email
        or _first_env("DIARYSYNC_GARMIN_EMAIL", "GARMIN_EMAIL")
        or config.get("garmin_email"),
        garmin_password=password
        or _first_env("DIARYSYNC_GARMIN_PASSWORD", "GARMIN_PASSWORD")
        or config.get("garmin_password"),
        garmin_is_cn=is_cn if is_cn is not None else bool(config.get("garmin_is_cn", True)),
        token_store=Path(str(token_setting)).expanduser(),
        overlap_ratio=float(
            overlap_ratio if overlap_ratio is not None else config.get("overlap_ratio", 0.6)
        ),
        use_ledger=bool(config.get("use_ledger", True)),
        checkin_label=str(config.get("checkin_label", "运动")),
        complete_unchecked_checkin=bool(config.get("complete_unchecked_checkin", True)),
        summarizer_cmd=summarizer_cmd or config.get("summarizer_cmd"),
    )
    sources = config.get("work_sources")
    if isinstance(sources, list) and sources:
        settings.work_sources = tuple(str(item) for item in sources)
    return settings


def with_password(settings: Settings, password: str) -> Settings:
    return replace(settings, garmin_password=password)


def prompt_password(prompt: str = "Garmin password: ") -> str:
    import getpass

    if not sys.stdin.isatty():
        raise RuntimeError(
            "No Garmin password available. Pass --password, set "
            "DIARYSYNC_GARMIN_PASSWORD, or run in an interactive terminal."
        )
    return getpass.getpass(prompt)


def prompt_email(prompt: str = "Garmin email: ") -> str:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "No Garmin email available. Pass --email, set "
            "DIARYSYNC_GARMIN_EMAIL, or add garmin_email to .diarysync/config.toml."
        )
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def prompt_mfa() -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input("Garmin MFA code: ").strip()
    except EOFError:
        return ""
