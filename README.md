# RX16 Control Center

A lightweight control center for the **ACEMAGIC RX16** laptop (AMD Ryzen 7 H 255 "Hawk Point", Radeon 780M) on Windows 11.
مركز تحكم خفيف لجهاز **ACEMAGIC RX16**: البطارية، الحساسات، أوضاع الطاقة، والتحكم بواط المعالج.

## Features
- **Battery**: charge level, charge/discharge watts, time left, health, live charge-cycle count, lifespan estimate, monthly usage
- **Power modes**: Adaptive / Eco / Balanced / Performance (Windows power mode + CPU limits + RyzenAdj wattage, 8–54 W)
- **Sensors**: CPU/GPU temperature, clocks, load, real CPU-core watts from the SMU power table, RAM
- **Running apps** with battery impact, **NZXT-style overlay** (Ctrl+Shift+O), tray icon, notifications (80 % charge, low battery)
- Arabic / English UI, updates from GitHub Releases (asks before installing, SHA-256 verified)
- Light on resources: ~0.05 % CPU and ~36 MB RAM in the background

## Install
Download `RX16 Control Center Setup <version>.exe` from [Releases](../../releases) and run it.
Everything is bundled (embedded Python, WebView2 SDK, RyzenAdj) — nothing else to install on Windows 11.

> RyzenAdj uses the WinRing0 driver for wattage control. Battery/charging needs a laptop; wattage control needs a Ryzen mobile APU.

## Project layout
| Path | |
|---|---|
| `app/` | the service (`x16power.pyw`), UI (`ui/index.html`), sensors, updater, bundled Python / WebView2 / RyzenAdj |
| `source/` | C# host exe (tray, overlay, WebView2 window), setup wizard, build & release scripts |

Build: `source\build.ps1` → `source\build-setup.ps1` → `source\release.ps1 -Notes "..."`.

## Third-party
[RyzenAdj](https://github.com/FlyGoat/RyzenAdj) (LGPL-3.0) · WinRing0 · [Python](https://www.python.org/) embeddable (PSF License) · [Microsoft WebView2](https://www.nuget.org/packages/Microsoft.Web.WebView2) SDK.
