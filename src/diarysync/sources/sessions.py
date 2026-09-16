"""Reconstruct work blocks from local agent session transcripts.

Two transcript formats are supported:

* **Codex** — ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` (one JSON object
  per line) plus ``~/.codex/history.jsonl`` for prompt text.
* **DeepSeek Harness** — ``~/.dsh/sessions/<cwd>/session-<id>/session.jsonl.zstd``
  (zstd-compressed, one JSON object per line).

A session that spans several days is split per local day.  The time window is
the min/max event timestamp of that day; the summary is the first real user
prompt; the tag comes from :func:`infer_tag`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from ..models import WorkEntry

DEFAULT_CODEX_ROOT = Path("~/.codex")
DEFAULT_DSH_ROOT = Path("~/.dsh")

#: Marks a Codex rollout as an internal approval-review thread rather than a
#: session a human actually drove.
_SYNTHETIC_SESSION_MARKER = "The following is the Codex agent history"

#: User turns that are really framework boilerplate, not the human typing.
_BOILERPLATE_PREFIXES = (
    "<permissions instructions",
    "<multi_agent_mode",
    "<environment_context",
    "<system-reminder",
    "<available_skills",
    "<user_shell_command",
    "<command>",
    "<image",
    "<local-command",
    "</",
    "# AGENTS.md instructions",
    "You are `/root`",
    "Filesystem sandboxing defines",
    "The following is the Codex agent history",
    "TRANSCRIPT START",
)

_BULLET_PREFIX_RE = re.compile(r"^(?:[#>\-*+]\s*|\d+[.、)]\s*)+")
_WHITESPACE_RE = re.compile(r"\s+")

#: ISO-8601 stamps, with an optional fractional part and zone offset.
_TS_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"[T ](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


@dataclass
class SessionEvent:
    session_id: str
    ts: datetime
    role: str  # "user" | "assistant"
    text: str
    origin: str  # "codex" | "dsh"


# ---------------------------------------------------------------------------
# transcript readers
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def _iter_zstd_lines(path: Path) -> Iterator[str]:
    """Stream a ``.zstd`` file, preferring the Python module over the CLI."""
    try:
        import zstandard  # type: ignore

        decompressor = zstandard.ZstdDecompressor()
        with path.open("rb") as raw:
            with decompressor.stream_reader(raw) as reader:
                yield from _read_lines(_iter_chunks(reader.read))
        return
    except ImportError:
        pass

    executable = shutil.which("zstd")
    if not executable:
        raise RuntimeError(
            "Cannot read .zstd transcripts: install the 'sessions' extra "
            "(pip install 'diarysync[sessions]') or the zstd binary."
        )
    with subprocess.Popen(
        [executable, "-dc", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        assert process.stdout is not None
        for raw_line in process.stdout:
            yield raw_line.decode("utf-8", "replace")


def _iter_chunks(read: Callable[[int], bytes], size: int = 1 << 16) -> Iterator[bytes]:
    """Turn a ``read(size)`` callable into a chunk iterator.

    ``zstandard``'s stream reader supports ``read(n)`` but is not iterable, so
    both the module path and the CLI path go through this helper.
    """
    while True:
        chunk = read(size)
        if not chunk:
            return
        yield chunk


def _read_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line.decode("utf-8", "replace")
    if buffer:
        yield buffer.decode("utf-8", "replace")


def _parse_timestamp(value) -> datetime | None:
    """Parse an epoch number or ISO-8601 stamp into naive local time.

    Parsed by hand rather than with ``datetime.fromisoformat`` because before
    Python 3.11 that function rejects short fractional seconds and several
    offset spellings, which would silently drop transcript events.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None

    match = _TS_RE.match(str(value).strip())
    if match is None:
        return None
    fraction = (match.group("frac") or "0").ljust(6, "0")[:6]
    try:
        naive = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            int(fraction),
        )
    except ValueError:
        return None

    zone = match.group("tz")
    if not zone:
        return naive
    if zone == "Z":
        offset = timedelta(0)
    else:
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    return (naive - offset).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _is_boilerplate(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES)


def read_codex_events(root: str | Path = DEFAULT_CODEX_ROOT) -> Iterator[SessionEvent]:
    """Yield user/assistant events from Codex rollout transcripts.

    Only threads a human actually drove are kept.  Codex records a
    ``thread_source`` on every rollout: ``user`` for top-level sessions, and
    ``subagent`` / ``guardian_review`` for spawned workers and automatic
    reviews.  The latter contain templated prompts such as "You are a Senior
    Code Reviewer ..."; writing those into a diary would bury the real work.
    """
    sessions_dir = Path(root).expanduser() / "sessions"
    if not sessions_dir.is_dir():
        return
    for path in sorted(sessions_dir.rglob("*.jsonl")):
        yield from _codex_events_from_file(path)


