"""chunkyctl doctor 最小重建单测 — 核心聚合/巡检纯函数 (run_doctor 本体走 subprocess+DB 由实跑验证)。"""
from __future__ import annotations

import pathlib
import argparse
import json
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import chunkyctl  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]


def test_aggregate_verdict_fail_priority():
    """FAIL 优先 > WARN > PASS; returncode!=0 且非WARN/PASS 也算 FAIL。"""
    assert chunkyctl._aggregate_verdict([{"verdict": "PASS"}, {"verdict": "PASS"}]) == "PASS"
    assert chunkyctl._aggregate_verdict([{"verdict": "PASS"}, {"verdict": "WARN"}]) == "WARN"
    assert chunkyctl._aggregate_verdict([{"verdict": "WARN"}, {"verdict": "FAIL"}]) == "FAIL"
    # returncode!=0 (无 verdict) → FAIL
    assert chunkyctl._aggregate_verdict([{"returncode": 1}]) == "FAIL"
    # WARN 带 returncode!=0 仍 WARN (moth 不在 PATH 降级场景)
    assert chunkyctl._aggregate_verdict([{"verdict": "WARN", "returncode": 127}]) == "WARN"


def test_collect_alert_flags_no_flags(tmp_path, monkeypatch):
    """无 ALERT flag → PASS count=0。"""
    monkeypatch.setattr(chunkyctl.glob, "glob", lambda pat: [])
    r = chunkyctl.collect_alert_flags()
    assert r["verdict"] == "PASS" and r["count"] == 0


def test_json_from_stdout():
    assert chunkyctl._json_from_stdout({"stdout": '{"verdict":"PASS"}'}) == {"verdict": "PASS"}
    assert chunkyctl._json_from_stdout({"stdout": "not json"}) is None
    assert chunkyctl._json_from_stdout({"stdout": "[1,2]"}) is None   # 非 dict


def test_automation_surface_rejects_scheduled_data_writer_but_not_codex_rotation(tmp_path):
    """manual_only 只阻断数据写调度；Codex 日志轮转不是 ChunkyMonkey 数据任务。"""
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
    - 'com\\.chunkymonkey\\.daily-update'
