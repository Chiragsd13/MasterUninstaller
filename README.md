# Master Uninstaller

A high-fidelity Windows desktop application that gives you full control over uninstalling every component on your system — including stubborn programs, Windows services, Store apps, startup items, and optional features.

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## Features

- **Full System Access** — Uninstall programs from all 3 registry hives (HKLM 64-bit, HKLM 32-bit, HKCU), including hidden system components
- **Force Uninstall** — 5-strategy cascade: quiet uninstall string, MSI `/quiet`, silent flags (`/S`, `/silent`, `/quiet`, `/VERYSILENT`), registry cleanup, install folder deletion
- **Windows Services** — Stop and disable any Windows service
- **Store Apps (AppX)** — Remove Microsoft Store / UWP apps
- **Startup Items** — Disable or delete programs that run at startup
- **Optional Features** — Remove Windows optional features (Hyper-V, WSL, etc.)
- **Action Logging** — Every action is logged to JSON with full details for audit and restore
- **Confirmation Dialogs** — Nothing is deleted without explicit user confirmation
- **Auto-Refresh** — Lists update automatically after every action

## UI

- AMOLED black theme with glass-morphism effects
- Material Design 3 styling with design tokens
- Frameless window with custom titlebar
- Smooth spring animations and hover zoom with glass effect
- Per-item context menus ("...") for quick actions
- Filter chips: All / Has Uninstaller / No Uninstaller / System
- Inline app icon avatars

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
- **Force Uninstall:** Attempts multiple strategies in sequence until one succeeds, then cleans up leftover registry entries and install folders

## License

MIT
