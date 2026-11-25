"""X16 Power - battery, background apps and power modes for the ACEMAGIC RX16 (Ryzen 7 H 255).

Runs a tiny local server (127.0.0.1 only) and opens the UI in an Edge app window.
Closing the window keeps the service running (adaptive mode keeps working);
use the "إيقاف" button in the UI to quit completely.

  pythonw x16power.pyw               start (or just open the window if already running)
  pythonw x16power.pyw --background  start without opening a window (used at Windows startup)
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True   # keep the app folder clean (no __pycache__)

# Versioning rule (owner's request): every update adds 1 to the LAST digit; after 9 it rolls over
# to the next digit, e.g. 1.3.8 -> 1.3.9 -> 1.4.0 ... 1.9.9 -> 2.0.0
VERSION = "1.4.7"
HOST, PORT = "127.0.0.1", 47816
BASE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(os.environ.get("APPDATA", BASE), "X16Power")
CFG_FILE = os.path.join(CFG_DIR, "config.json")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\X16 Power.lnk")
NO_WINDOW = 0x08000000

# Windows 11 power-mode overlays (the slider in Settings > Power)
OVERLAY = {
    "efficiency":  "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced":    "00000000-0000-0000-0000-000000000000",
    "performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}

# Presets: overlay, max CPU state %, boost mode (0 off, 1 on, 2 aggressive), EPP (0 perf .. 100 saving)
PRESETS = {
    "eco":         {"overlay": "efficiency",  "max": 60,  "boost": 0, "epp": 85},
    "balanced":    {"overlay": "balanced",    "max": 100, "boost": 1, "epp": 50},
    "performance": {"overlay": "performance", "max": 100, "boost": 2, "epp": 0},
}

state_lock = threading.Lock()
state = {"sample": None, "history": [], "health": None, "effective": None, "applied_at": 0, "error": None,
         "hw": {}, "hw_hist": [], "ryzen": None}
def tr(ar, en):
    """Pick the Arabic or English string for the chosen interface language."""
    return en if config.get("lang") == "en" else ar


config = {"mode": "adaptive", "adaptive_low": 40, "adaptive_ac": "performance", "lang": "ar",
          "charge_alert": True, "charge_alert_pct": 80,
          "overlay": None,   # filled from OVERLAY_DEFAULT on load
          "tdp_enabled": True, "tdp": {"eco": 15, "balanced": 35, "performance": 54}}

# ---------------------------------------------------------------- RyzenAdj (CPU wattage, needs admin)
RYZENADJ = os.path.join(BASE, "ryzenadj", "ryzenadj.exe")
TDP_MIN, TDP_MAX, TDP_FACTORY = 8, 54, 35   # Ryzen 7 H 255 (Hawk Point) cTDP 35-54W, factory 35W on RX16
tdp_lock = threading.Lock()


def ryzenadj(*args):
    if not os.path.exists(RYZENADJ):
        return None
    with tdp_lock:
        r = subprocess.run([RYZENADJ, *args], cwd=os.path.dirname(RYZENADJ), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=15)
    return r


# Real CPU-core power lives in the SMU "PM table" (ADL can't split CPU vs GPU on Hawk Point).
# ryzenadj --dump-table reads it; we only do that every 5 s while someone is looking at a power value
# (window open, or overlay showing CPU/GPU watts) so the background stays at ~0 CPU.
PM_TABLE_VERSION = "4c0009"                  # Hawk Point layout verified on this RX16
PM_CORE_POWER = [0x0804 + 4 * i for i in range(8)]   # per-core power, W
PM_PPT_FAST_VALUE = 0x000C                   # whole-chip power, W
POWER_WANTED_UNTIL = 0.0
UPDATER = None                     # GitHub Releases updater (see updater.py)
OPEN_EVENT = threading.Event()     # "open the window" request for the exe host (see /api/wait-open)


def want_power():
    global POWER_WANTED_UNTIL
    POWER_WANTED_UNTIL = time.time() + 8


def read_pm_power():
    """{'cpu': W, 'chip': W, 'ms': read time} from one ryzenadj --dump-table, or None."""
    t = time.perf_counter()
    r = ryzenadj("--dump-table")
    if r is None or PM_TABLE_VERSION not in r.stdout:
        return None
    vals = {}
    for line in r.stdout.splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) == 3 and cols[0].startswith("0x"):
            try:
                vals[int(cols[0], 16)] = float(cols[2])
            except ValueError:
                pass
    cores = [vals.get(o) for o in PM_CORE_POWER]
    chip = vals.get(PM_PPT_FAST_VALUE)
    if None in cores or chip is None or any(c != c or c < 0 for c in cores):
        return None
    return {"cpu": round(sum(cores), 1), "chip": round(chip, 1), "ts": time.time(),
            "ms": round((time.perf_counter() - t) * 1000)}


def pm_loop():
    while True:
        overlay = overlay_cfg()
        ov_power = overlay["enabled"] and ("power" in overlay["items"].get("cpu", []) or
                                           "power" in overlay["items"].get("gpu", []))
        if is_admin() and os.path.exists(RYZENADJ) and (time.time() < POWER_WANTED_UNTIL or ov_power):
            pm = read_pm_power()
            if pm:
                with state_lock:
                    state["pm"] = pm
        time.sleep(5)

def ryzen_info():
    r = ryzenadj("--info")
    if r is None or r.returncode != 0:
        return None
    info = {}
    for line in r.stdout.splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) == 3:
            try:
                v = float(cols[1])
                if v == v and abs(v) != float("inf"):   # skip nan/inf (invalid JSON)
                    info[cols[0]] = v
            except ValueError:
                pass
    if "Hawk" in r.stdout or "CPU Family" in r.stdout:
        for line in r.stdout.splitlines():
            if line.startswith("CPU Family:"):
                info["family"] = line.split(":", 1)[1].strip()
    return info


def tdp_targets(key):
    w = TDP_FACTORY if not config.get("tdp_enabled") else int(config["tdp"].get(key, TDP_FACTORY))
    w = max(TDP_MIN, min(TDP_MAX, w))
    fast = w if (not config.get("tdp_enabled") or w >= TDP_MAX) else min(TDP_MAX, w + 5)
    return w, fast


def apply_tdp(key):
    if not is_admin():
        return False
    w, fast = tdp_targets(key)
    r = ryzenadj(f"--stapm-limit={w * 1000}", f"--slow-limit={w * 1000}", f"--fast-limit={fast * 1000}")
    with state_lock:
        state["ryzen_last"] = None if r is None else {"rc": r.returncode, "out": (r.stdout + r.stderr)[-600:]}
    if r is None:
        return False
    if r.returncode == 0:
        return True
    # ryzenadj 0.19 on this machine sets the limits, then crashes on exit (0xC0000005 in driver cleanup):
    # trust what the SMU reports instead of the exit code
    info = ryzen_info()
    if info:
        with state_lock:
            state["ryzen"] = info
        return abs(info.get("STAPM LIMIT", -1) - w) < 0.5 and abs(info.get("PPT LIMIT FAST", -1) - fast) < 0.5
    return False


def tdp_loop():
    """Reads limits every 30 s and re-applies if firmware reset them (sleep, AC change...).
    Mode / wattage changes are applied immediately elsewhere; this is only the safety net."""
    while True:
        if is_admin() and os.path.exists(RYZENADJ):
            info = ryzen_info()
            with state_lock:
                state["ryzen"] = info
                key = state["effective"]
            if info and key:
                w, fast = tdp_targets(key)
                if abs(info.get("STAPM LIMIT", w) - w) > 0.5 or abs(info.get("PPT LIMIT FAST", fast) - fast) > 0.5:
                    apply_tdp(key)
        time.sleep(30)


# ---------------------------------------------------------------- overlay (floating on-screen stats, like NZXT CAM)
OVERLAY_FIELDS = {   # group -> fields the user can pick
    "cpu": ["temp", "load", "clock", "power"],
    "gpu": ["temp", "load", "clock", "power"],
    "ram": ["used", "load"],
    "bat": ["pct", "watts", "time"],
    "pwr": ["mode", "tdp", "apu"],
    "sys": ["time"],
}
OVERLAY_POS = ["tl", "tc", "tr", "bl", "bc", "br", "custom"]
OVERLAY_DEFAULT = {
    "enabled": False, "pos": "tc", "x": None, "y": None,
    "opacity": 55, "scale": 100, "layout": "horizontal",
    "theme": "dark", "custom_colors": False, "color_value": "#ffffff", "color_label": "#e8ebed", "auto_color": True,
    "topmost": True, "snap": True, "lock": False,
    "items": {"cpu": ["temp", "load", "clock"], "gpu": ["load"], "ram": ["used"],
              "bat": ["pct", "watts"], "pwr": [], "sys": []},
}


def overlay_cfg():
    o = dict(OVERLAY_DEFAULT)
    o.update(config.get("overlay") or {})
    return o


def set_overlay_cfg(data):
    o = overlay_cfg()
    if "enabled" in data:
        o["enabled"] = bool(data["enabled"])
    if data.get("pos") in OVERLAY_POS:
        o["pos"] = data["pos"]
    if "opacity" in data:
        o["opacity"] = max(0, min(100, int(data["opacity"])))
    if "scale" in data:
        o["scale"] = max(50, min(150, int(data["scale"])))
    if data.get("layout") in ("vertical", "horizontal"):
        o["layout"] = data["layout"]
    if data.get("theme") in ("dark", "light"):
        o["theme"] = data["theme"]
    for k in ("custom_colors", "auto_color", "topmost", "snap", "lock"):
        if k in data:
            o[k] = bool(data[k])
    for k in ("color_value", "color_label"):
        v = str(data.get(k, ""))
        if len(v) == 7 and v[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in v[1:]):
            o[k] = v
    if "x" in data and "y" in data:          # dragged by hand
        o["x"], o["y"], o["pos"] = int(data["x"]), int(data["y"]), "custom"
    if isinstance(data.get("items"), dict):
        o["items"] = {g: [f for f in data["items"].get(g, []) if f in fields] for g, fields in OVERLAY_FIELDS.items()}
    config["overlay"] = o
    save_config()
    return o


def _temp_col(t):
    return "#ff4d55" if t >= 85 else "#ff8a1f" if t >= 70 else "#ffcc4d" if t >= 55 else "#2fd07a"


def _load_col(v):
    return "#ff4d55" if v >= 85 else "#ff8a1f" if v >= 50 else "#ffffff"


def overlay_rows():
    """NZXT-style tiles: [{v: value, u: unit, l: label, c: value colour}] + display settings."""
    o = overlay_cfg()
    with state_lock:
        s, hw, eff, rz = state["sample"], dict(state["hw"] or {}), state["effective"], dict(state["ryzen"] or {})
    it, tiles = o["items"], []
    light = o["theme"] == "light"
    base = o["color_value"] if o["custom_colors"] else ("#16181d" if light else "#ffffff")
    auto = o["auto_color"]

    def tile(v, u, label, col=None):
        tiles.append({"v": str(v), "u": u, "l": label, "c": (col if (auto and col) else base)})

    f = it.get("cpu", [])
    if "temp" in f and hw.get("cpu_temp") is not None:
        tile(hw["cpu_temp"], "°", tr("حرارة CPU", "CPU Temp"), _temp_col(hw["cpu_temp"]))
    if "load" in f and hw.get("cpu_load") is not None:
        tile(hw["cpu_load"], "%", tr("ضغط CPU", "CPU Load"), _load_col(hw["cpu_load"]) if hw["cpu_load"] >= 50 else None)
    if "clock" in f and hw.get("cpu_clk"):
        tile(hw["cpu_clk"], "MHz", tr("تردد CPU", "CPU Clock"))
    if "power" in f and hw.get("cpu_power") is not None:
        tile(hw["cpu_power"], "W", tr("واط CPU", "CPU Power"))
    f = it.get("gpu", [])
    if "temp" in f and hw.get("gpu_temp") is not None:
        tile(hw["gpu_temp"], "°", tr("حرارة GPU", "GPU Temp"), _temp_col(hw["gpu_temp"]))
    if "load" in f and hw.get("gpu_load") is not None:
        tile(hw["gpu_load"], "%", tr("ضغط GPU", "GPU Load"), _load_col(hw["gpu_load"]) if hw["gpu_load"] >= 50 else None)
    if "clock" in f and hw.get("gpu_clk"):
        tile(hw["gpu_clk"], "MHz", tr("تردد GPU", "GPU Clock"))
    if "power" in f and hw.get("gpu_power") is not None:
        tile(hw["gpu_power"], "W", tr("واط GPU", "GPU Power"))
    f = it.get("ram", [])
    if "used" in f and hw.get("ram_used") is not None:
        tile(f"{hw['ram_used']:.1f}", "GB", tr("الرام", "RAM Used"))
    if "load" in f and hw.get("ram_load") is not None:
        tile(hw["ram_load"], "%", tr("ضغط الرام", "RAM Load"), _load_col(hw["ram_load"]) if hw["ram_load"] >= 50 else None)
    f = it.get("bat", [])
    if s:
        pct = percent(s)
        charging = s["charge"] > 0
        w = (s["charge"] if charging else -s["discharge"]) / 1000
        if "pct" in f:
            tile(f"{pct:.0f}", "%", tr("يشحن", "Charging") if charging else tr("البطارية", "Battery"),
                 "#2fd07a" if s["online"] else "#ff4d55" if pct < 20 else None)
        if "watts" in f:
            tile(f"{w:+.1f}", "W", tr("واط البطارية", "Battery W"), "#2fd07a" if w > 0 else None)
        t = ((s["full"] - s["remaining"]) / s["charge"] if charging
             else s["remaining"] / s["discharge"] if s["discharge"] and not s["online"] else None)
        if "time" in f and t:
            tile(f"{int(t)}:{int((t % 1) * 60):02d}", "h", tr("للشحن الكامل", "To Full") if charging else tr("متبقي", "Remaining"))
    f = it.get("pwr", [])
    names = {"eco": tr("اقتصادي", "Eco"), "balanced": tr("متوسط", "Balanced"), "performance": tr("أداء", "Perf")}
    if "mode" in f and eff:
        tile(names.get(eff, eff), "", tr("الوضع", "Mode"))
    if "tdp" in f and rz.get("STAPM LIMIT"):
        tile(f"{rz['STAPM LIMIT']:.0f}", "W", tr("حد الواط", "TDP Limit"))
    if "apu" in f and hw.get("apu_power") is not None:
        tile(hw["apu_power"], "W", tr("الشريحة", "APU Power"))
    if "time" in it.get("sys", []):
        tile(time.strftime("%H:%M"), "", tr("الساعة", "Time"))

    keys = ("enabled", "pos", "x", "y", "opacity", "scale", "layout", "theme", "topmost", "snap", "lock")
    out = {k: o[k] for k in keys}
    out["label_color"] = o["color_label"] if o["custom_colors"] else ("#5d6470" if light else "#9aa1ad")
    out["tiles"] = tiles
    return out


# ---------------------------------------------------------------- config
def load_config():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            config.update(json.load(f))
    except (OSError, ValueError):
        pass
    config["overlay"] = overlay_cfg()


def save_config():
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- power control
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s):
        u = uuid.UUID(s)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


def set_overlay(name):
    fn = ctypes.windll.powrprof.PowerSetActiveOverlayScheme
    fn.argtypes = [ctypes.POINTER(GUID)]
    return fn(ctypes.byref(GUID.from_str(OVERLAY[name]))) == 0


def powercfg(*args):
    subprocess.run(["powercfg", *args], creationflags=NO_WINDOW, capture_output=True)


def apply_preset(key):
    p = PRESETS[key]
    for kind in ("/setacvalueindex", "/setdcvalueindex"):
        powercfg(kind, "scheme_current", "sub_processor", "PROCTHROTTLEMAX", str(p["max"]))
        powercfg(kind, "scheme_current", "sub_processor", "PERFBOOSTMODE", str(p["boost"]))
        powercfg(kind, "scheme_current", "sub_processor", "PERFEPP", str(p["epp"]))
    powercfg("/setactive", "scheme_current")
    ok = set_overlay(p["overlay"])
    apply_tdp(key)
    with state_lock:
        state["effective"] = key
        state["applied_at"] = time.time()
        state["error"] = None if ok else tr("تعذّر تغيير وضع الطاقة في ويندوز", "Could not change the Windows power mode")


def wanted_preset(sample):
    mode = config["mode"]
    if mode != "adaptive":
        return mode
    if sample is None:
        return "balanced"
    if sample["online"]:
        return config.get("adaptive_ac", "performance")
    pct = percent(sample)
    return "eco" if pct < config.get("adaptive_low", 40) else "balanced"


def percent(s):
    return round(100 * s["remaining"] / s["full"], 1) if s and s.get("full") else 0


# ---------------------------------------------------------------- sampling
def battery_health():
    out = os.path.join(tempfile.gettempdir(), "x16power_battery.xml")
    subprocess.run(["powercfg", "/batteryreport", "/xml", "/output", out], creationflags=NO_WINDOW, capture_output=True)
    try:
        root = ET.parse(out).getroot()
        b = root.find(".//{*}Battery")
        g = lambda tag: (b.find("{*}" + tag).text or "").strip()
        h = {"design": int(g("DesignCapacity")), "full": int(g("FullChargeCapacity")),
             "cycles": int(g("CycleCount") or 0), "chem": g("Chemistry"), "id": g("Id")}
        h.update(battery_history(root, h["design"]))
        return h
    except Exception:
        return None


def iso_hours(d):
    """ISO-8601 duration (P1DT2H3M4S) -> hours."""
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?", d or "")
    if not m:
        return 0.0
    dd, hh, mm, ss = (float(x) if x else 0.0 for x in m.groups())
    return dd * 24 + hh + mm / 60 + ss / 3600


def battery_history(root, design):
    """Charge cycles, age, runtime and usage from Windows' battery history (the EC reports 0 cycles)."""
    from datetime import datetime, timezone
    entries = root.findall(".//{*}History/{*}HistoryEntry")
    if not entries:
        return {}
    energy = dc_h = ac_h = cs_dc_h = active_dc_energy = excluded_h = 0.0
    months = {}
    for e in entries:
        a_e, c_e = float(e.get("ActiveDcEnergy") or 0), float(e.get("CsDcEnergy") or 0)
        energy += a_e + c_e
        dch_raw, csh_raw = iso_hours(e.get("ActiveDcTime")), iso_hours(e.get("CsDcTime"))
        # Windows sometimes logs hours without the matching energy (seen here: 60 h on battery = 5 Wh,
        # and 24 h/day "standby" with 0 Wh while the laptop was off). Keep the energy, drop those hours.
        dch = 0.0 if dch_raw > 2 and a_e / 1000 / dch_raw < 1.5 else dch_raw
        csh = 0.0 if csh_raw > 6 and c_e == 0 else csh_raw
        excluded_h += (dch_raw - dch) + (csh_raw - csh)
        if dch:
            active_dc_energy += a_e
        dc_h += dch
        cs_dc_h += csh
        ac_h += iso_hours(e.get("ActiveAcTime"))
        # an entry can span several months (Windows merges old days): split it by time overlap
        s = datetime.fromisoformat(e.get("LocalStartDate"))
        t = datetime.fromisoformat(e.get("LocalEndDate"))
        total = max((t - s).total_seconds(), 1)
        cur = s
        while cur < t:
            nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            frac = (min(nxt, t) - cur).total_seconds() / total
            mon = f"{cur.year:04d}-{cur.month:02d}"
            m = months.setdefault(mon, {"m": mon, "dc": 0.0, "ac": 0.0, "wh": 0.0})
            m["dc"] += dch * frac
            m["ac"] += iso_hours(e.get("ActiveAcTime")) * frac
            m["wh"] += (a_e + c_e) / 1000 * frac
            cur = nxt
    first = datetime.fromisoformat(entries[0].get("StartDate").replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - first).days
    cycles = energy / design if design else 0
    rated = 500  # typical Li-ion laptop rating to ~80% capacity
    per_month = cycles / max(age_days / 30.4, 1)
    years_left = (rated - cycles) / per_month / 12 if per_month > 0 else None

    est = root.find(".//{*}RuntimeEstimates/{*}FullChargeCapacity")
    runtime_win = iso_hours(est.findtext("{*}ActiveRuntime")) if est is not None else None
    standby_win = iso_hours(est.findtext("{*}ConnectedStandbyRuntime")) if est is not None else None
    avg_w = active_dc_energy / 1000 / dc_h if dc_h > 0.2 else None
    caps = {int(e.get("FullChargeCapacity") or 0) for e in entries}
    return {
        "cycles_est": round(cycles, 1), "drained_wh": round(energy / 1000, 1), "first_use": first.date().isoformat(),
        "age_days": age_days, "dc_hours": round(dc_h, 1), "ac_hours": round(ac_h, 1), "standby_dc_hours": round(cs_dc_h, 1),
        "avg_dc_w": round(avg_w, 1) if avg_w else None, "runtime_win_h": runtime_win, "standby_win_h": standby_win,
        "rated_cycles": rated, "cycles_per_month": round(per_month, 2),
        "years_left": round(years_left, 1) if years_left is not None else None,
        # Li-ion also ages with time (~5 years typical), whatever the cycle count
        "calendar_years_left": round(max(0.0, 5 - age_days / 365), 1),
        "capacity_static": len(caps) == 1, "months": [months[k] for k in sorted(months)][-12:],
        "design": design, "history_wh": round(energy / 1000, 1), "excluded_hours": round(excluded_h, 1),
        "history_end": (entries[-1].get("LocalEndDate") or "")[:13],   # last local hour Windows has logged
    }


