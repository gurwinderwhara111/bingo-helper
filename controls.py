"""Always-on-top control bar that never takes focus from the game.

Buttons: Scan (re-detect), Pause/Resume (helper only, not the game), Quit.
Clicking the bar must NOT activate it, otherwise the game pauses.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# window styles / messages
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
WDA_EXCLUDEFROMCAPTURE = 0x00000011

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_NCHITTEST = 0x0084
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSELEAVE = 0x02A3
WM_QUIT = 0x0012

MA_NOACTIVATE = 3
HTCLIENT = 1
IDC_ARROW = 32512
IDC_HAND = 32649
TME_LEAVE = 0x00000002

DT_LEFT = 0x0000
DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_END_ELLIPSIS = 0x8000
DT_NOPREFIX = 0x0800

SRCCOPY = 0x00CC0020
NULL_BRUSH = 5
NULL_PEN = 8
TRANSPARENT = 1

W, H = 330, 58

HDC = wintypes.HDC
HWND = wintypes.HWND
HANDLE = wintypes.HANDLE
DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
UINT = wintypes.UINT
LONG = wintypes.LONG
COLORREF = wintypes.DWORD
HGDIOBJ = wintypes.HANDLE
HFONT = wintypes.HANDLE
HBRUSH = wintypes.HANDLE
HPEN = wintypes.HANDLE
HBITMAP = wintypes.HANDLE
HRGN = wintypes.HANDLE


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", HDC),
        ("fErase", BOOL),
        ("rcPaint", RECT),
        ("fRestore", BOOL),
        ("fIncUpdate", BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("dwFlags", DWORD),
        ("hwndTrack", HWND),
        ("dwHoverTime", DWORD),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", DWORD),
        ("pt", POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", HANDLE),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)


def rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.WORD
user32.CreateWindowExW.argtypes = [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = HWND
user32.DefWindowProcW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.BeginPaint.argtypes = [HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = HDC
user32.EndPaint.argtypes = [HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = BOOL
user32.FillRect.argtypes = [HDC, ctypes.POINTER(RECT), HBRUSH]
user32.FillRect.restype = ctypes.c_int
user32.InvalidateRect.argtypes = [HWND, ctypes.POINTER(RECT), BOOL]
user32.InvalidateRect.restype = BOOL
user32.UpdateWindow.argtypes = [HWND]
user32.UpdateWindow.restype = BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetCapture.argtypes = [HWND]
user32.SetCapture.restype = HWND
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = BOOL
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, UINT]
user32.SetWindowPos.restype = BOOL
user32.SetWindowRgn.argtypes = [HWND, HRGN, BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowDisplayAffinity.argtypes = [HWND, DWORD]
user32.SetWindowDisplayAffinity.restype = BOOL
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = HANDLE
user32.SetCursor.argtypes = [HANDLE]
user32.SetCursor.restype = HANDLE
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
user32.TrackMouseEvent.restype = BOOL
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.ShowWindow.restype = BOOL
user32.DestroyWindow.argtypes = [HWND]
user32.DestroyWindow.restype = BOOL
user32.PostMessageW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT, UINT]
user32.PeekMessageW.restype = BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT

gdi32.CreateSolidBrush.argtypes = [COLORREF]
gdi32.CreateSolidBrush.restype = HBRUSH
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, COLORREF]
gdi32.CreatePen.restype = HPEN
gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
gdi32.SelectObject.restype = HGDIOBJ
gdi32.DeleteObject.argtypes = [HGDIOBJ]
gdi32.DeleteObject.restype = BOOL
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = HGDIOBJ
gdi32.RoundRect.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.RoundRect.restype = BOOL
gdi32.Ellipse.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.restype = BOOL
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = HBITMAP
gdi32.BitBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         HDC, ctypes.c_int, ctypes.c_int, DWORD]
gdi32.BitBlt.restype = BOOL
gdi32.DeleteDC.argtypes = [HDC]
gdi32.DeleteDC.restype = BOOL
gdi32.SetBkMode.argtypes = [HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.argtypes = [HDC, COLORREF]
gdi32.SetTextColor.restype = COLORREF
user32.DrawTextW.argtypes = [HDC, wintypes.LPCWSTR, ctypes.c_int,
                            ctypes.POINTER(RECT), UINT]
user32.DrawTextW.restype = ctypes.c_int
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, DWORD, DWORD, DWORD, DWORD, DWORD,
                              DWORD, DWORD, DWORD, wintypes.LPCWSTR]
gdi32.CreateFontW.restype = HFONT
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = HRGN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POS_FILE = os.path.join(BASE_DIR, "bar_pos.txt")


class ControlBar:
    def __init__(self, on_scan, on_pause, on_quit) -> None:
        self._on_scan = on_scan
        self._on_pause = on_pause
        self._on_quit = on_quit
        self._hwnd = None
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._hinst = kernel32.GetModuleHandleW(None)
        self._line1 = "STARTING"
        self._line2 = ""
        self._color = "amber"
        self._paused = False
        self._hover = None
        self._pressed = None
        self._dragging = False
        self._tracking = False
        self._drag_off = (0, 0)
        self._x = 0
        self._y = 0
        self._font_bold = self._make_font(-15, 650)
        self._font_small = self._make_font(-12, 400)
        self._arrow = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        self._hand = user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND))
        self._register()
        self._create()

    # ---------------------------------------------------------------- setup

    def _make_font(self, height: int, weight: int) -> HFONT:
        return gdi32.CreateFontW(
            height, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")

    def _register(self) -> None:
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = self._hinst
        wc.hCursor = self._arrow
        wc.lpszClassName = "BingoHelperControls"
        if not user32.RegisterClassW(ctypes.byref(wc)):
            if ctypes.get_last_error() != 1410:  # class already exists
                raise ctypes.WinError(ctypes.get_last_error())

    def _create(self) -> None:
        ex = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(
            ex, "BingoHelperControls", "Bingo Helper", WS_POPUP,
            0, 0, W, H, None, None, self._hinst, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)

        rgn = gdi32.CreateRoundRectRgn(0, 0, W + 1, H + 1, 14, 14)
        if rgn:
            if not user32.SetWindowRgn(self._hwnd, rgn, True):
                gdi32.DeleteObject(rgn)

        x, y = self._load_pos()
        self._place(x, y, save=False)
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(self._hwnd)

    def _load_pos(self):
        try:
            with open(POS_FILE, "r", encoding="utf-8") as f:
                x, y = (int(v) for v in f.read().strip().split(","))
        except Exception:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            x = sw - W - 24
            y = sh - H - 90
        return x, y

    def _save_pos(self) -> None:
        try:
            with open(POS_FILE, "w", encoding="utf-8") as f:
                f.write(f"{self._x},{self._y}")
        except Exception:
            pass

    def _place(self, x: int, y: int, save: bool = True) -> None:
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        x = max(0, min(sw - W, int(x)))
        y = max(0, min(sh - H, int(y)))
        self._x, self._y = x, y
        user32.SetWindowPos(self._hwnd, None, x, y, W, H,
                            SWP_NOZORDER | SWP_NOACTIVATE)
        if save:
            self._save_pos()

    # ---------------------------------------------------------------- public

    def set_status(self, line1: str, line2: str = "", color: str = "green") -> None:
        if (line1, line2, color) == (self._line1, self._line2, self._color):
            return
        self._line1 = line1
        self._line2 = line2
        self._color = color
        self._invalidate()

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._invalidate()

    def raise_(self) -> None:
        if self._hwnd:
            user32.SetWindowPos(self._hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def destroy(self) -> None:
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        for f in (self._font_bold, self._font_small):
            if f:
                gdi32.DeleteObject(f)
        self._font_bold = self._font_small = None

    # ---------------------------------------------------------------- events

    def _invalidate(self) -> None:
        if self._hwnd:
            user32.InvalidateRect(self._hwnd, None, False)

    @staticmethod
    def _xy(lparam: int):
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        return x, y

    def _buttons(self):
        return {
            "scan": (170, 15, 52, 28),
            "pause": (228, 15, 64, 28),
            "quit": (298, 15, 24, 28),
        }

    def _hit(self, x: int, y: int):
        for name, (bx, by, bw, bh) in self._buttons().items():
            if bx <= x < bx + bw and by <= y < by + bh:
                return name
        return None

    def _fire(self, action: str) -> None:
        try:
            if action == "scan":
                self._on_scan()
            elif action == "pause":
                self._on_pause()
            elif action == "quit":
                self._on_quit()
        except Exception as exc:  # never let a button kill the loop
            print(f"control error: {exc}")

    def _on_down(self, lparam: int) -> None:
        x, y = self._xy(lparam)
        hit = self._hit(x, y)
        if hit:
            self._pressed = hit
            self._invalidate()
            return

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._drag_off = (pt.x - self._x, pt.y - self._y)
        self._dragging = True
        user32.SetCapture(self._hwnd)

    def _on_up(self, lparam: int) -> None:
        if self._dragging:
            self._dragging = False
            user32.ReleaseCapture()
            self._save_pos()
            return

        if self._pressed:
            x, y = self._xy(lparam)
            if self._hit(x, y) == self._pressed:
                action = self._pressed
                self._pressed = None
                self._invalidate()
                self._fire(action)
                return
            self._pressed = None
            self._invalidate()

    def _on_move(self, lparam: int) -> None:
        if self._dragging:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self._place(pt.x - self._drag_off[0], pt.y - self._drag_off[1],
                        save=False)
            return

        if not self._tracking:
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE,
                                  self._hwnd, 0)
            user32.TrackMouseEvent(ctypes.byref(tme))
            self._tracking = True

        x, y = self._xy(lparam)
        hover = self._hit(x, y)
        if hover != self._hover:
            self._hover = hover
            self._invalidate()

    # ---------------------------------------------------------------- paint

    def _dot(self) -> int:
        return {
            "green": rgb(70, 220, 120),
            "amber": rgb(255, 190, 60),
            "red": rgb(255, 90, 90),
            "blue": rgb(90, 170, 255),
        }.get(self._color, rgb(70, 220, 120))

    def _paint(self, hwnd) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, W, H)
        old_bmp = gdi32.SelectObject(mem, bmp) if bmp else None
        target = mem if (mem and bmp) else hdc

        bg = gdi32.CreateSolidBrush(rgb(18, 18, 22))
        rect = RECT(0, 0, W, H)
        user32.FillRect(target, ctypes.byref(rect), bg)
        gdi32.DeleteObject(bg)

        pen = gdi32.CreatePen(0, 1, rgb(58, 58, 72))
        null_brush = gdi32.GetStockObject(NULL_BRUSH)
        old_pen = gdi32.SelectObject(target, pen)
        old_brush = gdi32.SelectObject(target, null_brush)
        gdi32.RoundRect(target, 0, 0, W, H, 14, 14)
        gdi32.SelectObject(target, old_pen)
        gdi32.SelectObject(target, old_brush)
        gdi32.DeleteObject(pen)

        # status dot
        dot = gdi32.CreateSolidBrush(self._dot())
        null_pen = gdi32.GetStockObject(NULL_PEN)
        old_pen = gdi32.SelectObject(target, null_pen)
        old_brush = gdi32.SelectObject(target, dot)
        gdi32.Ellipse(target, 10, 17, 19, 26)
        gdi32.SelectObject(target, old_pen)
        gdi32.SelectObject(target, old_brush)
        gdi32.DeleteObject(dot)

        gdi32.SetBkMode(target, TRANSPARENT)
        old_font = gdi32.SelectObject(target, self._font_bold)
        gdi32.SetTextColor(target, rgb(236, 236, 242))
        r1 = RECT(23, 5, 165, 27)
        user32.DrawTextW(target, self._line1, -1, ctypes.byref(r1),
                        DT_LEFT | DT_SINGLELINE | DT_VCENTER
                        | DT_END_ELLIPSIS | DT_NOPREFIX)

        gdi32.SelectObject(target, self._font_small)
        gdi32.SetTextColor(target, rgb(150, 150, 162))
        r2 = RECT(23, 28, 165, 50)
        user32.DrawTextW(target, self._line2, -1, ctypes.byref(r2),
                        DT_LEFT | DT_SINGLELINE | DT_VCENTER
                        | DT_END_ELLIPSIS | DT_NOPREFIX)

        labels = {
            "scan": "Scan",
            "pause": "Resume" if self._paused else "Pause",
            "quit": "X",
        }
        for name, (bx, by, bw, bh) in self._buttons().items():
            if self._pressed == name:
                fill = rgb(82, 82, 98)
            elif self._hover == name:
                fill = rgb(58, 58, 72)
            elif name == "pause" and self._paused:
                fill = rgb(30, 70, 46)
            else:
                fill = rgb(36, 36, 46)
            brush = gdi32.CreateSolidBrush(fill)
            br = RECT(bx, by, bx + bw, by + bh)
            user32.FillRect(target, ctypes.byref(br), brush)
            gdi32.DeleteObject(brush)

            gdi32.SetTextColor(target, rgb(236, 236, 242))
            tr = RECT(bx, by, bx + bw, by + bh)
            user32.DrawTextW(target, labels[name], -1, ctypes.byref(tr),
                            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)

        gdi32.SelectObject(target, old_font)

        if target is mem:
            gdi32.BitBlt(hdc, 0, 0, W, H, mem, 0, 0, SRCCOPY)
            if old_bmp:
                gdi32.SelectObject(mem, old_bmp)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)

        user32.EndPaint(hwnd, ctypes.byref(ps))

    # ---------------------------------------------------------------- wndproc

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_NCHITTEST:
            return HTCLIENT
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == WM_SETCURSOR:
            if (lparam & 0xFFFF) == HTCLIENT and self._hover:
                user32.SetCursor(self._hand)
                return 1
            user32.SetCursor(self._arrow)
            return 1
        if msg == WM_LBUTTONDOWN:
            self._on_down(lparam)
            return 0
        if msg == WM_LBUTTONUP:
            self._on_up(lparam)
            return 0
        if msg == WM_MOUSEMOVE:
            self._on_move(lparam)
            return 0
        if msg == WM_MOUSELEAVE:
            self._tracking = False
            self._hover = None
            self._invalidate()
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


if __name__ == "__main__":
    state = {"paused": False}
    bar = None

    def on_pause():
        state["paused"] = not state["paused"]
        bar.set_paused(state["paused"])
        bar.set_status("PAUSED" if state["paused"] else "SCANNING",
                       "click resume" if state["paused"] else "watching for calls",
                       "amber" if state["paused"] else "green")
        print("pause ->", state["paused"])

    def on_scan():
        print("scan")

    def on_quit():
        print("quit")

    bar = ControlBar(on_scan, on_pause, on_quit)
    bar.set_status("SCANNING", "click pause to test", "green")

    # visible to the test capture
    user32.SetWindowDisplayAffinity(bar._hwnd, 0)

    # simulate a click on the Pause button
    time.sleep(0.4)
    lp = (28 << 16) | (260 & 0xFFFF)
    user32.PostMessageW(bar._hwnd, WM_LBUTTONDOWN, 0, lp)
    user32.PostMessageW(bar._hwnd, WM_LBUTTONUP, 0, lp)

    t = time.time()
    msg = MSG()
    while time.time() - t < 0.8:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)

    import cv2
    import bettercam

    cam = bettercam.create(output_color="BGR")
    cam.start(target_fps=20, video_mode=True)
    time.sleep(0.4)
    frame = cam.get_latest_frame()
    cam.stop()
    cam.release()
    if frame is not None:
        cv2.imwrite("debug/controls_test.png", frame)
        print("saved debug/controls_test.png")

    time.sleep(1.2)
    bar.destroy()
    print("done")
