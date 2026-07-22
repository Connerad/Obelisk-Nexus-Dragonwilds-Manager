from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import os
from pathlib import Path

from .paths import data_root


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(value: str) -> str:
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain-local:" + base64.urlsafe_b64encode(raw).decode("ascii")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buffer = _blob(raw)
    out_blob = DATA_BLOB()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "DWSM", None, None, None, flags, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return "dpapi:" + base64.urlsafe_b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain-local:"):
        return base64.urlsafe_b64decode(value.split(":", 1)[1]).decode("utf-8")
    if not value.startswith("dpapi:"):
        return ""
    if os.name != "nt":
        return ""
    encrypted = base64.urlsafe_b64decode(value.split(":", 1)[1])
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buffer = _blob(encrypted)
    out_blob = DATA_BLOB()
    flags = 0x1
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, flags, ctypes.byref(out_blob)):
        return ""
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def save_secret(name: str, value: str) -> None:
    path = data_root() / "secrets" / f"{name}.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(protect(value), encoding="ascii")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_secret(name: str) -> str:
    path = data_root() / "secrets" / f"{name}.secret"
    if not path.exists():
        return ""
    try:
        return unprotect(path.read_text(encoding="ascii").strip())
    except Exception:
        return ""
