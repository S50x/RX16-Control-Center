// RX16 Control Center - Setup wizard + uninstaller (one exe).
// Build: source\build-setup.ps1 (zips the app as an embedded "payload.zip" resource, compiles with Windows' csc.exe).
//   Setup:      "RX16 Control Center Setup x.y.z.exe"          -> wizard
//   Uninstall:  "<install dir>\uninstall.exe" /uninstall        -> registered in Settings > Apps
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("RX16 Control Center Setup")]
[assembly: AssemblyProduct("RX16 Control Center")]
[assembly: AssemblyCompany("RX16")]
[assembly: AssemblyCopyright("RX16")]
[assembly: AssemblyVersion("1.4.7.0")]
[assembly: AssemblyFileVersion("1.4.7.0")]

static class App
{
    public const string Name = "RX16 Control Center";
    public const string Version = "1.4.7";
    public const string Publisher = "RX16";
    public const string ExeName = "RX16 Control Center.exe";
    public const string TaskName = "RX16 Control Center";
    public const string UninstKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\RX16ControlCenter";
    public static readonly Color Bg = Color.FromArgb(22, 24, 29), Panel2 = Color.FromArgb(28, 31, 37),
        Head = Color.FromArgb(11, 12, 15), Line = Color.FromArgb(38, 42, 50), Text = Color.FromArgb(238, 240, 244),
        Muted = Color.FromArgb(138, 145, 158), Red = Color.FromArgb(237, 28, 36), Green = Color.FromArgb(47, 208, 122),
        Amber = Color.FromArgb(255, 176, 32);

    [STAThread]
    static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Any(a => a.Equals("/update", StringComparison.OrdinalIgnoreCase)))
            Application.Run(new UpdateForm());
        else if (args.Any(a => a.Equals("/uninstall", StringComparison.OrdinalIgnoreCase)))
            Application.Run(new UninstallForm(args.Any(a => a.Equals("/quiet", StringComparison.OrdinalIgnoreCase))));
        else
            Application.Run(new Wizard());
    }

    public static string InstalledDir()
    {
        using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(UninstKey))
            return k == null ? null : k.GetValue("InstallLocation") as string;
    }
}

// ============================================================================ install / uninstall work
static class Work
{
    static void Run(string exe, string args)
    {
        try
        {
            var p = Process.Start(new ProcessStartInfo(exe, args) { CreateNoWindow = true, UseShellExecute = false,
                RedirectStandardOutput = true, RedirectStandardError = true });
            p.StandardOutput.ReadToEnd(); p.WaitForExit(30000);
        }
        catch { }
    }

