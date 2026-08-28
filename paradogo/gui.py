"""창으로 쓰는 화면.

명령어를 치지 않고도 설정하고, 감시를 켜고 끄고, 지금 무슨 일이 일어나는지
로그로 볼 수 있게 한다.

구조는 단순하다. 실제 일(브라우저 조작·감시)은 백그라운드 스레드에서 돌고,
화면은 큐를 통해서만 그 결과를 받는다. tkinter 위젯은 메인 스레드에서만 만질 수
있기 때문에, 스레드에서 화면을 직접 건드리지 않고 전부 큐로 넘긴다.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from .clock import now_kst
from .config import Config
from .errors import ParadogoError
from .notify import Notifier
from .selectors import SelectorMap
from .supervisor import Supervisor, heartbeat_path, read_heartbeat
from .wizard import Prompter, run_wizard

log = logging.getLogger(__name__)

POLL_MS = 120           # 큐를 확인하는 주기
MAX_LOG_LINES = 2000    # 오래 켜둬도 메모리가 늘지 않게


# ---------------------------------------------------------------- 로그 배관


class QueueLogHandler(logging.Handler):
    """로그를 화면으로 보내기 위한 통로."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put(("log", record.levelno, self.format(record)))
        except Exception:  # 로그 때문에 프로그램이 죽으면 안 된다
            pass


def level_tag(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    return "info"


# ---------------------------------------------------------------- 대화상자


@dataclass(slots=True)
class _Request:
    """스레드가 메인 스레드에 물어볼 내용."""

    kind: str            # ask | ask_yes | wait
    message: str
    default: Any = None
    done: threading.Event = None  # type: ignore[assignment]
    answer: Any = None


class DialogPrompter(Prompter):
    """마법사의 물음을 창으로 띄운다.

    마법사는 워커 스레드에서 도는데 tkinter 대화상자는 메인 스레드에서만 뜬다.
    그래서 요청을 큐에 넣고 답이 올 때까지 기다린다.
    """

    def __init__(self, sink: queue.Queue) -> None:
        self.sink = sink

    def _request(self, kind: str, message: str, default: Any = None) -> Any:
        req = _Request(kind=kind, message=message, default=default, done=threading.Event())
        self.sink.put(("prompt", req))
        req.done.wait()
        return req.answer

    def ask(self, prompt: str, default: str = "") -> str:
        answer = self._request("ask", prompt, default)
        return (answer or default or "").strip()

    def ask_yes(self, prompt: str, default: bool = True) -> bool:
        answer = self._request("ask_yes", prompt, default)
        return default if answer is None else bool(answer)

    async def wait(self, message: str) -> None:
        # 브라우저를 만지는 동안 이벤트 루프가 멈추면 안 되므로 별도 스레드에서 기다린다.
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._request("wait", message)
        )

    def info(self, message: str) -> None:
        self.sink.put(("log", logging.INFO, message.rstrip()))


# ---------------------------------------------------------------- 본체


