"""Brings an AI's app to the front, for a click on a session that's waiting on you.

focus_pid(pid)      the window of that process or the nearest program above it (the Claude app for a session
                    in it, the terminal for one in a terminal)
focus_exe(names)    the same for a running program found by its file name, e.g. ("codex.exe",)
open_link(url)      hands an app's own link (claude://..., codex://...) to that app, which opens that chat

Both return True when a window came forward. Nothing is started, closed or sent anywhere.
macOS: the nearest .app above the process is opened with `open`, which brings a running app forward.
Linux: the nearest window above the process is raised with wmctrl when it's installed; links go to xdg-open.
"""

import os
import re
import subprocess
import sys

_APP_LINK = re.compile(r"^(claude|codex)://[A-Za-z0-9/?=&_.-]+$")


def open_link(url):
    """Only the Claude and Codex apps' own links, made of plain characters, are ever handed on."""
    if not _APP_LINK.match(url):
        return False
    if sys.platform == "win32":
        os.startfile(url)
        return True
    try:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        return subprocess.run([opener, url], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _dwm = ctypes.WinDLL("dwmapi")

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetWindow.restype = wintypes.HWND
    _u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.BringWindowToTop.argtypes = [wintypes.HWND]
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def _processes():
        """pid -> (parent pid, file name), from one snapshot of every process."""
        snap = _k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return {}
        procs = {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                procs[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
                ok = _k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snap)
        return procs

    def _windows():
        """pid -> its biggest ordinary window: shown, not owned, with a title. One the system has hidden (on another
        desktop, or put away while it's not in use) counts only when the program has no other."""
        best = {}

        def visit(hwnd, _):
            if not _u32.IsWindowVisible(hwnd) or _u32.GetWindow(hwnd, 4) or not _u32.GetWindowTextLengthW(hwnd):
                return True  # 4 = GW_OWNER: dialogs and popups belong to a main window
            if _u32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
                return True
            cloaked = wintypes.DWORD()
            _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            r = wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            rank = (not cloaked.value, (r.right - r.left) * (r.bottom - r.top) if not _u32.IsIconic(hwnd) else 1)
            if rank > best.get(pid.value, ((False, 0), None))[0]:
                best[pid.value] = (rank, hwnd)
            return True

        _u32.EnumWindows(_WNDENUMPROC(visit), 0)
        return {pid: hwnd for pid, (_, hwnd) in best.items()}

    def _raise(hwnd):
        if _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if _u32.SetForegroundWindow(hwnd) and _u32.GetForegroundWindow() == hwnd:
            return True
        # Windows only lets the program you're using change the front window; borrowing its input lets this one
        ours = _k32.GetCurrentThreadId()
        theirs = _u32.GetWindowThreadProcessId(_u32.GetForegroundWindow(), None)
        attached = theirs and theirs != ours and _u32.AttachThreadInput(ours, theirs, True)
        try:
            _u32.BringWindowToTop(hwnd)
            _u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _u32.AttachThreadInput(ours, theirs, False)
        return _u32.GetForegroundWindow() == hwnd

    def focus_pid(pid):
        procs, windows = _processes(), _windows()
        seen = set()
        while pid and pid not in seen and len(seen) < 16:
            seen.add(pid)
            if pid in windows:
                return _raise(windows[pid])
            pid = procs.get(pid, (0, ""))[0]
        return False

    def running(names):
        wanted = {n.lower() for n in names}
        return any(exe.lower() in wanted for _, exe in _processes().values())

    def focus_exe(names):
        wanted = {n.lower() for n in names}
        return any(focus_pid(pid) for pid, (_, exe) in _processes().items() if exe.lower() in wanted)

elif sys.platform == "darwin":
    def _ps(pid):
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-o", "comm=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            ppid, _, comm = out.partition(" ")
            return int(ppid), comm.strip()
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0, ""

    def _open(app):
        try:
            return subprocess.run(["open", "-a", app], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def focus_pid(pid):
        seen = set()
        while pid > 1 and pid not in seen and len(seen) < 16:
            seen.add(pid)
            ppid, comm = _ps(pid)
            if ".app/" in comm:  # the outermost app, not a helper inside it
                return _open(comm[:comm.index(".app/") + 4])
            pid = ppid
        return False

    def focus_exe(names):
        return any(_open(os.path.splitext(n)[0]) for n in names)

else:  # Linux: windows are found and raised with wmctrl, if it's installed (nothing happens without it)
    def _parent(pid):
        try:
            with open(f"/proc/{pid}/stat") as f:
                return int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return 0

    def _wmctrl(*args):
        try:
            return subprocess.run(["wmctrl", *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None

    def focus_pid(pid):
        listed = _wmctrl("-lp")
        if not listed or listed.returncode:
            return False
        windows = {}  # pid -> its first window
        for line in listed.stdout.splitlines():
            cols = line.split(None, 3)
            if len(cols) >= 3 and cols[2].isdigit():
                windows.setdefault(int(cols[2]), cols[0])
        seen = set()
        while pid > 1 and pid not in seen and len(seen) < 16:
            seen.add(pid)
            if pid in windows:
                done = _wmctrl("-i", "-a", windows[pid])
                return bool(done) and done.returncode == 0
            pid = _parent(pid)
        return False

    def focus_exe(names):
        wanted = {os.path.splitext(n)[0].lower() for n in names}
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    with open(f"/proc/{entry}/comm") as f:
                        if f.read().strip().lower() in wanted and focus_pid(int(entry)):
                            return True
                except OSError:
                    pass
        return False
