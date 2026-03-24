#!/usr/bin/env python3
"""
Master Uninstaller v1.1 — High-fidelity Windows system manager.

Features:
  - Browse and uninstall all installed programs (registry + system components)
  - Force-uninstall stubborn apps (Edge-style: kill processes, silent uninstall,
    registry cleanup, folder deletion with ownership takeover, scheduled task removal)
  - Manage Windows optional features (enable/disable)
  - Control Windows services (start, stop, enable, disable)
  - Remove startup items (registry entries + startup folder shortcuts)
  - Uninstall Microsoft Store / AppX packages (including system/framework apps)
  - Manage Windows Scheduled Tasks (disable/delete)
  - Full action log with one-click restore
  - AMOLED dark theme with glass-morphism UI
  - Frameless window with custom titlebar + maximize/restore

Architecture:
  Python backend (pywebview js_api) <-> HTML/CSS/JS frontend (WebView2)
  All system operations use PowerShell with hidden windows (CREATE_NO_WINDOW).
  Uninstallers are launched with visible windows so the user can interact.

Author: Chirag
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import webview

# ─────────────────────────────────────────────────────────────────────────────
# Configuration paths
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


def ps(cmd: str, as_json: bool = False, timeout: int = 120):
    """
    Execute a PowerShell command silently (no visible window).

    Args:
        cmd: PowerShell script to execute.
        as_json: If True, parse stdout as JSON and return a list.
        timeout: Maximum execution time in seconds.

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
            timeout=timeout,
            startupinfo=_startupinfo,
            creationflags=_CREATE_NO_WINDOW,
        )
        if as_json and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                return data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                return []
        return result.stdout.strip()
    except Exception:
        return [] if as_json else ""


def ps_exit_code(cmd: str, timeout: int = 120) -> int:
    """Run PowerShell and return exit code (0=success)."""
    full_cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd
    ]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout,
            startupinfo=_startupinfo, creationflags=_CREATE_NO_WINDOW,
        )
        return result.returncode
    except Exception:
        return -1


