"""Topmost, click-through, per-pixel-alpha overlay window.

Pure Win32 via ctypes. No Qt, no Tk, no focus stealing.
The window covers exactly the grid rectangle: black with alpha everywhere,
fully transparent inside the target cell's rectangle.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wintypes.HDC,
                                       ctypes.POINTER(POINT), wintypes.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int


class Overlay:
    def __init__(self) -> None:
        self._hwnd = None
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._mem_dc = None
        self._screen_dc = None
        self._hbitmap = None
        self._ppv = ctypes.c_void_p()
        self._size = (0, 0)
        self._excluded = False
        self._hinst = kernel32.GetModuleHandleW(None)
        self._register()
        self._create()

    # ------------------------------------------------------------ window plumbing

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _register(self) -> None:
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = self._hinst
        wc.lpszClassName = "BingoHelperOverlay"
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(err)

    def _create(self) -> None:
        ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
              | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self._hwnd = user32.CreateWindowExW(
            ex, "BingoHelperOverlay", None, WS_POPUP,
            0, 0, 0, 0, None, None, self._hinst, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        if user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE):
            self._excluded = True

    @property
    def excluded(self) -> bool:
        return self._excluded

    # ------------------------------------------------------------ drawing

    def _destroy_dib(self) -> None:
        if self._hbitmap:
            gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = None
        if self._screen_dc:
            user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None
        self._ppv = ctypes.c_void_p()
        self._size = (0, 0)

    def _ensure_size(self, w: int, h: int) -> None:
        if self._size == (w, h):
            return
        self._destroy_dib()
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        self._ppv = ctypes.c_void_p()
        self._hbitmap = gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(bmi), 0, ctypes.byref(self._ppv), None, 0)
        if not self._hbitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        gdi32.SelectObject(self._mem_dc, self._hbitmap)
        self._size = (w, h)

    def show(self, grid_rect, hole_rect=None, dim_alpha: int = 220) -> None:
        gx, gy, gw, gh = (int(v) for v in grid_rect)
        if gw <= 0 or gh <= 0:
            return
        self._ensure_size(gw, gh)

        buf = np.zeros((gh, gw, 4), np.uint8)
        buf[:, :, 3] = dim_alpha  # black, semi-transparent

        if hole_rect is not None:
            hx, hy, hw, hh = (int(v) for v in hole_rect)
            lx = max(0, hx - gx)
            ly = max(0, hy - gy)
            rx = min(gw, lx + hw)
            ry = min(gh, ly + hh)
            if rx > lx and ry > ly:
                buf[ly:ry, lx:rx, :] = 0  # fully transparent hole

        ctypes.memmove(self._ppv, buf.ctypes.data, buf.nbytes)

        pt_dst = POINT(gx, gy)
        size = SIZE(gw, gh)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        if not user32.UpdateLayeredWindow(
                self._hwnd, self._screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
                self._mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA):
            raise ctypes.WinError(ctypes.get_last_error())

        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)

    def hide(self) -> None:
        if self._hwnd:
            user32.ShowWindow(self._hwnd, SW_HIDE)

    def destroy(self) -> None:
        self._destroy_dib()
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None


if __name__ == "__main__":
    import time

    import cv2

    ov = Overlay()
    ov.show((300, 200, 600, 400), (400, 300, 120, 90), dim_alpha=220)
    print("overlay shown for 4s")
    time.sleep(1.0)

    # temporarily allow capture so the test can prove the pixels
    user32.SetWindowDisplayAffinity(ov._hwnd, WDA_NONE)
    time.sleep(0.2)

    import bettercam
    cam = bettercam.create(output_color="BGR")
    cam.start(target_fps=30, video_mode=True)
    time.sleep(0.4)
    frame = cam.get_latest_frame()
    cam.stop()
    cam.release()
    if frame is not None:
        cv2.imwrite("debug/overlay_test.png", frame)
        print("saved debug/overlay_test.png", frame.shape)
    time.sleep(1.0)
    ov.hide()
    ov.destroy()
    print("done")
