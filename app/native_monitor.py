"""Direct Windows readings for RX16 Control Center (replaces the old PowerShell + WMI sampler).

- Battery: the battery driver itself via DeviceIoControl (IOCTL_BATTERY_QUERY_STATUS / _INFORMATION)
- Total CPU load: GetSystemTimes
- Running programs: one NtQuerySystemInformation call (names, CPU time, private memory for every
  process, no need to open each one) + EnumWindows for "has a window" + cached file descriptions.

No extra processes, no WMI (WmiPrvSE was costing ~3.7 CPU-seconds a minute for the old sampler).
"""
import ctypes
import struct
import time
from ctypes import wintypes as W

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
version = ctypes.WinDLL("version")

INVALID_HANDLE = ctypes.c_void_p(-1).value


# ------------------------------------------------------------------------------------- battery
class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


GUID_DEVINTERFACE_BATTERY = GUID(0x72631E54, 0x78A4, 0x11D0, (ctypes.c_ubyte * 8)(0xBC, 0xF7, 0x00, 0xAA, 0x00, 0xB7, 0xB3, 0x2A))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID), ("Flags", W.DWORD), ("Reserved", ctypes.c_size_t)]


class BATTERY_QUERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryTag", ctypes.c_ulong), ("InformationLevel", ctypes.c_int), ("AtRate", ctypes.c_long)]


class BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("Capabilities", ctypes.c_ulong), ("Technology", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte * 3),
                ("Chemistry", ctypes.c_ubyte * 4), ("DesignedCapacity", ctypes.c_ulong),
                ("FullChargedCapacity", ctypes.c_ulong), ("DefaultAlert1", ctypes.c_ulong),
                ("DefaultAlert2", ctypes.c_ulong), ("CriticalBias", ctypes.c_ulong), ("CycleCount", ctypes.c_ulong)]


class BATTERY_WAIT_STATUS(ctypes.Structure):
    _fields_ = [("BatteryTag", ctypes.c_ulong), ("Timeout", ctypes.c_ulong), ("PowerState", ctypes.c_ulong),
                ("LowCapacity", ctypes.c_ulong), ("HighCapacity", ctypes.c_ulong)]


class BATTERY_STATUS(ctypes.Structure):
    _fields_ = [("PowerState", ctypes.c_ulong), ("Capacity", ctypes.c_ulong),
                ("Voltage", ctypes.c_ulong), ("Rate", ctypes.c_long)]


IOCTL_BATTERY_QUERY_TAG = 0x294040
IOCTL_BATTERY_QUERY_INFORMATION = 0x294044
IOCTL_BATTERY_QUERY_STATUS = 0x29404C
BATTERY_POWER_ON_LINE, BATTERY_CHARGING = 0x1, 0x4
BATTERY_UNKNOWN_RATE = -0x80000000

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), W.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]
kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD,
                                     ctypes.POINTER(W.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class Battery:
    def __init__(self):
        self.h = None
        self.tag = 0
        self.full = 0
        self.info_at = 0

    def _open(self):
        hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID_DEVINTERFACE_BATTERY), None, None, 0x2 | 0x10)
        if not hdev or hdev == INVALID_HANDLE:
            return False
        try:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(GUID_DEVINTERFACE_BATTERY), 0, ctypes.byref(did)):
                return False
            need = W.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6   # cbSize
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), buf, need, None, None):
                return False
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        h = kernel32.CreateFileW(path, 0xC0000000, 0x3, None, 3, 0x80, None)
        if not h or h == INVALID_HANDLE:
            return False
        wait, tag, n = ctypes.c_ulong(0), ctypes.c_ulong(0), W.DWORD()
        if not kernel32.DeviceIoControl(h, IOCTL_BATTERY_QUERY_TAG, ctypes.byref(wait), 4, ctypes.byref(tag), 4, ctypes.byref(n), None) or not tag.value:
            kernel32.CloseHandle(h)
            return False
        self.h, self.tag, self.info_at = h, tag.value, 0
        return True

    def _close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
        self.h = None

    def read(self):
        """dict like the old sampler: online, charging, remaining, full, charge, discharge, voltage (mWh / mW / mV)."""
        for _ in range(2):
            if not self.h and not self._open():
                return None
            n = W.DWORD()
            if time.time() - self.info_at > 60:   # full-charge capacity changes slowly
                q = BATTERY_QUERY_INFORMATION(self.tag, 0, 0)        # BatteryInformation
                bi = BATTERY_INFORMATION()
                if kernel32.DeviceIoControl(self.h, IOCTL_BATTERY_QUERY_INFORMATION, ctypes.byref(q), ctypes.sizeof(q),
                                            ctypes.byref(bi), ctypes.sizeof(bi), ctypes.byref(n), None):
                    self.full, self.info_at = bi.FullChargedCapacity, time.time()
            ws = BATTERY_WAIT_STATUS(self.tag, 0, 0, 0, 0)
            st = BATTERY_STATUS()
            if kernel32.DeviceIoControl(self.h, IOCTL_BATTERY_QUERY_STATUS, ctypes.byref(ws), ctypes.sizeof(ws),
                                        ctypes.byref(st), ctypes.sizeof(st), ctypes.byref(n), None):
                rate = 0 if st.Rate == BATTERY_UNKNOWN_RATE else st.Rate
                return {
                    "online": bool(st.PowerState & BATTERY_POWER_ON_LINE),
                    "charging": bool(st.PowerState & BATTERY_CHARGING),
                    "remaining": int(st.Capacity), "full": int(self.full),
                    "charge": max(0, rate), "discharge": max(0, -rate), "voltage": int(st.Voltage),
                }
            self._close()          # tag changed (battery re-enumerated): reopen once
        return None


