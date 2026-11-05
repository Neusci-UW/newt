"""AmScope (and ToupTek) microscope cameras, through the vendor's own library.

Many AmScope cameras, the MU series among them, are made by ToupTek and use ToupTek's
driver rather than Windows' standard camera driver. Windows doesn't list them as
cameras, so Qt (and the Windows Camera app) can't see them. Their library can:
amcam.dll, which AmScope's software installs, or toupcam.dll from ToupTek's ToupView.
Both have the same functions, named Amcam_… or Toupcam_….

Where the library is looked for, in order:
  the ARGUS_CAMERA_SDK environment variable (the full path of amcam.dll or toupcam.dll)
  C:\\Program Files\\AmScope\\AmScope\\x64\\amcam.dll (or x86, for 32-bit Python)
  C:\\Program Files\\ToupTek\\ToupView\\x64\\toupcam.dll
  amcam / toupcam on the library path (libamcam.so, libtoupcam.dylib, …)
The library isn't part of Argus; it comes with the camera's software.

Frames arrive on the library's own thread. The callback only notes that one is ready;
Camera.latest() pulls it on the GUI thread, so Qt is never touched from that thread.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
import sys
import threading
from pathlib import Path

import numpy as np

WINDOWS = sys.platform == "win32"
EVENT_IMAGE = 0x0004
EVENT_ERROR = 0x0080
EVENT_DISCONNECTED = 0x0081
MAX_DEVICES = 128           # the library's limit has been 16, later 128: room for either
NAME_LEN = 64


def _candidates() -> list[tuple[Path | str, str]]:
    """[(library, function prefix)] to try, most specific first."""
    out: list[tuple[Path | str, str]] = []
    env = os.environ.get("ARGUS_CAMERA_SDK", "")
    if env:
        out.append((Path(env), "Toupcam_" if "toupcam" in Path(env).name.lower() else "Amcam_"))
    if WINDOWS:
        arch = "x64" if struct.calcsize("P") == 8 else "x86"
        for root in {os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")}:
            out.append((Path(root) / "AmScope" / "AmScope" / arch / "amcam.dll", "Amcam_"))
            out.append((Path(root) / "ToupTek" / "ToupView" / arch / "toupcam.dll", "Toupcam_"))
    for name, prefix in (("amcam", "Amcam_"), ("toupcam", "Toupcam_")):
        found = ctypes.util.find_library(name)
        if found:
            out.append((found, prefix))
    return out


class _Device(ctypes.Structure):
    """One entry of EnumV2: display name, id, and a pointer to the model."""
    _char = ctypes.c_wchar if WINDOWS else ctypes.c_char
    _fields_ = [("displayname", _char * NAME_LEN), ("id", _char * NAME_LEN), ("model", ctypes.c_void_p)]


_CALLBACK = (ctypes.WINFUNCTYPE if WINDOWS else ctypes.CFUNCTYPE)(None, ctypes.c_uint, ctypes.c_void_p)


class Sdk:
    """The library's functions this needs, with their C types."""

    def __init__(self, path: Path | str, prefix: str):
        self.path, self.prefix = Path(path), prefix
        if WINDOWS and self.path.parent.is_dir():
            os.add_dll_directory(str(self.path.parent))   # the DLLs it needs sit beside it
        loader = ctypes.WinDLL if WINDOWS else ctypes.CDLL
        self.lib = loader(str(path))
        H, P, U, I = ctypes.c_void_p, ctypes.POINTER, ctypes.c_uint, ctypes.c_int
        text = ctypes.c_wchar_p if WINDOWS else ctypes.c_char_p
        sig = {
            "EnumV2": (U, [P(_Device)]),
            "Open": (H, [text]),
            "Close": (None, [H]),
            "Stop": (ctypes.c_long, [H]),
            "StartPullModeWithCallback": (ctypes.c_long, [H, _CALLBACK, ctypes.c_void_p]),
            "PullImage": (ctypes.c_long, [H, ctypes.c_void_p, I, P(U), P(U)]),
            "get_ResolutionNumber": (ctypes.c_long, [H]),
            "get_Resolution": (ctypes.c_long, [H, U, P(I), P(I)]),
            "get_eSize": (ctypes.c_long, [H, P(U)]),
            "put_eSize": (ctypes.c_long, [H, U]),
            "put_AutoExpoEnable": (ctypes.c_long, [H, I]),
        }
        for name, (res, args) in sig.items():
            fn = getattr(self.lib, prefix + name)       # AttributeError: too old a library
            fn.restype, fn.argtypes = res, args
            setattr(self, name, fn)
        self._text = (lambda s: s) if WINDOWS else (lambda s: s.encode())

    def devices(self) -> list[tuple[str, str]]:
        """[(display name, id)] of the cameras connected now."""
        arr = (_Device * MAX_DEVICES)()
        n = min(int(self.EnumV2(arr)), MAX_DEVICES)
        dec = (lambda v: v) if WINDOWS else (lambda v: v.decode(errors="replace"))
        return [(dec(arr[i].displayname) or "AmScope camera", dec(arr[i].id)) for i in range(n)]


