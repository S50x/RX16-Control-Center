// RX16 Control Center - floating stats window (NZXT CAM "Mini Mode" style).
// Per-pixel alpha layered window: adjustable background opacity, crisp text, drag to move,
// snap to screen edges, lock (click-through), always-on-top, dark/light theme.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class OverlayForm : Form
{
    [StructLayout(LayoutKind.Sequential)] struct PT { public int x, y; public PT(int a, int b) { x = a; y = b; } }
    [StructLayout(LayoutKind.Sequential)] struct SZ { public int cx, cy; public SZ(int a, int b) { cx = a; cy = b; } }
    [StructLayout(LayoutKind.Sequential, Pack = 1)] struct BLEND { public byte op, flags, alpha, format; }
    [DllImport("user32.dll")] static extern bool UpdateLayeredWindow(IntPtr hwnd, IntPtr hdcDst, ref PT dst, ref SZ size, IntPtr hdcSrc, ref PT src, int key, ref BLEND blend, int flags);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr h);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h, IntPtr dc);
    [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc, IntPtr o);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr o);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll")] static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int l, t, r, b; }

    const int WS_EX_TRANSPARENT = 0x20;
    readonly Action<int, int> onMoved;
    string lastKey = "";
    bool dragging, locked, topmost = true, snap = true;
    Size curSize;

    public OverlayForm(Action<int, int> moved)
    {
        onMoved = moved;
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
    }

    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams
    {
        get
        {
            var cp = base.CreateParams;
            cp.ExStyle |= 0x80000 | 0x80 | 0x8000000;   // LAYERED | TOOLWINDOW | NOACTIVATE
            return cp;
        }
    }

    // drag anywhere on the window (when not locked)
    protected override void WndProc(ref Message m)
    {
        const int WM_NCHITTEST = 0x84, WM_ENTERSIZEMOVE = 0x231, WM_EXITSIZEMOVE = 0x232, WM_MOUSEACTIVATE = 0x21;
        if (m.Msg == WM_NCHITTEST) { m.Result = (IntPtr)2; return; }          // HTCAPTION
        if (m.Msg == WM_MOUSEACTIVATE) { m.Result = (IntPtr)3; return; }       // MA_NOACTIVATE
        if (m.Msg == WM_ENTERSIZEMOVE) dragging = true;
        if (m.Msg == WM_EXITSIZEMOVE)
        {
            dragging = false;
            RECT r; GetWindowRect(Handle, out r);
            int x = r.l, y = r.t;
            if (snap) SnapToEdges(ref x, ref y, r.r - r.l, r.b - r.t);
            SetWindowPos(Handle, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
            onMoved(x, y);
        }
        base.WndProc(ref m);
    }

    static void SnapToEdges(ref int x, ref int y, int w, int h)
    {
        var a = Screen.FromPoint(new Point(x + w / 2, y + h / 2)).WorkingArea;
        int t = 28;
        if (Math.Abs(x - a.Left) < t) x = a.Left;
        if (Math.Abs(a.Right - (x + w)) < t) x = a.Right - w;
        if (Math.Abs(y - a.Top) < t) y = a.Top;
        if (Math.Abs(a.Bottom - (y + h)) < t) y = a.Bottom - h;
        if (Math.Abs(x + w / 2 - (a.Left + a.Width / 2)) < t) x = a.Left + (a.Width - w) / 2;
    }

    public void HideOverlay() { if (Visible) Hide(); lastKey = ""; }

    static bool B(Dictionary<string, object> d, string k, bool def) { object v; return d.TryGetValue(k, out v) && v is bool ? (bool)v : def; }
    static string Str(Dictionary<string, object> d, string k, string def) { object v; return d.TryGetValue(k, out v) && v != null ? v.ToString() : def; }
    static int Int(Dictionary<string, object> d, string k, int def) { object v; if (!d.TryGetValue(k, out v) || v == null) return def; try { return Convert.ToInt32(v); } catch { return def; } }
    static Color Col(string hex, Color def) { try { return ColorTranslator.FromHtml(hex); } catch { return def; } }

    public void Apply(Dictionary<string, object> d)
    {
        if (!B(d, "enabled", false)) { HideOverlay(); return; }
        var tiles = d.ContainsKey("tiles") ? d["tiles"] as ArrayList : null;
        if (tiles == null || tiles.Count == 0) { HideOverlay(); return; }
        if (dragging) return;

        locked = B(d, "lock", false);
        topmost = B(d, "topmost", true);
        snap = B(d, "snap", true);
        ApplyStyles();

        string key = new JavaScriptSerializer().Serialize(d);
        if (key == lastKey && Visible) { ZOrder(); return; }
        lastKey = key;

        float scale = Int(d, "scale", 100) / 100f * (DeviceDpi / 96f);
        bool light = Str(d, "theme", "dark") == "light";
        using (var bmp = Render(tiles, scale, Int(d, "opacity", 55), Str(d, "layout", "horizontal") == "horizontal",
                                light, Col(Str(d, "label_color", "#9aa1ad"), Color.Gray)))
        {
            int x, y;
            string pos = Str(d, "pos", "tc");
            var area = Screen.PrimaryScreen.WorkingArea;
            if (pos == "custom" && d["x"] != null && d["y"] != null)
            {
                x = Int(d, "x", 0); y = Int(d, "y", 0);
                // keep it on a visible screen (e.g. monitor unplugged / size changed)
                var a = Screen.FromPoint(new Point(x + bmp.Width / 2, y + bmp.Height / 2)).WorkingArea;
                x = Math.Max(a.Left, Math.Min(x, a.Right - bmp.Width));
                y = Math.Max(a.Top, Math.Min(y, a.Bottom - bmp.Height));
            }
            else
            {
                int m = (int)(10 * scale);
                x = pos.EndsWith("l") ? area.Left + m : pos.EndsWith("r") ? area.Right - bmp.Width - m : area.Left + (area.Width - bmp.Width) / 2;
                y = pos.StartsWith("t") ? area.Top + m : area.Bottom - bmp.Height - m;
            }
            if (!Visible) Show();
            Blit(bmp, x, y);
            curSize = bmp.Size;
            ZOrder();
        }
    }

    void ApplyStyles()
    {
        int ex = GetWindowLong(Handle, -20);
        int want = locked ? (ex | WS_EX_TRANSPARENT) : (ex & ~WS_EX_TRANSPARENT);
        if (want != ex) SetWindowLong(Handle, -20, want);
    }

    void ZOrder()
    {
        // HWND_TOPMOST / HWND_NOTOPMOST; NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW
        SetWindowPos(Handle, new IntPtr(topmost ? -1 : -2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040);
    }

    // Text is drawn as filled glyph outlines with a contrasting stroke (the way game overlays do it):
    // anti-aliased text on a see-through layered window otherwise looks soft and thin, especially Arabic.
    static readonly FontFamily ValFam = SafeFamily("Segoe UI Semibold", "Segoe UI");
    static readonly FontFamily LblFam = SafeFamily("Segoe UI", "Tahoma");

    static FontFamily SafeFamily(string a, string b) { try { return new FontFamily(a); } catch { return new FontFamily(b); } }

    static float Ascent(FontFamily f, FontStyle st, float em) { return em * f.GetCellAscent(st) / f.GetEmHeight(st); }

    static GraphicsPath TextPath(string text, FontFamily fam, FontStyle st, float em)
    {
        var p = new GraphicsPath();
        p.AddString(text, fam, (int)st, em, new PointF(0, 0), LabelFormat(text));
        return p;
    }

    class TileGeo { public GraphicsPath v, u, l; public RectangleF vb, ub, lb; public float w; }

    Bitmap Render(ArrayList tiles, float s, int opacity, bool horizontal, bool light, Color labelCol)
    {
        float vEm = 21f * s, uEm = 11.5f * s, lEm = 11.5f * s;
        var vSt = FontStyle.Regular;            // family is already semibold
        var lSt = FontStyle.Bold;               // bold labels stay legible at small sizes (Arabic too)
        float padX = 11 * s, padY = 6 * s, gapU = 2 * s, minW = 60 * s;
        float baseV = padY + 0.74f * vEm;       // value baseline, from the tile top
        float baseL = baseV + 1.32f * lEm;      // label baseline
        float tileH = baseL + 0.42f * lEm + padY;

        var geo = new List<TileGeo>();
        foreach (Dictionary<string, object> t in tiles)
        {
            var g = new TileGeo();
            string u = t["u"].ToString();
            g.v = TextPath(t["v"].ToString(), ValFam, vSt, vEm); g.vb = g.v.GetBounds();
            if (u.Length > 0 && !IsArabic(t["v"].ToString())) { g.u = TextPath(u, LblFam, FontStyle.Regular, uEm); g.ub = g.u.GetBounds(); }
            g.l = TextPath(t["l"].ToString(), LblFam, lSt, lEm); g.lb = g.l.GetBounds();
            float vw = g.vb.Width + (g.u != null ? gapU + g.ub.Width : 0);
            g.w = Math.Max(minW, Math.Max(vw, g.lb.Width) + padX * 2);
            geo.Add(g);
        }
        float colW = 0; foreach (var g in geo) colW = Math.Max(colW, g.w);
        float W = 0, H;
        if (horizontal) { foreach (var g in geo) W += g.w; H = tileH; }
        else { W = colW; H = tileH * tiles.Count; }

        var bmp = new Bitmap((int)Math.Ceiling(W) + 1, (int)Math.Ceiling(H) + 1, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
        using (var gr = Graphics.FromImage(bmp))
        {
            gr.SmoothingMode = SmoothingMode.HighQuality;
            gr.PixelOffsetMode = PixelOffsetMode.HighQuality;
            gr.CompositingQuality = CompositingQuality.HighQuality;
            int a = (int)Math.Round(opacity * 2.55);
            if (!locked) a = Math.Max(a, 3);   // fully transparent pixels can't be grabbed: keep a hint of alpha to drag
            Color bg = light ? Color.FromArgb(a, 241, 243, 246) : Color.FromArgb(a, 18, 19, 23);
            Color line = light ? Color.FromArgb(Math.Min(255, a / 2 + 30), 0, 0, 0) : Color.FromArgb(Math.Min(255, a / 3 + 25), 255, 255, 255);
            // outline strength: strong when see-through, subtle on a solid background
            int oa = opacity >= 85 ? 110 : opacity >= 50 ? 170 : 230;
            Color outline = light ? Color.FromArgb(oa, 255, 255, 255) : Color.FromArgb(oa, 0, 0, 0);
            using (var b = new SolidBrush(bg)) gr.FillRectangle(b, 0, 0, bmp.Width - 1, bmp.Height - 1);
            using (var p = new Pen(line, 1f))
            {
                gr.DrawRectangle(p, 0, 0, bmp.Width - 1, bmp.Height - 1);
                float x = 0, y = 0;
                for (int i = 0; i < tiles.Count; i++)
                {
                    var t = (Dictionary<string, object>)tiles[i];
                    var g = geo[i];
                    float w = horizontal ? g.w : colW;
                    if (i > 0) { if (horizontal) gr.DrawLine(p, x, 0, x, bmp.Height - 1); else gr.DrawLine(p, 0, y, bmp.Width - 1, y); }

                    // value (+unit) centred on one baseline
                    float total = g.vb.Width + (g.u != null ? gapU + g.ub.Width : 0);
                    float start = x + (w - total) / 2;
                    float yv = y + baseV - Ascent(ValFam, vSt, vEm);
                    Place(g.v, start - g.vb.X, yv);
                    Stroke(gr, g.v, Col(t["c"].ToString(), Color.White), outline, 2.8f * s);
                    if (g.u != null)
                    {
                        Place(g.u, start + g.vb.Width + gapU - g.ub.X, y + baseV - Ascent(LblFam, FontStyle.Regular, uEm));
                        Stroke(gr, g.u, Color.FromArgb(230, labelCol), outline, 2.2f * s);
                    }
                    // label, centred
                    Place(g.l, x + (w - g.lb.Width) / 2 - g.lb.X, y + baseL - Ascent(LblFam, lSt, lEm));
                    Stroke(gr, g.l, labelCol, outline, 2.4f * s);

                    if (horizontal) x += w; else y += tileH;
                }
            }
        }
        foreach (var g in geo) { g.v.Dispose(); if (g.u != null) g.u.Dispose(); g.l.Dispose(); }
        return bmp;
    }

    static void Place(GraphicsPath p, float dx, float dy)
    {
        using (var m = new Matrix()) { m.Translate(dx, dy); p.Transform(m); }
    }

    static void Stroke(Graphics g, GraphicsPath p, Color fill, Color outline, float width)
    {
        using (var pen = new Pen(outline, width) { LineJoin = LineJoin.Round })
            g.DrawPath(pen, p);
        using (var b = new SolidBrush(fill)) g.FillPath(b, p);
    }

    static bool IsArabic(string text)
    {
        foreach (char ch in text) if (ch >= '؀' && ch <= 'ۿ') return true;
        return false;
    }

    static StringFormat LabelFormat(string text)
    {
        var f = new StringFormat(StringFormatFlags.NoWrap | StringFormatFlags.NoClip);
        if (IsArabic(text)) f.FormatFlags |= StringFormatFlags.DirectionRightToLeft;
        return f;
    }

    void Blit(Bitmap bmp, int x, int y)
    {
        IntPtr screen = GetDC(IntPtr.Zero), mem = CreateCompatibleDC(screen);
        IntPtr hbmp = bmp.GetHbitmap(Color.FromArgb(0)), old = SelectObject(mem, hbmp);
        var size = new SZ(bmp.Width, bmp.Height); var src = new PT(0, 0); var dst = new PT(x, y);
        var blend = new BLEND { op = 0, flags = 0, alpha = 255, format = 1 };   // AC_SRC_OVER, AC_SRC_ALPHA
        UpdateLayeredWindow(Handle, screen, ref dst, ref size, mem, ref src, 0, ref blend, 2);   // ULW_ALPHA
        SelectObject(mem, old); DeleteObject(hbmp); DeleteDC(mem); ReleaseDC(IntPtr.Zero, screen);
    }
}
