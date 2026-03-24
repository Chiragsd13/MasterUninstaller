#!/usr/bin/env python3
"""
Master Uninstaller — A high-fidelity Windows system manager.

Features:
  - Browse and uninstall all installed programs (registry + system components)
  - Force-uninstall stubborn apps (silent flags, registry cleanup, folder deletion)
  - Manage Windows optional features (enable/disable)
  - Control Windows services (start, stop, enable, disable)
  - Remove startup items (registry entries + startup folder shortcuts)
  - Uninstall Microsoft Store / AppX packages
  - Full action log with one-click restore
  - AMOLED dark theme with glass-morphism UI
  - Frameless window with custom titlebar

Architecture:
  Python backend (pywebview js_api) <-> HTML/CSS/JS frontend (WebView2)
  All system operations use PowerShell with hidden windows (CREATE_NO_WINDOW).
  Uninstallers are launched with visible windows so the user can interact.

Author: Built with Claude
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import webview

# ─────────────────────────────────────────────────────────────────────────────
# Configuration paths — all data stored next to the executable
# ─────────────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
LOG_DIR = APP_DIR / "logs"
BACKUP_DIR = APP_DIR / "backups"
ACTION_LOG = LOG_DIR / "actions.json"
SETTINGS_FILE = APP_DIR / "settings.json"

LOG_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# PowerShell execution — hide the console window for background commands
# ─────────────────────────────────────────────────────────────────────────────
_startupinfo = subprocess.STARTUPINFO()
_startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_startupinfo.wShowWindow = 0  # SW_HIDE
_CREATE_NO_WINDOW = 0x08000000

# Reference to the pywebview window (set at launch)
_window = None


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin():
    """Relaunch the current script with elevated (admin) privileges via UAC."""
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()


def ps(cmd: str, as_json: bool = False):
    """
    Execute a PowerShell command silently (no visible window).

    Args:
        cmd: PowerShell script to execute.
        as_json: If True, parse stdout as JSON and return a list.

    Returns:
        Parsed JSON list if as_json=True, otherwise raw stdout string.
    """
    full_cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd
    ]
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=120,
            startupinfo=_startupinfo,
            creationflags=_CREATE_NO_WINDOW,
        )
        if as_json and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                # PowerShell returns a single object (not array) when there's
                # only one result — normalize to always return a list.
                return data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                return []
        return result.stdout.strip()
    except Exception:
        return [] if as_json else ""


# ─────────────────────────────────────────────────────────────────────────────
# API — exposed to the frontend via pywebview's js_api bridge
# ─────────────────────────────────────────────────────────────────────────────
class Api:
    """Backend API called from JavaScript via `pywebview.api.*` methods."""

    def __init__(self):
        self._load_settings()

    # ── Settings ──────────────────────────────────────────────────────────

    def _load_settings(self):
        """Load settings from disk, applying defaults for missing keys."""
        if SETTINGS_FILE.exists():
            try:
                self.settings = json.loads(
                    SETTINGS_FILE.read_text(encoding="utf-8")
                )
            except Exception:
                self.settings = {}
        else:
            self.settings = {}

        defaults = {
            "auto_refresh": True,
            "refresh_interval": 30,
            "confirm_actions": True,
        }
        for key, val in defaults.items():
            self.settings.setdefault(key, val)

    def get_settings(self) -> dict:
        return self.settings

    def save_settings(self, settings_json) -> bool:
        self.settings = (
            json.loads(settings_json)
            if isinstance(settings_json, str)
            else settings_json
        )
        SETTINGS_FILE.write_text(
            json.dumps(self.settings, indent=2), encoding="utf-8"
        )
        return True

    def get_admin_status(self) -> bool:
        return is_admin()

    # ── Window controls (frameless mode) ──────────────────────────────────

    def minimize_window(self):
        """Minimize the app window."""
        if _window:
            _window.minimize()

    def close_window(self):
        """Close and destroy the app window."""
        if _window:
            _window.destroy()

    # ── Action Log ────────────────────────────────────────────────────────
    #  Every destructive operation is logged with a timestamp and a
    #  restore command so the user can undo accidental removals.

    def _load_log(self) -> list:
        try:
            return json.loads(ACTION_LOG.read_text(encoding="utf-8")) \
                if ACTION_LOG.exists() else []
        except Exception:
            return []

    def _save_log(self, entries: list):
        ACTION_LOG.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def log_action(self, category, name, action, details="", restore_cmd=""):
        """Append an action entry to the log."""
        entries = self._load_log()
        entries.append({
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "name": name,
            "action": action,
            "details": details,
            "restore_cmd": restore_cmd,
            "restored": False,
        })
        self._save_log(entries)

    def get_action_log(self) -> list:
        return self._load_log()

    def restore_action(self, index: int) -> dict:
        """Execute the restore command for a logged action."""
        entries = self._load_log()
        if 0 <= index < len(entries):
            entry = entries[index]
            cmd = entry.get("restore_cmd", "")
            if cmd and not entry.get("restored"):
                try:
                    ps(cmd)
                    entries[index]["restored"] = True
                    self._save_log(entries)
                    return {"ok": True, "msg": f"Restored: {entry['name']}"}
                except Exception as ex:
                    return {"ok": False, "msg": str(ex)}
        return {"ok": False, "msg": "Cannot restore this entry."}

    def export_log(self) -> str:
        """Export the action log to a timestamped JSON file."""
        path = LOG_DIR / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(
            json.dumps(self._load_log(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(path)

    # ── Data Fetchers ─────────────────────────────────────────────────────
    #  Each fetcher runs a PowerShell command and returns structured JSON.

    def get_programs(self) -> list:
        """
        Fetch ALL installed programs from the Windows registry.

        Reads from three registry hives:
          - HKLM (64-bit programs)
          - HKLM WOW6432Node (32-bit programs on 64-bit OS)
          - HKCU (per-user installs)

        Includes system components (SystemComponent=1) so the user
        can see everything on their system.
        """
        cmd = r"""
        $all = @{}
        $paths = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
        )
        foreach ($p in $paths) {
            Get-ItemProperty $p -ErrorAction SilentlyContinue | ForEach-Object {
                $name = $_.DisplayName
                if ($name -and -not $all.ContainsKey($name)) {
                    $all[$name] = [PSCustomObject]@{
                        DisplayName        = $name
                        DisplayVersion     = $_.DisplayVersion
                        Publisher          = $_.Publisher
                        InstallDate        = $_.InstallDate
                        UninstallString    = $_.UninstallString
                        QuietUninstallString = $_.QuietUninstallString
                        InstallLocation    = $_.InstallLocation
                        EstimatedSize      = $_.EstimatedSize
                        SystemComponent    = $_.SystemComponent
                        PSPath             = $_.PSPath
                    }
                }
            }
        }
        $all.Values | Sort-Object DisplayName | ConvertTo-Json -Depth 3 -Compress
        """
        return ps(cmd, as_json=True)

    def get_features(self) -> list:
        """Fetch Windows optional features (e.g., Hyper-V, WSL, .NET)."""
        return ps(
            "Get-WindowsOptionalFeature -Online "
            "| Select-Object FeatureName, State "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    def get_services(self) -> list:
        """Fetch all Windows services with their status and startup type."""
        return ps(
            "Get-Service "
            "| Select-Object Name, DisplayName, Status, StartType "
            "| Sort-Object DisplayName "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    def get_startup(self) -> list:
        """
        Fetch startup items from:
          - Registry Run/RunOnce keys (HKLM + HKCU)
          - User's Startup folder
        """
        cmd = r"""
        $items = @()
        $keys = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'
        )
        foreach ($k in $keys) {
            if (Test-Path $k) {
                $props = Get-ItemProperty $k -EA SilentlyContinue
                $props.PSObject.Properties | Where-Object {
                    $_.Name -notlike 'PS*'
                } | ForEach-Object {
                    $items += [PSCustomObject]@{
                        Name    = $_.Name
                        Command = $_.Value
                        Source  = $k
                    }
                }
            }
        }
        $startupFolder = [Environment]::GetFolderPath('Startup')
        Get-ChildItem $startupFolder -EA SilentlyContinue | ForEach-Object {
            $items += [PSCustomObject]@{
                Name    = $_.Name
                Command = $_.FullName
                Source  = 'StartupFolder'
            }
        }
        $items | ConvertTo-Json -Depth 2 -Compress
        """
        return ps(cmd, as_json=True)

    def get_store_apps(self) -> list:
        """Fetch installed Microsoft Store (AppX) packages."""
        return ps(
            "Get-AppxPackage "
            "| Select-Object Name, PackageFullName, Version, Publisher, "
            "IsFramework, NonRemovable "
            "| Sort-Object Name "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    # ── Uninstall Actions ─────────────────────────────────────────────────

    def uninstall_program(self, name: str, uninst_str: str, details_json: str) -> dict:
        """
        Launch the program's own uninstaller.

        The uninstaller runs in a NEW CONSOLE (visible window) so the user
        can interact with any prompts or wizard steps.
        """
        self.log_action(
            "Program", name, "uninstall", details_json,
            f"Reinstall '{name}' manually.",
        )
        try:
            if "msiexec" in uninst_str.lower():
                # MSI uninstallers work best through shell
                subprocess.Popen(uninst_str, shell=True)
            else:
                # Give the uninstaller its own visible console window
                subprocess.Popen(
                    uninst_str, shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            return {"ok": True, "msg": f"Uninstaller launched for '{name}'."}
        except Exception as ex:
            return {"ok": False, "msg": str(ex)}

    def force_uninstall_program(self, name: str, details_json: str) -> dict:
        """
        Force-uninstall a program using multiple strategies:
          1. Try the QuietUninstallString (if available)
          2. Try MSI quiet uninstall (msiexec /x GUID /quiet)
          3. Try common silent flags (/S, /silent, /quiet, /VERYSILENT)
          4. Remove the registry uninstall entry
          5. Delete the installation folder
        """
        try:
            info = json.loads(details_json) if isinstance(details_json, str) \
                else details_json
        except Exception:
            info = {}

        self.log_action(
            "Program", name, "force_uninstall", details_json,
            f"Reinstall '{name}' manually.",
        )
        results = []

        # Strategy 1: QuietUninstallString
        quiet = info.get("QuietUninstallString", "")
        if quiet:
            try:
                subprocess.run(
                    quiet, shell=True,
                    startupinfo=_startupinfo, creationflags=_CREATE_NO_WINDOW,
                    timeout=120,
                )
                results.append("Quiet uninstall executed")
            except Exception:
                pass

        # Strategy 2: MSI quiet uninstall
        uninst = info.get("UninstallString", "")
        if uninst and "msiexec" in uninst.lower():
            # Extract the GUID from the uninstall string
            guid = ""
            for part in uninst.split():
                if part.startswith("{") and part.endswith("}"):
                    guid = part
                    break
            if guid:
                try:
                    subprocess.run(
                        f"msiexec /x {guid} /quiet /norestart", shell=True,
                        startupinfo=_startupinfo, creationflags=_CREATE_NO_WINDOW,
                        timeout=120,
                    )
                    results.append("MSI quiet uninstall")
                except Exception:
                    pass

        # Strategy 3: Common silent flags
        if uninst and not results:
            for flag in ["/S", "/silent", "/quiet", "/VERYSILENT", "-uninstall"]:
                try:
                    subprocess.run(
                        f"{uninst} {flag}", shell=True,
                        startupinfo=_startupinfo, creationflags=_CREATE_NO_WINDOW,
                        timeout=60,
                    )
                    results.append(f"Tried uninstall with {flag}")
                    break
                except Exception:
                    pass

        # Strategy 4: Remove registry entry
        ps_path = info.get("PSPath", "")
        if ps_path:
            reg_path = ps_path.replace(
                "Microsoft.PowerShell.Core\\Registry::", ""
            )
            ps(f"Remove-Item -Path '{reg_path}' -Recurse -Force "
               "-EA SilentlyContinue")
            results.append("Registry entry removed")

        # Strategy 5: Delete installation folder
        install_loc = info.get("InstallLocation", "")
        if install_loc and os.path.isdir(install_loc):
            try:
                shutil.rmtree(install_loc, ignore_errors=True)
                results.append(f"Deleted: {install_loc}")
            except Exception:
                pass

        if results:
            return {"ok": True, "msg": "; ".join(results)}
        return {"ok": False, "msg": "No paths found to remove."}

    def open_location(self, path: str) -> dict:
        """Open a folder in Windows Explorer."""
        if path and os.path.isdir(path):
            os.startfile(path)
            return {"ok": True}
        return {"ok": False, "msg": "Path not found."}

    # ── Feature Actions ───────────────────────────────────────────────────

    def toggle_feature(self, name: str, enable: bool) -> dict:
        """Enable or disable a Windows optional feature."""
        action = "enable" if enable else "disable"
        verb = "Enable" if enable else "Disable"
        rev_verb = "Disable" if enable else "Enable"

        cmd = (f"{verb}-WindowsOptionalFeature -Online "
               f"-FeatureName '{name}' -NoRestart")
        restore = (f"{rev_verb}-WindowsOptionalFeature -Online "
                    f"-FeatureName '{name}' -NoRestart")

        self.log_action("Feature", name, action, restore_cmd=restore)
        ps(cmd)
        return {"ok": True, "msg": f"Feature '{name}' {action}d."}

    # ── Service Actions ───────────────────────────────────────────────────

    def service_action(self, svc_name: str, display_name: str, action: str) -> dict:
        """Perform a service action: start, stop, enable, or disable."""
        commands = {
            "stop": (
                f"Stop-Service '{svc_name}' -Force",
                f"Start-Service '{svc_name}'",
            ),
            "start": (
                f"Start-Service '{svc_name}'",
                f"Stop-Service '{svc_name}' -Force",
            ),
            "disable": (
                f"Set-Service '{svc_name}' -StartupType Disabled; "
                f"Stop-Service '{svc_name}' -Force -EA SilentlyContinue",
                f"Set-Service '{svc_name}' -StartupType Manual; "
                f"Start-Service '{svc_name}'",
            ),
            "enable": (
                f"Set-Service '{svc_name}' -StartupType Manual",
                f"Set-Service '{svc_name}' -StartupType Disabled",
            ),
        }
        if action not in commands:
            return {"ok": False, "msg": "Unknown action."}

        cmd, restore = commands[action]
        self.log_action(
            "Service", f"{display_name} ({svc_name})", action,
            restore_cmd=restore,
        )
        ps(cmd)
        return {"ok": True, "msg": f"{display_name} — {action} done."}

    # ── Startup Actions ───────────────────────────────────────────────────

    def remove_startup(self, name: str, command: str, source: str) -> dict:
        """
        Remove a startup item.

        For startup folder shortcuts, the file is backed up before deletion.
        For registry entries, the key/value is removed.
        """
        if source == "StartupFolder":
            # Back up the shortcut before deleting
            try:
                shutil.copy2(command, BACKUP_DIR / f"startup_{name}")
            except Exception:
                pass
            restore = (
                f"Copy-Item '{BACKUP_DIR / f'startup_{name}'}' '{command}'"
            )
            ps(f"Remove-Item '{command}' -Force")
        else:
            restore = (
                f"New-ItemProperty -Path '{source}' -Name '{name}' "
                f"-Value '{command}' -PropertyType String"
            )
            ps(f"Remove-ItemProperty -Path '{source}' -Name '{name}' -Force")

        self.log_action("Startup", name, "remove", command, restore)
        return {"ok": True, "msg": f"'{name}' removed from startup."}

    # ── Store App Actions ─────────────────────────────────────────────────

    def uninstall_store_app(self, name: str, package_full_name: str) -> dict:
        """Remove a Microsoft Store (AppX) package."""
        restore = (
            f"Get-AppxPackage -AllUsers *{name}* | ForEach-Object {{"
            f"Add-AppxPackage -Register "
            f"\"$($_.InstallLocation)\\AppXManifest.xml\" "
            f"-DisableDevelopmentMode}}"
        )
        self.log_action("StoreApp", name, "uninstall", package_full_name, restore)
        ps(f"Remove-AppxPackage -Package '{package_full_name}'")
        return {"ok": True, "msg": f"'{name}' uninstalled."}


# ─────────────────────────────────────────────────────────────────────────────
# HTML / CSS / JS Frontend
#
# Embedded as a single string. Uses:
#   - Inter font (Google Fonts, with system font fallbacks)
#   - Inline SVGs (no external icon font dependency)
#   - AMOLED black (#000) with glass-morphism surfaces
#   - Material Design 3 color tokens
#   - CSS animations with spring easing
#   - Frameless window with custom drag region
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Master Uninstaller</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ═══════════════════════════════════════════════════════════════════════
   RESET & DESIGN TOKENS
   ═══════════════════════════════════════════════════════════════════════ */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  /* Surfaces */
  --bg:#000;
  --sf1:rgba(255,255,255,.03);
  --sf2:rgba(255,255,255,.05);
  --sf3:rgba(255,255,255,.07);
  --sf4:rgba(255,255,255,.10);
  --sf5:rgba(255,255,255,.14);
  /* Glass */
  --gl:rgba(255,255,255,.04);
  --glb:rgba(255,255,255,.06);
  --glh:rgba(255,255,255,.09);
  --gla:rgba(255,255,255,.13);
  /* Text */
  --t1:#f0ecf4;
  --t2:rgba(240,236,244,.7);
  --t3:rgba(240,236,244,.4);
  /* Accent (Purple — MD3 Primary) */
  --pr:#d0bcff;--prd:#9a82db;--prc:rgba(208,188,255,.10);
  /* Semantic */
  --er:#f2b8b5;--erc:rgba(242,184,181,.10);
  --ok:#a8d5a2;--okc:rgba(168,213,162,.10);
  --wa:#f9deac;--wac:rgba(249,222,172,.10);
  --in:#89b4fa;--inc:rgba(137,180,250,.10);
  /* Layout */
  --r1:8px;--r2:12px;--r3:16px;--r4:24px;
  /* Motion */
  --ease:220ms cubic-bezier(.2,0,0,1);
  --spring:320ms cubic-bezier(.34,1.56,.64,1);
  --smooth:280ms cubic-bezier(.4,0,.2,1);
}

html,body{
  height:100%;width:100%;
  font-family:'Inter','Segoe UI Variable','Segoe UI',system-ui,-apple-system,sans-serif;
  background:var(--bg);color:var(--t1);overflow:hidden;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  font-feature-settings:'cv01','cv02','cv03','cv04';
  letter-spacing:-.01em;
}

/* Scrollbar */
::-webkit-scrollbar{width:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18)}

/* Selection */
::selection{background:rgba(208,188,255,.25);color:#fff}

/* SVG icon base */
svg.i{width:18px;height:18px;fill:currentColor;flex-shrink:0;display:inline-block;vertical-align:middle}
svg.sm{width:15px;height:15px}svg.xs{width:13px;height:13px}

/* ═══════════════════════════════════════════════════════════════════════
   LAYOUT
   ═══════════════════════════════════════════════════════════════════════ */
.app{display:flex;flex-direction:column;height:100vh}

/* ── Custom Titlebar ─────────────────────────────────────────────────── */
.titlebar{
  display:flex;align-items:center;gap:10px;padding:7px 10px 7px 14px;
  background:var(--sf1);border-bottom:1px solid var(--glb);
  -webkit-app-region:drag;user-select:none;flex-shrink:0;
  backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
}
.tlogo{
  width:28px;height:28px;
  background:linear-gradient(135deg,#d0bcff 0%,#9a82db 50%,#7c5cbf 100%);
  border-radius:7px;display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 8px rgba(154,130,219,.3),inset 0 1px 0 rgba(255,255,255,.2);
}
.tlogo svg{width:14px;height:14px;fill:#fff}
.titlebar h1{font-size:12px;font-weight:600;flex:1;color:var(--t2)}
.abadge{
  font-size:10px;font-weight:500;padding:2px 8px;border-radius:12px;
  display:flex;align-items:center;gap:3px;-webkit-app-region:no-drag;
  backdrop-filter:blur(10px);
}
.abadge.y{background:var(--okc);color:var(--ok)}
.abadge.n{background:var(--erc);color:var(--er)}
.wbtn{
  -webkit-app-region:no-drag;background:none;border:none;color:var(--t3);
  width:28px;height:28px;border-radius:6px;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:all var(--ease);
}
.wbtn:hover{background:var(--sf3);color:var(--t1)}
.wbtn.close:hover{background:rgba(242,184,181,.15);color:var(--er)}
.wbtn svg{width:16px;height:16px}

/* ── Settings Dropdown ───────────────────────────────────────────────── */
.sdd{
  position:absolute;top:42px;right:10px;
  background:rgba(14,12,20,.97);
  backdrop-filter:blur(60px) saturate(200%);-webkit-backdrop-filter:blur(60px) saturate(200%);
  border:1px solid var(--sf3);border-radius:var(--r2);padding:4px;
  min-width:240px;box-shadow:0 12px 48px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.03);
  z-index:200;opacity:0;transform:translateY(-4px) scale(.98);
  pointer-events:none;transition:all 160ms ease;
}
.sdd.open{opacity:1;transform:translateY(0) scale(1);pointer-events:all}
.sdi{
  display:flex;align-items:center;justify-content:space-between;
  padding:7px 10px;border-radius:var(--r1);font-size:11.5px;
  cursor:pointer;transition:background var(--ease);color:var(--t2);
}
.sdi:hover{background:var(--sf3);color:var(--t1)}
.sdi label{cursor:pointer;flex:1}
.tog{
  width:36px;height:20px;border-radius:10px;background:var(--sf3);
  position:relative;cursor:pointer;transition:background var(--ease);flex-shrink:0;
}
.tog.on{background:var(--pr)}
.tog::after{
  content:'';position:absolute;width:14px;height:14px;border-radius:50%;
  background:#fff;top:3px;left:3px;transition:left var(--spring);
  box-shadow:0 1px 3px rgba(0,0,0,.3);
}
.tog.on::after{left:19px}
.sddiv{height:1px;background:var(--sf3);margin:2px 6px}

/* ── Tabs ────────────────────────────────────────────────────────────── */
.tabs{
  display:flex;gap:1px;padding:4px 12px 0;
  background:var(--sf1);overflow-x:auto;flex-shrink:0;
}
.tab{
  padding:7px 13px;font-size:11px;font-weight:500;color:var(--t3);
  border:none;background:none;cursor:pointer;
  border-radius:var(--r1) var(--r1) 0 0;transition:all var(--ease);
  display:flex;align-items:center;gap:5px;white-space:nowrap;position:relative;
}
.tab::after{
  content:'';position:absolute;bottom:0;left:24%;right:24%;height:2px;
  border-radius:2px 2px 0 0;background:var(--pr);
  transform:scaleX(0);transition:transform var(--spring);
}
.tab:hover{color:var(--t2);background:var(--sf2)}
.tab.a{color:var(--pr)}.tab.a::after{transform:scaleX(1)}
.tab svg{opacity:.7}.tab.a svg{opacity:1}
.badge{
  background:var(--prc);color:var(--pr);font-size:9px;font-weight:600;
  padding:1px 5px;border-radius:6px;min-width:16px;text-align:center;
  letter-spacing:.02em;
}

/* ── Content ─────────────────────────────────────────────────────────── */
.content{flex:1;overflow:hidden;position:relative}
.pn{
  position:absolute;inset:0;padding:12px 14px;overflow-y:auto;
  opacity:0;transform:translateY(6px);
  transition:opacity 200ms ease,transform 200ms ease;pointer-events:none;
}
.pn.a{opacity:1;transform:translateY(0);pointer-events:all}

/* ── Toolbar ─────────────────────────────────────────────────────────── */
.tb{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.sbox{
  display:flex;align-items:center;gap:6px;
  background:var(--sf2);border:1px solid transparent;
  border-radius:var(--r4);padding:6px 12px;flex:1;max-width:340px;
  transition:all var(--ease);
}
.sbox:focus-within{
  border-color:rgba(208,188,255,.3);background:rgba(208,188,255,.04);
  box-shadow:0 0 0 3px rgba(208,188,255,.08);
}
.sbox input{
  background:none;border:none;outline:none;color:var(--t1);
  font-size:12px;width:100%;font-family:inherit;
}
.sbox input::placeholder{color:var(--t3)}
.tsp{flex:1}
.fg{display:flex;gap:2px}
.fc{
  padding:4px 10px;border-radius:14px;font-size:10px;font-weight:500;
  font-family:inherit;border:1px solid var(--sf3);background:transparent;
  color:var(--t3);cursor:pointer;transition:all var(--ease);letter-spacing:.01em;
}
.fc:hover{background:var(--sf3);color:var(--t2)}
.fc.a{background:var(--prc);color:var(--pr);border-color:rgba(208,188,255,.2)}

/* ── Buttons ─────────────────────────────────────────────────────────── */
.bt{
  display:inline-flex;align-items:center;gap:4px;
  padding:5px 12px;font-size:11px;font-weight:500;font-family:inherit;
  border:none;border-radius:var(--r4);cursor:pointer;
  transition:all var(--ease);white-space:nowrap;
}
.bt:hover{filter:brightness(1.2)}.bt:active{transform:scale(.96)}
.bd{background:var(--erc);color:var(--er)}
.bs{background:var(--okc);color:var(--ok)}
.bo{background:var(--sf2);color:var(--t2);border:1px solid var(--sf3)}
.bo:hover{background:var(--sf3);color:var(--t1)}
.bg{
  background:none;color:var(--t3);padding:4px;border-radius:6px;
  border:none;cursor:pointer;display:flex;align-items:center;
  justify-content:center;transition:all var(--ease);
}
.bg:hover{background:var(--sf3);color:var(--t1)}

/* ── Cards ───────────────────────────────────────────────────────────── */
.cl{display:flex;flex-direction:column;gap:2px}
.cd{
  display:grid;grid-template-columns:38px 1fr auto;align-items:center;gap:10px;
  padding:8px 11px;background:var(--gl);
  border:1px solid var(--glb);border-radius:var(--r1);
  transition:
    transform 220ms cubic-bezier(.34,1.56,.64,1),
    background 160ms ease,
    border-color 160ms ease,
    box-shadow 220ms ease;
  cursor:default;animation:ci 260ms cubic-bezier(.16,1,.3,1) both;
  position:relative;
}
.cd:hover{
  transform:scale(1.015);
  background:var(--gla);
  border-color:rgba(208,188,255,.15);
  box-shadow:
    0 8px 32px -4px rgba(0,0,0,.4),
    0 0 0 1px rgba(208,188,255,.06),
    inset 0 1px 0 rgba(255,255,255,.05);
  z-index:2;
}
.cd:active{transform:scale(.998)}
@keyframes ci{from{opacity:0;transform:translateY(5px) scale(.99)}to{opacity:1;transform:translateY(0) scale(1)}}

/* Stagger delays */
.cd:nth-child(1){animation-delay:0ms}.cd:nth-child(2){animation-delay:15ms}
.cd:nth-child(3){animation-delay:30ms}.cd:nth-child(4){animation-delay:45ms}
.cd:nth-child(5){animation-delay:60ms}.cd:nth-child(6){animation-delay:75ms}
.cd:nth-child(7){animation-delay:90ms}.cd:nth-child(8){animation-delay:105ms}
.cd:nth-child(9){animation-delay:120ms}.cd:nth-child(10){animation-delay:130ms}
.cd:nth-child(n+11){animation-delay:140ms}

/* App avatar (colored initial letter) */
.av{
  width:38px;height:38px;border-radius:var(--r1);
  display:flex;align-items:center;justify-content:center;
  font-size:16px;font-weight:700;text-transform:uppercase;flex-shrink:0;
  letter-spacing:0;
}

/* Card content */
.ci{min-width:0}
.ci .nm{font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--t1)}
.ci .mt{font-size:10px;color:var(--t3);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ca{display:flex;gap:3px;flex-shrink:0;align-items:center}
.ca .bt{padding:4px 9px;font-size:10px}

/* ── Context Menu ────────────────────────────────────────────────────── */
.ctx{
  position:fixed;z-index:300;
  background:rgba(14,12,20,.97);
  backdrop-filter:blur(60px) saturate(200%);-webkit-backdrop-filter:blur(60px) saturate(200%);
  border:1px solid var(--sf3);border-radius:var(--r2);padding:4px;
  min-width:180px;box-shadow:0 12px 48px rgba(0,0,0,.6);
  animation:mi 140ms cubic-bezier(.16,1,.3,1);
}
@keyframes mi{from{opacity:0;transform:scale(.95) translateY(-2px)}to{opacity:1;transform:scale(1) translateY(0)}}
.cxi{
  display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;
  font-size:11.5px;cursor:pointer;transition:background var(--ease);color:var(--t2);
}
.cxi:hover{background:var(--sf3);color:var(--t1)}
.cxi.dg{color:var(--er)}.cxi.dg:hover{background:var(--erc)}
.cxd{height:1px;background:var(--sf3);margin:2px 4px}

/* ── Chips ────────────────────────────────────────────────────────────── */
.ch{
  display:inline-flex;align-items:center;padding:1px 6px;border-radius:10px;
  font-size:9.5px;font-weight:600;letter-spacing:.02em;
}
.ch.run{background:var(--okc);color:var(--ok)}
.ch.stp{background:var(--erc);color:var(--er)}
.ch.en{background:var(--okc);color:var(--ok)}
.ch.dis{background:var(--sf2);color:var(--t3)}
.ch.rst{background:var(--prc);color:var(--pr)}

/* ── Empty / Loading ─────────────────────────────────────────────────── */
.ey{display:flex;flex-direction:column;align-items:center;padding:48px 16px;color:var(--t3)}
.ey p{font-size:12px;margin-top:8px;font-weight:500}
.ld{display:flex;align-items:center;justify-content:center;padding:48px}
.sp{
  width:24px;height:24px;border:2.5px solid var(--sf3);
  border-top-color:var(--pr);border-radius:50%;
  animation:spin .65s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Toast ────────────────────────────────────────────────────────────── */
.tbox{position:fixed;bottom:12px;right:12px;display:flex;flex-direction:column;gap:4px;z-index:500}
.tst{
  display:flex;align-items:center;gap:6px;padding:8px 12px;
  background:rgba(14,12,20,.96);
  backdrop-filter:blur(40px);-webkit-backdrop-filter:blur(40px);
  border:1px solid var(--sf3);border-radius:var(--r2);
  box-shadow:0 8px 32px rgba(0,0,0,.5);font-size:11.5px;max-width:340px;
  animation:ti 260ms cubic-bezier(.34,1.56,.64,1);transition:all 200ms ease;
}
@keyframes ti{from{opacity:0;transform:translateX(20px) scale(.95)}}

/* ── Dialog ──────────────────────────────────────────────────────────── */
.dlbg{
  position:fixed;inset:0;background:rgba(0,0,0,.5);
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  display:flex;align-items:center;justify-content:center;
  z-index:400;opacity:0;pointer-events:none;transition:opacity 140ms ease;
}
.dlbg.op{opacity:1;pointer-events:all}
.dl{
  background:rgba(18,16,24,.98);
  backdrop-filter:blur(60px);-webkit-backdrop-filter:blur(60px);
  border:1px solid var(--sf3);border-radius:var(--r3);padding:20px;
  max-width:380px;width:92%;
  box-shadow:0 16px 64px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.03);
  transform:scale(.95);transition:transform 200ms cubic-bezier(.34,1.56,.64,1);
}
.dlbg.op .dl{transform:scale(1)}
.dl h3{font-size:14px;font-weight:600;margin-bottom:6px}
.dl p{font-size:11.5px;color:var(--t2);line-height:1.5;margin-bottom:4px}
.dl code{
  display:block;background:var(--sf2);border-radius:var(--r1);
  padding:6px 8px;margin:4px 0 12px;font-size:10.5px;
  font-family:'Cascadia Code','Fira Code',Consolas,monospace;
  color:var(--wa);word-break:break-all;max-height:56px;overflow-y:auto;
  border:1px solid var(--sf3);
}
.dlb{display:flex;gap:6px;justify-content:flex-end}

/* ── Status Bar ──────────────────────────────────────────────────────── */
.sb{
  display:flex;align-items:center;gap:5px;padding:3px 14px;
  background:var(--sf1);border-top:1px solid var(--glb);
  font-size:9.5px;color:var(--t3);flex-shrink:0;letter-spacing:.01em;
}
.sb .dot{width:4px;height:4px;border-radius:50%;background:var(--ok);animation:pu 2s ease infinite}
@keyframes pu{0%,100%{opacity:1}50%{opacity:.25}}
.sb .sp2{flex:1}

/* Log detail */
.ld2{
  background:var(--sf2);border-radius:var(--r1);padding:10px;margin-top:8px;
  font-size:10.5px;font-family:'Cascadia Code','Fira Code',Consolas,monospace;
  color:var(--t3);white-space:pre-wrap;word-break:break-all;
  max-height:100px;overflow-y:auto;border:1px solid var(--sf3);
}
</style></head><body>
<div class="app">
  <!-- ═══════════════════════════════════════════════════════════════════
       TITLEBAR — custom frameless, drag-to-move, window controls
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="titlebar">
    <div class="tlogo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg></div>
    <h1>Master Uninstaller</h1>
    <div class="abadge" id="ab"></div>
    <button class="wbtn" onclick="tRef()" id="rBtn" title="Auto-refresh"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 6v3l4-4-4-4v3c-4.42 0-8 3.58-8 8 0 1.57.46 3.03 1.24 4.26L6.7 14.8A5.87 5.87 0 016 12c0-3.31 2.69-6 6-6zm6.76 1.74L17.3 9.2c.44.84.7 1.79.7 2.8 0 3.31-2.69 6-6 6v-3l-4 4 4 4v-3c4.42 0 8-3.58 8-8 0-1.57-.46-3.03-1.24-4.26z"/></svg></button>
    <button class="wbtn" onclick="tSet()" title="Settings"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg></button>
    <button class="wbtn" onclick="pywebview.api.minimize_window()" title="Minimize"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 19h12v2H6z"/></svg></button>
    <button class="wbtn close" onclick="pywebview.api.close_window()" title="Close"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button>
  </div>

  <!-- Settings dropdown -->
  <div class="sdd" id="sdd">
    <div class="sdi" onclick="tS('auto_refresh')"><label>Auto-refresh</label><div class="tog" id="t_ar"></div></div>
    <div class="sdi" onclick="tS('confirm_actions')"><label>Confirm actions</label><div class="tog" id="t_ca"></div></div>
    <div class="sddiv"></div>
    <div class="sdi" onclick="rAll()"><label>Refresh all tabs</label></div>
    <div class="sdi" onclick="expLog()"><label>Export log</label></div>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════
       TABS — navigation between sections
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="tabs">
    <button class="tab a" data-t="prog" onclick="sT('prog')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M4 8h4V4H4v4zm6 12h4v-4h-4v4zm-6 0h4v-4H4v4zm0-6h4v-4H4v4zm6 0h4v-4h-4v4zm6-10v4h4V4h-4zm-6 4h4V4h-4v4zm6 6h4v-4h-4v4zm0 6h4v-4h-4v4z"/></svg> Programs <span class="badge" id="bp">-</span></button>
    <button class="tab" data-t="feat" onclick="sT('feat')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5C13 2.12 11.88 1 10.5 1S8 2.12 8 3.5V5H4c-1.1 0-2 .9-2 2v3.8H3.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7s2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.38 0 2.5-1.12 2.5-2.5S21.88 11 20.5 11z"/></svg> Features <span class="badge" id="bf">-</span></button>
    <button class="tab" data-t="svc" onclick="sT('svc')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg> Services <span class="badge" id="bs">-</span></button>
    <button class="tab" data-t="start" onclick="sT('start')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg> Startup <span class="badge" id="bst">-</span></button>
    <button class="tab" data-t="store" onclick="sT('store')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M18.36 9l.6 3H5.04l.6-3h12.72M20 4H4v2h16V4zm0 3H4l-1 5v2h1v6h10v-6h4v6h2v-6h1v-2l-1-5zM6 18v-4h6v4H6z"/></svg> Store <span class="badge" id="bsr">-</span></button>
    <button class="tab" data-t="logs" onclick="sT('logs')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0013 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg> Log</button>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════
       CONTENT PANELS — one per tab, animated in/out
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="content">
    <div class="pn a" id="pn_prog"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search programs..." id="sp" oninput="fP()"></div><div class="fg"><button class="fc a" data-f="all" onclick="sPF('all',this)">All</button><button class="fc" data-f="has" onclick="sPF('has',this)">Has Uninstaller</button><button class="fc" data-f="no" onclick="sPF('no',this)">No Uninstaller</button><button class="fc" data-f="sys" onclick="sPF('sys',this)">System</button></div><div class="tsp"></div><button class="bt bo" onclick="lP()">Refresh</button></div><div class="cl" id="lp"><div class="ld"><div class="sp"></div></div></div></div>
    <div class="pn" id="pn_feat"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search features..." id="sf" oninput="fF()"></div><div class="tsp"></div><button class="bt bo" onclick="lF()">Refresh</button></div><div class="cl" id="lf"><div class="ld"><div class="sp"></div></div></div></div>
    <div class="pn" id="pn_svc"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search services..." id="ss" oninput="fS()"></div><div class="tsp"></div><button class="bt bo" onclick="lS()">Refresh</button></div><div class="cl" id="ls"><div class="ld"><div class="sp"></div></div></div></div>
    <div class="pn" id="pn_start"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search startup..." id="sst" oninput="fSt()"></div><div class="tsp"></div><button class="bt bo" onclick="lSt()">Refresh</button></div><div class="cl" id="lst"><div class="ld"><div class="sp"></div></div></div></div>
    <div class="pn" id="pn_store"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search store apps..." id="ssr" oninput="fSr()"></div><div class="tsp"></div><button class="bt bo" onclick="lSr()">Refresh</button></div><div class="cl" id="lsr"><div class="ld"><div class="sp"></div></div></div></div>
    <div class="pn" id="pn_logs"><div class="tb"><button class="bt bo" onclick="lLg()">Refresh</button><button class="bt bo" onclick="expLog()">Export</button></div><div class="cl" id="llg"></div><div class="ld2" id="lgd" style="display:none"></div></div>
  </div>

  <!-- Status bar -->
  <div class="sb"><div class="dot"></div><span id="stx">Ready</span><div class="sp2"></div><span id="stt"></span></div>
</div>

<!-- Confirm dialog overlay -->
<div class="dlbg" id="dbg"><div class="dl"><h3 id="dT">Confirm</h3><p id="dM"></p><code id="dC" style="display:none"></code><div class="dlb"><button class="bt bo" onclick="cD(false)">Cancel</button><button class="bt bd" onclick="cD(true)">Confirm</button></div></div></div>
<!-- Toast container -->
<div class="tbox" id="tbox"></div>

<!-- ═══════════════════════════════════════════════════════════════════════
     JAVASCRIPT — app logic, data binding, event handlers
     ═══════════════════════════════════════════════════════════════════════ -->
<script>
// ── Inline SVG icon library (no external font dependency) ─────────────
const S=`<svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24">`;
const I={
  del:S+`<path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`,
  x:S+`<path d="M12 2C6.47 2 2 6.47 2 12s4.47 10 10 10 10-4.47 10-10S17.53 2 12 2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z"/></svg>`,
  fld:S+`<path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>`,
  inf:S+`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>`,
  dots:S+`<path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>`,
  pl:S+`<path d="M8 5v14l11-7z"/></svg>`,
  st:S+`<path d="M6 6h12v12H6z"/></svg>`,
  ok:S+`<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>`,
  off:S+`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/></svg>`,
  un:S+`<path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/></svg>`,
};

// ── Avatar colors — deterministic based on app name ───────────────────
const CL=['#7c4dff','#448aff','#00bfa5','#ff6d00','#d500f9','#00b8d4','#64dd17','#ff1744','#651fff','#00c853','#aa00ff','#2979ff'];
function ac(s){let h=0;for(let i=0;i<s.length;i++)h=s.charCodeAt(i)+((h<<5)-h);return CL[Math.abs(h)%CL.length]}
function av(n){const c=ac(n),l=(n||'?')[0].toUpperCase();return`<div class="av" style="background:${c}18;color:${c};text-shadow:0 0 20px ${c}40">${l}</div>`}
function ib(svg,c){return`<div class="av" style="background:var(--${c}c);color:var(--${c})">${svg}</div>`}

// ── Application state ─────────────────────────────────────────────────
let set={auto_refresh:true,refresh_interval:30,confirm_actions:true};
let D={p:[],f:[],s:[],st:[],sr:[]};  // cached data per tab
let aT='prog',arT=null,dR=null,pF='all',oC=null;
const SM={1:'Stopped',2:'StartPending',3:'StopPending',4:'Running'};
const TM={0:'Boot',1:'System',2:'Automatic',3:'Manual',4:'Disabled'};

// ── Initialization ────────────────────────────────────────────────────
async function init(){
  const a=await pywebview.api.get_admin_status();
  const b=document.getElementById('ab');
  b.className='abadge '+(a?'y':'n');b.textContent=a?'Admin':'Limited';
  set=await pywebview.api.get_settings();uSU();sAR();lP();
}
window.addEventListener('pywebviewready',init);

// ── Settings panel ────────────────────────────────────────────────────
function tSet(){document.getElementById('sdd').classList.toggle('open')}
document.addEventListener('click',e=>{
  const d=document.getElementById('sdd');
  if(d.classList.contains('open')&&!d.contains(e.target)&&!e.target.closest('[title="Settings"]'))d.classList.remove('open');
  if(oC&&!oC.contains(e.target)){oC.remove();oC=null}
});
function tS(k){set[k]=!set[k];uSU();pywebview.api.save_settings(JSON.stringify(set));if(k==='auto_refresh')sAR()}
function uSU(){
  document.getElementById('t_ar').className='tog'+(set.auto_refresh?' on':'');
  document.getElementById('t_ca').className='tog'+(set.confirm_actions?' on':'');
  document.getElementById('rBtn').style.color=set.auto_refresh?'var(--pr)':'var(--t3)';
}
function tRef(){tS('auto_refresh')}
function sAR(){if(arT)clearInterval(arT);if(set.auto_refresh)arT=setInterval(rC,(set.refresh_interval||30)*1000)}

// ── Tab switching ─────────────────────────────────────────────────────
function sT(t){
  aT=t;
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('a',x.dataset.t===t));
  document.querySelectorAll('.pn').forEach(x=>x.classList.toggle('a',x.id==='pn_'+t));
  const m={prog:lP,feat:lF,svc:lS,start:lSt,store:lSr,logs:lLg};
  const el=document.getElementById({prog:'lp',feat:'lf',svc:'ls',start:'lst',store:'lsr',logs:'llg'}[t]);
  if(el&&el.querySelector('.ld'))m[t]?.();
}
function rC(){({prog:lP,feat:lF,svc:lS,start:lSt,store:lSr,logs:lLg})[aT]?.()}
function rAll(){document.getElementById('sdd').classList.remove('open');lP();lF();lS();lSt();lSr();lLg()}

// ── Context menu (right-click style, anchored to ... button) ──────────
function ctx(e,items){
  e.stopPropagation();if(oC){oC.remove();oC=null}
  const m=document.createElement('div');m.className='ctx';
  m.innerHTML=items.map(i=>i==='---'?'<div class="cxd"></div>':`<div class="cxi ${i.c||''}" onclick="${i.a}">${i.i||''} ${i.l}</div>`).join('');
  document.body.appendChild(m);
  let x=e.clientX,y=e.clientY;const r=m.getBoundingClientRect();
  if(x+r.width>innerWidth)x=innerWidth-r.width-8;
  if(y+r.height>innerHeight)y=innerHeight-r.height-8;
  m.style.left=x+'px';m.style.top=y+'px';oC=m;
}

// ═════════════════════════════════════════════════════════════════════════
// PROGRAMS TAB
// ═════════════════════════════════════════════════════════════════════════
function sPF(f,btn){pF=f;document.querySelectorAll('.fc').forEach(c=>c.classList.toggle('a',c.dataset.f===f));fP()}
async function lP(){st('Loading programs...');D.p=await pywebview.api.get_programs()||[];document.getElementById('bp').textContent=D.p.length;fP();st(D.p.length+' programs')}
function gFP(){
  const q=(document.getElementById('sp').value||'').toLowerCase();
  let r=D.p;
  if(q)r=r.filter(p=>(p.DisplayName||'').toLowerCase().includes(q)||(p.Publisher||'').toLowerCase().includes(q));
  if(pF==='has')r=r.filter(p=>p.UninstallString);
  else if(pF==='no')r=r.filter(p=>!p.UninstallString);
  else if(pF==='sys')r=r.filter(p=>p.SystemComponent===1);
  return r;
}
function fP(){
  const items=gFP(),el=document.getElementById('lp');
  if(!items.length){el.innerHTML=ey('No programs found');return}
  el.innerHTML=items.map((p,i)=>{
    const sz=p.EstimatedSize?fSz(p.EstimatedSize):'';
    const mt=[p.DisplayVersion,p.Publisher,sz].filter(Boolean).join(' \u00b7 ');
    const hu=!!p.UninstallString;
    return`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms">
      ${av(p.DisplayName||'')}
      <div class="ci"><div class="nm">${esc(p.DisplayName||'')}</div><div class="mt">${esc(mt)}</div></div>
      <div class="ca">
        ${hu?`<button class="bt bd" onclick="uP(${i})">${I.del} Uninstall</button>`
            :`<button class="bt bd" onclick="fUP(${i})">${I.x} Force</button>`}
        <button class="bg" onclick="pM(event,${i})">${I.dots}</button>
      </div></div>`;
  }).join('');
}
function pM(e,i){
  const p=gFP()[i];if(!p)return;const items=[];
  if(p.UninstallString)items.push({l:'Uninstall',i:I.del,a:`uP(${i})`,c:'dg'});
  items.push({l:'Force Uninstall',i:I.x,a:`fUP(${i})`,c:'dg'});
  if(p.InstallLocation)items.push('---',{l:'Open Location',i:I.fld,a:`oL(${i})`});
  items.push('---',{l:'Details',i:I.inf,a:`pD(${i})`});
  ctx(e,items);
}
async function uP(i){
  const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}
  if(set.confirm_actions&&!await dlg('Uninstall',`Uninstall <strong>${esc(p.DisplayName)}</strong>?`,p.UninstallString))return;
  st('Uninstalling '+p.DisplayName+'...');
  const r=await pywebview.api.uninstall_program(p.DisplayName,p.UninstallString,JSON.stringify(p));
  tst(r.ok?'ok':'er',r.msg);if(r.ok)setTimeout(lP,3000);
}
async function fUP(i){
  const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}
  if(!await dlg('Force Uninstall','<span style="color:var(--er)">Force remove</span> <strong>'+esc(p.DisplayName)+'</strong>?<br><br>This will try silent uninstall, remove registry entries, and delete the install folder.',p.InstallLocation||'N/A'))return;
  st('Force removing...');
  const r=await pywebview.api.force_uninstall_program(p.DisplayName,JSON.stringify(p));
  tst(r.ok?'ok':'er',r.msg);if(r.ok)setTimeout(lP,1500);
}
async function oL(i){const p=gFP()[i];if(oC){oC.remove();oC=null}if(p)await pywebview.api.open_location(p.InstallLocation||'')}
function pD(i){const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}dlg('Details',`<strong>${esc(p.DisplayName)}</strong><br>Version: ${esc(p.DisplayVersion||'\u2014')}<br>Publisher: ${esc(p.Publisher||'\u2014')}<br>Size: ${p.EstimatedSize?fSz(p.EstimatedSize):'\u2014'}<br>Location: ${esc(p.InstallLocation||'\u2014')}<br>System: ${p.SystemComponent?'Yes':'No'}`,p.UninstallString||'No uninstall string')}

// ═════════════════════════════════════════════════════════════════════════
// FEATURES TAB
// ═════════════════════════════════════════════════════════════════════════
async function lF(){st('Loading features...');D.f=await pywebview.api.get_features()||[];document.getElementById('bf').textContent=D.f.length;fF();st(D.f.length+' features')}
function fF(){
  const q=(document.getElementById('sf').value||'').toLowerCase();
  const items=D.f.filter(f=>!q||(f.FeatureName||'').toLowerCase().includes(q));
  const el=document.getElementById('lf');
  if(!items.length){el.innerHTML=ey('No features found');return}
  el.innerHTML=items.map((f,i)=>{const on=f.State===2||f.State==='Enabled';
    return`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms">${ib(on?I.ok:I.off,on?'ok':'er')}<div class="ci"><div class="nm">${esc(f.FeatureName||'')}</div><div class="mt"><span class="ch ${on?'en':'dis'}">${on?'Enabled':'Disabled'}</span></div></div><div class="ca">${on?`<button class="bt bd" onclick="tF('${ea(f.FeatureName)}',0)">${I.off} Disable</button>`:`<button class="bt bs" onclick="tF('${ea(f.FeatureName)}',1)">${I.ok} Enable</button>`}</div></div>`}).join('');
}
async function tF(n,en){if(set.confirm_actions&&!await dlg(en?'Enable':'Disable',(en?'Enable':'Disable')+' <strong>'+esc(n)+'</strong>?'))return;st((en?'Enabling':'Disabling')+' '+n+'...');const r=await pywebview.api.toggle_feature(n,!!en);tst(r.ok?'ok':'er',r.msg);if(r.ok)setTimeout(lF,1000)}

// ═════════════════════════════════════════════════════════════════════════
// SERVICES TAB
// ═════════════════════════════════════════════════════════════════════════
async function lS(){st('Loading services...');D.s=await pywebview.api.get_services()||[];document.getElementById('bs').textContent=D.s.length;fS();st(D.s.length+' services')}
function fS(){
  const q=(document.getElementById('ss').value||'').toLowerCase();
  const items=D.s.filter(s=>!q||(s.DisplayName||'').toLowerCase().includes(q)||(s.Name||'').toLowerCase().includes(q));
  const el=document.getElementById('ls');
  if(!items.length){el.innerHTML=ey('No services found');return}
  el.innerHTML=items.map((s,i)=>{
    const st2=typeof s.Status==='number'?(SM[s.Status]||s.Status):s.Status;
    const tp=typeof s.StartType==='number'?(TM[s.StartType]||s.StartType):s.StartType;
    const run=st2==='Running',dis=tp==='Disabled';
    return`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms">${ib(run?I.pl:I.st,run?'ok':dis?'er':'wa')}<div class="ci"><div class="nm">${esc(s.DisplayName||s.Name)}</div><div class="mt">${esc(s.Name)} \u00b7 <span class="ch ${run?'run':'stp'}">${st2}</span> \u00b7 ${tp}</div></div><div class="ca">${run?`<button class="bt bd" onclick="sA('${ea(s.Name)}','${ea(s.DisplayName)}','stop')">${I.st} Stop</button>`:`<button class="bt bs" onclick="sA('${ea(s.Name)}','${ea(s.DisplayName)}','start')">${I.pl} Start</button>`}<button class="bg" onclick="sM(event,'${ea(s.Name)}','${ea(s.DisplayName)}',${run},${dis})">${I.dots}</button></div></div>`}).join('');
}
function sM(e,n,dn,run,dis){const items=[];if(run)items.push({l:'Stop',i:I.st,a:`sA('${n}','${dn}','stop')`,c:'dg'});else items.push({l:'Start',i:I.pl,a:`sA('${n}','${dn}','start')`});items.push('---');if(!dis)items.push({l:'Disable',i:I.off,a:`sA('${n}','${dn}','disable')`,c:'dg'});else items.push({l:'Enable',i:I.ok,a:`sA('${n}','${dn}','enable')`});ctx(e,items)}
async function sA(n,dn,a){if(oC){oC.remove();oC=null}if(set.confirm_actions&&!await dlg(a+' Service',a+' <strong>'+esc(dn)+'</strong>?'))return;st(a+'ing '+dn+'...');const r=await pywebview.api.service_action(n,dn,a);tst(r.ok?'ok':'er',r.msg);if(r.ok){lS();setTimeout(lS,2000)}}

// ═════════════════════════════════════════════════════════════════════════
// STARTUP TAB
// ═════════════════════════════════════════════════════════════════════════
async function lSt(){st('Loading startup...');D.st=await pywebview.api.get_startup()||[];document.getElementById('bst').textContent=D.st.length;fSt();st(D.st.length+' startup items')}
function fSt(){
  const q=(document.getElementById('sst').value||'').toLowerCase();
  const items=D.st.filter(s=>!q||(s.Name||'').toLowerCase().includes(q));
  const el=document.getElementById('lst');
  if(!items.length){el.innerHTML=ey('No startup items');return}
  el.innerHTML=items.map((s,i)=>`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms">${av(s.Name||'')}<div class="ci"><div class="nm">${esc(s.Name)}</div><div class="mt" title="${esc(s.Command)}">${esc(s.Command)}</div></div><div class="ca"><button class="bt bd" onclick="rSt(${i})">${I.del} Remove</button></div></div>`).join('');
}
async function rSt(i){const q=(document.getElementById('sst').value||'').toLowerCase();const s=D.st.filter(s=>!q||(s.Name||'').toLowerCase().includes(q))[i];if(!s)return;if(set.confirm_actions&&!await dlg('Remove','Remove <strong>'+esc(s.Name)+'</strong>?',s.Command))return;const r=await pywebview.api.remove_startup(s.Name,s.Command,s.Source);tst(r.ok?'ok':'er',r.msg);if(r.ok)setTimeout(lSt,500)}

// ═════════════════════════════════════════════════════════════════════════
// STORE APPS TAB
// ═════════════════════════════════════════════════════════════════════════
async function lSr(){st('Loading store apps...');D.sr=await pywebview.api.get_store_apps()||[];document.getElementById('bsr').textContent=D.sr.length;fSr();st(D.sr.length+' store apps')}
function fSr(){
  const q=(document.getElementById('ssr').value||'').toLowerCase();
  const items=D.sr.filter(a=>!q||(a.Name||'').toLowerCase().includes(q));
  const el=document.getElementById('lsr');
  if(!items.length){el.innerHTML=ey('No store apps');return}
  el.innerHTML=items.map((a,i)=>`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms">${av(a.Name||'')}<div class="ci"><div class="nm">${esc(a.Name)}</div><div class="mt">v${esc(a.Version||'')}${a.IsFramework?' \u00b7 Framework':''}</div></div><div class="ca"><button class="bt bd" onclick="uSr(${i})">${I.del} Uninstall</button><button class="bg" onclick="srM(event,${i})">${I.dots}</button></div></div>`).join('');
}
function srM(e,i){ctx(e,[{l:'Uninstall',i:I.del,a:`uSr(${i})`,c:'dg'},'---',{l:'Details',i:I.inf,a:`srD(${i})`}])}
function srD(i){const q=(document.getElementById('ssr').value||'').toLowerCase();const a=D.sr.filter(a=>!q||(a.Name||'').toLowerCase().includes(q))[i];if(!a)return;if(oC){oC.remove();oC=null}dlg('Store App','<strong>'+esc(a.Name)+'</strong><br>v'+esc(a.Version)+(a.IsFramework?'<br>Framework':''),a.PackageFullName)}
async function uSr(i){const q=(document.getElementById('ssr').value||'').toLowerCase();const a=D.sr.filter(a=>!q||(a.Name||'').toLowerCase().includes(q))[i];if(!a)return;if(oC){oC.remove();oC=null}if(set.confirm_actions&&!await dlg('Uninstall','Uninstall <strong>'+esc(a.Name)+'</strong>?',a.PackageFullName))return;st('Uninstalling...');const r=await pywebview.api.uninstall_store_app(a.Name,a.PackageFullName);tst(r.ok?'ok':'er',r.msg);if(r.ok)setTimeout(lSr,1500)}

// ═════════════════════════════════════════════════════════════════════════
// ACTION LOG TAB
// ═════════════════════════════════════════════════════════════════════════
async function lLg(){const e=await pywebview.api.get_action_log()||[];const el=document.getElementById('llg');if(!e.length){el.innerHTML=ey('No actions logged');return}el.innerHTML=e.map((x,i)=>`<div class="cd" style="animation-delay:${Math.min(i*12,140)}ms;cursor:pointer" onclick="lgD(${i})">${ib(x.restored?I.un:I.del,x.restored?'pr':'er')}<div class="ci"><div class="nm">${esc(x.name)} ${x.restored?'<span class="ch rst">Restored</span>':''}</div><div class="mt">${esc(x.category)} \u00b7 ${esc(x.action)} \u00b7 ${new Date(x.timestamp).toLocaleString()}</div></div><div class="ca">${!x.restored&&x.restore_cmd?`<button class="bt bs" onclick="event.stopPropagation();rsA(${i})">${I.un} Restore</button>`:''}</div></div>`).join('');document.getElementById('lgd').style.display='none'}
async function lgD(i){const e=(await pywebview.api.get_action_log())[i];const d=document.getElementById('lgd');d.style.display='block';d.textContent='Restore: '+(e.restore_cmd||'N/A')+'\n\nDetails: '+(e.details||'N/A')}
async function rsA(i){if(set.confirm_actions){const e=(await pywebview.api.get_action_log())[i];if(!await dlg('Restore','Restore <strong>'+esc(e.name)+'</strong>?',e.restore_cmd))return}const r=await pywebview.api.restore_action(i);tst(r.ok?'ok':'er',r.msg);lLg()}
async function expLog(){document.getElementById('sdd').classList.remove('open');tst('ok','Exported: '+await pywebview.api.export_log())}

// ═════════════════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS
// ═════════════════════════════════════════════════════════════════════════
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function ea(s){return(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'")}
function fSz(k){return k>1048576?(k/1048576).toFixed(1)+' GB':k>1024?(k/1024).toFixed(1)+' MB':k+' KB'}
function ey(t){return`<div class="ey"><p>${t}</p></div>`}
function st(t){document.getElementById('stx').textContent=t;document.getElementById('stt').textContent=new Date().toLocaleTimeString()}
function tst(t,m){const c=document.getElementById('tbox'),e=document.createElement('div');e.className='tst';e.style.color=t==='er'?'var(--er)':'var(--ok)';e.innerHTML=(t==='ok'?I.ok:I.x)+' '+esc(m);c.appendChild(e);setTimeout(()=>{e.style.opacity='0';e.style.transform='translateX(20px)';setTimeout(()=>e.remove(),200)},4000)}
function dlg(title,msg,code){return new Promise(r=>{dR=r;document.getElementById('dT').textContent=title;document.getElementById('dM').innerHTML=msg;const c=document.getElementById('dC');if(code){c.textContent=code;c.style.display='block'}else c.style.display='none';document.getElementById('dbg').classList.add('op')})}
function cD(r){document.getElementById('dbg').classList.remove('op');if(dR){dR(r);dR=null}}
</script></body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Prompt for admin elevation if not already running as admin
    if not is_admin():
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        if messagebox.askyesno(
            "Administrator Required",
            "Master Uninstaller needs admin privileges for full access.\n\n"
            "Relaunch as Administrator?",
        ):
            run_as_admin()
        root.destroy()

    # Create the API and launch the frameless window
    api = Api()
    _window = webview.create_window(
        "Master Uninstaller",
        html=HTML,
        js_api=api,
        width=1100,
        height=720,
        min_size=(800, 500),
        background_color="#000000",
        text_select=False,
        frameless=True,
    )
    webview.start(debug=False)