def _thread_is_user_session(payload: dict) -> bool | None:
    """Classify a ``session_meta`` record.

    Returns ``None`` when the transcript carries no usable metadata, so the
    caller can fall back to text heuristics instead of discarding everything.
    """
    if payload.get("type") != "session_meta":
        return None
    body = payload.get("payload")
    if not isinstance(body, dict):
        return None
    thread_source = body.get("thread_source")
    source = body.get("source")
    if isinstance(source, dict) and "subagent" in source:
        return False
    if thread_source is None:
        return None
    return thread_source == "user"


def _codex_events_from_file(path: Path) -> list[SessionEvent]:
    session_id = _session_id_from_name(path.name)
    events: list[SessionEvent] = []
    for payload in _iter_jsonl(path):
        if _thread_is_user_session(payload) is False:
            return []
        ts = _parse_timestamp(payload.get("timestamp"))
        if ts is None:
            continue
        if payload.get("type") != "response_item":
            continue
        body = payload.get("payload")
        if not isinstance(body, dict) or body.get("type") != "message":
            continue
        role = body.get("role")
        if role not in ("user", "assistant"):
            continue
        content = body.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if role == "user" and text.lstrip().startswith(_SYNTHETIC_SESSION_MARKER):
                return []
            if role == "user" and _is_boilerplate(text):
                continue
            events.append(SessionEvent(session_id, ts, role, text, "codex"))
    return events


def read_dsh_events(root: str | Path = DEFAULT_DSH_ROOT) -> Iterator[SessionEvent]:
    """Yield user/assistant events from DeepSeek Harness sessions."""
    sessions_dir = Path(root).expanduser() / "sessions"
    if not sessions_dir.is_dir():
        return
    for path in sorted(sessions_dir.glob("*/session-*/session.jsonl.zstd")):
        session_id = path.parent.name
        try:
            lines = list(_iter_zstd_lines(path))
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            ts = _parse_timestamp(payload.get("time"))
            if ts is None:
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            if kind == "user/message":
                source = data.get("source")
                if isinstance(source, dict) and source.get("kind") not in (None, "user"):
                    continue
                for text in _dsh_texts(data.get("content")):
                    if _is_boilerplate(text):
                        continue
                    yield SessionEvent(session_id, ts, "user", text, "dsh")
            elif kind == "assistant/message":
                message = data.get("message")
                if not isinstance(message, dict):
                    continue
                for text in _dsh_texts(message.get("content")):
                    yield SessionEvent(session_id, ts, "assistant", text, "dsh")


def _dsh_texts(content) -> Iterator[str]:
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in ("text", "output_text"):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            yield text


def _session_id_from_name(name: str) -> str:
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", name)
    if match:
        return match.group(1)
    return name.removesuffix(".jsonl")


# ---------------------------------------------------------------------------
# summarising
# ---------------------------------------------------------------------------

_GOAL_RE = re.compile(r"^#{1,3}\s*(?:goal|目标)\s*$(.*?)(?=^#{1,3}\s|\Z)", re.IGNORECASE | re.MULTILINE | re.DOTALL)

_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("服务器", ("ssh", "服务器", "远端", "tmux", "部署", "systemd", "ecs", "隧道", "sync 到", "训练服务器")),
    ("故障", ("卡死", "报错", "错误", "失败", "排查", "修复", "bug", "error", "traceback", "connection refused", "崩溃")),
    ("审查", ("审查", "复核", "review", "检查一下", "核对", "reviewer")),
    ("配置", ("配置", "安装", "plugin", "插件", "skill", "环境变量", "设置", "provider")),
    ("文档", ("文档", "readme", "日志文件", "撰写", "记录一下", "写一下", "笔记")),
    ("科研", ("模型", "实验", "数据", "分析", "论文", "训练", "评估", "贝叶斯", "统计", "被试", "假设", "文献")),
    ("开发", ("实现", "代码", "脚本", "功能", "重构", "cli", "命令行", "repo", "仓库", "接口", "程序")),
    ("整理", ("整理", "汇总", "归纳", "合并")),
)

DEFAULT_TAG = "工作"


def infer_tag(text: str) -> str:
    """Pick a ``#tag`` for a work block from its prompt text."""
    lowered = text.lower()
    best_tag = DEFAULT_TAG
    best_score = 0
    for tag, keywords in _TAG_RULES:
        score = sum(1 for keyword in keywords if keyword.lower() in lowered)
        if score > best_score:
            best_tag, best_score = tag, score
    return best_tag


