// RX16 Control Center - native host (.NET Framework 4.8, compiled with the csc.exe that ships with Windows).
// Owns the tray icon + notifications (so Windows shows "RX16 Control Center", not "Python")
// and runs the Python service (x16power.pyw --no-tray), restarting it after updates.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("RX16 Control Center")]
[assembly: AssemblyProduct("RX16 Control Center")]
[assembly: AssemblyDescription("Battery, sensors and power modes for the ACEMAGIC RX16")]
[assembly: AssemblyCompany("RX16")]
[assembly: AssemblyCopyright("RX16")]
[assembly: AssemblyVersion("1.4.5.0")]
[assembly: AssemblyFileVersion("1.4.5.0")]

static class RX16
{
    const string Api = "http://127.0.0.1:47816";
    const string TaskName = "RX16 Control Center";
    const int ExitRestart = 3;

    static readonly string Dir = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string AppDir = Path.Combine(Dir, "app");   // the Python service and its files
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

    static NotifyIcon tray;
    static Process service;
    static bool quitting;
    static string currentMode = "";
    static string iconKey = "";
    static readonly List<DateTime> crashes = new List<DateTime>();

    [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr h);
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool SetDllDirectory(string path);

    static readonly string WebViewDir = Path.Combine(AppDir, "webview2");
    static Control ui;   // marshals work onto the UI thread

    // WebView2's .NET assemblies + native loader live in app\webview2 (keeps the top folder clean)
    static Assembly ResolveWebView(object sender, ResolveEventArgs e)
    {
        var f = Path.Combine(WebViewDir, new AssemblyName(e.Name).Name + ".dll");
        return File.Exists(f) ? Assembly.LoadFrom(f) : null;
    }

