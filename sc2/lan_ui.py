"""Windows LAN lobby. The established room is handed to the existing game runner."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import queue
import select
import socket
import sys
import threading
import time

if sys.stdout is None or sys.stderr is None:
    log_path = Path(__file__).parent / "runs" / "lobby-ui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = log_path.open("a", encoding="utf-8", buffering=1)

from . import lan
from .policies import AI_CHOICES


CHOICES = {"真人操作": ("human", "default")}
CHOICES.update({f"AI · {'原版' if name == 'default' else name.upper()}": ("ai", name)
                for name in AI_CHOICES})
SPEED_CHOICES = {(f"≈{speed:g}×（流畅）" if speed in lan.NATIVE_SPEEDS and speed != 1 else f"{speed:g}×"): speed
                 for speed in lan.SPEEDS}


def check_selection(message, host):
    if (message.get("kind") != "lobby" or message.get("protocol") != 3
            or message.get("control") not in ("human", "ai")
            or not isinstance(message.get("ai"), str) or message["ai"] not in AI_CHOICES
            or type(message.get("ready")) is not bool
            or type(message.get("no_window")) is not bool
            or type(message.get("sound")) is not bool
            or (message["control"] == "human" and (message["ai"] != "default" or message["no_window"]))):
        raise ValueError("房间信息不兼容，请双方使用相同版本的联机大厅")
    speed = message.get("speed")
    if host:
        if type(speed) not in (int, float) or speed not in lan.SPEEDS:
            raise ValueError("房主的游戏速度无效")
        if type(message.get("rounds")) is not int or not 1 <= message["rounds"] <= 1000:
            raise ValueError("房主的局数必须在 1 到 1000 之间")
    elif speed is not None or message.get("rounds") is not None:
        raise ValueError("游戏速度和局数只能由房主设置")


def wait_ready(room, args, commands, events, cancelled):
    """Freeze each choice on ready; acknowledge start before handing over the socket."""
    host = args.mode == "host"
    local = dict(kind="lobby", protocol=3, control=args.control, ai=args.ai, ready=False,
                 speed=args.speed, rounds=args.rounds, sound=args.sound, no_window=args.no_window)
    check_selection(local, host)
    peer, starting = None, False
    room.send(local)
    last_ping = time.monotonic()
    while not cancelled.is_set():
        while not commands.empty():
            updated = commands.get_nowait()
            check_selection(updated, host)
            if local["ready"]:
                raise ValueError("准备后不能更换参战方式，请退出房间后重新连接")
            local = updated
            room.send(local)
        if select.select([room.socket], [], [], 0.1)[0]:
            message = room.receive(timeout=2)
            kind = message.get("kind")
            if kind == "lobby":
                check_selection(message, not host)
                if peer and peer["ready"]:
                    raise ValueError("对方在准备后更改了参战信息")
                peer = message
                events.put(("peer", peer))
            elif kind == "lobby_start" and args.mode == "join" and peer and peer["ready"] and local["ready"]:
                room.send(dict(kind="lobby_started"))
                return local
            elif kind == "lobby_started" and args.mode == "host" and starting:
                return local
            elif kind != "ping":
                raise ValueError("收到意外的房间消息，请双方使用联机大厅连接")
        now = time.monotonic()
        if now - room.last_seen > 20:
            raise lan.PeerDisconnected("对方已断开连接（20 秒没有响应）")
        if args.mode == "host" and local["ready"] and peer and peer["ready"] and not starting:
            room.send(dict(kind="lobby_start"))
            starting = True
        if not starting and now - last_ping >= 2:
            room.send(dict(kind="ping"))
            last_ping = now
    raise lan.PeerDisconnected("已退出房间")


class Session(threading.Thread):
    def __init__(self, args, events):
        super().__init__(daemon=True)
        self.args, self.events = args, events
        self.commands = queue.Queue()
        self.cancelled = threading.Event()
        self.transport = None

    def stop(self):
        self.cancelled.set()
        connection = self.transport
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def connect(self):
        args = self.args
        self.transport = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if args.mode == "host":
            with self.transport as listener:
                listener.bind((args.address, args.port))
                listener.listen(1)
                try:
                    addresses = sorted({address[4][0] for address in socket.getaddrinfo(
                        socket.gethostname(), None, socket.AF_INET) if not address[4][0].startswith("127.")})
                except OSError:
                    addresses = []
                self.events.put(("status", "房间已开启，等待加入。本机 IPv4：" + (" / ".join(addresses) or "请查看网络设置")))
                while not self.cancelled.is_set():
                    if select.select([listener], [], [], 0.2)[0]:
                        self.transport, _ = listener.accept()
                        break
        else:
            self.transport.settimeout(5)
            self.transport.connect((args.address, args.port))
        if self.cancelled.is_set():
            raise lan.PeerDisconnected("已退出房间")
        room = lan.Room(self.transport, args.timeout)
        room.authenticate(args.mode == "host", args.key)
        return room

    def run(self):
        try:
            room = self.connect()
            self.events.put(("connected", room.socket.getpeername()[0]))
            chosen = wait_ready(room, self.args, self.commands, self.events, self.cancelled)
            if self.cancelled.is_set():
                return
            self.args.control, self.args.ai = chosen["control"], chosen["ai"]
            self.args.speed = chosen["speed"]
            self.args.rounds = chosen["rounds"]
            self.args.sound = chosen["sound"]
            self.args.no_window = chosen["no_window"]
            self.args.windowed = self.args.windowed and not self.args.no_window
            self.args.output = Path(__file__).parent / "runs" / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-lobby-" + self.args.mode)
            self.events.put(("starting", "双方已准备，正在启动游戏。" +
                             ("本方 AI 在后台运行。" if self.args.no_window else "即将显示游戏画面。")))
            result = lan.play(self.args, room=room)
            if not self.cancelled.is_set():
                summary = json.loads((self.args.output / "series.json").read_text(encoding="utf-8"))
                if result:
                    detail = summary.get("error")
                    if not detail and summary.get("games"):
                        manifest = Path(summary["games"][-1]["manifest"])
                        detail = json.loads(manifest.read_text(encoding="utf-8")).get("error")
                    raise RuntimeError(f"对局未正常完成：{detail or summary['status']}\n记录：{self.args.output}")
                self.events.put(("status", f"对局结束，录像已保存：{self.args.output}"))
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(("error", str(exc)))
        finally:
            if self.transport is not None:
                self.transport.close()
            if self.cancelled.is_set():
                self.events.put(("status", "已退出房间，可以重新创建或加入。"))
            self.events.put(("done", None))


class Lobby:
    def __init__(self, root, options):
        import tkinter as tk
        from tkinter import ttk

        self.root, self.options = root, options
        self.session = None
        self.mode = None
        self.events = queue.Queue()
        self.connected = self.ready = self.closing = False
        root.title("三兵缩圈 · 联机大厅")
        root.minsize(660, 480)
        root.option_add("*Font", "{Microsoft YaHei UI} 10")
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", padding=(12, 7))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        self.address = tk.StringVar()
        self.port = tk.StringVar(value=str(options.port))
        self.choice = tk.StringVar(value=next(iter(CHOICES)))
        self.speed = tk.StringVar(value="1×")
        self.rounds = tk.StringVar(value="1")
        self.sound = tk.BooleanVar(value=True)
        self.no_window = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="创建房间，或输入房主的局域网 / VPN IPv4 地址加入。")
        self.local_status = tk.StringVar(value="未准备")
        self.peer_status = tk.StringVar(value="等待另一位玩家")
        main = ttk.Frame(root, padding=24)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        ttk.Label(main, text="三兵缩圈", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="联机大厅  /  双方准备后自动开战").grid(row=1, column=0, sticky="w", pady=(2, 18))

        connection = ttk.Frame(main)
        connection.grid(row=2, column=0, sticky="ew")
        connection.columnconfigure(0, weight=1)
        ttk.Label(connection, text="房主 IPv4（加入时填写）").grid(row=0, column=0, sticky="w")
        ttk.Label(connection, text="端口").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.ip_entry = ttk.Entry(connection, textvariable=self.address)
        self.ip_entry.grid(row=1, column=0, sticky="ew", pady=6)
        self.port_entry = ttk.Entry(connection, textvariable=self.port, width=8)
        self.port_entry.grid(row=1, column=1, padx=(12, 0))
        buttons = ttk.Frame(main)
        buttons.grid(row=3, column=0, sticky="w", pady=(4, 18))
        self.host_button = ttk.Button(buttons, text="创建房间", command=lambda: self.connect("host"))
        self.join_button = ttk.Button(buttons, text="加入房间", command=lambda: self.connect("join"))
        self.leave_button = ttk.Button(buttons, text="退出房间", command=self.leave, state="disabled")
        for button in (self.host_button, self.join_button, self.leave_button):
            button.pack(side="left", padx=(0, 8))

        players = ttk.Frame(main)
        players.grid(row=4, column=0, sticky="ew")
        players.columnconfigure((0, 1), weight=1, uniform="player")
        local = ttk.LabelFrame(players, text="本方", padding=14)
        local.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        peer = ttk.LabelFrame(players, text="对方", padding=14)
        peer.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.selector = ttk.Combobox(local, textvariable=self.choice, values=list(CHOICES), state="readonly", width=23)
        self.selector.pack(fill="x")
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.update_selection())
        self.hide_check = ttk.Checkbutton(local, text="不显示游戏画面（仅 AI）", variable=self.no_window,
                                          command=self.update_selection, state="disabled")
        self.hide_check.pack(anchor="w", pady=(8, 0))
        self.sound_check = ttk.Checkbutton(local, text="开启本方游戏声音", variable=self.sound,
                                           command=self.update_selection)
        self.sound_check.pack(anchor="w", pady=(6, 0))
        ttk.Label(local, textvariable=self.local_status).pack(anchor="w", pady=(10, 0))
        ttk.Label(peer, textvariable=self.peer_status, wraplength=240).pack(anchor="w")
        settings = ttk.Frame(main)
        settings.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(settings, text="对局速度（房主决定）").pack(side="left")
        self.speed_selector = ttk.Combobox(settings, textvariable=self.speed,
            values=list(SPEED_CHOICES), state="readonly", width=17)
        self.speed_selector.pack(side="left", padx=(10, 0))
        self.speed_selector.bind("<<ComboboxSelected>>", lambda _: self.update_selection())
        ttk.Label(settings, text="局数（房主决定）").pack(side="left", padx=(18, 0))
        self.rounds_selector = ttk.Spinbox(settings, textvariable=self.rounds, from_=1, to=1000,
                                          width=6, command=self.update_selection)
        self.rounds_selector.pack(side="left", padx=(10, 0))
        self.rounds_selector.bind("<FocusOut>", lambda _: self.update_selection())
        self.rounds_selector.bind("<Return>", lambda _: self.update_selection())
        self.ready_button = ttk.Button(main, text="准备", command=self.prepare, state="disabled")
        self.ready_button.grid(row=6, column=0, sticky="ew", pady=(14, 8))
        ttk.Label(main, text="准备后锁定设置；需要修改时可退出房间重新连接。\nAI 默认显示画面，可勾选后台运行；真人始终显示画面。\n慢速建议选择标注“流畅”的档位。", foreground="#555555").grid(row=7, column=0, sticky="w")
        ttk.Separator(main).grid(row=8, column=0, sticky="ew", pady=14)
        ttk.Label(main, textvariable=self.status, wraplength=600).grid(row=9, column=0, sticky="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def read_rounds(self):
        try:
            count = int(self.rounds.get())
            if 1 <= count <= 1000:
                return count
        except ValueError:
            pass
        raise ValueError("局数必须是 1 到 1000 之间的整数")

    def connect(self, mode):
        try:
            port = int(self.port.get())
            if not 1024 <= port <= 65531:
                raise ValueError("端口必须在 1024 到 65531 之间")
            address = "0.0.0.0" if mode == "host" else str(ipaddress.IPv4Address(self.address.get().strip()))
            if mode == "join" and (ipaddress.IPv4Address(address).is_unspecified or ipaddress.IPv4Address(address).is_multicast
                                   or address == "255.255.255.255"):
                raise ValueError("请输入房主可连接的 IPv4 地址")
        except ValueError as exc:
            self.status.set(f"地址或端口无效：{exc}")
            return
        try:
            rounds = self.read_rounds() if mode == "host" else None
        except ValueError as exc:
            self.status.set(str(exc))
            return
        control, ai = CHOICES[self.choice.get()]
        args = lan.arguments([mode, "--address", address, "--port", str(port), "--control", control,
                              "--ai", ai, "--version", self.options.version, "--yes",
                              *(["--speed", str(SPEED_CHOICES[self.speed.get()]), "--rounds", str(rounds)] if mode == "host" else []),
                              "--sound" if self.sound.get() else "--no-sound",
                              *(["--windowed"] if self.options.windowed else [])])
        args.no_window = self.no_window.get() and control == "ai"
        self.mode = mode
        self.speed_selector.configure(state="readonly" if mode == "host" else "disabled")
        self.rounds_selector.configure(state="normal" if mode == "host" else "disabled")
        if mode == "join":
            self.speed.set("等待房主")
            self.rounds.set("—")
        self.connected = self.ready = False
        self.local_status.set("未准备")
        self.peer_status.set("等待另一位玩家")
        self.status.set("正在创建房间…" if mode == "host" else "正在连接房主…")
        self.root.title("三兵缩圈 · 联机大厅 — " + ("房主" if mode == "host" else "加入方"))
        for widget in (self.host_button, self.join_button, self.ip_entry, self.port_entry):
            widget.configure(state="disabled")
        self.leave_button.configure(state="normal")
        self.session = Session(args, self.events)
        self.session.start()

    def update_selection(self, ready=False):
        if self.ready:
            return False
        control, ai = CHOICES[self.choice.get()]
        if control == "human":
            self.no_window.set(False)
        self.hide_check.configure(state="normal" if control == "ai" and not self.ready else "disabled")
        if self.session:
            try:
                rounds = self.read_rounds() if self.mode == "host" else None
            except ValueError as exc:
                self.status.set(str(exc))
                return False
            self.session.commands.put(dict(kind="lobby", protocol=3, control=control, ai=ai, ready=ready,
                no_window=self.no_window.get(), sound=self.sound.get(), rounds=rounds,
                speed=SPEED_CHOICES[self.speed.get()] if self.mode == "host" else None))
        return True

    def prepare(self):
        if not self.connected or self.ready or not self.update_selection(ready=True):
            return
        self.ready = True
        self.local_status.set("已准备")
        self.selector.configure(state="disabled")
        self.hide_check.configure(state="disabled")
        self.speed_selector.configure(state="disabled")
        self.rounds_selector.configure(state="disabled")
        self.sound_check.configure(state="disabled")
        self.ready_button.configure(state="disabled", text="已准备，等待对方")

    def leave(self):
        if self.session:
            self.session.stop()
            self.status.set("正在退出，等待游戏和连接关闭…")
            self.connected = False
            self.leave_button.configure(state="disabled")
            self.ready_button.configure(state="disabled")
            self.selector.configure(state="disabled")
            self.hide_check.configure(state="disabled")
            self.speed_selector.configure(state="disabled")
            self.rounds_selector.configure(state="disabled")
            self.sound_check.configure(state="disabled")

    def close(self):
        self.closing = True
        if self.session:
            self.leave()
        else:
            self.root.destroy()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind in ("status", "error", "starting"):
                self.status.set(value)
                if kind == "starting":
                    self.ready_button.configure(text="游戏进行中")
                elif kind == "error" and not self.closing:
                    from tkinter import messagebox
                    messagebox.showerror("联机未完成", value, parent=self.root)
            elif kind == "connected":
                if self.session and not self.session.cancelled.is_set():
                    self.connected = True
                    self.status.set(f"已连接 {value}。各自选择参战方式，然后点击准备。")
                    self.ready_button.configure(state="normal")
            elif kind == "peer":
                if self.mode == "join":
                    self.speed.set(next(label for label, speed in SPEED_CHOICES.items() if speed == value["speed"]))
                    self.rounds.set(str(value["rounds"]))
                label = next(label for label, selection in CHOICES.items() if selection == (value["control"], value["ai"]))
                self.peer_status.set(label + "\n\n" + ("已准备" if value["ready"] else "未准备"))
            elif kind == "done":
                self.session = None
                self.mode = None
                self.connected = self.ready = False
                if self.closing:
                    self.root.destroy()
                    return
                for widget in (self.host_button, self.join_button, self.ip_entry, self.port_entry):
                    widget.configure(state="normal")
                self.selector.configure(state="readonly")
                self.hide_check.configure(state="normal" if CHOICES[self.choice.get()][0] == "ai" else "disabled")
                self.speed_selector.configure(state="readonly")
                self.rounds_selector.configure(state="normal")
                self.sound_check.configure(state="normal")
                if self.rounds.get() == "—":
                    self.rounds.set("1")
                if self.speed.get() == "等待房主":
                    self.speed.set("1×")
                self.leave_button.configure(state="disabled")
                self.ready_button.configure(state="disabled", text="准备")
                self.local_status.set("未准备")
                self.peer_status.set("等待另一位玩家")
        self.root.after(100, self.poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description="三兵缩圈 Windows 联机大厅：建房、加入、选择真人 / AI、双方准备后开战")
    parser.add_argument("--version", default="latest")
    parser.add_argument("--port", type=int, default=14380)
    parser.add_argument("--windowed", action="store_true", help="用窗口模式显示游戏，默认全屏")
    options = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("联机大厅的可见游戏窗口当前需要 Windows")
    import tkinter as tk

    root = tk.Tk()
    Lobby(root, options)
    root.mainloop()


if __name__ == "__main__":
    main()