    /// Ask a running copy to quit (it puts the fan/limits back), then make sure nothing is left.
    public static void StopRunningApp()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:47816/api/quit");
            req.Method = "POST"; req.ContentType = "application/json"; req.Timeout = 3000; req.Proxy = null;
            var b = Encoding.UTF8.GetBytes("{}");
            using (var s = req.GetRequestStream()) s.Write(b, 0, b.Length);
            using (req.GetResponse()) { }
        }
        catch { }
        for (int i = 0; i < 20 && Process.GetProcessesByName("RX16 Control Center").Length > 0; i++) Thread.Sleep(250);
        foreach (var p in Process.GetProcessesByName("RX16 Control Center")) { try { p.Kill(); p.WaitForExit(3000); } catch { } }
        // the Python service (bundled or system pythonw) running x16power.pyw
        foreach (var p in Process.GetProcessesByName("pythonw"))
        {
            try
            {
                string cmd = CommandLine(p.Id);
                if (cmd.IndexOf("x16power.pyw", StringComparison.OrdinalIgnoreCase) >= 0) { p.Kill(); p.WaitForExit(3000); }
            }
            catch { }
        }
        Thread.Sleep(500);
    }

    static string CommandLine(int pid)
    {
        try
        {
            using (var s = new System.Management.ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE ProcessId=" + pid))
                foreach (System.Management.ManagementObject o in s.Get()) return (o["CommandLine"] as string) ?? "";
        }
        catch { }
        return "";
    }

    public static long PayloadSize()
    {
        using (var z = Payload()) return z.Entries.Sum(e => e.Length);
    }

    static ZipArchive Payload()
    {
        return new ZipArchive(Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"), ZipArchiveMode.Read);
    }

    public static void Extract(string dir, Action<int, string> progress)
    {
        // a clean app\ folder, so files removed in newer versions don't linger
        var app = Path.Combine(dir, "app");
        if (Directory.Exists(app)) Directory.Delete(app, true);
        Directory.CreateDirectory(dir);
        using (var z = Payload())
        {
            int i = 0, n = z.Entries.Count;
            foreach (var e in z.Entries)
            {
                i++;
                var dest = Path.GetFullPath(Path.Combine(dir, e.FullName.Replace('/', '\\')));
                if (!dest.StartsWith(Path.GetFullPath(dir), StringComparison.OrdinalIgnoreCase)) continue;   // zip-slip guard
                if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(dest); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                e.ExtractToFile(dest, true);
                progress(i * 100 / n, e.FullName);
            }
        }
    }

    public static void CopyUninstaller(string dir)
    {
        File.Copy(Application.ExecutablePath, Path.Combine(dir, "uninstall.exe"), true);
    }

    public static void RegisterUninstall(string dir, long bytes)
    {
        using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).CreateSubKey(App.UninstKey))
        {
            string exe = Path.Combine(dir, App.ExeName), un = Path.Combine(dir, "uninstall.exe");
            k.SetValue("DisplayName", App.Name);
            k.SetValue("DisplayVersion", App.Version);
            k.SetValue("Publisher", App.Publisher);
            k.SetValue("DisplayIcon", exe + ",0");
            k.SetValue("InstallLocation", dir);
            k.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
            k.SetValue("UninstallString", "\"" + un + "\" /uninstall");
            k.SetValue("QuietUninstallString", "\"" + un + "\" /uninstall /quiet");
            k.SetValue("NoModify", 1, RegistryValueKind.DWord);
            k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            k.SetValue("EstimatedSize", (int)(bytes / 1024), RegistryValueKind.DWord);
        }
    }

    public static void Shortcut(string lnk, string target, string workDir)
    {
        var t = Type.GetTypeFromProgID("WScript.Shell");
        object sh = Activator.CreateInstance(t);
        object sc = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, sh, new object[] { lnk });
        var st = sc.GetType();
        st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, sc, new object[] { target });
        st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, sc, new object[] { workDir });
        st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, sc, new object[] { target + ",0" });
        st.InvokeMember("Description", BindingFlags.SetProperty, null, sc, new object[] { App.Name });
        st.InvokeMember("Save", BindingFlags.InvokeMethod, null, sc, null);
    }

    public static string DesktopLnk { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), App.Name + ".lnk"); } }
    public static string StartLnk { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms), App.Name + ".lnk"); } }

    static string X(string s) { return System.Security.SecurityElement.Escape(s); }

    /// Logon task with highest privileges: admin rights (power limits) without a UAC prompt every time.
    public static void RegisterTask(string dir, bool atLogon)
    {
        string user = System.Security.Principal.WindowsIdentity.GetCurrent().Name;
        string exe = Path.Combine(dir, App.ExeName);
        string xml =
@"<?xml version=""1.0"" encoding=""UTF-16""?>
<Task version=""1.4"" xmlns=""http://schemas.microsoft.com/windows/2004/02/mit/task"">
  <RegistrationInfo><Description>RX16 Control Center</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>" + (atLogon ? "true" : "false") + @"</Enabled><UserId>" + X(user) + @"</UserId></LogonTrigger></Triggers>
  <Principals><Principal id=""Author""><UserId>" + X(user) + @"</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context=""Author""><Exec><Command>" + X(exe) + @"</Command><Arguments>--background</Arguments><WorkingDirectory>" + X(dir) + @"</WorkingDirectory></Exec></Actions>
</Task>";
        string tmp = Path.Combine(Path.GetTempPath(), "rx16-task.xml");
        File.WriteAllText(tmp, xml, Encoding.Unicode);
        Run("schtasks.exe", "/Create /TN \"" + App.TaskName + "\" /XML \"" + tmp + "\" /F");
        try { File.Delete(tmp); } catch { }
        // the per-user startup shortcut from early versions is replaced by the task
        try { File.Delete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "X16 Power.lnk")); } catch { }
    }

    public static void Uninstall(string dir, bool removeSettings, Action<string> log)
    {
        log("إيقاف البرنامج…"); StopRunningApp();
        log("حذف التشغيل مع ويندوز…"); Run("schtasks.exe", "/Delete /TN \"" + App.TaskName + "\" /F");
        log("إرجاع إعدادات المعالج في ويندوز للوضع الطبيعي…");
        Run("powercfg.exe", "/setacvalueindex scheme_current sub_processor PROCTHROTTLEMAX 100");
        Run("powercfg.exe", "/setdcvalueindex scheme_current sub_processor PROCTHROTTLEMAX 100");
        Run("powercfg.exe", "/setactive scheme_current");
        log("حذف الاختصارات…");
        foreach (var l in new[] { DesktopLnk, StartLnk }) { try { File.Delete(l); } catch { } }
        log("حذفه من قائمة البرامج المثبّتة…");
        try { RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).DeleteSubKeyTree(App.UninstKey, false); } catch { }
        if (removeSettings)
        {
            log("حذف إعداداتك…");
            try { Directory.Delete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "X16Power"), true); } catch { }
        }
        log("حذف ملفات البرنامج…");
        if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir) && File.Exists(Path.Combine(dir, App.ExeName)))
        {
            foreach (var f in Directory.GetFiles(dir)) { if (!f.EndsWith("uninstall.exe", StringComparison.OrdinalIgnoreCase)) try { File.Delete(f); } catch { } }
            foreach (var d in Directory.GetDirectories(dir)) { try { Directory.Delete(d, true); } catch { } }
            // uninstall.exe can't delete itself while running: remove the folder right after we exit
            Process.Start(new ProcessStartInfo("cmd.exe", "/c ping 127.0.0.1 -n 3 > nul & rmdir /s /q \"" + dir + "\"")
                { CreateNoWindow = true, UseShellExecute = false, WindowStyle = ProcessWindowStyle.Hidden });
        }
    }
}

