"""PC 를 켜면 알아서 뜨도록 등록하기.

터미널을 열어 두는 것만으로는 '계속 켜져 있다'고 하기 어렵다. 창을 닫거나 로그아웃하면
꺼지고, PC 를 재부팅하면 다시 띄워야 한다는 걸 기억해야 한다.

OS 별로 방식이 다르므로 각각에 맞는 등록 파일을 만들어 주고, 실행할 명령을 알려준다.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

SERVICE_NAME = "paradogo"


def detect_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


@dataclass(slots=True)
class ServicePlan:
    """어떤 파일을 어디에 쓰고, 그다음 무엇을 실행해야 하는지."""

    os_name: str
    path: Path
    content: str
    steps: list[str]
    note: str = ""


def _python() -> str:
    return sys.executable or "python"


def render_systemd(workdir: Path, python: str, args: str) -> str:
    return f"""[Unit]
Description=파라다이스 도고 캐빈 취소표 감시
After=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={python} -m paradogo track --forever{args}
Restart=always
RestartSec=30
# 서비스로 돌 때는 창을 띄울 수 없다.
Environment=PARADOGO_HEADLESS=1

[Install]
WantedBy=default.target
"""


def render_launchd(workdir: Path, python: str, args: str) -> str:
    extra = "".join(
        f"\n    <string>{part}</string>" for part in args.split() if part
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.{SERVICE_NAME}.track</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>paradogo</string>
    <string>track</string>
    <string>--forever</string>{extra}
  </array>
  <key>WorkingDirectory</key>
  <string>{workdir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PARADOGO_HEADLESS</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{workdir}/paradogo.log</string>
  <key>StandardErrorPath</key>
  <string>{workdir}/paradogo.log</string>
</dict>
</plist>
"""


def render_windows_bat(workdir: Path, python: str, args: str) -> str:
    return f"""@echo off
rem 파라다이스 도고 캐빈 취소표 감시 — 로그인할 때 자동 실행
cd /d "{workdir}"
set PARADOGO_HEADLESS=1
"{python}" -m paradogo track --forever{args} >> "{workdir}\\paradogo.log" 2>&1
"""


def build_plan(
    workdir: Path,
    os_name: str | None = None,
    extra_args: str = "",
    python: str | None = None,
) -> ServicePlan:
    os_name = os_name or detect_os()
    python = python or _python()
    args = (" " + extra_args.strip()) if extra_args.strip() else ""
    home = Path.home()

    if os_name == "linux":
        path = home / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        return ServicePlan(
            os_name=os_name,
            path=path,
            content=render_systemd(workdir, python, args),
            steps=[
                "systemctl --user daemon-reload",
                f"systemctl --user enable --now {SERVICE_NAME}",
                f"systemctl --user status {SERVICE_NAME}",
                # 로그아웃해도 계속 돌게 하려면 이게 필요하다.
                "sudo loginctl enable-linger $USER",
            ],
            note="로그를 보려면: journalctl --user -u paradogo -f",
        )

    if os_name == "macos":
        path = home / "Library" / "LaunchAgents" / f"com.{SERVICE_NAME}.track.plist"
        return ServicePlan(
            os_name=os_name,
            path=path,
            content=render_launchd(workdir, python, args),
            steps=[
                f"launchctl unload {path} 2>/dev/null",
                f"launchctl load {path}",
                f"launchctl list | grep {SERVICE_NAME}",
            ],
            note=(
                "맥이 잠들면 감시도 멈춥니다. 시스템 설정 > 배터리(또는 에너지 절약)에서 "
                "'디스플레이가 꺼져도 자동으로 잠자지 않음'을 켜 두세요."
            ),
        )

    path = workdir / f"{SERVICE_NAME}-start.bat"
    task = (
        f'schtasks /create /tn "{SERVICE_NAME}" /tr "\\"{path}\\"" '
        f"/sc onlogon /rl highest /f"
    )
    return ServicePlan(
        os_name="windows",
        path=path,
        content=render_windows_bat(workdir, python, args),
        steps=[
            task,
            f'schtasks /run /tn "{SERVICE_NAME}"',
            f'schtasks /query /tn "{SERVICE_NAME}"',
        ],
        note=(
            "PC 가 절전으로 들어가면 감시도 멈춥니다. "
            "설정 > 전원 및 절전에서 '절전 모드: 안 함'으로 두세요.\n"
            "끄려면: schtasks /delete /tn \"paradogo\" /f"
        ),
    )


def install(plan: ServicePlan) -> Path:
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_text(plan.content, encoding="utf-8")
    if plan.os_name != "windows":
        plan.path.chmod(0o644)
    return plan.path


def describe(plan: ServicePlan, installed: bool) -> str:
    label = {"linux": "systemd (Linux)", "macos": "launchd (macOS)",
             "windows": "작업 스케줄러 (Windows)"}[plan.os_name]
    lines = [f"등록 방식: {label}"]
    lines.append(("파일을 만들었습니다: " if installed else "만들 파일: ") + str(plan.path))
    lines.append("")
    lines.append("이제 아래를 순서대로 실행하세요.")
    for step in plan.steps:
        lines.append(f"  {step}")
    if plan.note:
        lines.append("")
        lines.append(plan.note)
    lines.append("")
    lines.append("등록 후에는 `python -m paradogo status` 로 살아 있는지 확인하세요.")
    return "\n".join(lines)