""",
        encoding="utf-8",
    )
    plist = repo / "configs" / "launchd" / "com.chunkymonkey.daily-update.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>com.chunkymonkey.daily-update</string>
<key>ProgramArguments</key><array>
<string>/bin/bash</string><string>scripts/daily_update.sh</string>
</array><key>StartInterval</key><integer>60</integer>
</dict></plist>
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "home",
        crontab_text="0 * * * * /Users/dp/bin/rotate_codex_tui_log.sh\n",
        launchctl_text="",
    )

    assert result["name"] == "automation_surface"
    assert result["verdict"] == "FAIL"
    assert [finding["source"] for finding in result["findings"]] == [
        "repo:configs/launchd/com.chunkymonkey.daily-update.plist"
    ]


def test_doctor_blocks_when_automation_surface_fails(tmp_path, monkeypatch, capsys):
    """automation_surface 是 doctor 的阻断 section，不是只打印不传播的旁路。"""
    monkeypatch.setattr(chunkyctl, "_moth_gate", lambda _repo: {"name": "tooling_gate", "verdict": "PASS"})
    monkeypatch.setattr(
        chunkyctl,
        "collect_alert_flags",
        lambda: {"verdict": "PASS", "count": 0, "flags": []},
    )
    monkeypatch.setattr(
        chunkyctl,
        "audit_automation_surface",
        lambda _repo: {
            "name": "automation_surface",
            "verdict": "FAIL",
            "mode": "manual_only",
            "findings": [{"source": "crontab:1", "reason": "data writer"}],
        },
    )
    monkeypatch.setattr(
        chunkyctl,
        "_run_command",
        lambda *_args, **_kwargs: {"cmd": [], "returncode": 0, "stdout": '{"verdict":"PASS"}', "stderr": ""},
    )

    rc = chunkyctl.run_doctor(argparse.Namespace(repo=str(tmp_path)))
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["verdict"] == "FAIL"
    assert [section["name"] for section in report["sections"]] == [
        "tooling_gate",
        "automation_surface",
        "alert_flags",
        "universe",
        "data_health",
    ]


def test_automation_surface_fails_closed_for_unsupported_mode(tmp_path):
    """拼错/放宽 policy mode 不能静默关闭 manual-only 守门。"""
    policy = tmp_path / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manualish
  forbidden_command_patterns: []
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        tmp_path, home=tmp_path / "home", crontab_text="", launchctl_text=""
    )

    assert result["verdict"] == "FAIL"
    assert "unsupported mode" in result["findings"][0]["reason"]


def test_automation_surface_checks_system_launchd_directories(tmp_path):
    """system-level LaunchAgent/Daemon 也是自动执行面，不能只查用户目录。"""
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )
    system_dir = tmp_path / "Library" / "LaunchDaemons"
    plist = system_dir / "com.chunkymonkey.writer.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>com.chunkymonkey.writer</string>
<key>ProgramArguments</key><array>
<string>/bin/bash</string><string>scripts/daily_update.sh</string>
</array><key>RunAtLoad</key><true/>
</dict></plist>
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "home",
        system_launchd_dirs=(system_dir,),
        crontab_text="",
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["source"] == f"system:{plist}"


def test_automation_surface_rejects_data_writer_crontab(tmp_path):
    policy = tmp_path / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        tmp_path,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="0 18 * * 1-5 cd /repo && bash scripts/daily_update.sh\n",
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["source"] == "crontab:1"


def test_repo_policy_covers_independent_pipeline_stage_scheduler(tmp_path):
    """通过 chunkyctl 单跑写阶段也属于数据任务，不能绕过 manual-only。"""
    result = chunkyctl.audit_automation_surface(
        REPO,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="0 18 * * 1-5 cd /repo && scripts/chunkyctl pipeline acquire\n",
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["source"] == "crontab:1"


def test_automation_surface_checks_launchd_program_key(tmp_path):
    policy = tmp_path / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )
    plist = tmp_path / "configs" / "launchd" / "writer.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>generic.writer</string>
<key>Program</key><string>scripts/daily_update.sh</string>
<key>StartInterval</key><integer>60</integer>
</dict></plist>
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        tmp_path,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="",
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"


def test_automation_surface_rejects_unknown_repo_scheduled_plist(tmp_path):
    """writer 改名不能绕门：repo 自有 launchd 目录不允许任何 scheduled plist。"""
    policy = tmp_path / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )
    plist = tmp_path / "backend" / "scripts" / "launchd" / "renamed.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>opaque.writer</string>
<key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>foo.py</string></array>
<key>StartInterval</key><integer>60</integer>
</dict></plist>
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        tmp_path,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="",
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["reason"] == "scheduled plist forbidden by manual_only"


def test_automation_surface_rejects_project_identity_on_external_surfaces(tmp_path):
    """外部面未知命令只要带项目 label/path 也阻断，防改名绕过 patterns。"""
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )
    installed = tmp_path / "home" / "Library" / "LaunchAgents"
    installed.mkdir(parents=True)
    (installed / "renamed.plist").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>com.chunkymonkey.renamed</string>
<key>Program</key><string>/usr/bin/python3</string>
<key>StartInterval</key><integer>60</integer>
</dict></plist>
""",
        encoding="utf-8",
    )

    installed_result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="",
        launchctl_text="",
    )
    cron_result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "empty-home",
        system_launchd_dirs=(),
        crontab_text=f"0 18 * * * cd {repo} && python foo.py\n",
        launchctl_text="",
    )
    launchctl_result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "empty-home",
        system_launchd_dirs=(),
        crontab_text="",
        launchctl_text="-\t0\tcom.chunkymonkey.renamed\n",
    )

    assert installed_result["verdict"] == "FAIL"
    assert cron_result["verdict"] == "FAIL"
    assert launchctl_result["verdict"] == "FAIL"