# ---------------------------------------------------------------- our own drain counter
DRAIN_FILE = os.path.join(CFG_DIR, "battery_drain.json")


class DrainLog:
    """Energy taken out of the battery and time spent on battery, measured by us every 3 s and kept per
    local hour. Windows only adds a day to its battery history after the day ends, and some of its
    records are wrong, so this keeps the cycle count live and honest."""

    def __init__(self):
        self.lock = threading.Lock()
        self.hours, self.last_rem, self.last_t, self.saved = {}, None, None, 0.0
        try:
            with open(DRAIN_FILE, encoding="utf-8") as f:
                d = json.load(f)
            self.hours = {k: list(v) for k, v in d.get("hours", {}).items()}
            self.last_rem = d.get("last_remaining")
        except (OSError, ValueError):
            pass

    def feed(self, s):
        rem, full, now = s.get("remaining") or 0, s.get("full") or 0, time.time()
        if not rem or not full:
            return
        key = time.strftime("%Y-%m-%dT%H", time.localtime(now))
        with self.lock:
            b = self.hours.setdefault(key, [0, 0])           # [mWh drained, seconds on battery]
            if self.last_rem is not None and rem < self.last_rem and s.get("charge", 0) <= 0:
                drop = self.last_rem - rem
                # first sample after a restart covers the time we were off: only trust small drops
                if drop < full * (0.6 if self.last_t else 0.2):
                    b[0] += drop
            if self.last_t and not s.get("online"):
                b[1] += min(now - self.last_t, 30)           # sleep gaps don't count as use
            self.last_rem, self.last_t = rem, now
            if now - self.saved > 60:
                self._save()
                self.saved = now

    def _save(self):
        cutoff = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() - 400 * 86400))
        self.hours = {k: v for k, v in self.hours.items() if k >= cutoff}
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
            tmp = DRAIN_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"hours": self.hours, "last_remaining": self.last_rem}, f)
            os.replace(tmp, DRAIN_FILE)
        except OSError:
            pass

    def after(self, hour_key):
        """{month: [mWh, s]} for every hour later than hour_key ('YYYY-MM-DDTHH')."""
        out = {}
        with self.lock:
            for k, (mwh, secs) in self.hours.items():
                if k > hour_key:
                    m = out.setdefault(k[:7], [0, 0])
                    m[0] += mwh
                    m[1] += secs
        return out