    static void ShowWindow()
    {
        if (ui == null) return;
        ui.BeginInvoke(new Action(() =>
        {
            try
            {
                MainWindow.ShowOrActivate(Api + "/",
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "X16Power", "webview2"),
                    Icon.ExtractAssociatedIcon(Application.ExecutablePath));
            }
            catch (Exception ex)
            {
                tray.ShowBalloonTip(6000, "RX16 Control Center", "WebView2: " + ex.Message, ToolTipIcon.Error);
            }
        }));
    }

    // Long-poll: the service answers when someone asked to open the window (e.g. the desktop shortcut)
    static void WaitOpenLoop()
    {
        while (!quitting)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(Api + "/api/wait-open");
                req.Timeout = 70000; req.ReadWriteTimeout = 70000; req.Proxy = null;
                using (var resp = req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    if (rd.ReadToEnd().Contains("\"open\": true")) ShowWindow();
            }
            catch { Thread.Sleep(2000); }
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        AppDomain.CurrentDomain.AssemblyResolve += ResolveWebView;
        SetDllDirectory(WebViewDir);
        bool background = Array.IndexOf(args, "--background") >= 0;
        bool admin = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);

        // Already running: just open the window.
        if (Ping()) { if (!background) { AllowSetForegroundWindow(-1); Post("/api/open"); } return; }

        // Normal (non-admin) start while the admin logon task exists: let the task host us elevated.
        if (!admin && Array.IndexOf(args, "--host") < 0 && TaskExists())
        {
            Run("schtasks", "/run /tn \"" + TaskName + "\"");
            for (int i = 0; i < 60 && !Ping(); i++) Thread.Sleep(250);
            if (Ping())
            {
                if (!background) { AllowSetForegroundWindow(-1); Post("/api/open"); }
                return;
            }
            // task disabled ("start with Windows" off) or failed: host the app here, without admin rights
        }

        bool created;
        using (var mutex = new Mutex(true, "Local\\RX16ControlCenterHost", out created))
        {
            if (!created) return;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            ui = new Control();
            ui.CreateControl();

            StartService();
            BuildTray();
            StartTrayPin();
            new Thread(WaitOpenLoop) { IsBackground = true }.Start();
            if (!background)
                new Thread(() => { for (int i = 0; i < 60 && !Ping(); i++) Thread.Sleep(250); ShowWindow(); }) { IsBackground = true }.Start();

            var timer = new System.Windows.Forms.Timer { Interval = 3000 };
            timer.Tick += (s, e) => Refresh();
            timer.Start();

            // floating on-screen stats (overlay) + global hotkey Ctrl+Shift+O
            overlay = new OverlayForm((x, y) => Post("/api/overlay", "{\"x\":" + x + ",\"y\":" + y + "}"));
            hotkeys = new HotkeyWindow(() => { Post("/api/overlay", "{\"toggle\":true}"); RefreshOverlay(); });
            var otimer = new System.Windows.Forms.Timer { Interval = 1000 };
            otimer.Tick += (s, e) => RefreshOverlay();
            otimer.Start();
            Refresh();
            Application.Run();
            tray.Visible = false;
        }
    }

    // ------------------------------------------------------------------ service process
    static string PythonW()
    {
        // bundled embeddable Python (installed by the setup) first, then a system Python on PATH
        var bundled = Path.Combine(AppDir, "python", "pythonw.exe");
        if (File.Exists(bundled)) return bundled;
        foreach (var p in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            try { var f = Path.Combine(p.Trim(), "pythonw.exe"); if (File.Exists(f)) return f; } catch { }
        }
        return "pythonw.exe";
    }

    static void StartService()
    {
        var psi = new ProcessStartInfo(PythonW(), "\"" + Path.Combine(AppDir, "x16power.pyw") + "\" --background --no-tray --wait-port")
        {
            WorkingDirectory = AppDir, UseShellExecute = false, CreateNoWindow = true
        };
        service = Process.Start(psi);
        service.EnableRaisingEvents = true;
        service.Exited += (s, e) =>
        {
            int code = 0;
            try { code = service.ExitCode; } catch { }
            if (quitting) return;
            if (code == 0) { Quit(); return; }                                   // "إيقاف" from the window
            if (code != ExitRestart)                                             // crash: max 5 restarts / 2 min
            {
                crashes.RemoveAll(t => (DateTime.Now - t).TotalMinutes > 2);
                crashes.Add(DateTime.Now);
                if (crashes.Count > 5) { tray.ShowBalloonTip(8000, "RX16 Control Center", T("الخدمة توقفت أكثر من مرة. شغّل البرنامج من جديد.", "The service stopped several times. Please start the app again."), ToolTipIcon.Error); Thread.Sleep(9000); Quit(); return; }
            }
            Thread.Sleep(800);
            StartService();
        };
    }

    static void Quit()
    {
        quitting = true;
        if (tray != null) tray.Visible = false;
        Environment.Exit(0);
    }

    // ------------------------------------------------------------------ tray
    // key, Arabic, English
    static readonly string[][] Modes = {
        new[] { "adaptive", "متكيّف", "Adaptive" }, new[] { "eco", "اقتصادي", "Eco" },
        new[] { "balanced", "متوسط", "Balanced" }, new[] { "performance", "عالي الأداء", "Performance" } };
    static readonly List<ToolStripMenuItem> modeItems = new List<ToolStripMenuItem>();
    static string lang = "ar";
    static ContextMenuStrip menu;
    static ToolStripItem miOpen, miRestart, miQuit;

    static string T(string ar, string en) { return lang == "en" ? en : ar; }

    // Windows 11 puts new tray icons in the hidden (^) area. Pin ours next to the clock the first time only:
    // Windows records each icon under HKCU\Control Panel\NotifyIconSettings\<id> (ExecutablePath, IsPromoted).
    // If IsPromoted already exists the user has chosen (pinned or hidden it) - leave it alone.
    [DllImport("shell32.dll")] static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid id, uint flags, IntPtr token, out IntPtr path);

    // Windows stores tray paths under known folders as "{KNOWNFOLDERID}\rest", e.g. Program Files =
    // "{6D809377-6AF0-444B-8957-A3773F02200E}\RX16 Control Center\RX16 Control Center.exe".
    static string ExpandKnownFolder(string p)
    {
        if (string.IsNullOrEmpty(p)) return null;
        try
        {
            if (p.StartsWith("{") && p.IndexOf("}\\", StringComparison.Ordinal) > 0)
            {
                int end = p.IndexOf('}');
                IntPtr raw;
                if (SHGetKnownFolderPath(new Guid(p.Substring(1, end - 1)), 0, IntPtr.Zero, out raw) == 0)
                {
                    string folder = Marshal.PtrToStringUni(raw);
                    Marshal.FreeCoTaskMem(raw);
                    p = Path.Combine(folder, p.Substring(end + 2));
                }
            }
            return Path.GetFullPath(p);
        }
        catch { return null; }
    }

    static void StartTrayPin()
    {
        int tries = 0;
        var t = new System.Windows.Forms.Timer { Interval = 5000 };
        t.Tick += (s, e) =>
        {
            tries++;
            bool done = false;
            try
            {
                using (var root = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Control Panel\NotifyIconSettings"))
                    if (root != null)
                        foreach (var name in root.GetSubKeyNames())
                            using (var k = root.OpenSubKey(name, true))
                            {
                                var path = ExpandKnownFolder(k == null ? null : k.GetValue("ExecutablePath") as string);
                                if (path == null || !string.Equals(path, Path.GetFullPath(Application.ExecutablePath),
                                                                   StringComparison.OrdinalIgnoreCase)) continue;
                                if (k.GetValue("IsPromoted") == null) k.SetValue("IsPromoted", 1, Microsoft.Win32.RegistryValueKind.DWord);
                                done = true;
                            }
            }
            catch { }
            if (done || tries >= 24) { t.Stop(); t.Dispose(); }   // entry appears a few seconds after the icon; give up after 2 min
        };
        t.Start();
    }

    static void BuildTray()
    {
        menu = new ContextMenuStrip { ShowImageMargin = false, Font = new Font("Segoe UI", 10f) };
        miOpen = menu.Items.Add("", null, (s, e) => ShowWindow());
        miOpen.Font = new Font("Segoe UI", 10f, FontStyle.Bold);
        menu.Items.Add(new ToolStripSeparator());
        foreach (var m in Modes)
        {
            var key = m[0];
            var item = new ToolStripMenuItem("", null, (s, e) => { Post("/api/mode", "{\"mode\":\"" + key + "\"}"); Refresh(); }) { Tag = m };
            modeItems.Add(item);
            menu.Items.Add(item);
        }
        menu.Items.Add(new ToolStripSeparator());
        miRestart = menu.Items.Add("", null, (s, e) => Post("/api/restart"));
        miQuit = menu.Items.Add("", null, (s, e) => { quitting = true; Post("/api/quit"); Quit(); });
        menu.Opening += (s, e) => { foreach (var it in modeItems) it.Checked = ((string[])it.Tag)[0] == currentMode; };
        LocalizeMenu();

        tray = new NotifyIcon { ContextMenuStrip = menu, Visible = true };
        SetIcon("RX", "#ed1c24", "#ffffff");
        SetTip("RX16 Control Center");
        tray.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) ShowWindow(); };
        tray.BalloonTipClicked += (s, e) => ShowWindow();
    }

    static void LocalizeMenu()
    {
        menu.RightToLeft = lang == "en" ? RightToLeft.No : RightToLeft.Yes;
        miOpen.Text = T("فتح RX16 Control Center", "Open RX16 Control Center");
        foreach (var it in modeItems) { var m = (string[])it.Tag; it.Text = lang == "en" ? m[2] : m[1]; }
        miRestart.Text = T("إعادة تشغيل البرنامج", "Restart app");
        miQuit.Text = T("إيقاف البرنامج", "Quit app");
    }

    static void Refresh()
    {
        string body = Get("/api/tray");
        if (body == null) { SetTip("RX16 Control Center\n" + T("الخدمة تشتغل…", "Service starting…")); return; }
        Dictionary<string, object> st;
        try { st = Json.Deserialize<Dictionary<string, object>>(body); } catch { return; }
        string l = S(st, "lang");
        if (l.Length > 0 && l != lang) { lang = l; LocalizeMenu(); }
        SetIcon(S(st, "text"), S(st, "bg"), S(st, "fg"));
        SetTip(S(st, "tip"));
        currentMode = S(st, "mode");
        object notes;
        if (st.TryGetValue("notes", out notes) && notes is System.Collections.ArrayList)
            foreach (Dictionary<string, object> n in (System.Collections.ArrayList)notes)
                tray.ShowBalloonTip(8000, S(n, "title"), S(n, "text"), S(n, "kind") == "info" ? ToolTipIcon.Info : ToolTipIcon.Warning);
    }

    static OverlayForm overlay;
    static HotkeyWindow hotkeys;
    static bool overlayBusy;

    static void RefreshOverlay()
    {
        if (overlayBusy) return;
        overlayBusy = true;
        string body = Get("/api/overlay");
        overlayBusy = false;
        if (body == null) { overlay.HideOverlay(); return; }
        try { overlay.Apply(Json.Deserialize<Dictionary<string, object>>(body)); } catch { }
    }

    static string S(Dictionary<string, object> d, string k) { object v; return d.TryGetValue(k, out v) && v != null ? v.ToString() : ""; }

    static void SetIcon(string text, string bg, string fg)
    {
        string key = text + bg + fg;
        if (key == iconKey) return;
        iconKey = key;
        int size = Math.Max(32, SystemInformation.SmallIconSize.Width * 2);
        using (var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb))
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
            float r = size * 0.22f;
            using (var path = new GraphicsPath())
            {
                path.AddArc(0, 0, r * 2, r * 2, 180, 90);
                path.AddArc(size - r * 2 - 1, 0, r * 2, r * 2, 270, 90);
                path.AddArc(size - r * 2 - 1, size - r * 2 - 1, r * 2, r * 2, 0, 90);
                path.AddArc(0, size - r * 2 - 1, r * 2, r * 2, 90, 90);
                path.CloseFigure();
                using (var b = new SolidBrush(ColorTranslator.FromHtml(bg))) g.FillPath(b, path);
            }
            float em = size * (text.Length <= 2 ? 0.58f : 0.44f);
            using (var f = new Font("Segoe UI", em, FontStyle.Bold, GraphicsUnit.Pixel))
            using (var b = new SolidBrush(ColorTranslator.FromHtml(fg)))
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                g.DrawString(text, f, b, new RectangleF(0, size * 0.03f, size, size), sf);
            IntPtr h = bmp.GetHicon();
            var old = tray.Icon;
            tray.Icon = (Icon)Icon.FromHandle(h).Clone();
            DestroyIcon(h);
            if (old != null) old.Dispose();
        }
    }

    // NotifyIcon.Text is capped at 63 chars in .NET Framework; Windows allows 127.
    static void SetTip(string text)
    {
        if (text.Length > 127) text = text.Substring(0, 127);
        var t = typeof(NotifyIcon);
        var flags = BindingFlags.NonPublic | BindingFlags.Instance;
        try
        {
            t.GetField("text", flags).SetValue(tray, text);
            if ((bool)t.GetField("added", flags).GetValue(tray))
                t.GetMethod("UpdateIcon", flags).Invoke(tray, new object[] { true });
        }
        catch { tray.Text = text.Length > 63 ? text.Substring(0, 63) : text; }
    }

    // ------------------------------------------------------------------ helpers
    static bool Ping()
    {
        var s = Get("/api/ping");
        return s != null && s.Contains("x16power");
    }

    static string Get(string path)
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Api + path);
            req.Timeout = 1500; req.Proxy = null;
            using (var resp = req.GetResponse())
            using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8)) return rd.ReadToEnd();
        }
        catch { return null; }
    }

    static void Post(string path, string json = "{}")
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Api + path);
            req.Method = "POST"; req.ContentType = "application/json"; req.Timeout = 5000; req.Proxy = null;
            var data = Encoding.UTF8.GetBytes(json);
            using (var s = req.GetRequestStream()) s.Write(data, 0, data.Length);
            using (req.GetResponse()) { }
        }
        catch { }
    }

    internal static int I(Dictionary<string, object> d, string k, int def)
    {
        object v; if (!d.TryGetValue(k, out v) || v == null) return def;
        try { return Convert.ToInt32(v); } catch { return def; }
    }

    static bool TaskExists() { return Run("schtasks", "/query /tn \"" + TaskName + "\"") == 0; }

    static int Run(string exe, string args)
    {
        try
        {
            var p = Process.Start(new ProcessStartInfo(exe, args) { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true });
            p.WaitForExit(15000);
            return p.ExitCode;
        }
        catch { return -1; }
    }
}

// ---------------------------------------------------------------------- global hotkey (Ctrl+Shift+O)
class HotkeyWindow : NativeWindow
{
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr h, int id, uint mods, uint vk);
    readonly Action onHotkey;
    public HotkeyWindow(Action a)
    {
        onHotkey = a;
        CreateHandle(new CreateParams());
        RegisterHotKey(Handle, 1, 0x0002 | 0x0004 | 0x4000, (uint)Keys.O);   // CTRL | SHIFT | NOREPEAT
    }
    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x0312) onHotkey();   // WM_HOTKEY
        base.WndProc(ref m);
    }
}



