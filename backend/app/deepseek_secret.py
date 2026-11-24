"""Store the local DeepSeek key with Windows user-level DPAPI."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .core import Problem, uid


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(data, *, decrypt):
    if os.name != "nt":
        raise Problem(422, "WINDOWS_SECRET_STORE", "本机密钥保存需要 Windows，也可使用 DEEPSEEK_API_KEY 环境变量。")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "Windows credential store")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def location(settings):
    return settings.database.with_suffix(".deepseek-key.dpapi")


def load(settings):
    if value := os.getenv("DEEPSEEK_API_KEY", "").strip():
        return value
    path = location(settings)
    try:
        return transform(path.read_bytes(), decrypt=True).decode("utf-8")
    except (OSError, UnicodeError, Problem):
        return ""


def save(settings, value):
    target = location(settings)
    if value is None:
        target.unlink(missing_ok=True)
        return
    ciphertext = transform(value.encode("utf-8"), decrypt=False)
    pending = Path(str(target) + "." + uid() + ".tmp")
    try:
        pending.write_bytes(ciphertext)
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