// ============================================================================ UI helpers
class Ui
{
    public static Font F(float size, FontStyle st = FontStyle.Regular) { return new Font("Segoe UI", size, st); }

    public static Label L(string text, float size, Color c, FontStyle st = FontStyle.Regular)
    {
        return new Label { Text = text, Font = F(size, st), ForeColor = c, AutoSize = false, BackColor = Color.Transparent };
    }

    public static Button B(string text, bool primary)
    {
        var b = new Button { Text = text, Font = F(10f, primary ? FontStyle.Bold : FontStyle.Regular), FlatStyle = FlatStyle.Flat,
            ForeColor = Color.White, BackColor = primary ? App.Red : App.Panel2, Size = new Size(118, 36), Cursor = Cursors.Hand };
        b.FlatAppearance.BorderColor = primary ? App.Red : App.Line;
        b.FlatAppearance.MouseOverBackColor = primary ? Color.FromArgb(255, 59, 66) : Color.FromArgb(40, 44, 52);
        return b;
    }

    public static CheckBox C(string text, bool on)
    {
        return new CheckBox { Text = text, Checked = on, Font = F(10.5f), ForeColor = App.Text, AutoSize = true, BackColor = Color.Transparent };
    }

    public static Bitmap Logo(int s)
    {
        var bmp = new Bitmap(s, s);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
            using (var p = new System.Drawing.Drawing2D.GraphicsPath())
            {
                float r = s * 0.22f;
                p.AddArc(0, 0, r * 2, r * 2, 180, 90); p.AddArc(s - r * 2 - 1, 0, r * 2, r * 2, 270, 90);
                p.AddArc(s - r * 2 - 1, s - r * 2 - 1, r * 2, r * 2, 0, 90); p.AddArc(0, s - r * 2 - 1, r * 2, r * 2, 90, 90);
                p.CloseFigure();
                using (var br = new System.Drawing.Drawing2D.LinearGradientBrush(new Point(0, 0), new Point(s, s),
                    Color.FromArgb(255, 74, 80), Color.FromArgb(179, 15, 22))) g.FillPath(br, p);
            }
            using (var f = new Font("Segoe UI", s * 0.4f, FontStyle.Bold, GraphicsUnit.Pixel))
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                g.DrawString("RX", f, Brushes.White, new RectangleF(0, s * 0.03f, s, s), sf);
        }
        return bmp;
    }
}

