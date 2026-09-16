"""Garmin Connect China (connect.garmin.cn) activities, via the Connect API.

This is the online path.  It talks to the same backend as the website's
"导出为csv文献" button, but returns structured JSON instead of a CSV, so no
browser automation is needed.

Requires the ``garmin`` extra (``pip install 'diarysync[garmin]'``), which in
turn requires Python 3.12+.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from ..activity_types import canonical_type_key, type_label
from ..models import Activity


class GarminAuthError(RuntimeError):
    """Raised when Garmin rejects the credentials or demands MFA."""


def _import_garmin():
    try:
        from garminconnect import Garmin  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise GarminAuthError(
            "garminconnect is not installed. Install the extra:\n"
            "    pip install 'diarysync[garmin]'\n"
            "(garminconnect requires Python 3.12 or newer.)"
        ) from exc
    return Garmin


def _build_client(
    email: str,
    password: str,
    *,
    is_cn: bool,
    mfa_prompt: Callable[[], str] | None,
):
    Garmin = _import_garmin()
    if not email or not password:
        raise GarminAuthError("Both a Garmin email and password are required for online sync.")
    return Garmin(
        email,
        password,
        is_cn=is_cn,
        prompt_mfa=mfa_prompt if mfa_prompt is not None else (lambda: ""),
        return_on_mfa=mfa_prompt is None,
    )


def login(
    email: str,
    password: str,
    *,
    is_cn: bool = True,
    token_store: str | Path | None = None,
    mfa_prompt: Callable[[], str] | None = None,
):
    """Authenticate and return a ready-to-use Garmin client.

    Tokens are cached by the library at ``token_store`` (default
    ``~/.garminconnect``) so later runs skip the credential exchange.
    """
    client = _build_client(email, password, is_cn=is_cn, mfa_prompt=mfa_prompt)
    store = str(Path(token_store).expanduser()) if token_store else None
    try:
        result = client.login(store)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
        raise GarminAuthError(f"Garmin login failed: {exc}") from exc
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        raise GarminAuthError(
            "Garmin requires a multi-factor authentication code. Re-run with an "
            "interactive terminal so diarysync can prompt for it (do not pass "
            "--non-interactive)."
        )
    return client


def probe_login(
    email: str,
    password: str,
    *,
    is_cn: bool = True,
    token_store: str | Path | None = None,
) -> str:
    """Return a short human-readable confirmation that login works."""
    client = login(email, password, is_cn=is_cn, token_store=token_store)
    name = ""
    try:
        profile = client.get_full_name()  # type: ignore[attr-defined]
        if isinstance(profile, str):
            name = profile
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass
    domain = "connect.garmin.cn" if is_cn else "connect.garmin.com"
    return f"login ok against {domain}" + (f" as {name}" if name else "")


def _activity_from_payload(payload: dict) -> Activity | None:
    raw_start = payload.get("startTimeLocal") or payload.get("startTimeGMT")
    if not raw_start:
        return None
    try:
        start = datetime.strptime(str(raw_start)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    duration_s = _first_number(payload, "duration", "movingDuration", "elapsedDuration")
    if duration_s is None:
        return None
    # The diary convention is minute precision.  Add the duration to the *real*
    # start before flooring, otherwise every window is silently shortened.
    end = start + timedelta(seconds=float(duration_s))
    start = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)
    if end <= start:
        end = start + timedelta(minutes=1)

    type_info = payload.get("activityType") or {}
    if isinstance(type_info, dict):
        raw_type = type_info.get("typeKey") or ""
    else:
        raw_type = str(type_info)

    activity_id = payload.get("activityId") or payload.get("activityUUID")
    return Activity.build(
        day=start.date(),
        start=start,
        end=end,
        type_key=canonical_type_key(raw_type),
        type_label=type_label(raw_type),
        title=str(payload.get("activityName") or "").strip(),
        source_id=f"garmin:{activity_id}" if activity_id else None,
        distance_m=_first_number(payload, "distance"),
        calories=_first_number(payload, "calories"),
        source="garmin",
    )


def _first_number(payload: dict, *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def fetch_activities(
    email: str,
    password: str,
    *,
    start: date,
    end: date,
    is_cn: bool = True,
    token_store: str | Path | None = None,
    mfa_prompt: Callable[[], str] | None = None,
    client=None,
) -> list[Activity]:
    """Fetch activities in ``[start, end]`` (inclusive) as diary records."""
    if client is None:
        client = login(
            email,
            password,
            is_cn=is_cn,
            token_store=token_store,
            mfa_prompt=mfa_prompt,
        )

    payloads = client.get_activities_by_date(
        start.isoformat(), end.isoformat(), sortorder="asc"
    )
    if payloads is None:
        payloads = []
    if isinstance(payloads, dict):  # pragma: no cover - defensive
        payloads = payloads.get("activityList", []) or []

    activities: list[Activity] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        activity = _activity_from_payload(payload)
        if activity is None:
            continue
        if not (start <= activity.day <= end):
            continue
        activities.append(activity)
    return activities