DRAIN = DrainLog()


def health_now():
    """Windows' history (sanitised) + our live counter for everything after Windows' last logged hour."""
    with state_lock:
        h = dict(state["health"] or {})
    if not h.get("design") or "history_end" not in h:
        return h or None
    live = DRAIN.after(h["history_end"])
    live_mwh = sum(v[0] for v in live.values())
    live_s = sum(v[1] for v in live.values())
    energy = h["history_wh"] * 1000 + live_mwh
    cycles = energy / h["design"]
    per_month = cycles / max(h["age_days"] / 30.4, 1)
    h.update({
        "cycles_est": round(cycles, 1), "drained_wh": round(energy / 1000, 1), "live_wh": round(live_mwh / 1000, 1),
        "dc_hours": round(h["dc_hours"] + live_s / 3600, 1), "cycles_per_month": round(per_month, 2),
        "years_left": round((h["rated_cycles"] - cycles) / per_month / 12, 1) if per_month > 0 else None,
    })
    months = {m["m"]: dict(m) for m in h.get("months", [])}
    for mon, (mwh, secs) in live.items():
        m = months.setdefault(mon, {"m": mon, "dc": 0.0, "ac": 0.0, "wh": 0.0})
        m["wh"] += mwh / 1000
        m["dc"] += secs / 3600
    h["months"] = [months[k] for k in sorted(months)][-12:]
    return h


