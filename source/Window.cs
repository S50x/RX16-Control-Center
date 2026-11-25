// RX16 Control Center - the app window, hosted inside the exe with WebView2 (the Edge engine that ships
// with Windows 11). Replaces opening a separate Edge "--app" window: one taskbar icon, the app's own name,
// no Edge warnings. Closing the window disposes it (frees ~140 MB); the tray icon keeps the app running.
using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class MainWindow : Form
{
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);

    static MainWindow current;
    readonly WebView2 web;
    static readonly Color Bg = Color.FromArgb(11, 12, 15);

    // Browser switches: the window only shows our local page, so skip Edge's background housekeeping.
    const string BrowserArgs =
        "--disable-background-networking --disable-component-update --disable-sync --disable-domain-reliability " +
        "--disable-breakpad --disk-cache-size=1 --media-cache-size=1 --disable-gpu-shader-disk-cache " +
        "--disable-features=Translate,msEdgeSidebarV2,msEdgeShopping,msEdgeCollections,AutofillServerCommunication,OptimizationHints,MediaRouter";

    public static void ShowOrActivate(string url, string dataDir, Icon icon)
    {
        if (current != null && !current.IsDisposed)
        {
            if (current.WindowState == FormWindowState.Minimized) current.WindowState = FormWindowState.Normal;
            current.Show();
            current.Activate();
            SetForegroundWindow(current.Handle);
            return;
        }
        current = new MainWindow(url, dataDir, icon);
        current.FormClosed += (s, e) => { current = null; };
        current.Show();
        current.Activate();
        SetForegroundWindow(current.Handle);
    }

    MainWindow(string url, string dataDir, Icon icon)
    {
        Text = "RX16 Control Center";
        Icon = icon;
        BackColor = Bg;
        StartPosition = FormStartPosition.Manual;

        // 1240x820 at 100 %, scaled for DPI but never bigger than ~92 % of the screen's work area
        var area = Screen.FromPoint(Cursor.Position).WorkingArea;
        float scale = DeviceDpi / 96f;
        int w = (int)Math.Min(1240 * scale, area.Width * 0.92), h = (int)Math.Min(820 * scale, area.Height * 0.92);
        Bounds = new Rectangle(area.Left + (area.Width - w) / 2, area.Top + (area.Height - h) / 2, w, h);
        MinimumSize = new Size((int)Math.Min(900 * scale, area.Width * 0.8), (int)Math.Min(600 * scale, area.Height * 0.8));

        web = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Bg };
        web.CreationProperties = new CoreWebView2CreationProperties
        {
            UserDataFolder = dataDir,
            AdditionalBrowserArguments = BrowserArgs,
        };
        web.CoreWebView2InitializationCompleted += (s, e) =>
        {
            if (!e.IsSuccess)
            {
                MessageBox.Show(this, "WebView2: " + e.InitializationException.Message, "RX16 Control Center",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            var st = web.CoreWebView2.Settings;
            st.AreDevToolsEnabled = false;
            st.IsStatusBarEnabled = false;
            st.IsZoomControlEnabled = false;
            st.IsPasswordAutosaveEnabled = false;
            st.IsGeneralAutofillEnabled = false;
            st.AreBrowserAcceleratorKeysEnabled = false;      // no Ctrl+P / Ctrl+F / F12 browser UI
            web.CoreWebView2.WindowCloseRequested += (a, b) => Close();          // the page's quit button
            web.CoreWebView2.NewWindowRequested += (a, b) => { b.Handled = true; }; // never pop browser windows
            web.CoreWebView2.DocumentTitleChanged += (a, b) =>
            {
                var t = web.CoreWebView2.DocumentTitle;
                if (!string.IsNullOrEmpty(t) && !t.StartsWith("127.0.0.1")) Text = t;
            };
        };
        Controls.Add(web);
        web.Source = new Uri(url);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int on = 1;
        DwmSetWindowAttribute(Handle, 20, ref on, 4);   // DWMWA_USE_IMMERSIVE_DARK_MODE: dark title bar
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        base.OnFormClosed(e);
        web.Dispose();                                   // shuts the WebView2 processes down, frees the RAM
    }
}
