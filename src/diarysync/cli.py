"""Command line interface for diarysync.

The CLI is a thin shell over :mod:`diarysync.api`: it only parses arguments,
prints reports and maps errors onto exit codes.  Anything you can do here is
also available as a Python call.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import __version__
from .api import (
    DEFAULT_WINDOW_DAYS,
    DEFAULT_WORK_WINDOW_DAYS,
    DiarySync,
    parse_day,
    resolve_window,
)
from .config import Settings, load_settings
from .ledger import Ledger
from .sources.garmin import GarminAuthError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_day(value: str) -> date:
    try:
        return parse_day(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _window(args, default_days: int) -> tuple[date, date]:
    try:
        return resolve_window(
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            days=getattr(args, "days", None),
            default_days=default_days,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _facade(args, *, need_password: bool = False) -> DiarySync:
    return DiarySync(
        vault=getattr(args, "vault", None),
        email=getattr(args, "email", None),
        password=getattr(args, "password", None),
        is_cn=False if getattr(args, "global_account", False) else None,
        token_store=getattr(args, "token_store", None),
        overlap_ratio=getattr(args, "overlap_ratio", None),
        summarizer_cmd=getattr(args, "summarizer", None),
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
        prompt_for_password=need_password,
    )


def _settings(args) -> Settings:
    return load_settings(
        vault=getattr(args, "vault", None),
        overlap_ratio=getattr(args, "overlap_ratio", None),
    )


def _print_header(text: str) -> None:
    print(f"\n=== {text} ===")


def _print_report(report, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return

    if report.inserted:
        _print_header(f"新增 {len(report.inserted)} 条")
        for record in report.inserted:
            print(f"  {record.day}  {record.schedule_line()}")
    if report.skipped:
        _print_header(f"跳过 {len(report.skipped)} 条（去重）")
        for record, reason in report.skipped:
            print(f"  {record.day}  {record.schedule_line()}")
            print(f"      原因: {reason}")
    if report.checkins_added:
        _print_header("补充运动打卡")
        for day in report.checkins_added:
            print(f"  {day}  - [x] 运动")
    if report.files_written:
        _print_header("写入文件")
        for path in report.files_written:
            created = "（新建）" if path in report.files_created else ""
            print(f"  {path}{created}")
    if not (report.inserted or report.skipped):
        print("\n没有需要处理的记录。")


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _finish(report, args) -> int:
    _print_report(report, as_json=args.json)
    if args.dry_run:
        print("\n（--dry-run：未写入任何文件）")
    return 0


def _offline_hint(exc: GarminAuthError) -> SystemExit:
    return SystemExit(
        f"Garmin 登录失败：{exc}\n\n"
        "可以改用离线模式：从 https://connect.garmin.cn/app/activities 点击"
        "「导出为csv文献」下载 Activities.csv，然后运行\n"
        "    diarysync exercise --csv ~/Downloads/Activities.csv"
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_exercise(args) -> int:
    sync = _facade(args, need_password=not args.csv)
    since, until = _window(args, DEFAULT_WINDOW_DAYS)
    try:
        activities = sync.activities(since=since, until=until, csv=args.csv)
    except GarminAuthError as exc:
        raise _offline_hint(exc) from exc
    if args.csv:
        _log(f"[csv] {args.csv}: 窗口 {since} ~ {until} 内 {len(activities)} 条活动")
    else:
        domain = "connect.garmin.cn" if sync.settings.garmin_is_cn else "connect.garmin.com"
        _log(f"[garmin] {domain}: 窗口 {since} ~ {until} 内 {len(activities)} 条活动")
    return _finish(sync.apply(activities), args)


def cmd_work(args) -> int:
    sync = _facade(args)
    since, until = _window(args, DEFAULT_WORK_WINDOW_DAYS)
    entries = sync.work_entries(
        since=since,
        until=until,
        source=args.source,
        min_minutes=args.min_minutes,
        summary_limit=args.summary_limit,
    )
    _log(f"[sessions] 来源 {args.source}：窗口 {since} ~ {until} 内 {len(entries)} 段工作记录")
    return _finish(sync.apply(entries), args)


def cmd_sync(args) -> int:
    want_exercise = not args.work_only
    want_work = not args.exercise_only
    sync = _facade(args, need_password=want_exercise and not args.csv)
    since, until = _window(args, DEFAULT_WINDOW_DAYS)

    records = []
    if want_exercise:
        try:
            activities = sync.activities(since=since, until=until, csv=args.csv)
        except GarminAuthError as exc:
            raise _offline_hint(exc) from exc
        records.extend(activities)
        _log(f"[garmin] 窗口 {since} ~ {until} 内 {len(activities)} 条活动")
    if want_work:
        entries = sync.work_entries(
            since=since,
            until=until,
            source=args.source,
            min_minutes=args.min_minutes,
            summary_limit=args.summary_limit,
        )
        records.extend(entries)
        _log(f"[sessions] 来源 {args.source}：窗口 {since} ~ {until} 内 {len(entries)} 段工作记录")
    return _finish(sync.apply(records), args)


def cmd_login(args) -> int:
    sync = _facade(args, need_password=True)
    try:
        message = sync.login()
    except (GarminAuthError, ValueError) as exc:
        print(f"登录失败：{exc}")
        return 1
    print(message)
    print(f"token store: {sync.settings.token_store}")
    return 0


def cmd_ledger(args) -> int:
    settings = _settings(args)
    ledger = Ledger(settings.ledger_path).load()
    if args.forget:
        removed = ledger.forget(args.forget)
        ledger.save(dry_run=args.dry_run)
        print(f"已从 ledger 移除 {removed} 个键")
        return 0
    entries = ledger.entries()
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    print(f"ledger: {settings.ledger_path}（{len(entries)} 条记录）")
    for key, value in list(entries.items())[-args.limit :]:
        print(f"  {value.get('day')}  {value.get('source'):<10} {value.get('line')}  [{key}]")
    return 0


def cmd_doctor(args) -> int:
    settings = _settings(args)
    print(f"diarysync {__version__}")
    print(f"python       : {sys.version.split()[0]}  ({sys.executable})")
    print(f"vault        : {settings.vault}")
    print(f"diary_dir    : {settings.diary_dir}  (exists={settings.diary_dir.is_dir()})")
    print(f"ledger       : {settings.ledger_path}")
    print(f"config       : {settings.config_path}  (exists={settings.config_path.is_file()})")
    print(f"garmin email : {settings.garmin_email or '(未设置)'}")
    print(f"garmin 密码  : {'已设置' if settings.garmin_password else '(未设置)'}")
    region = "中国站 connect.garmin.cn" if settings.garmin_is_cn else "国际站 connect.garmin.com"
    print(f"garmin 区域  : {region}")
    print(f"token store  : {settings.token_store}")
    print(f"overlap_ratio: {settings.overlap_ratio}")

    print("\n依赖检查：")
    for module, extra, note in (
        ("garminconnect", "garmin", "在线同步 Garmin"),
        ("curl_cffi", "garmin", "garminconnect 的 TLS 指纹库"),
        ("zstandard", "sessions", "读取 .jsonl.zstd 会话"),
    ):
        try:
            __import__(module)
            state = "OK"
        except ImportError:
            state = f"缺失（pip install 'diarysync[{extra}]'）"
        print(f"  {module:<15} {state:<10} {note}")

    if sys.version_info < (3, 12):
        print("\n提示：在线同步 Garmin 需要 Python ≥ 3.12（上游 garminconnect 的限制）。")
        print("      当前解释器较旧，可用 --csv 离线模式，或另建 3.12 环境跑在线同步。")

    from .sources import sessions as session_sources

    roots = session_sources.default_session_roots()
    print("\n工作记录来源：")
    if not roots:
        print("  未发现 ~/.codex/sessions 或 ~/.dsh/sessions")
    for origin, root in roots.items():
        print(f"  {origin:<6} {root}")
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", help="Obsidian vault 根目录（默认自动向上查找含 diary/ 的目录）")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要写入的内容，不修改文件")
    parser.add_argument("--force", action="store_true", help="忽略所有去重规则，强制写入")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument(
        "--overlap-ratio",
        type=float,
        default=None,
        help="时间窗重叠比例阈值（默认 0.6）；越大越不容易判定为重复",
    )


def _add_garmin(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", help="Garmin 账号邮箱（默认取 DIARYSYNC_GARMIN_EMAIL）")
    parser.add_argument("--password", help="Garmin 密码（默认取 DIARYSYNC_GARMIN_PASSWORD，否则交互输入）")
    parser.add_argument("--token-store", help="Garmin token 缓存目录（默认 ~/.garminconnect）")
    parser.add_argument(
        "--global-account",
        action="store_true",
        help="改用国际站 connect.garmin.com（默认使用中国站 connect.garmin.cn）",
    )


def _add_window(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--since", "--from", dest="since", type=_parse_day, help="起始日期（含）")
    parser.add_argument("--until", "--to", dest="until", type=_parse_day, help="结束日期（含）")
    parser.add_argument("--days", type=int, help="以今天为终点的天数窗口")


def _add_work_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=("all", "codex", "dsh"),
        default="all",
        help="工作记录读取哪些会话来源（不影响运动同步）",
    )
    parser.add_argument("--min-minutes", type=int, default=0, help="忽略短于该时长的会话")
    parser.add_argument("--summary-limit", type=int, default=80, help="摘要最大字数")
    parser.add_argument(
        "--summarizer",
        help="外部摘要命令（从 stdin 读 prompt，向 stdout 输出一行摘要）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diarysync",
        description="把 Garmin 运动记录和本机 agent 工作记录写进 Obsidian 日记（自动去重）。",
    )
    parser.add_argument("--version", action="version", version=f"diarysync {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    exercise = subparsers.add_parser("exercise", help="同步 Garmin 运动记录")
    _add_common(exercise)
    _add_garmin(exercise)
    _add_window(exercise)
    exercise.add_argument("--csv", help="离线模式：读取导出的 Activities.csv")
    exercise.set_defaults(func=cmd_exercise)

    work = subparsers.add_parser("work", help="从本机 agent 会话历史整理工作记录")
    _add_common(work)
    _add_window(work)
    _add_work_options(work)
    work.set_defaults(func=cmd_work)

    sync = subparsers.add_parser("sync", help="同时同步运动和工作记录")
    _add_common(sync)
    _add_garmin(sync)
    _add_window(sync)
    _add_work_options(sync)
    sync.add_argument("--csv", help="离线模式：读取导出的 Activities.csv")
    sync.add_argument("--work-only", action="store_true", help="只同步工作记录")
    sync.add_argument("--exercise-only", action="store_true", help="只同步运动记录")
    sync.set_defaults(func=cmd_sync)

    login = subparsers.add_parser("login", help="验证 Garmin 账号能否登录")
    _add_garmin(login)
    login.add_argument("--vault")
    login.set_defaults(func=cmd_login)

    ledger = subparsers.add_parser("ledger", help="查看或清理去重 ledger")
    ledger.add_argument("--vault")
    ledger.add_argument("--json", action="store_true")
    ledger.add_argument("--limit", type=int, default=20)
    ledger.add_argument("--forget", nargs="*", help="要移除的 ledger 键")
    ledger.add_argument("--dry-run", action="store_true")
    ledger.set_defaults(func=cmd_ledger)

    doctor = subparsers.add_parser("doctor", help="显示解析后的配置与依赖状态")
    doctor.add_argument("--vault")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
