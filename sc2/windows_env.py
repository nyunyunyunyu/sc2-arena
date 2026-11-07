"""Native Windows client launch options; reuse SC2Env's LAN lifecycle."""
import ctypes
from ctypes import wintypes

from pysc2.lib import portspicker

from .arena_env import ArenaEnv


def window_options(visible):
    return dict(window_size=(1600, 900) if visible else (640, 480),
                window_loc=(40, 40),
                # Limit native drawing, independently of simulation and AI cadence.
                extra_args=[f"-frameratecap={120 if visible else 30}", "-frameratecapglue=30"])


def set_client_window(pid, title=None):
    """Hide or label only visible windows owned by this launched SC2 process."""
    if not pid:
        raise RuntimeError("SC2 client exited before window setup")
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    windows = []

    @callback_type
    def collect(window, unused):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(window):
            windows.append(window)
        return True

    if not api.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if title and not windows:
        raise RuntimeError("No visible human SC2 window found")
    for window in windows:
        if title:
            if not api.SetWindowTextW(window, title):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            # PySC2 4.0 discards Popen startupinfo; hide the actual created window.
            api.ShowWindow(window, 0)
            if api.IsWindowVisible(window):
                raise RuntimeError("Could not hide the AI SC2 window")


class WindowsArenaEnv(ArenaEnv):
    def __init__(self, *, human_slot=None, **kwargs):
        self.native_human_slot = human_slot
        try:
            super().__init__(**kwargs)
        except BaseException:
            self.close()
            raise

    def _launch_game(self):
        # Only process presentation differs; map/join/observation logic stays upstream.
        self._ports = portspicker.pick_unused_ports(self._num_agents * 2)
        self._sc2_procs = []
        for slot, interface in enumerate(self._interface_options):
            process = self._run_config.start(
                extra_ports=self._ports, want_rgb=interface.HasField("render"),
                **window_options(slot == self.native_human_slot))
            self._sc2_procs.append(process)
            if slot != self.native_human_slot:
                set_client_window(process.pid)
        self._controllers = [process.controller for process in self._sc2_procs]

    def reset(self):
        timesteps = super().reset()
        for slot, process in enumerate(self._sc2_procs):
            if slot != self.native_human_slot:
                set_client_window(process.pid)
        if self.native_human_slot is not None:
            self.set_human_title("三兵缩圈 — 玩家窗口")
        return timesteps

    def set_human_title(self, title):
        set_client_window(self._sc2_procs[self.native_human_slot].pid, title)