#: A prompt line shorter than this is treated as a heading ("Purpose",
#: "背景") and skipped when a meatier line follows.
MIN_LINE_CHARS = 8
#: Once this much text is collected, a blank line ends the summary.
ENOUGH_CHARS = 20


def summarise_prompt(text: str, limit: int = 80) -> str:
    """Turn a raw prompt into one diary-sized line.

    A ``## goal`` block wins when present, otherwise the opening lines are
    used.  Fenced code and markdown punctuation are stripped, and short
    section headings are skipped in favour of the prose underneath.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""

    goal = _GOAL_RE.search(cleaned)
    candidate = goal.group(1) if goal else cleaned

    lines: list[str] = []
    fallback = ""
    in_fence = False
    for raw_line in candidate.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            if len(" ".join(lines)) >= ENOUGH_CHARS:
                break
            continue
        line = _BULLET_PREFIX_RE.sub("", line).strip()
        if not line:
            continue
        if not fallback:
            fallback = line
        if not lines and len(line) < MIN_LINE_CHARS:
            continue
        lines.append(line)
        if len(" ".join(lines)) >= limit:
            break

    summary = " ".join(lines) or fallback
    summary = _WHITESPACE_RE.sub(" ", summary).strip().strip(" 　|")
    if len(summary) > limit:
        summary = summary[: limit - 1].rstrip(" ,，。;；:：") + "…"
    return summary


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------


def collect_work_entries(
    events: Iterable[SessionEvent],
    *,
    start: date,
    end: date,
    summarizer: Callable[[str], str] | None = None,
    summary_limit: int = 80,
    min_minutes: int = 0,
) -> list[WorkEntry]:
    """Group events into one work entry per (origin, session, local day)."""
    summarise = summarizer or (lambda text: summarise_prompt(text, summary_limit))

    def sort_key(event: SessionEvent):
        return (event.origin, event.session_id, event.ts.date(), event.ts)

    ordered = sorted(
        (event for event in events if start <= event.ts.date() <= end),
        key=sort_key,
    )

    entries: list[WorkEntry] = []
    for (origin, session_id, day), bucket in groupby(
        ordered, key=lambda e: (e.origin, e.session_id, e.ts.date())
    ):
        group = list(bucket)
        if not group:
            continue
        timestamps = [event.ts for event in group]
        first = min(timestamps).replace(second=0, microsecond=0)
        last = max(timestamps)
        end_dt = (last + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if end_dt < first:
            end_dt = first + timedelta(minutes=1)
        if min_minutes and (end_dt - first) < timedelta(minutes=min_minutes):
            continue

        prompt = next((e.text for e in group if e.role == "user" and e.text.strip()), "")
        if not prompt.strip():
            # No human turn at all: an internal sub-agent / approval-review
            # rollout.  Recording it would only add noise.
            continue
        summary = summarise(prompt) or "对话与工具操作"
        entries.append(
            WorkEntry.build(
                day=day,
                start=first,
                end=end_dt,
                tag=infer_tag(prompt),
                summary=summary,
                session_id=session_id,
                prompt=prompt,
                source=origin,
            )
        )
    return merge_overlapping(entries)


def merge_overlapping(entries: list[WorkEntry]) -> list[WorkEntry]:
    """Collapse same-day entries that describe the same work.

    A session and its spawned review sub-sessions can produce several rows with
    an identical summary inside one another's time window; keeping only the
    widest window is what a human would have written.
    """
    buckets: dict[tuple, list[WorkEntry]] = {}
    for entry in entries:
        key = (entry.day, entry.tag, entry.summary, entry.extra.get("source"))
        buckets.setdefault(key, []).append(entry)

    merged: list[WorkEntry] = []
    for group in buckets.values():
        group.sort(key=lambda item: (item.start, item.end))
        current = group[0]
        for following in group[1:]:
            if following.start <= current.end:
                if following.end > current.end:
                    current = replace(current, end=following.end, source_id=None)
                continue
            merged.append(current)
            current = following
        merged.append(current)
    merged.sort(key=lambda item: (item.day, item.start))
    return merged


def default_session_roots() -> dict[str, Path]:
    roots: dict[str, Path] = {}
    codex = Path(os.environ.get("CODEX_HOME", DEFAULT_CODEX_ROOT)).expanduser()
    if (codex / "sessions").is_dir():
        roots["codex"] = codex
    dsh = Path(os.environ.get("DSH_HOME", DEFAULT_DSH_ROOT)).expanduser()
    if (dsh / "sessions").is_dir():
        roots["dsh"] = dsh
    return roots
