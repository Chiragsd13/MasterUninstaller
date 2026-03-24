# Master Uninstaller

A high-fidelity Windows desktop application that gives you full control over uninstalling every component on your system — including stubborn programs, Windows services, Store apps, startup items, and optional features.

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)


## Features

- **Full System Access** — Uninstall programs from all 3 registry hives (HKLM 64-bit, HKLM 32-bit, HKCU), including hidden system components
- **Force Uninstall (10-strategy cascade)** — Inspired by [microsoft-edge-uninstaller](https://github.com/Chiragsd13/microsoft-edge-uninstaller):
  1. Kill related processes
  2. QuietUninstallString
  3. MSI `/quiet` uninstall
  4. WMI Win32_Product uninstall
  5. Find & run setup.exe with `--force-uninstall` (Edge-style)
  6. Common silent flags (`/S`, `/silent`, `/VERYSILENT`, etc.)
  7. Registry entry removal
  8. Folder deletion with ownership takeover (`takeown` + `icacls`)
  9. Shortcut cleanup (Start Menu, Desktop)
  10. Scheduled task cleanup
- **Windows Services** — Start, stop, enable, disable any Windows service with filters (Running/Stopped/Disabled)
- **Store Apps (AppX)** — Remove Microsoft Store / UWP apps; force-remove for all users including provisioned packages
- **Scheduled Tasks** — View, disable, enable, and delete scheduled tasks
- **Startup Items** — Disable or delete programs that run at startup
- **Optional Features** — Remove Windows optional features (Hyper-V, WSL, etc.)
- **Action Logging** — Every action is logged to JSON with full details, one-click restore, export, and clear
- **Confirmation Dialogs** — Nothing is deleted without explicit user confirmation
- **Auto-Refresh** — Lists update automatically after every action
- **Keyboard Shortcuts** — Ctrl+R (refresh all), Ctrl+F (search), Ctrl+1-7 (switch tabs), Esc (close dialogs)

## UI

- AMOLED black theme with glass-morphism effects and noise texture overlay
- Material Design 3 styling with design tokens and semantic colors
- Frameless window with custom titlebar, maximize/restore, and drag region
- Smooth spring/bounce animations, staggered card entry, hover zoom with glass
- Progress bar for async operations
- Per-item context menus ("...") with slide animation
- Filter chips per tab (Programs, Services, Store Apps)
- Inline colored letter avatars with gradient overlays
- Toast notifications with dismiss button
- Tooltips on window controls
- System info in status bar (OS version, RAM usage)

## Installation

### From Source

```bash
pip install -r requirements.txt
python master_uninstaller.py
```

> **Note:** Must be run as Administrator for full functionality.

### Build Standalone EXE

```bash
pip install pyinstaller
python -m PyInstaller build.spec --distpath ./dist --workpath ./build_temp --clean -y
```

The built `.exe` will be in `dist/MasterUninstaller/`. It automatically requests admin elevation via UAC.

## Requirements

- Windows 10/11
- Python 3.10+ (for running from source)
- [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (pre-installed on Windows 10 1803+ and Windows 11)

## Project Structure

```
MasterUninstaller/
├── master_uninstaller.py   # Main application (backend + embedded frontend)
├── build.spec              # PyInstaller build configuration
├── requirements.txt        # Python dependencies
├── logs/                   # Action logs (created at runtime)
├── backups/                # Backup data (created at runtime)
└── dist/                   # Built executable (after PyInstaller)
```

## How It Works

- **Backend:** Python with `pywebview` (WebView2) serving an embedded HTML/CSS/JS frontend
- **System Queries:** PowerShell executed silently (no visible windows) to read registry, services, AppX packages, startup entries, and optional features
- **Uninstaller Launch:** Uninstallers run in a visible console window so users can interact with installer GUIs
- **Force Uninstall:** 10-strategy cascade inspired by the [Edge uninstaller](https://github.com/Chiragsd13/microsoft-edge-uninstaller) — kills processes, tries multiple uninstall methods, takes ownership of locked files, and cleans up shortcuts + scheduled tasks