PROCS_WANTED_UNTIL = 0.0          # the "running apps" page asks for the process list; otherwise skip it
procs_wake = threading.Event()


def want_procs():
    """Called when the UI shows the running-apps page: keep the process list fresh for a few seconds."""
    global PROCS_WANTED_UNTIL
    stale = time.time() > PROCS_WANTED_UNTIL
    PROCS_WANTED_UNTIL = time.time() + 8
    if stale:
        procs_wake.set()


def monitor_loop():
    """Battery + CPU + (on demand) process list every 3 s, read straight from Windows (no PowerShell/WMI)."""
    from native_monitor import Battery, CpuLoad, Processes
    battery, cpu, processes = Battery(), CpuLoad(), Processes()
    last_hist, alerts, procs = 0, set(), []
    while True:
        try:
            b = battery.read() or {"online": True, "charging": False, "remaining": 0, "full": 0,
                                   "charge": 0, "discharge": 0, "voltage": 0}
            if time.time() < PROCS_WANTED_UNTIL:
                procs = processes.read()
            elif procs:
                procs, processes.prev, processes.prev_t = [], {}, None   # page closed: drop the list
            s = dict(b, ts=int(time.time()), cpu=cpu.read(), procs=procs)
        except Exception:
            time.sleep(3)
            continue
        with state_lock:
            state["sample"] = s
            if s["ts"] - last_hist >= 30:
                last_hist = s["ts"]
                rate = -s["discharge"] if s["discharge"] else s["charge"]
                state["history"].append({"t": s["ts"], "p": percent(s), "w": round(rate / 1000, 1)})
                del state["history"][:-240]
            effective = state["effective"]
        want = wanted_preset(s)
        if want != effective:
            apply_preset(want)
        battery_alerts(s, alerts)
        DRAIN.feed(s)
        procs_wake.wait(3)
        procs_wake.clear()


