"""System-tray icon for RX16 Control Center (pure ctypes, no extra packages).

The icon shows the battery percentage (red on battery, green while charging, amber when low).
Left click opens the window; right click shows a menu with the power modes and quit.
"""
import ctypes
import threading
from ctypes import wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

WM_DESTROY, WM_TIMER, WM_COMMAND, WM_APP = 0x0002, 0x0113, 0x0111, 0x8000
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_WARNING = 0x2
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_LAYOUTRTL = 0x100, 0x2, 0x8000
WM_TRAY = WM_APP + 1


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", W.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                ("lpszClassName", W.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", W.BOOL), ("xHotspot", W.DWORD), ("yHotspot", W.DWORD),
                ("hbmMask", W.HBITMAP), ("hbmColor", W.HBITMAP)]


# 64-bit safe signatures
user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
user32.CreateWindowExW.restype = W.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
user32.SetTimer.argtypes = [W.HWND, ctypes.c_size_t, W.UINT, W.LPVOID]
user32.SetTimer.restype = ctypes.c_size_t
user32.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.LPVOID]
user32.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
user32.CreatePopupMenu.restype = W.HMENU
user32.DestroyMenu.argtypes = [W.HMENU]
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.GetDC.restype = W.HDC
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
user32.DrawTextW.argtypes = [W.HDC, W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.RECT), W.UINT]
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = W.HICON
user32.DestroyIcon.argtypes = [W.HICON]
user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
shell32.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
gdi32.CreateDIBSection.restype = W.HBITMAP
gdi32.CreateBitmap.restype = W.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, W.UINT, W.UINT, W.LPVOID]
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.DeleteDC.argtypes = [W.HDC]
gdi32.CreateSolidBrush.restype = W.HBRUSH
gdi32.GetStockObject.restype = W.HGDIOBJ
gdi32.RoundRect.argtypes = [W.HDC] + [ctypes.c_int] * 6
gdi32.SetBkMode.argtypes = [W.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [W.HDC, W.COLORREF]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]
gdi32.CreateFontW.restype = W.HFONT


def _rgb(hexstr):
    r, g, b = int(hexstr[1:3], 16), int(hexstr[3:5], 16), int(hexstr[5:7], 16)
    return r | (g << 8) | (b << 16)


def make_icon(text, bg, fg="#ffffff", size=32):
    """Draws a rounded square with text and returns an HICON with real alpha."""
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    color = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, color)

    brush = gdi32.CreateSolidBrush(_rgb(bg))
    old_brush = gdi32.SelectObject(dc, brush)
    old_pen = gdi32.SelectObject(dc, gdi32.GetStockObject(8))  # NULL_PEN
    gdi32.RoundRect(dc, 0, 0, size + 1, size + 1, size // 3, size // 3)

    px = -int(size * (0.62 if len(text) <= 2 else 0.47))
    font = gdi32.CreateFontW(px, 0, 0, 0, 800, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
    old_font = gdi32.SelectObject(dc, font)
    gdi32.SetBkMode(dc, 1)
    gdi32.SetTextColor(dc, _rgb(fg))
    rect = W.RECT(0, 0, size, size + 1)
    user32.DrawTextW(dc, text, -1, ctypes.byref(rect), 0x1 | 0x4 | 0x20 | 0x100)  # CENTER|VCENTER|SINGLELINE|NOCLIP

    # GDI leaves alpha at 0: make every drawn pixel opaque
    buf = (ctypes.c_uint32 * (size * size)).from_address(bits.value)
    for i in range(size * size):
        if buf[i] & 0xFFFFFF:
            buf[i] |= 0xFF000000

    gdi32.SelectObject(dc, old_font)
    gdi32.SelectObject(dc, old_pen)
    gdi32.SelectObject(dc, old_brush)
    gdi32.SelectObject(dc, old)
    mask = gdi32.CreateBitmap(size, size, 1, 1, None)
    info = ICONINFO(True, 0, 0, mask, color)
    icon = user32.CreateIconIndirect(ctypes.byref(info))
    for h in (font, brush, color, mask):
        gdi32.DeleteObject(h)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return icon


class Tray:
    """get_status() -> dict(text, bg, fg, tip, mode, warn); on_open(); on_mode(key); on_quit()."""

    MODES = [("adaptive", "متكيّف"), ("eco", "اقتصادي"), ("balanced", "متوسط"), ("performance", "عالي الأداء")]

    def __init__(self, get_status, on_open, on_mode, on_quit):
        self.get_status, self.on_open, self.on_mode, self.on_quit = get_status, on_open, on_mode, on_quit
        self.hwnd = None
        self.icon = None
        self.key = None
        self._proc = WNDPROC(self._wndproc)  # keep a reference
        self.ready = threading.Event()

    @staticmethod
    def set_app_identity(app_id, name, icon_path):
        """Notifications show `name` + icon instead of "Python" (registered per user under HKCU)."""
        import winreg
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{app_id}") as k:
                winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, name)
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon_path)
            shell32.SetCurrentProcessExplicitAppUserModelID(W.LPCWSTR(app_id))
        except OSError:
            pass

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        self.ready.wait(5)

    # ------------------------------------------------------------ window + loop
    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "RX16TrayWindow"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "RX16TrayWindow", "RX16", 0, 0, 0, 0, 0, None, None, hinst, None)
        self.msg_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")
        self._notify(NIM_ADD)
        user32.SetTimer(self.hwnd, 1, 3000, None)
        self.ready.set()
        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _notify(self, op):
        try:
            st = self.get_status()
        except Exception:
            st = {"text": "RX", "bg": "#ed1c24", "fg": "#ffffff", "tip": "RX16 Control Center"}
        key = (st["text"], st["bg"], st["fg"])
        if key != self.key or self.icon is None or op == NIM_ADD:
            new = make_icon(*key)
            old, self.icon, self.key = self.icon, new, key
        else:
            old = None
        nid = self._data()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.icon
        nid.szTip = st["tip"][:127]
        shell32.Shell_NotifyIconW(op, ctypes.byref(nid))
        if op == NIM_ADD:
            nid.uVersion = 4
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
        if old:
            user32.DestroyIcon(old)
        self.mode = st.get("mode")

    def balloon(self, title, text, info=False):
        nid = self._data()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = 0x1 if info else NIIF_WARNING  # NIIF_INFO / NIIF_WARNING
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def brand_icon(self):
        if not getattr(self, "_brand", None):
            self._brand = make_icon("RX", "#ed1c24", size=64)
        return self._brand

    def remove(self):
        if self.hwnd:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))

    def _menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, 1, "فتح RX16 Control Center")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for i, (k, name) in enumerate(self.MODES):
            user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.mode == k else 0), 10 + i, name)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, 99, "إيقاف البرنامج")
        pt = W.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_LAYOUTRTL, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == 1:
            self.on_open()
        elif 10 <= cmd < 10 + len(self.MODES):
            self.on_mode(self.MODES[cmd - 10][0])
            self._notify(NIM_MODIFY)
        elif cmd == 99:
            self.remove()
            self.on_quit()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                threading.Thread(target=self.on_open, daemon=True).start()
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._menu()
            return 0
        if msg == WM_TIMER:
            self._notify(NIM_MODIFY)
            return 0
        if msg == getattr(self, "msg_taskbar", -1):  # Explorer restarted
            self._notify(NIM_ADD)
            return 0
        if msg == WM_DESTROY:
            self.remove()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