// ============================================================================ wizard
class Wizard : Form
{
    [System.Runtime.InteropServices.DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int a, ref int v, int s);
    protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); int on = 1; DwmSetWindowAttribute(Handle, 20, ref on, 4); }
    readonly Panel header = new Panel(), body = new Panel(), footer = new Panel();
    readonly Label hTitle, hSub;
    readonly Button back, next, cancel;
    readonly List<Panel> pages = new List<Panel>();
    int page;
    readonly string[] titles = { "مرحباً", "فحص المتطلبات", "مكان التثبيت", "الخيارات", "جاري التثبيت", "اكتمل التثبيت" };

    TextBox pathBox; Label spaceLbl;
    CheckBox cbDesktop, cbStart, cbAutorun, cbLaunch;
    ProgressBar bar; Label stepLbl; TextBox logBox;
    Label doneTitle, doneText;
    bool installing, installed, failed;
    readonly long need;
    readonly string previous = App.InstalledDir();

    public Wizard()
    {
        Text = "تثبيت " + App.Name;
        RightToLeft = RightToLeft.Yes; RightToLeftLayout = true;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(700, 500);
        BackColor = App.Bg; Font = Ui.F(10f);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        AutoScaleMode = AutoScaleMode.Dpi;
        need = Work.PayloadSize();

        header.SetBounds(0, 0, 700, 88); header.BackColor = App.Head;
        var logo = new PictureBox { Image = Ui.Logo(52), SizeMode = PictureBoxSizeMode.AutoSize, Location = new Point(24, 18), BackColor = Color.Transparent };
        hTitle = Ui.L(App.Name + "  " + App.Version, 15f, App.Text, FontStyle.Bold); hTitle.SetBounds(92, 14, 560, 34);
        hSub = Ui.L("", 10.5f, App.Muted); hSub.SetBounds(92, 48, 560, 26);
        header.Controls.AddRange(new Control[] { logo, hTitle, hSub });
        var redLine = new Panel { BackColor = App.Red }; redLine.SetBounds(0, 86, 700, 2); header.Controls.Add(redLine);

        body.SetBounds(0, 88, 700, 344); body.BackColor = App.Bg;
        footer.SetBounds(0, 432, 700, 68); footer.BackColor = App.Head;
        cancel = Ui.B("إلغاء", false); cancel.Location = new Point(24, 16);
        next = Ui.B("التالي", true); next.Location = new Point(700 - 24 - 118, 16);
        back = Ui.B("رجوع", false); back.Location = new Point(700 - 24 - 118 - 128, 16);
        footer.Controls.AddRange(new Control[] { cancel, back, next });
        Controls.AddRange(new Control[] { header, body, footer });

        BuildPages();
        back.Click += (s, e) => Go(page - 1);
        next.Click += (s, e) => Next();
        cancel.Click += (s, e) => Close();
        FormClosing += (s, e) =>
        {
            if (installing) { e.Cancel = true; return; }
            if (!installed && !failed && page < 4 &&
                MessageBox.Show(this, "تبي توقف التثبيت؟", Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                    MessageBoxDefaultButton.Button2, MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign) != DialogResult.Yes)
                e.Cancel = true;
        };
        Go(0);
    }

    Panel NewPage()
    {
        var p = new Panel { Dock = DockStyle.Fill, BackColor = App.Bg, Visible = false, Padding = new Padding(28, 22, 28, 10) };
        body.Controls.Add(p); pages.Add(p); return p;
    }

    static Label Para(Panel p, string text, int y, int h, float size = 10.5f, Color? c = null, FontStyle st = FontStyle.Regular)
    {
        var l = Ui.L(text, size, c ?? App.Text, st); l.SetBounds(28, y, 644, h); p.Controls.Add(l); return l;
    }

    void BuildPages()
    {
        // 1 welcome
        var p = NewPage();
        Para(p, "مرحباً بك في معالج تثبيت " + App.Name, 22, 32, 14f, App.Text, FontStyle.Bold);
        Para(p, previous != null
            ? "البرنامج مثبّت عندك من قبل. بيتحدّث للإصدار " + App.Version + " وإعداداتك بتبقى مثل ما هي."
            : "هذا المعالج بيثبّت " + App.Name + " الإصدار " + App.Version + " على جهازك.", 62, 48, 10.5f, App.Muted);
        Para(p, "• مراقبة البطارية ودورات الشحن وعمرها\n• أوضاع الطاقة والتحكم بواط المعالج (RyzenAdj)\n• حرارة وتردد وضغط المعالج وكرت الشاشة\n• الشاشة العائمة، والبرامج الشغالة، والإشعارات\n\nكل شي يشتغل من داخل البرنامج: ما يحتاج Python ولا أي برنامج ثاني.",
            120, 150, 10.5f);
        Para(p, "اضغط «التالي» عشان تكمل.", 290, 26, 10f, App.Muted);

        // 2 requirements
        p = NewPage();
        Para(p, "فحصت جهازك:", 18, 28, 12f, App.Text, FontStyle.Bold);
        int y = 56;
        foreach (var r in Requirements())
        {
            var mark = Ui.L(r.Item1 == 1 ? "✓" : r.Item1 == 0 ? "!" : "✗", 13f, r.Item1 == 1 ? App.Green : r.Item1 == 0 ? App.Amber : App.Red, FontStyle.Bold);
            mark.SetBounds(28, y - 2, 26, 26); p.Controls.Add(mark);
            var t = Ui.L(r.Item2, 10.5f, App.Text); t.SetBounds(58, y, 614, 24); p.Controls.Add(t);
            var d = Ui.L(r.Item3, 9.5f, App.Muted); d.SetBounds(58, y + 22, 614, 22); p.Controls.Add(d);
            y += 50;
        }

        // 3 location
        p = NewPage();
        Para(p, "وين تبي تثبّت البرنامج؟", 18, 28, 12f, App.Text, FontStyle.Bold);
        Para(p, "اضغط «التالي» للمكان المقترح، أو «تصفّح» عشان تختار مكان ثاني.", 52, 26, 10f, App.Muted);
        pathBox = new TextBox { Font = Ui.F(10.5f), BackColor = App.Panel2, ForeColor = App.Text, BorderStyle = BorderStyle.FixedSingle,
            RightToLeft = RightToLeft.No, Text = previous ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), App.Name) };
        pathBox.SetBounds(28, 96, 520, 30); p.Controls.Add(pathBox);
        var browse = Ui.B("تصفّح…", false); browse.SetBounds(556, 94, 116, 32); p.Controls.Add(browse);
        browse.Click += (s, e) =>
        {
            using (var d = new FolderBrowserDialog { Description = "اختر مكان التثبيت", SelectedPath = pathBox.Text })
                if (d.ShowDialog(this) == DialogResult.OK)
                    pathBox.Text = d.SelectedPath.EndsWith(App.Name) ? d.SelectedPath : Path.Combine(d.SelectedPath, App.Name);
        };
        spaceLbl = Para(p, "", 140, 50, 10f, App.Muted);
        pathBox.TextChanged += (s, e) => UpdateSpace();
        UpdateSpace();

        // 4 options
        p = NewPage();
        Para(p, "اختر اللي تبيه:", 18, 28, 12f, App.Text, FontStyle.Bold);
        cbDesktop = Ui.C("اختصار على سطح المكتب", true); cbDesktop.Location = new Point(28, 60);
        cbStart = Ui.C("اختصار في قائمة ابدأ", true); cbStart.Location = new Point(28, 96);
        cbAutorun = Ui.C("يشتغل مع تشغيل ويندوز (بصلاحيات مدير، يحتاجها التحكم بالواط)", true); cbAutorun.Location = new Point(28, 132);
        p.Controls.AddRange(new Control[] { cbDesktop, cbStart, cbAutorun });
        Para(p, "إعداداتك (الأوضاع، الواط، الشاشة العائمة، اللغة) محفوظة في مجلد X16Power داخل AppData، وبتبقى مثل ما هي.",
            190, 46, 9.5f, App.Muted);
        Para(p, "اضغط «تثبيت» عشان تبدأ.", 250, 26, 10f, App.Muted);

        // 5 installing
        p = NewPage();
        stepLbl = Para(p, "جاري التحضير…", 22, 28, 11f, App.Text, FontStyle.Bold);
        bar = new ProgressBar { Style = ProgressBarStyle.Continuous }; bar.SetBounds(28, 62, 644, 22); p.Controls.Add(bar);
        logBox = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BackColor = App.Panel2,
            ForeColor = App.Muted, BorderStyle = BorderStyle.None, Font = Ui.F(9.5f) };
        logBox.SetBounds(28, 100, 644, 220); p.Controls.Add(logBox);

        // 6 done
        p = NewPage();
        doneTitle = Para(p, "", 22, 34, 14f, App.Text, FontStyle.Bold);
        doneText = Para(p, "", 66, 120, 10.5f, App.Muted);
        cbLaunch = Ui.C("شغّل " + App.Name + " الحين", true); cbLaunch.Location = new Point(28, 200); p.Controls.Add(cbLaunch);
    }

    IEnumerable<Tuple<int, string, string>> Requirements()
    {
        // 1 = ok, 0 = works with limits, -1 = blocking
        yield return Tuple.Create(Environment.Is64BitOperatingSystem ? 1 : -1, "ويندوز \u200E64-bit",
            Environment.Is64BitOperatingSystem ? "موجود" : "البرنامج يحتاج ويندوز 64-bit");
        int net = 0;
        using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(@"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"))
            if (k != null) net = Convert.ToInt32(k.GetValue("Release", 0));
        yield return Tuple.Create(net >= 528040 ? 1 : -1, "\u200E.NET Framework 4.8", net >= 528040 ? "موجود في ويندوز" : "ثبّته من موقع مايكروسوفت");
        string wv = null;
        foreach (var path in new[] { @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                                     @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" })
            using (var k = Registry.LocalMachine.OpenSubKey(path)) if (k != null && wv == null) wv = k.GetValue("pv") as string;
        yield return Tuple.Create(wv != null ? 1 : 0, "WebView2 Runtime (لعرض نافذة البرنامج)",
            wv != null ? "موجود، الإصدار \u200E" + wv : "مو موجود: النافذة ما بتفتح لين تثبّته (ابحث عن WebView2 Runtime Evergreen من مايكروسوفت)");
        string cpu = "";
        using (var k = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\CentralProcessor\0"))
            if (k != null) cpu = ((k.GetValue("ProcessorNameString") as string) ?? "").Trim();
        bool ryzen = cpu.IndexOf("Ryzen", StringComparison.OrdinalIgnoreCase) >= 0;
        yield return Tuple.Create(ryzen ? 1 : 0, "المعالج: \u200E" + (cpu.Length > 0 ? cpu : "غير معروف"),
            ryzen ? "معالج AMD Ryzen: التحكم بالواط والحساسات تشتغل" : "مو AMD Ryzen: التحكم بالواط وبعض الحساسات ما بتشتغل");
        bool battery = SystemInformation.PowerStatus.BatteryChargeStatus != BatteryChargeStatus.NoSystemBattery;
        yield return Tuple.Create(battery ? 1 : 0, "البطارية", battery ? "موجودة" : "ما فيه بطارية: صفحة البطارية والشحن ما بتشتغل");
    }

    void UpdateSpace()
    {
        string free = "";
        try { var root = Path.GetPathRoot(Path.GetFullPath(pathBox.Text)); free = "  ·  المتوفر: \u200E" + (new DriveInfo(root).AvailableFreeSpace / 1048576) + " MB\u200E"; }
        catch { }
        spaceLbl.Text = "المساحة المطلوبة: \u200E" + (need / 1048576 + 1) + " MB\u200E" + free;
    }

    void Go(int i)
    {
        page = Math.Max(0, Math.Min(pages.Count - 1, i));
        for (int k = 0; k < pages.Count; k++) pages[k].Visible = k == page;
        hSub.Text = "الخطوة " + Math.Min(page + 1, 5) + " من 5  ·  " + titles[page];
        back.Visible = page > 0 && page < 4;
        cancel.Visible = page < 4;
        next.Text = page == 3 ? "تثبيت" : page == 5 ? "إنهاء" : "التالي";
        next.Visible = page != 4;
        if (page == 1)   // block on hard requirements
            next.Enabled = Requirements().All(r => r.Item1 >= 0);
        else next.Enabled = true;
    }

    void Next()
    {
        if (page == 2)
        {
            string dir;
            try { dir = Path.GetFullPath(pathBox.Text.Trim()).TrimEnd('\\'); }
            catch { MessageBox.Show(this, "المسار غير صحيح.", Text); return; }
            string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            if (Path.GetPathRoot(dir).TrimEnd('\\') == dir || dir.StartsWith(win, StringComparison.OrdinalIgnoreCase))
            { MessageBox.Show(this, "اختر مجلد خاص بالبرنامج، مو جذر القرص أو مجلد ويندوز.", Text); return; }
            pathBox.Text = dir;
        }
        if (page == 3) { Go(4); Install(); return; }
        if (page == 5)
        {
            if (installed && cbLaunch.Checked)
                try { Process.Start(new ProcessStartInfo(Path.Combine(pathBox.Text, App.ExeName)) { WorkingDirectory = pathBox.Text }); } catch { }
            Close(); return;
        }
        Go(page + 1);
    }

    void Log(string s) { BeginInvoke(new Action(() => { logBox.AppendText(s + Environment.NewLine); })); }
    void Step(string s, int pct) { BeginInvoke(new Action(() => { stepLbl.Text = s; if (pct >= 0) bar.Value = Math.Min(100, pct); })); }

    void Install()
    {
        installing = true;
        string dir = pathBox.Text;
        bool desk = cbDesktop.Checked, start = cbStart.Checked, auto = cbAutorun.Checked;
        new Thread(() =>
        {
            try
            {
                Step("إيقاف البرنامج لو كان شغّال…", 2); Log("إيقاف أي نسخة شغّالة من البرنامج");
                Work.StopRunningApp();
                Step("نسخ الملفات…", 5); Log("نسخ الملفات إلى: " + dir);
                Work.Extract(dir, (pct, name) => Step("نسخ الملفات…  " + name, 5 + pct * 80 / 100));
                Work.CopyUninstaller(dir); Log("تجهيز إلغاء التثبيت");
                Step("تسجيل البرنامج في ويندوز…", 88);
                Work.RegisterUninstall(dir, need); Log("إضافة البرنامج لقائمة «التطبيقات» في الإعدادات");
                Work.RegisterTask(dir, auto); Log(auto ? "تشغيله مع ويندوز بصلاحيات مدير" : "مهمة الصلاحيات (بدون تشغيل مع ويندوز)");
                string exe = Path.Combine(dir, App.ExeName);
                if (desk) { Work.Shortcut(Work.DesktopLnk, exe, dir); Log("اختصار سطح المكتب"); }
                else try { File.Delete(Work.DesktopLnk); } catch { }
                if (start) { Work.Shortcut(Work.StartLnk, exe, dir); Log("اختصار قائمة ابدأ"); }
                Step("تم.", 100); Log("اكتمل التثبيت");
                installed = true;
            }
            catch (Exception ex)
            {
                failed = true; Log("خطأ: " + ex.Message);
            }
            BeginInvoke(new Action(() =>
            {
                installing = false;
                doneTitle.Text = installed ? "تم تثبيت " + App.Name + " بنجاح" : "ما اكتمل التثبيت";
                doneTitle.ForeColor = installed ? App.Text : App.Red;
                doneText.Text = installed
                    ? "تقدر تفتحه من سطح المكتب أو قائمة ابدأ أو الأيقونة جنب الساعة.\nولإلغاء التثبيت: الإعدادات ← التطبيقات ← " + App.Name + "."
                    : "صار خطأ وقت التثبيت:\n" + logBox.Lines.LastOrDefault(l => l.StartsWith("خطأ")) + "\nجرّب مرة ثانية.";
                cbLaunch.Visible = installed;
                Go(5);
            }));
        }) { IsBackground = true }.Start();
    }
}

// ============================================================================ silent update (/update)
// Started by the running app (already admin) after it downloaded and SHA-256-checked this setup.
// No wizard: replace the files in the existing install, keep the user's choices, start the app again.
class UpdateForm : Form
{
    [System.Runtime.InteropServices.DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int a, ref int v, int s);
    protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); int on = 1; DwmSetWindowAttribute(Handle, 20, ref on, 4); }

    readonly Label status;

    public UpdateForm()
    {
        Text = App.Name;
        RightToLeft = RightToLeft.Yes; RightToLeftLayout = true;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false; ControlBox = false;
        StartPosition = FormStartPosition.CenterScreen; ClientSize = new Size(460, 150); TopMost = true;
        BackColor = App.Bg; Font = Ui.F(10f); AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var logo = new PictureBox { Image = Ui.Logo(40), SizeMode = PictureBoxSizeMode.AutoSize, Location = new Point(22, 24) };
        var title = Ui.L("جاري تحديث " + App.Name + " إلى " + App.Version, 12f, App.Text, FontStyle.Bold); title.SetBounds(76, 22, 370, 28);
        status = Ui.L("جاري التحضير…", 10f, App.Muted); status.SetBounds(76, 56, 370, 24);
        var bar = new ProgressBar { Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 25 }; bar.SetBounds(22, 100, 416, 16);
        Controls.AddRange(new Control[] { logo, title, status, bar });
        Shown += (s, e) => new Thread(Run) { IsBackground = true }.Start();
    }

    void Say(string s) { BeginInvoke(new Action(() => status.Text = s)); }

    static string Cmd(string exe, string args)
    {
        try
        {
            var p = Process.Start(new ProcessStartInfo(exe, args) { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true });
            string o = p.StandardOutput.ReadToEnd(); p.WaitForExit(30000); return o;
        }
        catch { return ""; }
    }

    void Run()
    {
        string dir = App.InstalledDir() ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), App.Name);
        string err = null;
        try
        {
            // remember the user's startup choices before touching the task
            string xml = Cmd("schtasks.exe", "/Query /TN \"" + App.TaskName + "\" /XML");
            bool hadTask = xml.Contains("<Task");
            var m = System.Text.RegularExpressions.Regex.Match(xml, @"<LogonTrigger>.*?<Enabled>(\w+)</Enabled>", System.Text.RegularExpressions.RegexOptions.Singleline);
            bool atLogon = !m.Success || m.Groups[1].Value != "false";
            var st = System.Text.RegularExpressions.Regex.Match(xml, @"<Settings>.*?<Enabled>(\w+)</Enabled>", System.Text.RegularExpressions.RegexOptions.Singleline);
            bool taskDisabled = st.Success && st.Groups[1].Value == "false";

            Say("إيقاف البرنامج…"); Work.StopRunningApp();
            Say("تحديث الملفات…"); Work.Extract(dir, (pct, name) => { });
            Work.CopyUninstaller(dir);
            Work.RegisterUninstall(dir, Work.PayloadSize());
            if (hadTask) { Work.RegisterTask(dir, atLogon); if (taskDisabled) Cmd("schtasks.exe", "/Change /TN \"" + App.TaskName + "\" /DISABLE"); }
            else Work.RegisterTask(dir, true);
            Say("تشغيل البرنامج…");
            bool started = !taskDisabled && Cmd("schtasks.exe", "/Run /TN \"" + App.TaskName + "\"").IndexOf("SUCCESS", StringComparison.OrdinalIgnoreCase) >= 0;
            if (!started) Process.Start(new ProcessStartInfo(Path.Combine(dir, App.ExeName), "--background") { WorkingDirectory = dir });
            // reopen the window the user had when they pressed "update now"
            for (int i = 0; i < 60; i++)
            {
                try
                {
                    var rq = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:47816/api/ping"); rq.Timeout = 1000; rq.Proxy = null;
                    using (rq.GetResponse()) { }
                    Process.Start(new ProcessStartInfo(Path.Combine(dir, App.ExeName)) { WorkingDirectory = dir });   // asks the running app to show its window
                    break;
                }
                catch { Thread.Sleep(500); }
            }
        }
        catch (Exception ex) { err = ex.Message; }
        BeginInvoke(new Action(() =>
        {
            if (err != null)
                MessageBox.Show(this, "ما اكتمل التحديث:\n" + err + "\n\nشغّل ملف التثبيت يدوياً من GitHub.", Text, MessageBoxButtons.OK,
                    MessageBoxIcon.Error, MessageBoxDefaultButton.Button1, MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign);
            else { status.Text = "تم التحديث."; Thread.Sleep(800); }
            Close();
        }));
    }
}