def sensor_loop():
    from sensors import Sensors
    sensors = Sensors()
    while True:
        try:
            hw = sensors.read()
        except Exception:
            hw = {}
        # CPU = sum of the 8 per-core powers, chip = PPT fast value, GPU(+SoC) = chip - CPU  (SMU table).
        # Without a fresh table the CPU/GPU split is unknown, so don't show a made-up number.
        with state_lock:
            pm = state.get("pm")
        if pm and time.time() - pm["ts"] < 12:
            hw["cpu_power"], hw["apu_power"] = pm["cpu"], pm["chip"]
            hw["gpu_power"] = round(max(0.0, pm["chip"] - pm["cpu"]), 1)
            hw["power_src"] = "smu"
        else:
            hw.pop("cpu_power", None)
            hw.pop("gpu_power", None)
        with state_lock:
            s = state["sample"]
            hw["cpu_load"] = s["cpu"] if s else None
            state["hw"] = hw
            state["hw_hist"].append({"ct": hw.get("cpu_temp"), "gt": hw.get("gpu_temp"),
                                     "cl": hw.get("cpu_load"), "gl": hw.get("gpu_load"),
                                     "w": hw.get("apu_power")})
            del state["hw_hist"][:-90]
        time.sleep(2)


def health_loop():
    while True:
        h = battery_health()
        with state_lock:
            state["health"] = h
        time.sleep(1800)