def test_automation_surface_ignores_crontab_environment_assignments(tmp_path):
    """crontab 的 KEY=value 不是自动命令；只由后续真实 job 行决定。"""
    policy = tmp_path / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        """version: 1
data_jobs:
  mode: manual_only
  forbidden_command_patterns:
    - 'scripts/daily_update\\.sh'
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        tmp_path,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        crontab_text="REPO=/Users/dp/Documents/M/stock/chunkymonkey\nCHUNKYMONKEY=1\n",
        launchctl_text="",
    )

    assert result["verdict"] == "PASS"


def test_automation_surface_expands_cron_env_and_blocks_build_writer(tmp_path):
    """自然的 REPO=$... 写法与 build_* writer 均不得绕过 manual-only。"""
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text((REPO / "backend/config/automation_policy.yaml").read_text(), encoding="utf-8")

    result = chunkyctl.audit_automation_surface(
        repo,
        home=tmp_path / "home",
        system_launchd_dirs=(),
        system_cron_files=(),
        crontab_text=(
            f"REPO={repo}\n"
            "0 18 * * * cd $REPO && python backend/scripts/build_price_kline_qfq_tushare.py\n"
        ),
        launchctl_text="",
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["source"] == "crontab:2"


def test_automation_surface_recognizes_watchpaths_trigger(tmp_path):
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text((REPO / "backend/config/automation_policy.yaml").read_text(), encoding="utf-8")
    plist = repo / "backend/scripts/launchd/watch.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>opaque</string>
<key>Program</key><string>/usr/bin/true</string>
<key>WatchPaths</key><array><string>/tmp/input</string></array>
</dict></plist>
""",
        encoding="utf-8",
    )

    result = chunkyctl.audit_automation_surface(
        repo, home=tmp_path / "home", system_launchd_dirs=(), system_cron_files=(),
        crontab_text="", launchctl_text="",
    )
    assert result["verdict"] == "FAIL"
    assert "scheduled plist" in result["findings"][0]["reason"]


def test_automation_surface_expands_launchd_environment_variables(tmp_path):
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text((REPO / "backend/config/automation_policy.yaml").read_text(), encoding="utf-8")
    installed = tmp_path / "home/Library/LaunchAgents"
    installed.mkdir(parents=True)
    (installed / "generic.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>generic.writer</string>
<key>ProgramArguments</key><array>
<string>/bin/sh</string><string>-c</string>
<string>cd &quot;$REPO&quot; &amp;&amp; python opaque_writer.py</string>
</array>
<key>EnvironmentVariables</key><dict><key>REPO</key><string>{repo}</string></dict>
<key>StartInterval</key><integer>60</integer>
</dict></plist>
""",
        encoding="utf-8",
    )
    result = chunkyctl.audit_automation_surface(
        repo, home=tmp_path / "home", system_launchd_dirs=(), system_cron_files=(),
        crontab_text="", launchctl_text="",
    )
    assert result["verdict"] == "FAIL"
    assert "project identity" in result["findings"][0]["reason"]


def test_automation_surface_fails_closed_when_audit_commands_fail(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text((REPO / "backend/config/automation_policy.yaml").read_text(), encoding="utf-8")
    monkeypatch.setattr(
        chunkyctl,
        "_run_command",
        lambda cmd, **_kwargs: {
            "cmd": cmd, "returncode": 126, "stdout": "", "stderr": "permission denied"
        },
    )

    result = chunkyctl.audit_automation_surface(
        repo, home=tmp_path / "home", system_launchd_dirs=(), system_cron_files=(),
    )
    assert result["verdict"] == "FAIL"
    assert {item["source"] for item in result["findings"]} == {"crontab", "launchctl"}


def test_automation_surface_scans_injected_system_cron(tmp_path):
    repo = tmp_path / "repo"
    policy = repo / "backend" / "config" / "automation_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text((REPO / "backend/config/automation_policy.yaml").read_text(), encoding="utf-8")
    system_cron = tmp_path / "etc/crontab"
    system_cron.parent.mkdir(parents=True)
    system_cron.write_text(
        f"0 18 * * * root cd {repo} && scripts/daily_update.sh\n", encoding="utf-8"
    )

    result = chunkyctl.audit_automation_surface(
        repo, home=tmp_path / "home", system_launchd_dirs=(),
        system_cron_files=(system_cron,), crontab_text="", launchctl_text="",
    )
    assert result["verdict"] == "FAIL"
    assert result["findings"][0]["source"].startswith("system-cron:")