# ─────────────────────────────────────────────────────────────────────────────
# API — exposed to the frontend via pywebview's js_api bridge
# ─────────────────────────────────────────────────────────────────────────────
class Api:
    """Backend API called from JavaScript via `pywebview.api.*` methods."""

    def __init__(self):
        self._load_settings()

    # ── Settings ──────────────────────────────────────────────────────────

    def _load_settings(self):
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
        if _window:
            _window.minimize()

    def maximize_window(self):
        """Toggle maximize/restore."""
        if _window:
            if getattr(self, '_maximized', False):
                _window.restore()
                self._maximized = False
            else:
                _window.maximize()
                self._maximized = True
        return getattr(self, '_maximized', False)

    def close_window(self):
        if _window:
            _window.destroy()

    # ── Action Log ────────────────────────────────────────────────────────

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

    def clear_log(self) -> dict:
        """Clear the entire action log."""
        self._save_log([])
        return {"ok": True, "msg": "Log cleared."}

    def export_log(self) -> str:
        path = LOG_DIR / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(
            json.dumps(self._load_log(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(path)

    # ── Data Fetchers ─────────────────────────────────────────────────────

    def get_programs(self) -> list:
        """
        Fetch ALL installed programs from 3 registry hives.
        Includes system components so the user can see everything.
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
                        ModifyPath         = $_.ModifyPath
                    }
                }
            }
        }
        $all.Values | Sort-Object DisplayName | ConvertTo-Json -Depth 3 -Compress
        """
        return ps(cmd, as_json=True)

    def get_features(self) -> list:
        return ps(
            "Get-WindowsOptionalFeature -Online "
            "| Select-Object FeatureName, State "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    def get_services(self) -> list:
        return ps(
            "Get-Service "
            "| Select-Object Name, DisplayName, Status, StartType "
            "| Sort-Object DisplayName "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    def get_startup(self) -> list:
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
        return ps(
            "Get-AppxPackage "
            "| Select-Object Name, PackageFullName, Version, Publisher, "
            "IsFramework, NonRemovable, InstallLocation "
            "| Sort-Object Name "
            "| ConvertTo-Json -Depth 2 -Compress",
            as_json=True,
        )

    def get_scheduled_tasks(self) -> list:
        """Fetch scheduled tasks with their state and next run time."""
        return ps(
            "Get-ScheduledTask -EA SilentlyContinue "
            "| Where-Object { $_.TaskPath -notlike '\\Microsoft\\Windows\\*' -or "
            "$_.TaskName -match 'Edge|Update|OneDrive|Teams|Office|Google|Adobe|Brave' } "
            "| Select-Object TaskName, TaskPath, State, "
            "@{N='Description';E={$_.Description}}, "
            "@{N='Author';E={$_.Principal.UserId}} "
            "| Sort-Object TaskName "
            "| ConvertTo-Json -Depth 3 -Compress",
            as_json=True,
        )

    # ── Uninstall Actions ─────────────────────────────────────────────────

    def uninstall_program(self, name: str, uninst_str: str, details_json: str) -> dict:
        """Launch the program's own uninstaller in a visible window."""
        self.log_action(
            "Program", name, "uninstall", details_json,
            f"Reinstall '{name}' manually.",
        )
        try:
            if "msiexec" in uninst_str.lower():
                subprocess.Popen(uninst_str, shell=True)
            else:
                subprocess.Popen(
                    uninst_str, shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            return {"ok": True, "msg": f"Uninstaller launched for '{name}'."}
        except Exception as ex:
            return {"ok": False, "msg": str(ex)}

    def force_uninstall_program(self, name: str, details_json: str) -> dict:
        """
        Force-uninstall using Edge-uninstaller-style strategies:
          1. Kill all related processes
          2. Set registry to allow uninstall (EdgeUpdateDev-style)
          3. Try QuietUninstallString
          4. Try MSI quiet uninstall (msiexec /x GUID /quiet)
          5. Try WMI Win32_Product uninstall
          6. Try setup.exe --uninstall --force-uninstall (Edge-style)
          7. Try common silent flags (/S, /silent, /quiet, /VERYSILENT, --uninstall)
          8. Remove registry uninstall entries
          9. Delete installation folder (with ownership takeover)
          10. Clean up shortcuts, scheduled tasks
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
        install_loc = info.get("InstallLocation", "")
        uninst = info.get("UninstallString", "")
        quiet = info.get("QuietUninstallString", "")

        # ── Strategy 1: Kill related processes ──
        safe_name = name.replace("'", "''")
        ps(f"""
        $procs = Get-Process -EA SilentlyContinue | Where-Object {{
            $_.ProcessName -like '*{safe_name.split()[0]}*' -or
            ($_.Path -and $_.Path -like '*{safe_name.split()[0]}*')
        }}
        $procs | Stop-Process -Force -EA SilentlyContinue
        """)
        results.append("Killed related processes")

        # ── Strategy 2: QuietUninstallString ──
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

        # ── Strategy 3: MSI quiet uninstall ──
        if uninst and "msiexec" in uninst.lower():
            guid = ""
            for part in uninst.split():
                if part.startswith("{") and part.endswith("}"):
                    guid = part
                    break
            if not guid:
                # Try regex extraction
                m = re.search(r'\{[A-F0-9-]+\}', uninst, re.IGNORECASE)
                if m:
                    guid = m.group(0)
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

        # ── Strategy 4: WMI Win32_Product (slow but thorough) ──
        if not results or len(results) <= 1:
            wmi_result = ps(
                f"$p = Get-WmiObject Win32_Product -Filter \"Name LIKE '%{safe_name}%'\" -EA SilentlyContinue; "
                f"if ($p) {{ $p.Uninstall() | Out-Null; 'removed' }} else {{ 'notfound' }}",
                timeout=180,
            )
            if "removed" in str(wmi_result):
                results.append("WMI uninstall succeeded")

        # ── Strategy 5: Find and run setup.exe with force flags (Edge-style) ──
        if install_loc and os.path.isdir(install_loc):
            for root_dir, dirs, files in os.walk(install_loc):
                for f in files:
                    if f.lower() in ("setup.exe", "uninstall.exe", "uninst.exe",
                                     "unins000.exe", "unins001.exe"):
                        exe_path = os.path.join(root_dir, f)
                        for flags in [
                            "--uninstall --system-level --force-uninstall",
                            "/S",
                            "/silent",
                            "/quiet",
                            "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
                            "-uninstall",
                        ]:
                            try:
                                ret = subprocess.run(
                                    f'"{exe_path}" {flags}', shell=True,
                                    startupinfo=_startupinfo,
                                    creationflags=_CREATE_NO_WINDOW,
                                    timeout=60,
                                )
                                if ret.returncode == 0:
                                    results.append(f"Setup uninstall: {f} {flags}")
                                    break
                            except Exception:
                                pass
                break  # only top-level

        # ── Strategy 6: Silent flags on UninstallString ──
        if uninst and "msiexec" not in uninst.lower() and not any("Setup" in r for r in results):
            for flag in ["/S", "/silent", "/quiet", "/VERYSILENT", "-uninstall",
                         "--uninstall", "/SUPPRESSMSGBOXES /NORESTART",
                         "-silent -uninstall"]:
                try:
                    ret = subprocess.run(
                        f"{uninst} {flag}", shell=True,
                        startupinfo=_startupinfo, creationflags=_CREATE_NO_WINDOW,
                        timeout=60,
                    )
                    if ret.returncode == 0:
                        results.append(f"Uninstall with {flag}")
                        break
                except Exception:
                    pass

        # ── Strategy 7: Remove registry entries ──
        ps_path = info.get("PSPath", "")
        if ps_path:
            reg_path = ps_path.replace(
                "Microsoft.PowerShell.Core\\Registry::", ""
            )
            ps(f"Remove-Item -Path '{reg_path}' -Recurse -Force -EA SilentlyContinue")
            results.append("Registry entry removed")

        # ── Strategy 8: Delete installation folder (with ownership takeover) ──
        if install_loc and os.path.isdir(install_loc):
            # Try taking ownership first (like Edge uninstaller)
            safe_loc = install_loc.replace("'", "''")
            ps(f"""
            $path = '{safe_loc}'
            try {{
                takeown /F $path /R /D Y 2>$null | Out-Null
                icacls $path /grant administrators:F /T /C /Q 2>$null | Out-Null
            }} catch {{}}
            try {{
                Remove-Item -Path $path -Recurse -Force -EA Stop
            }} catch {{}}
            """)
            # Fallback with Python
            if os.path.isdir(install_loc):
                try:
                    shutil.rmtree(install_loc, ignore_errors=True)
                except Exception:
                    pass
            if not os.path.isdir(install_loc):
                results.append(f"Deleted: {install_loc}")
            else:
                results.append(f"Partially cleaned: {install_loc}")

        # ── Strategy 9: Clean up shortcuts ──
        shortcut_dirs = [
            os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
            os.path.join(os.path.expanduser("~"), "Desktop"),
        ]
        cleaned_shortcuts = 0
        name_lower = name.lower()
        for sdir in shortcut_dirs:
            if os.path.isdir(sdir):
                for item in os.listdir(sdir):
                    if name_lower in item.lower() or (
                        name.split()[0].lower() in item.lower() if name else False
                    ):
                        full = os.path.join(sdir, item)
                        try:
                            if os.path.isdir(full):
                                shutil.rmtree(full, ignore_errors=True)
                            else:
                                os.remove(full)
                            cleaned_shortcuts += 1
                        except Exception:
                            pass
        if cleaned_shortcuts:
            results.append(f"Removed {cleaned_shortcuts} shortcut(s)")

        # ── Strategy 10: Remove related scheduled tasks ──
        task_name_part = name.split()[0] if name else ""
        if task_name_part:
            safe_tn = task_name_part.replace("'", "''")
            ps(f"""
            Get-ScheduledTask -EA SilentlyContinue |
                Where-Object {{ $_.TaskName -like '*{safe_tn}*' }} |
                ForEach-Object {{
                    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -EA SilentlyContinue
                }}
            """)
            results.append("Cleaned scheduled tasks")

        if results:
            return {"ok": True, "msg": "; ".join(results)}
        return {"ok": False, "msg": "No removal paths found."}

    def open_location(self, path: str) -> dict:
        if path and os.path.isdir(path):
            os.startfile(path)
            return {"ok": True}
        return {"ok": False, "msg": "Path not found."}

    # ── Feature Actions ───────────────────────────────────────────────────

    def toggle_feature(self, name: str, enable: bool) -> dict:
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
        if source == "StartupFolder":
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
        restore = (
            f"Get-AppxPackage -AllUsers *{name}* | ForEach-Object {{"
            f"Add-AppxPackage -Register "
            f"\"$($_.InstallLocation)\\AppXManifest.xml\" "
            f"-DisableDevelopmentMode}}"
        )
        self.log_action("StoreApp", name, "uninstall", package_full_name, restore)
        ps(f"Remove-AppxPackage -Package '{package_full_name}'")
        return {"ok": True, "msg": f"'{name}' uninstalled."}

    def force_remove_store_app(self, name: str, package_full_name: str) -> dict:
        """Force remove store app including provisioned packages."""
        restore = f"# Reinstall {name} from Microsoft Store"
        self.log_action("StoreApp", name, "force_uninstall", package_full_name, restore)
        # Remove for current user
        ps(f"Remove-AppxPackage -Package '{package_full_name}' -EA SilentlyContinue")
        # Remove for all users (provisioned)
        safe_name = name.replace("'", "''")
        ps(f"""
        Get-AppxPackage -AllUsers '*{safe_name}*' -EA SilentlyContinue |
            Remove-AppxPackage -AllUsers -EA SilentlyContinue
        Get-AppxProvisionedPackage -Online -EA SilentlyContinue |
            Where-Object {{ $_.PackageName -like '*{safe_name}*' }} |
            Remove-AppxProvisionedPackage -Online -EA SilentlyContinue
        """)
        return {"ok": True, "msg": f"'{name}' force-removed for all users."}

    # ── Scheduled Task Actions ────────────────────────────────────────────

    def disable_task(self, task_name: str) -> dict:
        safe = task_name.replace("'", "''")
        self.log_action("Task", task_name, "disable",
                        restore_cmd=f"Enable-ScheduledTask -TaskName '{safe}'")
        ps(f"Disable-ScheduledTask -TaskName '{safe}' -EA SilentlyContinue")
        return {"ok": True, "msg": f"Task '{task_name}' disabled."}

    def enable_task(self, task_name: str) -> dict:
        safe = task_name.replace("'", "''")
        self.log_action("Task", task_name, "enable",
                        restore_cmd=f"Disable-ScheduledTask -TaskName '{safe}'")
        ps(f"Enable-ScheduledTask -TaskName '{safe}' -EA SilentlyContinue")
        return {"ok": True, "msg": f"Task '{task_name}' enabled."}

    def delete_task(self, task_name: str) -> dict:
        safe = task_name.replace("'", "''")
        self.log_action("Task", task_name, "delete",
                        restore_cmd=f"# Task '{task_name}' was deleted. Manual re-creation required.")
        ps(f"Unregister-ScheduledTask -TaskName '{safe}' -Confirm:$false -EA SilentlyContinue")
        return {"ok": True, "msg": f"Task '{task_name}' deleted."}

    # ── System Info ───────────────────────────────────────────────────────

    def get_system_info(self) -> dict:
        """Get basic system information for the status bar."""
        info = ps(
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$cpu = (Get-CimInstance Win32_Processor).LoadPercentage; "
            "[PSCustomObject]@{ "
            "  ComputerName = $os.CSName; "
            "  OS = $os.Caption; "
            "  Build = $os.BuildNumber; "
            "  RAM = [math]::Round($os.TotalVisibleMemorySize/1MB,1); "
            "  FreeRAM = [math]::Round($os.FreePhysicalMemory/1MB,1); "
            "  CPU = $cpu "
            "} | ConvertTo-Json -Compress",
            as_json=True,
        )
        return info[0] if info else {}


# ─────────────────────────────────────────────────────────────────────────────
# HTML / CSS / JS Frontend — v1.1
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Master Uninstaller</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* ═══════════════════════════════════════════════════════════════════════
   RESET & DESIGN TOKENS
   ═══════════════════════════════════════════════════════════════════════ */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  /* Surfaces — layered opacity for depth */
  --bg:#000;
  --sf1:rgba(255,255,255,.025);
  --sf2:rgba(255,255,255,.045);
  --sf3:rgba(255,255,255,.07);
  --sf4:rgba(255,255,255,.10);
  --sf5:rgba(255,255,255,.14);
  /* Glass */
  --gl:rgba(255,255,255,.035);
  --glb:rgba(255,255,255,.055);
  --glh:rgba(255,255,255,.085);
  --gla:rgba(255,255,255,.12);
  /* Text */
  --t1:#f0ecf4;
  --t2:rgba(240,236,244,.7);
  --t3:rgba(240,236,244,.38);
  /* Accent — rich purple MD3 primary */
  --pr:#d0bcff;--prd:#9a82db;--prc:rgba(208,188,255,.10);
  --prg:linear-gradient(135deg,#d0bcff 0%,#9a82db 50%,#7c5cbf 100%);
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
  --bounce:400ms cubic-bezier(.34,1.8,.64,1);
}

html,body{
  height:100%;width:100%;
  font-family:'Inter','Segoe UI Variable','Segoe UI',system-ui,-apple-system,sans-serif;
  background:var(--bg);color:var(--t1);overflow:hidden;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  font-feature-settings:'cv01','cv02','cv03','cv04';
  letter-spacing:-.01em;
}

/* Scrollbar — thin and subtle */
::-webkit-scrollbar{width:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.08);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.16)}

::selection{background:rgba(208,188,255,.25);color:#fff}

/* SVG icon base */
svg.i{width:18px;height:18px;fill:currentColor;flex-shrink:0;display:inline-block;vertical-align:middle}
svg.sm{width:15px;height:15px}svg.xs{width:13px;height:13px}
svg.lg{width:20px;height:20px}

/* ═══════════════════════════════════════════════════════════════════════
   LAYOUT
   ═══════════════════════════════════════════════════════════════════════ */
.app{display:flex;flex-direction:column;height:100vh;position:relative}

/* Subtle noise texture overlay */
.app::before{
  content:'';position:absolute;inset:0;pointer-events:none;z-index:9999;
  opacity:.015;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}

/* ── Custom Titlebar ─────────────────────────────────────────────────── */
.titlebar{
  display:flex;align-items:center;gap:10px;padding:6px 10px 6px 14px;
  background:linear-gradient(180deg, rgba(255,255,255,.04) 0%, rgba(255,255,255,.015) 100%);
  border-bottom:1px solid var(--glb);
  -webkit-app-region:drag;user-select:none;flex-shrink:0;
  backdrop-filter:blur(30px) saturate(150%);-webkit-backdrop-filter:blur(30px) saturate(150%);
  position:relative;z-index:10;
}
.tlogo{
  width:30px;height:30px;
  background:var(--prg);
  border-radius:8px;display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 12px rgba(154,130,219,.35),inset 0 1px 0 rgba(255,255,255,.25);
  position:relative;overflow:hidden;
}
.tlogo::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,transparent 40%,rgba(255,255,255,.15) 50%,transparent 60%);
  animation:shimmer 3s ease infinite;
}
@keyframes shimmer{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
.tlogo svg{width:14px;height:14px;fill:#fff;position:relative;z-index:1}
.ttl{display:flex;flex-direction:column;flex:1}
.titlebar h1{font-size:12.5px;font-weight:700;color:var(--t1);letter-spacing:-.02em}
.tsub{font-size:9px;color:var(--t3);font-weight:400;margin-top:-1px}
.abadge{
  font-size:9.5px;font-weight:600;padding:3px 10px;border-radius:14px;
  display:flex;align-items:center;gap:4px;-webkit-app-region:no-drag;
  backdrop-filter:blur(10px);letter-spacing:.02em;
}
.abadge.y{background:var(--okc);color:var(--ok);box-shadow:0 0 12px rgba(168,213,162,.1)}
.abadge.n{background:var(--erc);color:var(--er);box-shadow:0 0 12px rgba(242,184,181,.1)}
.wbtn{
  -webkit-app-region:no-drag;background:none;border:none;color:var(--t3);
  width:30px;height:30px;border-radius:7px;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:all var(--ease);position:relative;
}
.wbtn:hover{background:var(--sf3);color:var(--t1)}
.wbtn:active{transform:scale(.9)}
.wbtn.close:hover{background:rgba(242,184,181,.15);color:var(--er)}
.wbtn svg{width:16px;height:16px}
/* Tooltip */
.wbtn[title]:hover::after{
  content:attr(title);position:absolute;bottom:-26px;left:50%;transform:translateX(-50%);
  background:rgba(20,18,28,.95);color:var(--t2);font-size:9px;padding:3px 8px;
  border-radius:5px;white-space:nowrap;pointer-events:none;z-index:100;
  border:1px solid var(--sf3);backdrop-filter:blur(10px);
}

/* ── Settings Dropdown ───────────────────────────────────────────────── */
.sdd{
  position:absolute;top:44px;right:10px;
  background:rgba(12,10,18,.97);
  backdrop-filter:blur(80px) saturate(200%);-webkit-backdrop-filter:blur(80px) saturate(200%);
  border:1px solid var(--sf3);border-radius:var(--r2);padding:4px;
  min-width:250px;box-shadow:0 16px 64px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.03);
  z-index:200;opacity:0;transform:translateY(-6px) scale(.97);
  pointer-events:none;transition:all 180ms cubic-bezier(.16,1,.3,1);
}
.sdd.open{opacity:1;transform:translateY(0) scale(1);pointer-events:all}
.sdi{
  display:flex;align-items:center;justify-content:space-between;
  padding:8px 12px;border-radius:var(--r1);font-size:11.5px;
  cursor:pointer;transition:background var(--ease);color:var(--t2);gap:8px;
}
.sdi:hover{background:var(--sf3);color:var(--t1)}
.sdi label{cursor:pointer;flex:1}
.sdi svg{width:14px;height:14px;opacity:.5}
.tog{
  width:36px;height:20px;border-radius:10px;background:var(--sf3);
  position:relative;cursor:pointer;transition:background var(--spring);flex-shrink:0;
}
.tog.on{background:var(--prd)}
.tog::after{
  content:'';position:absolute;width:14px;height:14px;border-radius:50%;
  background:#fff;top:3px;left:3px;transition:left var(--spring);
  box-shadow:0 1px 4px rgba(0,0,0,.3);
}
.tog.on::after{left:19px}
.sddiv{height:1px;background:var(--sf3);margin:3px 8px}

/* ── Tabs ────────────────────────────────────────────────────────────── */
.tabs{
  display:flex;gap:0;padding:0 12px;
  background:var(--sf1);overflow-x:auto;flex-shrink:0;
  border-bottom:1px solid rgba(255,255,255,.03);
}
.tab{
  padding:9px 14px 8px;font-size:11px;font-weight:500;color:var(--t3);
  border:none;background:none;cursor:pointer;
  transition:all var(--ease);
  display:flex;align-items:center;gap:5px;white-space:nowrap;position:relative;
}
.tab::after{
  content:'';position:absolute;bottom:0;left:20%;right:20%;height:2.5px;
  border-radius:2px 2px 0 0;background:var(--prg);
  transform:scaleX(0);transition:transform var(--spring);
}
.tab:hover{color:var(--t2);background:rgba(255,255,255,.02)}
.tab.a{color:var(--pr);font-weight:600}.tab.a::after{transform:scaleX(1)}
.tab svg{opacity:.6;transition:opacity var(--ease)}.tab.a svg{opacity:1}
.badge{
  background:var(--prc);color:var(--pr);font-size:9px;font-weight:700;
  padding:1px 6px;border-radius:8px;min-width:18px;text-align:center;
  letter-spacing:.02em;
}

/* ── Content ─────────────────────────────────────────────────────────── */
.content{flex:1;overflow:hidden;position:relative}
.pn{
  position:absolute;inset:0;padding:12px 14px;overflow-y:auto;
  opacity:0;transform:translateY(8px);
  transition:opacity 240ms ease,transform 240ms cubic-bezier(.16,1,.3,1);
  pointer-events:none;
}
.pn.a{opacity:1;transform:translateY(0);pointer-events:all}

/* ── Toolbar ─────────────────────────────────────────────────────────── */
.tb{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.sbox{
  display:flex;align-items:center;gap:6px;
  background:var(--sf2);border:1px solid transparent;
  border-radius:var(--r4);padding:7px 14px;flex:1;max-width:360px;
  transition:all var(--ease);
}
.sbox:focus-within{
  border-color:rgba(208,188,255,.25);background:rgba(208,188,255,.04);
  box-shadow:0 0 0 3px rgba(208,188,255,.06),0 4px 16px rgba(0,0,0,.2);
}
.sbox input{
  background:none;border:none;outline:none;color:var(--t1);
  font-size:12px;width:100%;font-family:inherit;
}
.sbox input::placeholder{color:var(--t3)}
.tsp{flex:1}
.fg{display:flex;gap:2px}
.fc{
  padding:5px 12px;border-radius:16px;font-size:10px;font-weight:500;
  font-family:inherit;border:1px solid var(--sf3);background:transparent;
  color:var(--t3);cursor:pointer;transition:all var(--ease);letter-spacing:.01em;
}
.fc:hover{background:var(--sf3);color:var(--t2);border-color:var(--sf4)}
.fc.a{
  background:var(--prc);color:var(--pr);border-color:rgba(208,188,255,.2);
  box-shadow:0 0 12px rgba(208,188,255,.06);
}

/* ── Buttons ─────────────────────────────────────────────────────────── */
.bt{
  display:inline-flex;align-items:center;gap:4px;
  padding:5px 12px;font-size:11px;font-weight:500;font-family:inherit;
  border:none;border-radius:var(--r4);cursor:pointer;
  transition:all var(--ease);white-space:nowrap;position:relative;overflow:hidden;
}
.bt::before{
  content:'';position:absolute;inset:0;opacity:0;
  background:radial-gradient(circle at center,rgba(255,255,255,.15),transparent 70%);
  transition:opacity var(--ease);
}
.bt:hover::before{opacity:1}
.bt:hover{filter:brightness(1.15)}.bt:active{transform:scale(.95)}
.bd{background:var(--erc);color:var(--er)}
.bs{background:var(--okc);color:var(--ok)}
.bw{background:var(--wac);color:var(--wa)}
.bi{background:var(--inc);color:var(--in)}
.bo{background:var(--sf2);color:var(--t2);border:1px solid var(--sf3)}
.bo:hover{background:var(--sf3);color:var(--t1)}
.bg{
  background:none;color:var(--t3);padding:4px;border-radius:7px;
  border:none;cursor:pointer;display:flex;align-items:center;
  justify-content:center;transition:all var(--ease);
}
.bg:hover{background:var(--sf3);color:var(--t1)}

/* ── Cards ───────────────────────────────────────────────────────────── */
.cl{display:flex;flex-direction:column;gap:2px}
.cd{
  display:grid;grid-template-columns:40px 1fr auto;align-items:center;gap:10px;
  padding:8px 12px;background:var(--gl);
  border:1px solid var(--glb);border-radius:var(--r1);
  transition:
    transform 240ms cubic-bezier(.34,1.56,.64,1),
    background 180ms ease,
    border-color 180ms ease,
    box-shadow 240ms ease;
  cursor:default;animation:ci 280ms cubic-bezier(.16,1,.3,1) both;
  position:relative;
}
.cd:hover{
  transform:scale(1.012) translateY(-1px);
  background:var(--gla);
  border-color:rgba(208,188,255,.12);
  box-shadow:
    0 12px 40px -8px rgba(0,0,0,.45),
    0 0 0 1px rgba(208,188,255,.05),
    inset 0 1px 0 rgba(255,255,255,.04);
  z-index:2;
}
.cd:active{transform:scale(.998)}
@keyframes ci{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}

/* Stagger delays — smoother cascade */
.cd:nth-child(1){animation-delay:0ms}.cd:nth-child(2){animation-delay:18ms}
.cd:nth-child(3){animation-delay:36ms}.cd:nth-child(4){animation-delay:54ms}
.cd:nth-child(5){animation-delay:72ms}.cd:nth-child(6){animation-delay:88ms}
.cd:nth-child(7){animation-delay:100ms}.cd:nth-child(8){animation-delay:112ms}
.cd:nth-child(9){animation-delay:124ms}.cd:nth-child(10){animation-delay:134ms}
.cd:nth-child(n+11){animation-delay:144ms}

/* App avatar */
.av{
  width:40px;height:40px;border-radius:var(--r1);
  display:flex;align-items:center;justify-content:center;
  font-size:17px;font-weight:800;text-transform:uppercase;flex-shrink:0;
  letter-spacing:0;position:relative;overflow:hidden;
}
.av::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,transparent 30%,rgba(255,255,255,.08) 50%,transparent 70%);
  pointer-events:none;
}

/* Card content */
.ci{min-width:0}
.ci .nm{font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--t1)}
.ci .mt{font-size:10px;color:var(--t3);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ca{display:flex;gap:3px;flex-shrink:0;align-items:center}
.ca .bt{padding:4px 10px;font-size:10px}

/* ── Context Menu ────────────────────────────────────────────────────── */
.ctx{
  position:fixed;z-index:300;
  background:rgba(12,10,18,.97);
  backdrop-filter:blur(80px) saturate(200%);-webkit-backdrop-filter:blur(80px) saturate(200%);
  border:1px solid var(--sf3);border-radius:var(--r2);padding:4px;
  min-width:200px;box-shadow:0 16px 64px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.03);
  animation:mi 160ms cubic-bezier(.16,1,.3,1);
}
@keyframes mi{from{opacity:0;transform:scale(.94) translateY(-3px)}to{opacity:1;transform:scale(1) translateY(0)}}
.cxi{
  display:flex;align-items:center;gap:8px;padding:7px 12px;border-radius:7px;
  font-size:11.5px;cursor:pointer;transition:all var(--ease);color:var(--t2);
}
.cxi:hover{background:var(--sf3);color:var(--t1);transform:translateX(2px)}
.cxi.dg{color:var(--er)}.cxi.dg:hover{background:var(--erc)}
.cxd{height:1px;background:var(--sf3);margin:3px 6px}

/* ── Chips ────────────────────────────────────────────────────────────── */
.ch{
  display:inline-flex;align-items:center;padding:2px 7px;border-radius:10px;
  font-size:9px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
}
.ch.run{background:var(--okc);color:var(--ok)}
.ch.stp{background:var(--erc);color:var(--er)}
.ch.en{background:var(--okc);color:var(--ok)}
.ch.dis{background:var(--sf2);color:var(--t3)}
.ch.rdy{background:var(--inc);color:var(--in)}
.ch.rst{background:var(--prc);color:var(--pr)}
.ch.fw{background:var(--wac);color:var(--wa)}

/* ── Empty / Loading ─────────────────────────────────────────────────── */
.ey{display:flex;flex-direction:column;align-items:center;padding:60px 16px;color:var(--t3)}
.ey svg{width:48px;height:48px;opacity:.15;margin-bottom:12px}
.ey p{font-size:12px;font-weight:500}
.ey .sub{font-size:10px;margin-top:4px;color:var(--t3);opacity:.6}
.ld{display:flex;align-items:center;justify-content:center;padding:60px;flex-direction:column;gap:12px}
.sp{
  width:28px;height:28px;border:2.5px solid var(--sf3);
  border-top-color:var(--pr);border-radius:50%;
  animation:spin .6s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
.ld .lt{font-size:10px;color:var(--t3);animation:pulse 1.5s ease infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── Toast ────────────────────────────────────────────────────────────── */
.tbox{position:fixed;bottom:14px;right:14px;display:flex;flex-direction:column;gap:5px;z-index:500}
.tst{
  display:flex;align-items:center;gap:8px;padding:10px 14px;
  background:rgba(12,10,18,.96);
  backdrop-filter:blur(60px) saturate(180%);-webkit-backdrop-filter:blur(60px) saturate(180%);
  border:1px solid var(--sf3);border-radius:var(--r2);
  box-shadow:0 12px 48px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.02);
  font-size:11.5px;max-width:380px;
  animation:ti 300ms cubic-bezier(.34,1.56,.64,1);transition:all 200ms ease;
}
.tst .tc{display:flex;align-items:center;gap:6px;flex:1}
.tst .tx{cursor:pointer;opacity:.4;transition:opacity var(--ease)}
.tst .tx:hover{opacity:1}
@keyframes ti{from{opacity:0;transform:translateX(24px) scale(.94)}}

/* ── Dialog ──────────────────────────────────────────────────────────── */
.dlbg{
  position:fixed;inset:0;background:rgba(0,0,0,.55);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  display:flex;align-items:center;justify-content:center;
  z-index:400;opacity:0;pointer-events:none;transition:opacity 160ms ease;
}
.dlbg.op{opacity:1;pointer-events:all}
.dl{
  background:rgba(16,14,22,.98);
  backdrop-filter:blur(80px);-webkit-backdrop-filter:blur(80px);
  border:1px solid var(--sf3);border-radius:var(--r3);padding:24px;
  max-width:420px;width:92%;
  box-shadow:0 24px 80px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.03),
    0 0 120px -40px rgba(208,188,255,.08);
  transform:scale(.94) translateY(8px);
  transition:transform 240ms cubic-bezier(.34,1.56,.64,1);
}
.dlbg.op .dl{transform:scale(1) translateY(0)}
.dl h3{font-size:15px;font-weight:700;margin-bottom:8px;letter-spacing:-.02em}
.dl p{font-size:12px;color:var(--t2);line-height:1.6;margin-bottom:4px}
.dl code{
  display:block;background:var(--sf2);border-radius:var(--r1);
  padding:8px 10px;margin:6px 0 16px;font-size:10.5px;
  font-family:'Cascadia Code','Fira Code',Consolas,monospace;
  color:var(--wa);word-break:break-all;max-height:60px;overflow-y:auto;
  border:1px solid var(--sf3);
}
.dlb{display:flex;gap:8px;justify-content:flex-end}

/* ── Status Bar ──────────────────────────────────────────────────────── */
.sb{
  display:flex;align-items:center;gap:6px;padding:4px 14px;
  background:linear-gradient(180deg, rgba(255,255,255,.02) 0%, rgba(255,255,255,.01) 100%);
  border-top:1px solid var(--glb);
  font-size:9.5px;color:var(--t3);flex-shrink:0;letter-spacing:.01em;
}
.sb .dot{width:5px;height:5px;border-radius:50%;background:var(--ok);animation:pu 2s ease infinite;
  box-shadow:0 0 6px rgba(168,213,162,.3)}
@keyframes pu{0%,100%{opacity:1}50%{opacity:.2}}
.sb .sp2{flex:1}
.sb .si{display:flex;align-items:center;gap:4px;padding:1px 6px;border-radius:4px;background:var(--sf1)}

/* Log detail pane */
.ld2{
  background:var(--sf2);border-radius:var(--r1);padding:12px;margin-top:10px;
  font-size:10.5px;font-family:'Cascadia Code','Fira Code',Consolas,monospace;
  color:var(--t3);white-space:pre-wrap;word-break:break-all;
  max-height:120px;overflow-y:auto;border:1px solid var(--sf3);
}

/* ── Progress indicator ─────────────────────────────────────────────── */
.pbar{
  position:absolute;top:0;left:0;height:2px;background:var(--prg);
  border-radius:0 2px 2px 0;transition:width 300ms ease;z-index:50;
  box-shadow:0 0 8px rgba(208,188,255,.3);
}

/* ── Resize handle ──────────────────────────────────────────────────── */
.resize-handle{
  position:absolute;bottom:0;right:0;width:16px;height:16px;cursor:nwse-resize;
  z-index:100;opacity:.2;transition:opacity var(--ease);
}
.resize-handle:hover{opacity:.5}

/* ── Keyboard shortcut hints ────────────────────────────────────────── */
.kbd{
  font-size:8px;background:var(--sf2);border:1px solid var(--sf3);
  border-radius:3px;padding:1px 4px;color:var(--t3);font-family:inherit;
  margin-left:auto;
}
</style></head><body>
<div class="app">
  <!-- Progress bar -->
  <div class="pbar" id="pbar" style="width:0%"></div>

  <!-- ═══════════════════════════════════════════════════════════════════
       TITLEBAR
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="titlebar">
    <div class="tlogo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg></div>
    <div class="ttl"><h1>Master Uninstaller</h1><div class="tsub" id="tsub">Loading...</div></div>
    <div class="abadge" id="ab"></div>
    <button class="wbtn" onclick="tRef()" id="rBtn" title="Auto-refresh"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 6v3l4-4-4-4v3c-4.42 0-8 3.58-8 8 0 1.57.46 3.03 1.24 4.26L6.7 14.8A5.87 5.87 0 016 12c0-3.31 2.69-6 6-6zm6.76 1.74L17.3 9.2c.44.84.7 1.79.7 2.8 0 3.31-2.69 6-6 6v-3l-4 4 4 4v-3c4.42 0 8-3.58 8-8 0-1.57-.46-3.03-1.24-4.26z"/></svg></button>
    <button class="wbtn" onclick="tSet()" title="Settings"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg></button>
    <button class="wbtn" onclick="pywebview.api.minimize_window()" title="Minimize"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 19h12v2H6z"/></svg></button>
    <button class="wbtn" onclick="maxW()" id="maxBtn" title="Maximize"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 4h16v16H4V4zm2 2v12h12V6H6z"/></svg></button>
    <button class="wbtn close" onclick="pywebview.api.close_window()" title="Close"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></button>
  </div>

  <!-- Settings dropdown -->
  <div class="sdd" id="sdd">
    <div class="sdi" onclick="tS('auto_refresh')"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 6v3l4-4-4-4v3c-4.42 0-8 3.58-8 8 0 1.57.46 3.03 1.24 4.26L6.7 14.8A5.87 5.87 0 016 12c0-3.31 2.69-6 6-6zm6.76 1.74L17.3 9.2c.44.84.7 1.79.7 2.8 0 3.31-2.69 6-6 6v-3l-4 4 4 4v-3c4.42 0 8-3.58 8-8 0-1.57-.46-3.03-1.24-4.26z" fill="currentColor"/></svg><label>Auto-refresh</label><div class="tog" id="t_ar"></div></div>
    <div class="sdi" onclick="tS('confirm_actions')"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" fill="currentColor"/></svg><label>Confirm actions</label><div class="tog" id="t_ca"></div></div>
    <div class="sddiv"></div>
    <div class="sdi" onclick="rAll()"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" fill="currentColor"/></svg><label>Refresh all tabs</label><span class="kbd">Ctrl+R</span></div>
    <div class="sdi" onclick="expLog()"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z" fill="currentColor"/></svg><label>Export log</label></div>
    <div class="sddiv"></div>
    <div class="sdi" onclick="clrLog()"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/></svg><label>Clear log</label></div>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════
       TABS
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="tabs">
    <button class="tab a" data-t="prog" onclick="sT('prog')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M4 8h4V4H4v4zm6 12h4v-4h-4v4zm-6 0h4v-4H4v4zm0-6h4v-4H4v4zm6 0h4v-4h-4v4zm6-10v4h4V4h-4zm-6 4h4V4h-4v4zm6 6h4v-4h-4v4zm0 6h4v-4h-4v4z"/></svg>Programs<span class="badge" id="bp">-</span></button>
    <button class="tab" data-t="feat" onclick="sT('feat')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5C13 2.12 11.88 1 10.5 1S8 2.12 8 3.5V5H4c-1.1 0-2 .9-2 2v3.8H3.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7s2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.38 0 2.5-1.12 2.5-2.5S21.88 11 20.5 11z"/></svg>Features<span class="badge" id="bf">-</span></button>
    <button class="tab" data-t="svc" onclick="sT('svc')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>Services<span class="badge" id="bs">-</span></button>
    <button class="tab" data-t="start" onclick="sT('start')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg>Startup<span class="badge" id="bst">-</span></button>
    <button class="tab" data-t="store" onclick="sT('store')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M18.36 9l.6 3H5.04l.6-3h12.72M20 4H4v2h16V4zm0 3H4l-1 5v2h1v6h10v-6h4v6h2v-6h1v-2l-1-5zM6 18v-4h6v4H6z"/></svg>Store<span class="badge" id="bsr">-</span></button>
    <button class="tab" data-t="tasks" onclick="sT('tasks')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zM9 10H7v2h2v-2zm4 0h-2v2h2v-2zm4 0h-2v2h2v-2z"/></svg>Tasks<span class="badge" id="btk">-</span></button>
    <button class="tab" data-t="logs" onclick="sT('logs')"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24"><path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0013 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>Log</button>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════
       CONTENT PANELS
       ═══════════════════════════════════════════════════════════════════ -->
  <div class="content">
    <!-- Programs -->
    <div class="pn a" id="pn_prog"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search programs... (Ctrl+F)" id="sp" oninput="fP()"></div><div class="fg"><button class="fc a" data-f="all" onclick="sPF('all',this)">All</button><button class="fc" data-f="has" onclick="sPF('has',this)">Has Uninstaller</button><button class="fc" data-f="no" onclick="sPF('no',this)">No Uninstaller</button><button class="fc" data-f="sys" onclick="sPF('sys',this)">System</button></div><div class="tsp"></div><button class="bt bo" onclick="lP()">Refresh</button></div><div class="cl" id="lp"><div class="ld"><div class="sp"></div><div class="lt">Scanning registry...</div></div></div></div>
    <!-- Features -->
    <div class="pn" id="pn_feat"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search features..." id="sf" oninput="fF()"></div><div class="tsp"></div><button class="bt bo" onclick="lF()">Refresh</button></div><div class="cl" id="lf"><div class="ld"><div class="sp"></div><div class="lt">Loading features...</div></div></div></div>
    <!-- Services -->
    <div class="pn" id="pn_svc"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search services..." id="ss" oninput="fS()"></div><div class="fg"><button class="fc a" data-f="all" onclick="sSF('all',this)">All</button><button class="fc" data-f="run" onclick="sSF('run',this)">Running</button><button class="fc" data-f="stp" onclick="sSF('stp',this)">Stopped</button><button class="fc" data-f="dis" onclick="sSF('dis',this)">Disabled</button></div><div class="tsp"></div><button class="bt bo" onclick="lS()">Refresh</button></div><div class="cl" id="ls"><div class="ld"><div class="sp"></div><div class="lt">Loading services...</div></div></div></div>
    <!-- Startup -->
    <div class="pn" id="pn_start"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search startup items..." id="sst" oninput="fSt()"></div><div class="tsp"></div><button class="bt bo" onclick="lSt()">Refresh</button></div><div class="cl" id="lst"><div class="ld"><div class="sp"></div><div class="lt">Loading startup items...</div></div></div></div>
    <!-- Store -->
    <div class="pn" id="pn_store"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search store apps..." id="ssr" oninput="fSr()"></div><div class="fg"><button class="fc a" data-f="all" onclick="sRF('all',this)">All</button><button class="fc" data-f="app" onclick="sRF('app',this)">Apps</button><button class="fc" data-f="fw" onclick="sRF('fw',this)">Frameworks</button></div><div class="tsp"></div><button class="bt bo" onclick="lSr()">Refresh</button></div><div class="cl" id="lsr"><div class="ld"><div class="sp"></div><div class="lt">Loading store apps...</div></div></div></div>
    <!-- Tasks -->
    <div class="pn" id="pn_tasks"><div class="tb"><div class="sbox"><svg xmlns="http://www.w3.org/2000/svg" class="i sm" viewBox="0 0 24 24" style="fill:var(--t3)"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><input placeholder="Search tasks..." id="stk" oninput="fTk()"></div><div class="tsp"></div><button class="bt bo" onclick="lTk()">Refresh</button></div><div class="cl" id="ltk"><div class="ld"><div class="sp"></div><div class="lt">Loading tasks...</div></div></div></div>
    <!-- Logs -->
    <div class="pn" id="pn_logs"><div class="tb"><button class="bt bo" onclick="lLg()">Refresh</button><button class="bt bo" onclick="expLog()">Export</button><button class="bt bd" onclick="clrLog()">Clear</button></div><div class="cl" id="llg"></div><div class="ld2" id="lgd" style="display:none"></div></div>
  </div>

  <!-- Status bar -->
  <div class="sb">
    <div class="dot"></div>
    <span id="stx">Ready</span>
    <div class="sp2"></div>
    <div class="si" id="sinfo"></div>
    <span id="stt"></span>
  </div>
</div>

<!-- Confirm dialog -->
<div class="dlbg" id="dbg"><div class="dl"><h3 id="dT">Confirm</h3><p id="dM"></p><code id="dC" style="display:none"></code><div class="dlb"><button class="bt bo" onclick="cD(false)">Cancel</button><button class="bt bd" onclick="cD(true)">Confirm</button></div></div></div>
<!-- Toast container -->
<div class="tbox" id="tbox"></div>

<!-- ═══════════════════════════════════════════════════════════════════════
     JAVASCRIPT
     ═══════════════════════════════════════════════════════════════════════ -->
<script>
// ── Inline SVG icon library ───────────────────────────────────────────
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
  bolt:S+`<path d="M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z"/></svg>`,
  shield:S+`<path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/></svg>`,
  cal:S+`<path d="M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11z"/></svg>`,
  close:S+`<path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`,
};

// ── Avatar colors ─────────────────────────────────────────────────────
const CL=['#7c4dff','#448aff','#00bfa5','#ff6d00','#d500f9','#00b8d4','#64dd17','#ff1744','#651fff','#00c853','#aa00ff','#2979ff','#ff3d00','#00e676','#304ffe'];
function ac(s){let h=0;for(let i=0;i<s.length;i++)h=s.charCodeAt(i)+((h<<5)-h);return CL[Math.abs(h)%CL.length]}
function av(n){const c=ac(n),l=(n||'?')[0].toUpperCase();return`<div class="av" style="background:${c}15;color:${c};text-shadow:0 0 24px ${c}30">${l}</div>`}
function ib(svg,c){return`<div class="av" style="background:var(--${c}c);color:var(--${c})">${svg}</div>`}

// ── Application state ─────────────────────────────────────────────────
let set={auto_refresh:true,refresh_interval:30,confirm_actions:true};
let D={p:[],f:[],s:[],st:[],sr:[],tk:[]};
let aT='prog',arT=null,dR=null,pF='all',sF='all',rF='all',oC=null;
const SM={1:'Stopped',2:'StartPending',3:'StopPending',4:'Running'};
const TM={0:'Boot',1:'System',2:'Automatic',3:'Manual',4:'Disabled'};

// ── Progress bar ──────────────────────────────────────────────────────
let pI=0;
function pStart(){pI=0;pTick()}
function pTick(){if(pI<90){pI+=Math.random()*15;document.getElementById('pbar').style.width=Math.min(pI,90)+'%';setTimeout(pTick,200+Math.random()*300)}}
function pDone(){pI=100;const b=document.getElementById('pbar');b.style.width='100%';setTimeout(()=>{b.style.transition='none';b.style.width='0%';setTimeout(()=>b.style.transition='width 300ms ease',50)},400)}

// ── Initialization ────────────────────────────────────────────────────
async function init(){
  const a=await pywebview.api.get_admin_status();
  const b=document.getElementById('ab');
  b.className='abadge '+(a?'y':'n');
  b.innerHTML=a?I.shield+' Admin':I.shield+' Limited';
  set=await pywebview.api.get_settings();uSU();sAR();lP();
  // Load system info
  try{
    const si=await pywebview.api.get_system_info();
    if(si.OS)document.getElementById('tsub').textContent=si.OS+' (Build '+si.Build+')';
    if(si.RAM)document.getElementById('sinfo').textContent=si.FreeRAM+'/'+si.RAM+' GB RAM';
  }catch(e){document.getElementById('tsub').textContent='Windows System Manager'}
}
window.addEventListener('pywebviewready',init);

// ── Settings ──────────────────────────────────────────────────────────
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
  const m={prog:lP,feat:lF,svc:lS,start:lSt,store:lSr,tasks:lTk,logs:lLg};
  const ids={prog:'lp',feat:'lf',svc:'ls',start:'lst',store:'lsr',tasks:'ltk',logs:'llg'};
  const el=document.getElementById(ids[t]);
  if(el&&el.querySelector('.ld'))m[t]?.();
}
function rC(){({prog:lP,feat:lF,svc:lS,start:lSt,store:lSr,tasks:lTk,logs:lLg})[aT]?.()}
function rAll(){document.getElementById('sdd').classList.remove('open');lP();lF();lS();lSt();lSr();lTk();lLg()}

// ── Maximize ──────────────────────────────────────────────────────────
async function maxW(){
  const m=await pywebview.api.maximize_window();
  document.getElementById('maxBtn').title=m?'Restore':'Maximize';
}

// ── Keyboard shortcuts ────────────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='r'){e.preventDefault();rAll()}
  if(e.ctrlKey&&e.key==='f'){e.preventDefault();const inp=document.querySelector('.pn.a input');if(inp)inp.focus()}
  if(e.key==='Escape'){cD(false);if(oC){oC.remove();oC=null}document.getElementById('sdd').classList.remove('open')}
  // Tab shortcuts: Ctrl+1-7
  if(e.ctrlKey&&e.key>='1'&&e.key<='7'){
    e.preventDefault();
    const tabs=['prog','feat','svc','start','store','tasks','logs'];
    sT(tabs[parseInt(e.key)-1]);
  }
});

// ── Context menu ──────────────────────────────────────────────────────
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
function sPF(f,btn){pF=f;document.querySelectorAll('#pn_prog .fc').forEach(c=>c.classList.toggle('a',c.dataset.f===f));fP()}
async function lP(){pStart();st('Loading programs...');D.p=await pywebview.api.get_programs()||[];document.getElementById('bp').textContent=D.p.length;fP();st(D.p.length+' programs');pDone()}
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
  if(!items.length){el.innerHTML=ey('No programs found','Try adjusting your search or filters');return}
  el.innerHTML=items.map((p,i)=>{
    const sz=p.EstimatedSize?fSz(p.EstimatedSize):'';
    const mt=[p.DisplayVersion,p.Publisher,sz].filter(Boolean).join(' \u00b7 ');
    const hu=!!p.UninstallString;
    const sys=p.SystemComponent===1;
    return`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">
      ${av(p.DisplayName||'')}
      <div class="ci"><div class="nm">${esc(p.DisplayName||'')}${sys?' <span class="ch dis">SYS</span>':''}</div><div class="mt">${esc(mt)}</div></div>
      <div class="ca">
        ${hu?`<button class="bt bd" onclick="uP(${i})">${I.del} Uninstall</button>`:''}
        <button class="bt ${hu?'bw':'bd'}" onclick="fUP(${i})">${I.bolt} Force</button>
        <button class="bg" onclick="pM(event,${i})">${I.dots}</button>
      </div></div>`;
  }).join('');
}
function pM(e,i){
  const p=gFP()[i];if(!p)return;const items=[];
  if(p.UninstallString)items.push({l:'Uninstall',i:I.del,a:`uP(${i})`,c:'dg'});
  items.push({l:'Force Uninstall',i:I.bolt,a:`fUP(${i})`,c:'dg'});
  if(p.InstallLocation)items.push('---',{l:'Open Location',i:I.fld,a:`oL(${i})`});
  items.push('---',{l:'Details',i:I.inf,a:`pD(${i})`});
  ctx(e,items);
}
async function uP(i){
  const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}
  if(set.confirm_actions&&!await dlg('Uninstall',`Uninstall <strong>${esc(p.DisplayName)}</strong>?`,p.UninstallString))return;
  st('Uninstalling '+p.DisplayName+'...');pStart();
  const r=await pywebview.api.uninstall_program(p.DisplayName,p.UninstallString,JSON.stringify(p));
  tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lP,3000);
}
async function fUP(i){
  const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}
  if(!await dlg('Force Uninstall','<span style="color:var(--er)">Force remove</span> <strong>'+esc(p.DisplayName)+'</strong>?<br><br><span style="font-size:10px;color:var(--t3)">This will: kill processes, try silent uninstall, remove registry entries, delete install folder, clean shortcuts & scheduled tasks.</span>',p.InstallLocation||'N/A'))return;
  st('Force removing...');pStart();
  const r=await pywebview.api.force_uninstall_program(p.DisplayName,JSON.stringify(p));
  tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lP,1500);
}
async function oL(i){const p=gFP()[i];if(oC){oC.remove();oC=null}if(p)await pywebview.api.open_location(p.InstallLocation||'')}
function pD(i){const p=gFP()[i];if(!p)return;if(oC){oC.remove();oC=null}dlg('Program Details',`<strong>${esc(p.DisplayName)}</strong><br><br><table style="font-size:11px;color:var(--t2);width:100%"><tr><td style="color:var(--t3);padding:2px 8px 2px 0">Version</td><td>${esc(p.DisplayVersion||'\u2014')}</td></tr><tr><td style="color:var(--t3);padding:2px 8px 2px 0">Publisher</td><td>${esc(p.Publisher||'\u2014')}</td></tr><tr><td style="color:var(--t3);padding:2px 8px 2px 0">Size</td><td>${p.EstimatedSize?fSz(p.EstimatedSize):'\u2014'}</td></tr><tr><td style="color:var(--t3);padding:2px 8px 2px 0">Location</td><td style="word-break:break-all">${esc(p.InstallLocation||'\u2014')}</td></tr><tr><td style="color:var(--t3);padding:2px 8px 2px 0">System</td><td>${p.SystemComponent?'<span class="ch dis">Yes</span>':'No'}</td></tr></table>`,p.UninstallString||'No uninstall string')}

// ═════════════════════════════════════════════════════════════════════════
// FEATURES TAB
// ═════════════════════════════════════════════════════════════════════════
async function lF(){pStart();st('Loading features...');D.f=await pywebview.api.get_features()||[];document.getElementById('bf').textContent=D.f.length;fF();st(D.f.length+' features');pDone()}
function fF(){
  const q=(document.getElementById('sf').value||'').toLowerCase();
  const items=D.f.filter(f=>!q||(f.FeatureName||'').toLowerCase().includes(q));
  const el=document.getElementById('lf');
  if(!items.length){el.innerHTML=ey('No features found');return}
  el.innerHTML=items.map((f,i)=>{const on=f.State===2||f.State==='Enabled';
    return`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">${ib(on?I.ok:I.off,on?'ok':'er')}<div class="ci"><div class="nm">${esc(f.FeatureName||'')}</div><div class="mt"><span class="ch ${on?'en':'dis'}">${on?'Enabled':'Disabled'}</span></div></div><div class="ca">${on?`<button class="bt bd" onclick="tF('${ea(f.FeatureName)}',0)">${I.off} Disable</button>`:`<button class="bt bs" onclick="tF('${ea(f.FeatureName)}',1)">${I.ok} Enable</button>`}</div></div>`}).join('');
}
async function tF(n,en){if(set.confirm_actions&&!await dlg(en?'Enable Feature':'Disable Feature',(en?'Enable':'Disable')+' <strong>'+esc(n)+'</strong>?<br><br><span style="font-size:10px;color:var(--t3)">A restart may be required.</span>'))return;st((en?'Enabling':'Disabling')+' '+n+'...');pStart();const r=await pywebview.api.toggle_feature(n,!!en);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lF,1000)}

// ═════════════════════════════════════════════════════════════════════════
// SERVICES TAB
// ═════════════════════════════════════════════════════════════════════════
function sSF(f,btn){sF=f;document.querySelectorAll('#pn_svc .fc').forEach(c=>c.classList.toggle('a',c.dataset.f===f));fS()}
async function lS(){pStart();st('Loading services...');D.s=await pywebview.api.get_services()||[];document.getElementById('bs').textContent=D.s.length;fS();st(D.s.length+' services');pDone()}
function gFS(){
  const q=(document.getElementById('ss').value||'').toLowerCase();
  let r=D.s;
  if(q)r=r.filter(s=>(s.DisplayName||'').toLowerCase().includes(q)||(s.Name||'').toLowerCase().includes(q));
  if(sF==='run')r=r.filter(s=>{const st2=typeof s.Status==='number'?(SM[s.Status]||''):s.Status;return st2==='Running'});
  else if(sF==='stp')r=r.filter(s=>{const st2=typeof s.Status==='number'?(SM[s.Status]||''):s.Status;return st2==='Stopped'});
  else if(sF==='dis')r=r.filter(s=>{const tp=typeof s.StartType==='number'?(TM[s.StartType]||''):s.StartType;return tp==='Disabled'});
  return r;
}
function fS(){
  const items=gFS(),el=document.getElementById('ls');
  if(!items.length){el.innerHTML=ey('No services found');return}
  el.innerHTML=items.map((s,i)=>{
    const st2=typeof s.Status==='number'?(SM[s.Status]||s.Status):s.Status;
    const tp=typeof s.StartType==='number'?(TM[s.StartType]||s.StartType):s.StartType;
    const run=st2==='Running',dis=tp==='Disabled';
    return`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">${ib(run?I.pl:I.st,run?'ok':dis?'er':'wa')}<div class="ci"><div class="nm">${esc(s.DisplayName||s.Name)}</div><div class="mt">${esc(s.Name)} \u00b7 <span class="ch ${run?'run':'stp'}">${st2}</span> \u00b7 ${tp}</div></div><div class="ca">${run?`<button class="bt bd" onclick="sA('${ea(s.Name)}','${ea(s.DisplayName)}','stop')">${I.st} Stop</button>`:`<button class="bt bs" onclick="sA('${ea(s.Name)}','${ea(s.DisplayName)}','start')">${I.pl} Start</button>`}<button class="bg" onclick="sM(event,'${ea(s.Name)}','${ea(s.DisplayName)}',${run},${dis})">${I.dots}</button></div></div>`}).join('');
}
function sM(e,n,dn,run,dis){const items=[];if(run)items.push({l:'Stop',i:I.st,a:`sA('${n}','${dn}','stop')`,c:'dg'});else items.push({l:'Start',i:I.pl,a:`sA('${n}','${dn}','start')`});items.push('---');if(!dis)items.push({l:'Disable',i:I.off,a:`sA('${n}','${dn}','disable')`,c:'dg'});else items.push({l:'Enable',i:I.ok,a:`sA('${n}','${dn}','enable')`});ctx(e,items)}
async function sA(n,dn,a){if(oC){oC.remove();oC=null}if(set.confirm_actions&&!await dlg(a+' Service',a.charAt(0).toUpperCase()+a.slice(1)+' <strong>'+esc(dn)+'</strong>?'))return;st(a+'ing '+dn+'...');pStart();const r=await pywebview.api.service_action(n,dn,a);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok){lS();setTimeout(lS,2000)}}

// ═════════════════════════════════════════════════════════════════════════
// STARTUP TAB
// ═════════════════════════════════════════════════════════════════════════
async function lSt(){pStart();st('Loading startup...');D.st=await pywebview.api.get_startup()||[];document.getElementById('bst').textContent=D.st.length;fSt();st(D.st.length+' startup items');pDone()}
function fSt(){
  const q=(document.getElementById('sst').value||'').toLowerCase();
  const items=D.st.filter(s=>!q||(s.Name||'').toLowerCase().includes(q));
  const el=document.getElementById('lst');
  if(!items.length){el.innerHTML=ey('No startup items');return}
  el.innerHTML=items.map((s,i)=>`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">${av(s.Name||'')}<div class="ci"><div class="nm">${esc(s.Name)}</div><div class="mt" title="${esc(s.Command)}">${esc(s.Command)}</div></div><div class="ca"><button class="bt bd" onclick="rSt(${i})">${I.del} Remove</button></div></div>`).join('');
}
async function rSt(i){const q=(document.getElementById('sst').value||'').toLowerCase();const s=D.st.filter(s=>!q||(s.Name||'').toLowerCase().includes(q))[i];if(!s)return;if(set.confirm_actions&&!await dlg('Remove Startup','Remove <strong>'+esc(s.Name)+'</strong> from startup?',s.Command))return;st('Removing...');pStart();const r=await pywebview.api.remove_startup(s.Name,s.Command,s.Source);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lSt,500)}

// ═════════════════════════════════════════════════════════════════════════
// STORE APPS TAB
// ═════════════════════════════════════════════════════════════════════════
function sRF(f,btn){rF=f;document.querySelectorAll('#pn_store .fc').forEach(c=>c.classList.toggle('a',c.dataset.f===f));fSr()}
async function lSr(){pStart();st('Loading store apps...');D.sr=await pywebview.api.get_store_apps()||[];document.getElementById('bsr').textContent=D.sr.length;fSr();st(D.sr.length+' store apps');pDone()}
function gFSr(){
  const q=(document.getElementById('ssr').value||'').toLowerCase();
  let r=D.sr;
  if(q)r=r.filter(a=>(a.Name||'').toLowerCase().includes(q));
  if(rF==='app')r=r.filter(a=>!a.IsFramework);
  else if(rF==='fw')r=r.filter(a=>a.IsFramework);
  return r;
}
function fSr(){
  const items=gFSr(),el=document.getElementById('lsr');
  if(!items.length){el.innerHTML=ey('No store apps found');return}
  el.innerHTML=items.map((a,i)=>`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">${av(a.Name||'')}<div class="ci"><div class="nm">${esc(a.Name)}${a.IsFramework?' <span class="ch fw">Framework</span>':''}</div><div class="mt">v${esc(a.Version||'')}</div></div><div class="ca"><button class="bt bd" onclick="uSr(${i})">${I.del} Remove</button><button class="bt bw" onclick="fRSr(${i})">${I.bolt} Force</button><button class="bg" onclick="srM(event,${i})">${I.dots}</button></div></div>`).join('');
}
function srM(e,i){ctx(e,[{l:'Remove',i:I.del,a:`uSr(${i})`,c:'dg'},{l:'Force Remove (All Users)',i:I.bolt,a:`fRSr(${i})`,c:'dg'},'---',{l:'Details',i:I.inf,a:`srD(${i})`}])}
function srD(i){const a=gFSr()[i];if(!a)return;if(oC){oC.remove();oC=null}dlg('Store App','<strong>'+esc(a.Name)+'</strong><br>v'+esc(a.Version)+(a.IsFramework?'<br><span class="ch fw">Framework</span>':''),a.PackageFullName)}
async function uSr(i){const a=gFSr()[i];if(!a)return;if(oC){oC.remove();oC=null}if(set.confirm_actions&&!await dlg('Uninstall','Remove <strong>'+esc(a.Name)+'</strong>?',a.PackageFullName))return;st('Removing...');pStart();const r=await pywebview.api.uninstall_store_app(a.Name,a.PackageFullName);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lSr,1500)}
async function fRSr(i){const a=gFSr()[i];if(!a)return;if(oC){oC.remove();oC=null}if(!await dlg('Force Remove','<span style="color:var(--er)">Force remove</span> <strong>'+esc(a.Name)+'</strong> for all users?<br><br><span style="font-size:10px;color:var(--t3)">Removes app and provisioned package. Cannot be undone easily.</span>',a.PackageFullName))return;st('Force removing...');pStart();const r=await pywebview.api.force_remove_store_app(a.Name,a.PackageFullName);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lSr,1500)}

// ═════════════════════════════════════════════════════════════════════════
// SCHEDULED TASKS TAB
// ═════════════════════════════════════════════════════════════════════════
async function lTk(){pStart();st('Loading tasks...');D.tk=await pywebview.api.get_scheduled_tasks()||[];document.getElementById('btk').textContent=D.tk.length;fTk();st(D.tk.length+' tasks');pDone()}
function fTk(){
  const q=(document.getElementById('stk').value||'').toLowerCase();
  const items=D.tk.filter(t=>!q||(t.TaskName||'').toLowerCase().includes(q)||(t.Description||'').toLowerCase().includes(q));
  const el=document.getElementById('ltk');
  if(!items.length){el.innerHTML=ey('No scheduled tasks found');return}
  el.innerHTML=items.map((t,i)=>{
    const dis=t.State===0||t.State==='Disabled';
    const rdy=t.State===3||t.State==='Ready'||t.State===4||t.State==='Running';
    return`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms">${ib(I.cal,dis?'er':rdy?'ok':'wa')}<div class="ci"><div class="nm">${esc(t.TaskName)}</div><div class="mt">${esc(t.TaskPath||'')} \u00b7 <span class="ch ${dis?'dis':rdy?'rdy':'en'}">${dis?'Disabled':rdy?'Ready':'Active'}</span></div></div><div class="ca">${dis?`<button class="bt bs" onclick="enTk(${i})">${I.ok} Enable</button>`:`<button class="bt bw" onclick="disTk(${i})">${I.off} Disable</button>`}<button class="bt bd" onclick="delTk(${i})">${I.del}</button></div></div>`}).join('');
}
async function disTk(i){const t=D.tk.filter(t=>!((document.getElementById('stk').value||'').toLowerCase())||(t.TaskName||'').toLowerCase().includes((document.getElementById('stk').value||'').toLowerCase()))[i];if(!t)return;if(set.confirm_actions&&!await dlg('Disable Task','Disable <strong>'+esc(t.TaskName)+'</strong>?'))return;pStart();const r=await pywebview.api.disable_task(t.TaskName);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lTk,500)}
async function enTk(i){const t=D.tk.filter(t=>!((document.getElementById('stk').value||'').toLowerCase())||(t.TaskName||'').toLowerCase().includes((document.getElementById('stk').value||'').toLowerCase()))[i];if(!t)return;pStart();const r=await pywebview.api.enable_task(t.TaskName);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lTk,500)}
async function delTk(i){const t=D.tk.filter(t=>!((document.getElementById('stk').value||'').toLowerCase())||(t.TaskName||'').toLowerCase().includes((document.getElementById('stk').value||'').toLowerCase()))[i];if(!t)return;if(!await dlg('Delete Task','<span style="color:var(--er)">Permanently delete</span> task <strong>'+esc(t.TaskName)+'</strong>?'))return;pStart();const r=await pywebview.api.delete_task(t.TaskName);tst(r.ok?'ok':'er',r.msg);pDone();if(r.ok)setTimeout(lTk,500)}

// ═════════════════════════════════════════════════════════════════════════
// ACTION LOG TAB
// ═════════════════════════════════════════════════════════════════════════
async function lLg(){const e=await pywebview.api.get_action_log()||[];const el=document.getElementById('llg');if(!e.length){el.innerHTML=ey('No actions logged','Actions you perform will appear here');return}el.innerHTML=e.slice().reverse().map((x,i)=>`<div class="cd" style="animation-delay:${Math.min(i*15,150)}ms;cursor:pointer" onclick="lgD(${e.length-1-i})">${ib(x.restored?I.un:I.del,x.restored?'pr':'er')}<div class="ci"><div class="nm">${esc(x.name)} ${x.restored?'<span class="ch rst">Restored</span>':''}</div><div class="mt">${esc(x.category)} \u00b7 ${esc(x.action)} \u00b7 ${new Date(x.timestamp).toLocaleString()}</div></div><div class="ca">${!x.restored&&x.restore_cmd?`<button class="bt bs" onclick="event.stopPropagation();rsA(${e.length-1-i})">${I.un} Restore</button>`:''}</div></div>`).join('');document.getElementById('lgd').style.display='none'}
async function lgD(i){const e=(await pywebview.api.get_action_log())[i];const d=document.getElementById('lgd');d.style.display='block';d.textContent='Restore: '+(e.restore_cmd||'N/A')+'\n\nDetails: '+(e.details||'N/A')}
async function rsA(i){if(set.confirm_actions){const e=(await pywebview.api.get_action_log())[i];if(!await dlg('Restore','Restore <strong>'+esc(e.name)+'</strong>?',e.restore_cmd))return}pStart();const r=await pywebview.api.restore_action(i);tst(r.ok?'ok':'er',r.msg);pDone();lLg()}
async function expLog(){document.getElementById('sdd').classList.remove('open');tst('ok','Exported: '+await pywebview.api.export_log())}
async function clrLog(){document.getElementById('sdd').classList.remove('open');if(!await dlg('Clear Log','Clear the entire action log? This cannot be undone.'))return;await pywebview.api.clear_log();tst('ok','Log cleared');lLg()}

// ═════════════════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS
// ═════════════════════════════════════════════════════════════════════════
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function ea(s){return(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'")}
function fSz(k){return k>1048576?(k/1048576).toFixed(1)+' GB':k>1024?(k/1024).toFixed(1)+' MB':k+' KB'}
function ey(t,sub){return`<div class="ey"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg><p>${t}</p>${sub?`<div class="sub">${sub}</div>`:''}</div>`}
function st(t){document.getElementById('stx').textContent=t;document.getElementById('stt').textContent=new Date().toLocaleTimeString()}
function tst(t,m){
  const c=document.getElementById('tbox'),e=document.createElement('div');
  e.className='tst';e.style.color=t==='er'?'var(--er)':'var(--ok)';
  e.innerHTML=`<div class="tc">${t==='ok'?I.ok:I.x} ${esc(m)}</div><div class="tx" onclick="this.parentElement.remove()">${I.close}</div>`;
  c.appendChild(e);setTimeout(()=>{e.style.opacity='0';e.style.transform='translateX(24px)';setTimeout(()=>e.remove(),200)},5000);
}
function dlg(title,msg,code){return new Promise(r=>{dR=r;document.getElementById('dT').textContent=title;document.getElementById('dM').innerHTML=msg;const c=document.getElementById('dC');if(code){c.textContent=code;c.style.display='block'}else c.style.display='none';document.getElementById('dbg').classList.add('op')})}
function cD(r){document.getElementById('dbg').classList.remove('op');if(dR){dR(r);dR=null}}
</script></body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
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

    api = Api()
    _window = webview.create_window(
        "Master Uninstaller",
        html=HTML,
        js_api=api,
        width=1140,
        height=750,
        min_size=(800, 500),
        background_color="#000000",
        text_select=False,
        frameless=True,
    )
    webview.start(debug=False)
