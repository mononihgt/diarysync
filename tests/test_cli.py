"""CLI wiring: argument plumbing and the offline/online source switch."""

from __future__ import annotations

from datetime import date

import pytest

from diarysync import cli
from diarysync.api import DiarySync
from diarysync.config import Settings


@pytest.fixture
def isolated(monkeypatch, vault):
    """Capture what the CLI asks the facade to do, without touching the network."""

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

    recorder = Recorder()
    real_init = DiarySync.__init__

    def fake_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.__dict__["_recorder"] = recorder

    def fake_activities(self, **kwargs):
        recorder.calls.append(("activities", kwargs))
        return []

    def fake_work_entries(self, **kwargs):
        recorder.calls.append(("work_entries", kwargs))
        return []

    monkeypatch.setattr(DiarySync, "__init__", fake_init)
    monkeypatch.setattr(DiarySync, "activities", fake_activities)
    monkeypatch.setattr(DiarySync, "work_entries", fake_work_entries)
    return recorder


def test_sync_collects_both_kinds(isolated, vault):
    assert cli.main(["sync", "--vault", str(vault), "--source", "codex", "--days", "3"]) == 0
    kinds = [name for name, _ in isolated.calls]
    assert kinds == ["activities", "work_entries"]
    work_kwargs = dict(isolated.calls)["work_entries"]
    assert work_kwargs["source"] == "codex"


def test_sync_work_only_skips_exercise(isolated, vault):
    assert cli.main(["sync", "--vault", str(vault), "--work-only"]) == 0
    assert [name for name, _ in isolated.calls] == ["work_entries"]


def test_sync_exercise_only_skips_work(isolated, vault):
    assert cli.main(["sync", "--vault", str(vault), "--exercise-only"]) == 0
    assert [name for name, _ in isolated.calls] == ["activities"]


def test_exercise_csv_mode_does_not_require_a_garmin_password(vault, tmp_path, monkeypatch):
    import diarysync.api as api

    csv_path = tmp_path / "Activities.csv"
    csv_path.write_text(
        "活动类型,日期,标题,距离,热量消耗,时间\n"
        '跑步,2026-09-14 19:46:56,"杭州市 - 基础训练","6.79","380","00:33:02"\n',
        encoding="utf-8-sig",
    )

    def explode(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("should not prompt for a password in --csv mode")

    monkeypatch.setattr(api, "prompt_password", explode)

    assert cli.main(
        [
            "exercise",
            "--vault",
            str(vault),
            "--csv",
            str(csv_path),
            "--since",
            "2026-09-14",
            "--until",
            "2026-09-14",
            "--dry-run",
        ]
    ) == 0
    assert (vault / "diary" / "2026-09-14.md").exists() is False


def test_missing_since_after_until_exits(vault):
    with pytest.raises(SystemExit):
        cli.main(
            ["exercise", "--vault", str(vault), "--since", "2026-09-16", "--until", "2026-09-01"]
        )


def test_parse_day_accepts_common_formats():
    assert cli._parse_day("2026-09-14") == date(2026, 9, 14)
    assert cli._parse_day("2026/09/14") == date(2026, 9, 14)
    assert cli._parse_day("20260914") == date(2026, 9, 14)
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli._parse_day("14-09-2026")


def test_window_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["exercise", "--days", "7"])
    since, until = cli._window(args, 30)
    assert (until - since).days == 6


def test_missing_vault_is_a_clean_error(tmp_path, monkeypatch, capsys):
    from diarysync import cli

    workdir = tmp_path / "plain-directory"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert cli.main(["work", "--days", "1"]) == 2
    assert "vault" in capsys.readouterr().err
    assert not (workdir / "diary").exists()


def test_doctor_reports_configuration(vault, capsys, monkeypatch):
    monkeypatch.setenv("DIARYSYNC_GARMIN_EMAIL", "me@example.com")
    assert cli.main(["doctor", "--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "me@example.com" in out
    assert "中国站 connect.garmin.cn" in out
    assert str(vault / "diary") in out


def test_report_json_is_machine_readable(vault, capsys):
    from datetime import datetime

    from diarysync.models import Activity
    from diarysync.sync import run

    settings = Settings(
        vault=vault, diary_dir=vault / "diary", ledger_path=vault / ".diarysync" / "ledger.json"
    )
    record = Activity.build(
        day=date(2026, 9, 14),
        start=datetime(2026, 9, 14, 19, 46),
        end=datetime(2026, 9, 14, 20, 19),
        type_key="running",
        type_label="跑步",
        title="杭州市 - 基础训练",
    )
    report = run([record], settings, dry_run=True)
    payload = report.as_dict()
    assert payload["total"] == 1
    assert payload["inserted"][0]["line"] == "- [x] 19:46 - 20:19 #运动 跑步 杭州市 - 基础训练"