_sdk: Sdk | None = None
_why = ""
_tried = False


def sdk() -> Sdk | None:
    """The library, loaded once; None (and why_not() says why) if it isn't installed."""
    global _sdk, _why, _tried
    if _tried:
        return _sdk
    _tried = True
    reasons = []
    for path, prefix in _candidates():
        if isinstance(path, Path) and not path.exists():
            continue
        try:
            _sdk = Sdk(path, prefix)
            return _sdk
        except (OSError, AttributeError) as e:
            reasons.append(f"{path}: {e}")
    _why = ("AmScope's camera library (amcam.dll) wasn't found: install AmScope's software, or set "
            "ARGUS_CAMERA_SDK to the file's full path." if not reasons else
            "AmScope's camera library didn't load: " + "; ".join(reasons))
    return None


def why_not() -> str:
    sdk()
    return _why


def reset() -> None:
    """Forget the loaded library (tests use this to swap in a stand-in)."""
    global _sdk, _why, _tried
    _sdk, _why, _tried = None, "", False


def cameras() -> list[tuple[str, str]]:
    """[(name, id)] of the AmScope cameras connected now; [] without the library."""
    s = sdk()
    if s is None:
        return []
    try:
        return s.devices()
    except OSError:
        return []


class Camera:
    """One open AmScope camera. start(); then latest() on a timer gives the newest frame as
    an RGB array (height × width × 3, uint8) or None; stop() and close() when done."""

    def __init__(self, dev_id: str, s: Sdk | None = None):
        self.sdk = s or sdk()
        if self.sdk is None:
            raise OSError(why_not())
        self.h = self.sdk.Open(self.sdk._text(dev_id))
        if not self.h:
            raise OSError("The camera didn't open: is AmScope's own software using it?")
        self._ready = threading.Event()
        self.lost = False                     # unplugged, or the library reported an error
        self._cb = _CALLBACK(self._event)     # kept: the library calls it until stop()
        self._buf = None
        self.running = False
        self.sdk.put_AutoExpoEnable(self.h, 1)

    def _event(self, event, _ctx) -> None:   # on the library's thread: just take note
        if event == EVENT_IMAGE:
            self._ready.set()
        elif event in (EVENT_ERROR, EVENT_DISCONNECTED):
            self.lost = True

    def resolutions(self) -> list[tuple[int, int]]:
        out = []
        for i in range(max(0, int(self.sdk.get_ResolutionNumber(self.h)))):
            w, h = ctypes.c_int(), ctypes.c_int()
            if self.sdk.get_Resolution(self.h, i, ctypes.byref(w), ctypes.byref(h)) >= 0:
                out.append((w.value, h.value))
        return out

    def resolution(self) -> int:
        i = ctypes.c_uint()
        return i.value if self.sdk.get_eSize(self.h, ctypes.byref(i)) >= 0 else 0

    def set_resolution(self, index: int) -> None:
        """Pick a resolution (index into resolutions()); restarts the stream if running."""
        was = self.running
        if was:
            self.stop()
        if self.sdk.put_eSize(self.h, int(index)) < 0:
            raise OSError("The camera didn't take that resolution.")
        if was:
            self.start()

    def start(self) -> None:
        sizes = self.resolutions()
        w, h = sizes[self.resolution()] if sizes else (4096, 3072)
        stride = ((w * 24 + 31) // 32) * 4        # rows padded to 4 bytes
        self._buf = np.zeros(stride * h, np.uint8)
        self._ready.clear()
        if self.sdk.StartPullModeWithCallback(self.h, self._cb, None) < 0:
            raise OSError("The camera didn't start.")
        self.running = True

    def latest(self) -> np.ndarray | None:
        """The newest frame (RGB), or None if none came since the last call."""
        if not self.running or not self._ready.is_set():
            return None
        self._ready.clear()
        w, h = ctypes.c_uint(), ctypes.c_uint()
        if self.sdk.PullImage(self.h, self._buf.ctypes.data_as(ctypes.c_void_p), 24,
                              ctypes.byref(w), ctypes.byref(h)) < 0:
            return None
        w, h = w.value, h.value
        stride = ((w * 24 + 31) // 32) * 4
        if stride * h > self._buf.size:
            return None
        img = self._buf[:stride * h].reshape(h, stride)[:, :w * 3].reshape(h, w, 3)
        if WINDOWS:                               # Windows' default: BGR, bottom row first, and
            img = img[::-1, ::-1, ::-1]           # mirrored left to right (seen with an MU1000)
        return img.copy()                         # a copy: the next frame reuses the buffer

    def stop(self) -> None:
        if self.running:
            self.sdk.Stop(self.h)
            self.running = False

    def close(self) -> None:
        self.stop()
        if self.h:
            self.sdk.Close(self.h)
            self.h = None
