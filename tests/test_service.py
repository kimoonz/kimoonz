from pathlib import Path

import pytest

from paradogo.service import build_plan, describe, detect_os, install

WORKDIR = Path("/home/me/kimoonz")


@pytest.mark.parametrize("os_name", ["linux", "macos", "windows"])
def test_plan_has_a_path_content_and_steps(os_name):
    plan = build_plan(WORKDIR, os_name, python="/usr/bin/python3")
    assert plan.os_name == os_name
    assert plan.content.strip()
    assert plan.steps
    assert str(WORKDIR) in plan.content


def test_systemd_unit_restarts_and_runs_forever():
    content = build_plan(WORKDIR, "linux", python="/py").content
    assert "Restart=always" in content
    assert "track --forever" in content
    assert "PARADOGO_HEADLESS=1" in content


def test_launchd_plist_keeps_alive_and_is_valid_xml():
    import xml.dom.minidom

    content = build_plan(WORKDIR, "macos", python="/py").content
    xml.dom.minidom.parseString(content)  # 형식이 깨졌으면 여기서 터진다
    assert "<key>KeepAlive</key>" in content


def test_launchd_plist_carries_extra_arguments():
    content = build_plan(WORKDIR, "macos", "--date 2026-09-19 --nights 1", python="/py").content
    assert "<string>--date</string>" in content
    assert "<string>2026-09-19</string>" in content


def test_windows_bat_changes_directory_and_logs():
    content = build_plan(WORKDIR, "windows", python="C:/py.exe").content
    assert "cd /d" in content
    assert "paradogo.log" in content
    assert "--forever" in content


def test_windows_steps_register_a_logon_task():
    plan = build_plan(WORKDIR, "windows", python="C:/py.exe")
    assert any("schtasks /create" in step and "onlogon" in step for step in plan.steps)


def test_extra_args_are_appended_to_the_command():
    content = build_plan(WORKDIR, "linux", "--date 2026-09-19 --zones C,D", python="/py").content
    assert "track --forever --date 2026-09-19 --zones C,D" in content


def test_no_extra_args_leaves_no_trailing_space():
    content = build_plan(WORKDIR, "linux", "   ", python="/py").content
    assert "ExecStart=/py -m paradogo track --forever\n" in content


def test_install_writes_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    plan = build_plan(WORKDIR, "linux", python="/py")
    written = install(plan)
    assert written.exists()
    assert "Restart=always" in written.read_text(encoding="utf-8")


def test_describe_lists_the_next_steps():
    plan = build_plan(WORKDIR, "linux", python="/py")
    text = describe(plan, installed=True)
    assert "만들었습니다" in text
    assert "systemctl --user enable --now paradogo" in text
    assert "status" in text


def test_describe_warns_about_sleep_on_desktop_os():
    assert "절전" in describe(build_plan(WORKDIR, "windows", python="/py"), False)
    assert "잠들" in describe(build_plan(WORKDIR, "macos", python="/py"), False)


def test_detect_os_returns_a_known_value():
    assert detect_os() in {"linux", "macos", "windows"}