# ------------------------------------------------------------------------------------- CPU load
class _FT(ctypes.Structure):
    _fields_ = [("v", ctypes.c_ulonglong)]


class CpuLoad:
    def __init__(self):
        self.prev = None

    def read(self):
        i, k, u = _FT(), _FT(), _FT()
        kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
        cur = (i.v, k.v + u.v)                      # kernel time already includes idle
        if not self.prev:
            self.prev = cur
            return 0
        di, dt = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return max(0, min(100, round(100 * (dt - di) / dt))) if dt > 0 else 0


# ------------------------------------------------------------------------------------- processes
ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, W.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [W.HWND]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.GetWindow.argtypes = [W.HWND, ctypes.c_uint]
user32.GetWindow.restype = W.HWND
version.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, ctypes.c_void_p]
version.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(W.UINT)]


def _file_strings(path):
    """(FileDescription, CompanyName) from an exe's version resource."""
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return "", ""
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return "", ""
        p, n = ctypes.c_void_p(), W.UINT()
        langs = []
        if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(p), ctypes.byref(n)) and n.value >= 4:
            lang, cp = struct.unpack_from("<HH", ctypes.string_at(p.value, 4))
            langs.append(f"{lang:04x}{cp:04x}")
        langs += ["040904b0", "040904e4", "000004b0"]

        def get(key):
            for l in langs:
                if version.VerQueryValueW(data, f"\\StringFileInfo\\{l}\\{key}", ctypes.byref(p), ctypes.byref(n)) and n.value:
                    return ctypes.wstring_at(p.value, n.value).rstrip("\0").strip()
            return ""
        return get("FileDescription"), get("CompanyName")
    except Exception:
        return "", ""


class Processes:
    def __init__(self):
        self.buf = ctypes.create_string_buffer(1 << 20)
        self.prev = {}            # pid -> (create_time, cpu_100ns)
        self.prev_t = None
        self.path_info = {}       # exe path -> (desc, company)
        self.pid_path = {}        # (pid, create_time) -> path
        self.cores = max(1, __import__("os").cpu_count() or 1)

    def _snapshot(self):
        need = ctypes.c_ulong()
        while True:
            st = ntdll.NtQuerySystemInformation(5, self.buf, len(self.buf), ctypes.byref(need))   # SystemProcessInformation
            if st == 0:
                break
            if (st & 0xFFFFFFFF) == 0xC0000004:   # STATUS_INFO_LENGTH_MISMATCH
                self.buf = ctypes.create_string_buffer(max(need.value + 65536, len(self.buf) * 2))
                continue
            return []
        base = ctypes.addressof(self.buf)
        raw = self.buf.raw
        out, off = [], 0
        while True:
            nxt, = struct.unpack_from("<I", raw, off)
            ws_priv, = struct.unpack_from("<q", raw, off + 8)
            create, user, kern = struct.unpack_from("<qqq", raw, off + 32)
            nlen, = struct.unpack_from("<H", raw, off + 56)
            nptr, = struct.unpack_from("<Q", raw, off + 64)
            pid, = struct.unpack_from("<Q", raw, off + 80)
            name = raw[nptr - base: nptr - base + nlen].decode("utf-16-le", "replace") if nptr and nlen else ""
            if pid:
                out.append((int(pid), name, create, user + kern, ws_priv))
            if not nxt:
                break
            off += nxt
        return out

    @staticmethod
    def _windows():
        titles = {}

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # GW_OWNER
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    pid = W.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value not in titles:
                        b = ctypes.create_unicode_buffer(n + 1)
                        user32.GetWindowTextW(hwnd, b, n + 1)
                        titles[pid.value] = b.value
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return titles

    def _info(self, pid, create):
        key = (pid, create)
        path = self.pid_path.get(key)
        if path is None:
            path = ""
            h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                b, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
                    path = b.value
                kernel32.CloseHandle(h)
            self.pid_path[key] = path
        if not path:
            return "", ""
        if path not in self.path_info:
            self.path_info[path] = _file_strings(path)
        return self.path_info[path]

    def read(self):
        now = time.perf_counter()
        snap = self._snapshot()
        wins = self._windows()
        dt = (now - self.prev_t) * 1e7 * self.cores if self.prev_t else 0   # 100-ns units of all cores
        alive, procs = {}, []
        for pid, name, create, cpu, ws in snap:
            p = self.prev.get(pid)
            used = cpu - p[1] if p and p[0] == create else 0
            alive[pid] = (create, cpu)
            desc, co = self._info(pid, create)
            nm = name[:-4] if name.lower().endswith(".exe") else name
            procs.append({
                "pid": pid, "name": nm or "System",
                "cpu": round(100 * used / dt, 1) if dt > 0 else 0.0,
                "mem": round(ws / 1048576, 1),
                "win": pid in wins, "title": wins.get(pid), "desc": desc, "co": co,
            })
        self.prev, self.prev_t = alive, now
        live = set(alive)
        self.pid_path = {k: v for k, v in self.pid_path.items() if k[0] in live}
        return procs


if __name__ == "__main__":
    b, c, p = Battery(), CpuLoad(), Processes()
    c.read(); p.read(); time.sleep(2)
    t = time.perf_counter()
    bat, cpu, procs = b.read(), c.read(), p.read()
    print("battery:", bat)
    print("cpu:", cpu, "%")
    top = sorted(procs, key=lambda x: -x["cpu"])[:6]
    print("procs:", len(procs), "with windows:", sum(x["win"] for x in procs))
    for x in top:
        print("  ", x)
    print(f"one full sample took {1000 * (time.perf_counter() - t):.1f} ms")
