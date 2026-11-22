"""Small Windows helpers (ctypes, no extra dependencies). All functions degrade gracefully."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

DriveKind = Literal["fixed", "external", "network", "unknown"]

WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
_WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# winuser.h
MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_TASKMODAL = 0x2000
MB_TOPMOST = 0x40000
IDYES = 6

# Drive and bus types (winbase.h, ntddstor.h)
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
EXTERNAL_BUS_TYPES = {4, 7, 12, 13}  # 1394, USB, SD, MMC
BUS_TYPE_OFFSET = 28  # STORAGE_DEVICE_DESCRIPTOR.BusType


def message_box(text: str, title: str = "FotoDünner", flags: int = MB_OK) -> int:
    """Native message box; works before and without the WebView window."""
    if os.name != "nt":
        log.warning("%s: %s", title, text)
        return 0
    return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TASKMODAL))


def ask_yes_no(text: str, title: str = "FotoDünner") -> bool:
    return message_box(text, title, MB_YESNO | MB_ICONWARNING | MB_TOPMOST) == IDYES


def webview2_installed() -> bool:
    """Evergreen WebView2 runtime registered per machine or per user (A3)."""
    if os.name != "nt":
        return True
    import winreg

    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{_WEBVIEW2_CLIENT}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{_WEBVIEW2_CLIENT}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\{_WEBVIEW2_CLIENT}"),
    ]
    for hive, key in candidates:
        try:
            with winreg.OpenKey(hive, key) as handle:
                version = str(winreg.QueryValueEx(handle, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return True
    return False


def drive_kind(path: Path) -> DriveKind:
    """Classify the drive holding ``path``: USB disks report DRIVE_FIXED, so ask the bus too."""
    if os.name != "nt":
        return "unknown"
    anchor = Path(os.path.abspath(path)).anchor  # noqa: PTH100 – lexical
    if anchor.startswith("\\\\"):
        return "network"
    kernel32 = ctypes.windll.kernel32
    kind = kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor))
    if kind == DRIVE_REMOTE:
        return "network"
    if kind == DRIVE_REMOVABLE:
        return "external"
    if kind != DRIVE_FIXED:
        return "unknown"
    return "external" if _bus_type(anchor[:2]) in EXTERNAL_BUS_TYPES else "fixed"


def _bus_type(drive: str) -> int | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        f"\\\\.\\{drive}",
        0,
        0x3,
        None,
        3,
        0,
        None,  # no access rights needed; OPEN_EXISTING
    )
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        query = (ctypes.c_ubyte * 12)()  # PropertyId=StorageDeviceProperty, PropertyStandardQuery
        out = (ctypes.c_ubyte * 1024)()
        returned = wintypes.DWORD()
        ok = kernel32.DeviceIoControl(
            wintypes.HANDLE(handle),
            IOCTL_STORAGE_QUERY_PROPERTY,
            query,
            ctypes.sizeof(query),
            out,
            ctypes.sizeof(out),
            ctypes.byref(returned),
            None,
        )
        if not ok or returned.value < BUS_TYPE_OFFSET + 4:
            return None
        return int.from_bytes(bytes(out[BUS_TYPE_OFFSET : BUS_TYPE_OFFSET + 4]), "little")
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
