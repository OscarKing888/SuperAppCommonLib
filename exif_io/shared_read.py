"""Read handles that permit file moves and deletion on Windows."""
from __future__ import annotations

from functools import lru_cache
import os


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def open_shared_read(path):
    """Open a binary snapshot while permitting rename and unlink.

    Callers still validate the source fingerprint around a cached read. Keeping
    a reader attached to the original inode/handle during a move is safe.
    Windows replacement can still require the final old handle to close, so
    readers should release the stream before CPU work such as XML parsing.
    """
    if os.name != "nt":
        return open(path, "rb")
    import ctypes
    import msvcrt

    kernel = _windows_api()
    handle = kernel.CreateFileW(os.fsdecode(path), 0x80000000, 0x1 | 0x2 | 0x4,
                                None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = os.fspath(path)
        raise error
    try:
        # open_osfhandle transfers ownership to the CRT descriptor on success.
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_shared_bytes(path):
    with open_shared_read(path) as stream:
        return stream.read()
