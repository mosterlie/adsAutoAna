# -*- coding: utf-8 -*-
"""点击窗口内指定相对位置(默认飞鸟的「快速连接」大按钮), 点前后各截一张图便于核对

按钮位置以「窗口宽高的比例」给出, 这样窗口大小/位置变了也不会点偏。
飞鸟 V3.1.8 实测: 快速连接按钮中心 ≈ 窗口的 (55.3%, 44.5%)

用法:
    python explore/probe_click_window.py                  # 默认点飞鸟快速连接
    python explore/probe_click_window.py 0.5 0.7 记事本    # 自定义比例与关键字
"""
import ctypes
import sys
import time

import win32gui
import win32con
from PIL import ImageGrab

OUT_DIR = r"D:\myCoding\adsAutoAna\data\logs"


def dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:       # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:   # noqa: BLE001
            pass


def find_window(keyword: str) -> int:
    hits = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if keyword.lower() in win32gui.GetWindowText(hwnd).lower():
            hits.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not hits:
        raise SystemExit(f"没找到标题含 {keyword!r} 的可见窗口")
    return hits[0]


def rect(hwnd: int):
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return l, t, r, b


def grab(hwnd: int, name: str) -> str:
    l, t, r, b = rect(hwnd)
    img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    path = f"{OUT_DIR}\\{name}.png"
    img.save(path)
    print(f"截图: {path}  {img.size[0]}x{img.size[1]} @ ({l},{t})")
    return path


def main() -> None:
    rx = float(sys.argv[1]) if len(sys.argv) > 1 else 0.553
    ry = float(sys.argv[2]) if len(sys.argv) > 2 else 0.445
    kw = sys.argv[3] if len(sys.argv) > 3 else "FlyingBird"

    dpi_aware()
    hwnd = find_window(kw)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.6)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:       # noqa: BLE001
        pass
    time.sleep(0.9)

    grab(hwnd, "click_before")

    l, t, r, b = rect(hwnd)
    x = int(l + (r - l) * rx)
    y = int(t + (b - t) * ry)
    print(f"即将点击窗口 {kw!r}: 比例({rx},{ry}) → 屏幕坐标({x},{y})")

    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.35)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)     # LEFTDOWN
    time.sleep(0.09)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)     # LEFTUP
    print("已点击")
    time.sleep(2.5)

    grab(hwnd, "click_after")


if __name__ == "__main__":
    main()