class App(tk.Tk):
    def __init__(self, config_path: Path, selectors_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.selectors_path = selectors_path
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.supervisor: Supervisor | None = None
        self.busy = False

        self.title("파라다이스 도고 캐빈 예약 도우미")
        self.geometry("900x620")
        self.minsize(720, 480)

        self._build()
        self._attach_logging()
        self.after(POLL_MS, self._drain)
        self.after(1000, self._refresh_status)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_target()

    # ------------------------------------------------------------ 화면 구성

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")

        self.state_var = tk.StringVar(value="○ 멈춤")
        ttk.Label(top, textvariable=self.state_var, font=("", 15, "bold")).pack(anchor="w")

        self.target_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.target_var, foreground="#555").pack(anchor="w", pady=(2, 0))

        self.detail_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.detail_var, foreground="#555").pack(anchor="w")

        bar = ttk.Frame(self, padding=(12, 4))
        bar.pack(fill="x")

        self.btn_watch = ttk.Button(bar, text="감시 시작", command=self._toggle_watch)
        self.btn_watch.pack(side="left")

        self.btn_setup = ttk.Button(bar, text="설정하기", command=self._run_setup)
        self.btn_setup.pack(side="left", padx=(8, 0))

        self.btn_scan = ttk.Button(bar, text="지금 조회", command=self._run_scan)
        self.btn_scan.pack(side="left", padx=(8, 0))

        self.btn_doctor = ttk.Button(bar, text="점검", command=self._run_doctor)
        self.btn_doctor.pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="로그 지우기", command=self._clear_log).pack(side="right")

        self.log = scrolledtext.ScrolledText(
            self, wrap="word", state="disabled", font=("Consolas", 10), height=20
        )
        self.log.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log.tag_config("info", foreground="#222")
        self.log.tag_config("warn", foreground="#b26a00")
        self.log.tag_config("error", foreground="#c0392b")
        self.log.tag_config("good", foreground="#1e7e34")

    def _attach_logging(self) -> None:
        handler = QueueLogHandler(self.events)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

    # ------------------------------------------------------------ 로그 출력

    def write(self, text: str, tag: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n", tag)
        # 오래 켜둬도 메모리가 계속 늘지 않게 앞쪽을 잘라낸다.
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ 큐 처리

    def _drain(self) -> None:
        try:
            while True:
                item = self.events.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, levelno, text = item
                    self.write(text, level_tag(levelno))
                elif kind == "prompt":
                    self._answer(item[1])
                elif kind == "done":
                    self._finish(item[1])
        except queue.Empty:
            pass
        self.after(POLL_MS, self._drain)

    def _answer(self, req: _Request) -> None:
        """워커가 물어본 것에 창으로 답한다. (메인 스레드)"""
        try:
            if req.kind == "ask":
                req.answer = simpledialog.askstring(
                    "설정", req.message, initialvalue=req.default or "", parent=self
                )
            elif req.kind == "ask_yes":
                req.answer = messagebox.askyesno("확인", req.message, parent=self)
            else:  # wait
                messagebox.showinfo(
                    "브라우저에서 해주세요",
                    req.message + "\n\n다 하셨으면 [확인]을 누르세요.",
                    parent=self,
                )
                req.answer = True
        finally:
            req.done.set()

    # ------------------------------------------------------------ 작업 실행

    def _busy(self, on: bool) -> None:
        self.busy = on
        state = "disabled" if on else "normal"
        for btn in (self.btn_setup, self.btn_scan, self.btn_doctor):
            btn.configure(state=state)

    def _spawn(self, name: str, fn: Callable[[], None]) -> None:
        """한 번에 하나씩만 돌린다. 브라우저 세션이 겹치면 서로 방해한다."""
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("잠시만요", "다른 작업이 아직 돌고 있습니다.", parent=self)
            return

        def runner() -> None:
            try:
                fn()
            except ParadogoError as exc:
                self.events.put(("log", logging.ERROR, str(exc)))
            except Exception as exc:
                self.events.put(("log", logging.ERROR, f"예상치 못한 오류: {exc}"))
                self.events.put(("log", logging.ERROR, traceback.format_exc()))
            finally:
                self.events.put(("done", name))

        self._busy(True)
        self.worker = threading.Thread(target=runner, name=name, daemon=True)
        self.worker.start()

    def _finish(self, name: str) -> None:
        self._busy(False)
        if name == "watch":
            self.supervisor = None
            self.btn_watch.configure(text="감시 시작")
            self.write("감시를 멈췄습니다.", "warn")
        self._refresh_status()

    def _load(self) -> tuple[Config, SelectorMap]:
        cfg = Config.load(self.config_path)
        smap = SelectorMap.load(self.selectors_path)
        return cfg, smap

    # ------------------------------------------------------------ 각 버튼

    def _run_setup(self) -> None:
        raw = simpledialog.askstring(
            "설정",
            "잡고 싶은 체크인 날짜를 적어주세요.\n(YYYY-MM-DD, 여러 개면 쉼표로)",
            initialvalue="2026-09-19",
            parent=self,
        )
        if not raw:
            return
        try:
            dates = sorted({date.fromisoformat(d.strip()) for d in raw.split(",") if d.strip()})
        except ValueError:
            messagebox.showerror("설정", "날짜 형식이 잘못됐습니다. 예: 2026-09-19", parent=self)
            return

        nights_raw = simpledialog.askstring(
            "설정", "몇 박으로 잡을까요?\n(2박 우선하고 안 되면 1박이면 '2,1')",
            initialvalue="1", parent=self,
        )
        try:
            nights = [int(n) for n in (nights_raw or "1").replace(" ", ",").split(",") if n.strip()]
        except ValueError:
            messagebox.showerror("설정", "박수는 숫자여야 합니다.", parent=self)
            return
        nights = [n for n in dict.fromkeys(nights) if n >= 1] or [1]

        zones_raw = simpledialog.askstring(
            "설정", "희망 구역 (A~H, 우선순위 순).\n상관없으면 비워두세요.",
            initialvalue="", parent=self,
        )
        zones = [z.strip().upper() for z in (zones_raw or "").replace(" ", ",").split(",") if z.strip()]

        self.write("설정을 시작합니다. 브라우저 창이 뜨면 안내대로 따라와 주세요.", "good")
        prompter = DialogPrompter(self.events)

        def work() -> None:
            base = (
                Config.load(self.config_path)
                if self.config_path.exists()
                else Config.from_dict(
                    {"account": {}, "target": {"check_in_dates": [d.isoformat() for d in dates]}}
                )
            )
            base.target.check_in_dates = dates
            base.run.headless = False  # 사람이 직접 클릭해야 한다
            asyncio.run(
                run_wizard(
                    base, self.config_path, self.selectors_path,
                    dates, nights, zones, [], prompter=prompter,
                )
            )
            self.events.put(("log", logging.INFO, "설정이 끝났습니다. [지금 조회]로 확인해 보세요."))

        self._spawn("setup", work)

    def _run_scan(self) -> None:
        from .scan import render_scan, run_scan

        def work() -> None:
            cfg, smap = self._load()
            snapshot = asyncio.run(run_scan(cfg, smap))
            targets = {d.isoformat() for d in cfg.target.check_in_dates}
            for line in render_scan(snapshot, targets, color=False).splitlines():
                self.events.put(("log", logging.INFO, line))
            if not snapshot.slots:
                self.events.put((
                    "log", logging.WARNING,
                    "재고를 하나도 읽지 못했습니다. [설정하기]를 다시 하면서 "
                    "2단계에서 예약 달력이 실제로 보이는 화면까지 들어가 주세요.",
                ))

        self.write("지금 예약 가능한 날짜를 확인합니다…")
        self._spawn("scan", work)

    def _run_doctor(self) -> None:
        from .cli import check_environment, check_network

        def work() -> None:
            problems = check_environment()
            if not self.config_path.exists():
                self.events.put((
                    "log", logging.WARNING,
                    f"아직 설정하지 않았습니다 ({self.config_path} 없음). [설정하기]를 먼저 누르세요.",
                ))
                return
            cfg, smap = self._load()
            problems += check_network(cfg.site.base_url)
            self.events.put(("log", logging.INFO, f"대상 날짜: "
                             f"{', '.join(d.isoformat() for d in cfg.target.check_in_dates)}"))
            self.events.put(("log", logging.INFO, f"박수: "
                             f"{', '.join(f'{n}박' for n in cfg.target.nights_options)}"))
            missing = smap.missing(
                ["login.id_input", "login.pw_input", "login.submit", "login.success_marker",
                 "booking.day_cell", "booking.room_card", "booking.room_reserve_button",
                 "payment.marker"]
            )
            if missing:
                self.events.put(("log", logging.WARNING,
                                 f"비어 있는 셀렉터: {', '.join(missing)}"))
            state = cfg.run.storage_state
            self.events.put((
                "log", logging.INFO,
                f"로그인 세션: {'있음' if state.exists() else '없음 — [설정하기]를 먼저'}",
            ))
            for item in problems:
                self.events.put(("log", logging.WARNING, f"확인 필요: {item}"))
            if not problems:
                self.events.put(("log", logging.INFO, "문제 없습니다."))

        self.write("점검합니다…")
        self._spawn("doctor", work)

    def _toggle_watch(self) -> None:
        if self.supervisor is not None:
            self.write("감시를 멈추는 중입니다… (진행 중인 확인이 끝나면 멈춥니다)", "warn")
            self.supervisor.request_stop()
            self.btn_watch.configure(text="멈추는 중…", state="disabled")
            return

        if not self.config_path.exists():
            messagebox.showinfo("먼저 설정하세요", "[설정하기]를 먼저 눌러 주세요.", parent=self)
            return

        def work() -> None:
            cfg, smap = self._load()
            supervisor = Supervisor(cfg, smap, Notifier(cfg.notify))
            self.supervisor = supervisor
            asyncio.run(supervisor.run())

        self.write("감시를 시작합니다. 취소가 나오면 바로 잡으러 갑니다.", "good")
        self.btn_watch.configure(text="감시 중지")
        self._spawn("watch", work)

    # ------------------------------------------------------------ 상태 표시

    def _show_target(self) -> None:
        if not self.config_path.exists():
            self.target_var.set("아직 설정하지 않았습니다 — [설정하기]를 눌러 주세요.")
            return
        try:
            cfg = Config.load(self.config_path)
        except ParadogoError as exc:
            self.target_var.set(f"설정을 읽지 못했습니다: {exc}")
            return
        dates = ", ".join(d.isoformat() for d in cfg.target.check_in_dates) or "(없음)"
        nights = ", ".join(f"{n}박" for n in cfg.target.nights_options)
        zones = ", ".join(cfg.target.zones) or "전체"
        self.target_var.set(f"대상: {dates}   ·   {nights}   ·   구역 {zones}")

    def _refresh_status(self) -> None:
        try:
            if self.config_path.exists():
                cfg = Config.load(self.config_path)
                beat = read_heartbeat(heartbeat_path(cfg))
            else:
                beat = None
        except Exception:
            beat = None

        if beat is not None and beat.alive and beat.state == "starting":
            # 브라우저를 띄우고 로그인하는 중. 아직 '감시 중'이 아니다.
            self.state_var.set("◐ 준비 중")
            self.detail_var.set("브라우저를 띄우고 로그인하는 중입니다…")
            self.btn_watch.configure(text="감시 중지", state="normal")
        elif beat is not None and beat.alive:
            self.state_var.set("● 감시 중")
            self.detail_var.set(
                f"{beat.round}회 확인 · 재고 {beat.slots}칸 중 {beat.available}칸 예약가능 "
                f"· 누적 취소 {beat.opened_total}건 · 재시작 {beat.restarts}회"
            )
            self.btn_watch.configure(text="감시 중지", state="normal")
        elif beat is not None and beat.state == "needs_login":
            self.state_var.set("▲ 로그인이 필요합니다")
            self.detail_var.set("[설정하기]를 다시 눌러 로그인해 주세요.")
        else:
            self.state_var.set("○ 멈춤")
            self.detail_var.set("[감시 시작]을 누르면 취소를 지켜봅니다.")
            if self.supervisor is None:
                self.btn_watch.configure(text="감시 시작", state="normal")

        self._show_target()
        self.after(2000, self._refresh_status)

    def _on_close(self) -> None:
        if self.supervisor is not None:
            if not messagebox.askyesno(
                "종료", "감시가 돌고 있습니다. 정말 끄시겠어요?", parent=self
            ):
                return
            self.supervisor.request_stop()
        self.destroy()


def run_gui(config_path: Path, selectors_path: Path) -> int:
    app = App(config_path, selectors_path)
    app.write("파라다이스 도고 캐빈 예약 도우미입니다.", "good")
    app.write("처음이시면 [설정하기]부터 눌러 주세요.")
    app.mainloop()
    return 0
