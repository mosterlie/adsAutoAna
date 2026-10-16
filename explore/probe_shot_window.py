# -*- coding: utf-8 -*-
"""截取指定窗口(默认飞鸟代理客户端) —— 排查代理界面上到底显示什么

用法:
    python explore/probe_shot_window.py                 # 关键字 FlyingBird
    python explore/probe_shot_window.py 记事本 out.png   # 自定义关键字与输出路径
"""
import ctypes
import sys
import time

import win32gui
import win32con
from PIL import ImageGrab


def dpi_aware() -> None:
    """让本进程 DPI 感知, 否则 win32gui 的坐标与截图像素对不上(缩放屏会偏)"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:       # noqa: BLE001  老系统 / 已设置过
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:   # noqa: BLE001
            pass


def find_window(keyword: str) -> int:
    hits = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if keyword.lower() in title.lower():
            hits.append((hwnd, title))

    win32gui.EnumWindows(cb, None)
    if not hits:
        raise SystemExit(f"没找到标题含 {keyword!r} 的可见窗口")
    for hwnd, title in hits:
        print(f"命中窗口: hwnd={hwnd} title={title!r}")
    return hits[0][0]


def shot(hwnd: int, out: str) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.6)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:       # noqa: BLE001  前台切换偶发失败, 不影响截图
        pass
    time.sleep(0.9)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    print(f"窗口矩形: ({l},{t})-({r},{b})  {w}x{h}")
    img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    img.save(out)
    print(f"已保存: {out}  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "FlyingBird"
    dst = sys.argv[2] if len(sys.argv) > 2 else r"D:\myCoding\adsAutoAna\data\logs\window_shot.png"
    dpi_aware()
    shot(find_window(kw), dst)