# ---------------------------------------------------------------- startup shortcut
TASK = "RX16 Control Center"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def task_state():
    """None if the admin logon task is missing, else 'Ready'/'Running'/'Disabled'."""
    r = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "csv", "/nh"], capture_output=True,
                       text=True, encoding="oem", errors="replace", creationflags=NO_WINDOW)
    if r.returncode != 0:
        return None
    return r.stdout.strip().split(",")[-1].strip('"')


def startup_enabled():
    st = task_state()
    if st is not None:
        return st != "Disabled"
    return os.path.exists(STARTUP_LNK)


def set_startup(enable):
    if task_state() is not None:
        subprocess.run(["schtasks", "/change", "/tn", TASK, "/enable" if enable else "/disable"],
                       capture_output=True, creationflags=NO_WINDOW)
        return
    if not enable:
        if os.path.exists(STARTUP_LNK):
            os.remove(STARTUP_LNK)
        return
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.abspath(__file__)
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:PYW;$s.Arguments='\"'+$env:SCRIPT+'\" --background';"
        "$s.WorkingDirectory=$env:WD;$s.IconLocation=$env:WD+'\\rx16.ico';$s.Save()"
    )
    env = dict(os.environ, LNK=STARTUP_LNK, PYW=pyw, SCRIPT=script, WD=BASE)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, creationflags=NO_WINDOW, capture_output=True)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        # blocks DNS-rebinding: only our own address may talk to this (possibly elevated) service
        return self.headers.get("Host") in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        if self.path in ("/", "/index.html"):
            with open(os.path.join(BASE, "ui", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path.startswith("/api/state"):
            health = health_now()
            want_power()                     # window is open and visible (it only polls then)
            if "procs=1" in self.path:      # running-apps page is open: start/keep the process list
                want_procs()
            with state_lock:
                s = state["sample"]
                body = {
                    "sample": s, "percent": percent(s), "history": state["history"],
                    "health": health, "effective": state["effective"],
                    "mode": config["mode"], "config": config, "error": state["error"],
                    "startup": startup_enabled(), "presets": PRESETS,
                    "hw": state["hw"], "hw_hist": state["hw_hist"], "admin": is_admin(),
                    "ryzen": state["ryzen"], "ryzenadj": os.path.exists(RYZENADJ),
                    "pm_ms": (state.get("pm") or {}).get("ms"),
                    "update": UPDATER.state() if UPDATER else None,
                    "tdp_range": [TDP_MIN, TDP_MAX, TDP_FACTORY],
                    "version": VERSION, "overlay_fields": OVERLAY_FIELDS, "ryzen_last": state.get("ryzen_last"), "hosted": "--no-tray" in sys.argv,
                }
            return self._send(200, body)
        if self.path == "/api/ping":
            return self._send(200, {"ok": True, "app": "x16power"})
        if self.path == "/api/overlay":   # polled every second by the exe host's floating window
            return self._send(200, overlay_rows())
        if self.path == "/api/wait-open":  # long-poll from the exe host: returns when the window should open
            opened = OPEN_EVENT.wait(55)
            OPEN_EVENT.clear()
            return self._send(200, {"open": opened})
        if self.path == "/api/tray":      # polled by the exe host (tray icon + queued notifications)
            st = tray_status()
            with state_lock:
                st["notes"] = NOTES[:]
                NOTES.clear()
            return self._send(200, st)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # only accept requests from our own page
        origin = self.headers.get("Origin")
        if not self._host_ok() or (origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")):
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})

        if self.path == "/api/mode":
            mode = data.get("mode")
            if mode not in ("adaptive", *PRESETS):
                return self._send(400, {"error": "bad mode"})
            config["mode"] = mode
            for k in ("adaptive_low", "adaptive_ac"):
                if k in data:
                    config[k] = data[k]
            save_config()
            with state_lock:
                s = state["sample"]
            apply_preset(wanted_preset(s))
            return self._send(200, {"ok": True})

        if self.path == "/api/kill":
            pids = [int(p) for p in data.get("pids", [data.get("pid", 0)])]
            pids = [p for p in pids if p > 4 and p != os.getpid()]
            if not pids:
                return self._send(400, {"error": "protected"})
            args = ["taskkill", "/T"] + (["/F"] if data.get("force") else [])
            for p in pids:
                args += ["/PID", str(p)]
            r = subprocess.run(args, creationflags=NO_WINDOW, capture_output=True,
                               text=True, encoding="oem", errors="replace")
            return self._send(200, {"ok": r.returncode == 0, "msg": (r.stdout or r.stderr).strip()})

        if self.path == "/api/update/check":
            return self._send(200, UPDATER.check() if UPDATER else {"error": "no updater"})

        if self.path == "/api/update/install":
            return self._send(200, {"ok": bool(UPDATER and UPDATER.install())})

        if self.path == "/api/tdp":
            if "enabled" in data:
                config["tdp_enabled"] = bool(data["enabled"])
            for k, v in (data.get("tdp") or {}).items():
                if k in PRESETS:
                    config["tdp"][k] = max(TDP_MIN, min(TDP_MAX, int(v)))
            save_config()
            with state_lock:
                key = state["effective"] or "balanced"
            ok = apply_tdp(key)
            return self._send(200, {"ok": ok, "admin": is_admin()})

        if self.path == "/api/alerts":
            if "charge_alert" in data:
                config["charge_alert"] = bool(data["charge_alert"])
            if "charge_alert_pct" in data:
                config["charge_alert_pct"] = max(50, min(100, int(data["charge_alert_pct"])))
            save_config()
            return self._send(200, {"ok": True})

        if self.path == "/api/test-alert":
            notify(tr(f"البطارية وصلت {config.get('charge_alert_pct', 80)}%", f"Battery reached {config.get('charge_alert_pct', 80)}%"),
                   tr("هذا إشعار تجريبي من RX16 Control Center.", "This is a test notification from RX16 Control Center."), info=True)
            return self._send(200, {"ok": True})

        if self.path == "/api/lang":
            if data.get("lang") in ("ar", "en"):
                config["lang"] = data["lang"]
                save_config()
                with state_lock:
                    s = state["sample"]
                if state["error"]:
                    state["error"] = tr("تعذّر تغيير وضع الطاقة في ويندوز", "Could not change the Windows power mode")
            return self._send(200, {"ok": True, "lang": config.get("lang", "ar")})

        if self.path == "/api/overlay":
            if data.get("toggle"):
                data["enabled"] = not overlay_cfg()["enabled"]
            return self._send(200, set_overlay_cfg(data))

        if self.path == "/api/open":
            if "--no-tray" in sys.argv:          # the exe hosts the window (WebView2): wake its long-poll
                OPEN_EVENT.set()
            else:                                # standalone fallback: separate Edge app window
                threading.Thread(target=tray_open, daemon=True).start()
            return self._send(200, {"ok": True})

        if self.path == "/api/restart":
            self._send(200, {"ok": True})
            if TRAY:
                TRAY.remove()
            hosted = "--no-tray" in sys.argv
            if not hosted:   # no exe host to restart us: start a fresh copy ourselves
                subprocess.Popen([sys.executable, os.path.abspath(__file__), "--background", "--wait-port"],
                                 cwd=BASE, creationflags=NO_WINDOW)
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(EXIT_RESTART)), daemon=True).start()
            return

        if self.path == "/api/startup":
            set_startup(bool(data.get("enabled")))
            return self._send(200, {"ok": True, "startup": startup_enabled()})

        if self.path == "/api/quit":
            self._send(200, {"ok": True})
            if TRAY:
                TRAY.remove()
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        self._send(404, {"error": "not found"})


