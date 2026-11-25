"""Hardware sensors for the Ryzen 7 H 255 / Radeon 780M APU via AMD ADL (atiadlxx.dll).

Works without admin: ADL's PM-log exposes CPU + GPU temperatures, clocks, power and GPU activity
on AMD APUs. Sensor ids follow ADL_PMLOG_SENSORS in the ADL SDK (adl_defines.h).
"""
import ctypes
import subprocess
import threading

PM = {
    "gpu_clk": 1, "mem_clk": 2, "soc_clk": 3, "gpu_temp_edge": 8, "soc_volt": 16,
    "gpu_load": 19, "gpu_volt": 21, "apu_power": 23, "gpu_temp": 28, "soc_temp": 29,
    "gpu_power": 30, "cpu_temp": 32, "cpu_power_raw": 33, "cpu_clk": 34,
}


class _Sensor(ctypes.Structure):
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _PMLog(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("sensors", _Sensor * 256)]


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


_ALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)


class Sensors:
    def __init__(self):
        self.lock = threading.Lock()
        self.ctx = ctypes.c_void_p()
        self.adapter = None
        self._buffers = []
        self._alloc = _ALLOC(self._malloc)
        self.ram_info = self._ram_info()
        try:
            self.adl = ctypes.WinDLL("atiadlxx.dll")
            if self.adl.ADL2_Main_Control_Create(self._alloc, 1, ctypes.byref(self.ctx)) == 0:
                n = ctypes.c_int()
                self.adl.ADL2_Adapter_NumberOfAdapters_Get(self.ctx, ctypes.byref(n))
                for i in range(n.value):
                    if self._query(i) is not None:
                        self.adapter = i
                        break
        except OSError:
            self.adl = None

    def _malloc(self, size):
        buf = ctypes.create_string_buffer(size)
        self._buffers.append(buf)
        return ctypes.addressof(buf)

    def _query(self, idx):
        out = _PMLog()
        out.size = ctypes.sizeof(_PMLog)
        if self.adl.ADL2_New_QueryPMLogData_Get(self.ctx, idx, ctypes.byref(out)) != 0:
            return None
        return out

    @staticmethod
    def _ram_info():
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_PhysicalMemory | % { '{0}|{1}|{2}' -f $_.Capacity,$_.ConfiguredClockSpeed,$_.SMBIOSMemoryType }"],
                capture_output=True, text=True, creationflags=0x08000000, timeout=20)
            rows = [l.split("|") for l in r.stdout.split() if "|" in l]
            installed = sum(int(c) for c, _, _ in rows)
            speed = max(int(s) for _, s, _ in rows) if rows else 0
            mtype = {34: "DDR5", 35: "LPDDR5", 26: "DDR4", 30: "LPDDR4"}.get(int(rows[0][2]) if rows else 0, "DDR")
            return {"installed_gb": round(installed / 2**30), "speed": speed, "type": mtype, "sticks": len(rows)}
        except Exception:
            return {"installed_gb": 0, "speed": 0, "type": "", "sticks": 0}

    def read(self):
        data = {}
        if self.adl and self.adapter is not None:
            with self.lock:
                out = self._query(self.adapter)
            if out:
                for key, sid in PM.items():
                    s = out.sensors[sid]
                    if s.supported:
                        data[key] = s.value
                # ADL's CPU_POWER (33) reads 0-1 W on Hawk Point even under load, so derive the
                # CPU cores' share as whole-APU power minus the graphics power.
                if "apu_power" in data and "gpu_power" in data:
                    data["cpu_power"] = max(0, data["apu_power"] - data["gpu_power"])
        m = _MemStatus()
        m.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        data["ram_load"] = m.dwMemoryLoad
        data["ram_total"] = round(m.ullTotalPhys / 2**30, 1)
        data["ram_used"] = round((m.ullTotalPhys - m.ullAvailPhys) / 2**30, 1)
        data["ram"] = self.ram_info
        return data


if __name__ == "__main__":
    print(Sensors().read())