// ============================================================================ uninstaller
class UninstallForm : Form
{
    [System.Runtime.InteropServices.DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int a, ref int v, int s);
    protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); int on = 1; DwmSetWindowAttribute(Handle, 20, ref on, 4); }
    readonly bool quiet;
    readonly CheckBox keep;
    readonly Label status;
    readonly Button go, cancel;

    public UninstallForm(bool quiet)
    {
        this.quiet = quiet;
        Text = "إلغاء تثبيت " + App.Name;
        RightToLeft = RightToLeft.Yes; RightToLeftLayout = true;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen; ClientSize = new Size(560, 270);
        BackColor = App.Bg; Font = Ui.F(10f); AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

        var logo = new PictureBox { Image = Ui.Logo(44), SizeMode = PictureBoxSizeMode.AutoSize, Location = new Point(24, 22) };
        var title = Ui.L("إلغاء تثبيت " + App.Name, 14f, App.Text, FontStyle.Bold); title.SetBounds(82, 22, 450, 30);
        var text = Ui.L("بيتم إيقاف البرنامج وحذفه من جهازك، مع اختصاراته وتشغيله مع ويندوز.", 10.5f, App.Muted); text.SetBounds(82, 58, 450, 44);
        keep = Ui.C("احذف إعداداتي بعد (الأوضاع، الواط، الشاشة العائمة)", false); keep.Location = new Point(82, 112);
        status = Ui.L("", 10f, App.Muted); status.SetBounds(82, 150, 450, 26);
        go = Ui.B("إلغاء التثبيت", true); go.SetBounds(560 - 24 - 140, 206, 140, 38);
        cancel = Ui.B("إلغاء", false); cancel.SetBounds(24, 206, 118, 38);
        Controls.AddRange(new Control[] { logo, title, text, keep, status, go, cancel });
        cancel.Click += (s, e) => Close();
        go.Click += (s, e) => Start();
        if (quiet) Shown += (s, e) => Start();
    }

    void Start()
    {
        go.Enabled = cancel.Enabled = keep.Enabled = false;
        bool remove = keep.Checked;
        string dir = App.InstalledDir() ?? Path.GetDirectoryName(Application.ExecutablePath);
        new Thread(() =>
        {
            Work.Uninstall(dir, remove, s => BeginInvoke(new Action(() => status.Text = s)));
            BeginInvoke(new Action(() =>
            {
                status.Text = "تم حذف البرنامج.";
                if (!quiet)
                    MessageBox.Show(this, App.Name + " انحذف من جهازك.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information,
                        MessageBoxDefaultButton.Button1, MessageBoxOptions.RtlReading | MessageBoxOptions.RightAlign);
                Close();
            }));
        }) { IsBackground = true }.Start();
    }
}