# ---------------------------------------------------------------- tray icon
TRAY = None
NOTES = []          # notifications waiting for the exe host (RX16 Control Center.exe) to show
EXIT_RESTART = 3    # exit code that tells the exe host to start the service again


def notify(title, text, info=False):
    if TRAY is not None:
        TRAY.balloon(title, text, info=info)
    else:
        with state_lock:
            NOTES.append({"title": title, "text": text, "kind": "info" if info else "warning"})
            del NOTES[:-5]
MODE_NAMES = {"eco": ("اقتصادي", "Eco"), "balanced": ("متوسط", "Balanced"),
              "performance": ("عالي الأداء", "Performance"), "adaptive": ("متكيّف", "Adaptive")}


def mode_name(k):
    n = MODE_NAMES.get(k)
    return tr(*n) if n else ""
OPEN_LNK = os.path.join(CFG_DIR, "open-window.lnk")
_open_lnk_fresh = False


def tray_status():
    with state_lock:
        s, hw, eff, rz = state["sample"], state["hw"] or {}, state["effective"], state["ryzen"] or {}
    pct = round(percent(s)) if s else None
    charging = bool(s and s["charge"] > 0)
    online = bool(s and s["online"])
    if pct is None:
        text, bg, fg = "RX", "#ed1c24", "#ffffff"
    else:
        text = str(pct)
        bg, fg = ("#1fa75a", "#ffffff") if (charging or online) else ("#ffb020", "#111111") if pct < 20 else ("#ed1c24", "#ffffff")
    mode = config["mode"]
    arrow = " → " if config.get("lang") == "en" else " ← "
    mode_txt = mode_name(mode) + (f"{arrow}{mode_name(eff)}" if mode == "adaptive" and eff else "")
    # The Windows tooltip is always left-to-right: "label: value" lines with an LRM after each
    # Arabic label keep every number next to its own label.
    L = "‎"
    lines = ["RX16 Control Center"]
    if s:
        state_txt = tr("يشحن", "charging") if charging else (tr("موصول", "plugged in") if online else tr("على البطارية", "on battery"))
        watts = (s["charge"] if charging else s["discharge"]) / 1000
        lines.append(tr(f"البطارية{L}: {pct}% · {watts:.1f}W · {state_txt}", f"Battery: {pct}% · {watts:.1f}W · {state_txt}"))
    lines.append(tr(f"الوضع{L}: {mode_txt}{L}", f"Mode: {mode_txt}") + (f" · {rz['STAPM LIMIT']:.0f}W" if rz.get("STAPM LIMIT") else ""))
    if hw.get("cpu_temp") is not None:
        lines.append(tr(f"المعالج{L}: ", "CPU: ") + f"{hw['cpu_temp']}°C · {hw.get('cpu_load', 0)}%")
        lines.append(tr(f"الكرت{L}: ", "GPU: ") + f"{hw.get('gpu_temp', '--')}°C · {hw.get('gpu_load', 0)}%")
    return {"text": text, "bg": bg, "fg": fg, "tip": "\n".join(lines), "mode": mode, "lang": config.get("lang", "ar")}


def tray_open():
    if is_admin():
        # open the window un-elevated: Explorer launches the shortcut as the normal user
        global _open_lnk_fresh
        if not _open_lnk_fresh or not os.path.exists(OPEN_LNK):
            make_shortcut(OPEN_LNK, "")   # rebuilt once per run: stays valid if the folder is renamed/moved
            _open_lnk_fresh = True
        subprocess.Popen(["explorer.exe", OPEN_LNK])
    else:
        open_window()


def tray_mode(mode):
    config["mode"] = mode
    save_config()
    with state_lock:
        s = state["sample"]
    apply_preset(wanted_preset(s))


def tray_quit():
    os._exit(0)


def make_shortcut(lnk, args):
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);$s.TargetPath=$env:PYW;"
          "$s.Arguments='\"'+$env:SCRIPT+'\" '+$env:ARGS;$s.WorkingDirectory=$env:WD;"
          "$s.IconLocation=$env:WD+'\\rx16.ico';$s.Save()")
    env = dict(os.environ, LNK=lnk, PYW=pyw, SCRIPT=os.path.abspath(__file__), WD=BASE, ARGS=args)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, creationflags=NO_WINDOW, capture_output=True)


def battery_alerts(s, sent):
    """Balloon once when crossing 20% and 10% on battery, and when charging reaches the charge target."""
    if s is None:
        return
    pct = percent(s)
    if s["online"]:
        sent.discard(20)
        sent.discard(10)
        target = int(config.get("charge_alert_pct", 80))
        if pct < target:
            sent.add("armed")               # seen below target while charging -> notify on crossing
        elif "armed" in sent:
            sent.discard("armed")
            if config.get("charge_alert", True):
                notify(tr(f"البطارية وصلت {pct:.0f}%", f"Battery reached {pct:.0f}%"),
                       tr(f"وصل الشحن {target}%. تقدر تفصل الشاحن عشان تحافظ على عمر البطارية.",
                          f"Charge reached {target}%. You can unplug the charger to extend battery life."), info=True)
        return
    sent.discard("armed")
    for lvl in (20, 10):
        if pct <= lvl and lvl not in sent:
            sent.add(lvl)
            notify(tr(f"البطارية {pct:.0f}%", f"Battery {pct:.0f}%"),
                   tr("وصّل الشاحن. الوضع المتكيّف حوّل الجهاز للاقتصادي.", "Plug in the charger. Adaptive mode switched to Eco.")
                   if config["mode"] == "adaptive" else
                   tr("وصّل الشاحن أو حوّل للوضع الاقتصادي.", "Plug in the charger or switch to Eco mode."))


def open_window():
    url = f"http://{HOST}:{PORT}/"
    edge = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                        r"Microsoft\Edge\Application\msedge.exe")
    if os.path.exists(edge):
        prof = os.path.join(CFG_DIR, "edge")
        # The window only shows our local page, so switch off Edge's own background housekeeping
        # (Safe Browsing list downloads, component updates, built-in extensions, sync, disk cache):
        # measured ~10-47 MB/min of disk writes from these while the window sat minimized.
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1240,820",
                          "--no-first-run", "--no-default-browser-check",
                          "--disable-extensions", "--disable-component-extensions-with-background-pages",
                          "--disable-component-update", "--disable-background-networking",
                          "--safebrowsing-disable-auto-update", "--disable-sync", "--disable-domain-reliability",
                          "--disable-breakpad", "--disk-cache-size=1", "--media-cache-size=1",
                          "--disable-gpu-shader-disk-cache", "--renderer-process-limit=1",
                          "--disable-features=Translate,msEdgeSidebarV2,msEdgeShopping,msEdgeCollections,"
                          "AutofillServerCommunication,OptimizationHints,MediaRouter"])
        # (msSmartScreenProtection is left on: turning it off makes Edge show a security warning bar)
    else:
        os.startfile(url)


def already_running():
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "x16power"
    except Exception:
        return False


def main():
    background = "--background" in sys.argv
    if "--wait-port" in sys.argv:          # restarting: let the old copy release the port first
        for _ in range(40):
            if not already_running():
                break
            time.sleep(0.25)
    if already_running():
        if not background:
            open_window()
        return
    # prefer the elevated logon task (admin rights without a UAC prompt), then just open the window
    if not background and not is_admin() and task_state() is not None:
        subprocess.run(["schtasks", "/run", "/tn", TASK], capture_output=True, creationflags=NO_WINDOW)
        for _ in range(40):
            time.sleep(0.25)
            if already_running():
                open_window()
                return
    load_config()
    os.makedirs(CFG_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=health_loop, daemon=True).start()
    threading.Thread(target=sensor_loop, daemon=True).start()
    threading.Thread(target=tdp_loop, daemon=True).start()
    threading.Thread(target=pm_loop, daemon=True).start()
    global UPDATER
    try:
        from updater import Updater
        UPDATER = Updater(BASE, VERSION, notify, tr)
        threading.Thread(target=UPDATER.loop, daemon=True).start()
    except Exception:
        UPDATER = None
    global TRAY
    if "--no-tray" in sys.argv:        # the exe host shows the tray icon + notifications
        server.serve_forever()
        return
    try:
        from tray import Tray
        TRAY = Tray(tray_status, tray_open, tray_mode, tray_quit)
        TRAY.start()
    except Exception:
        TRAY = None
    if not background:
        threading.Timer(0.4, open_window).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
