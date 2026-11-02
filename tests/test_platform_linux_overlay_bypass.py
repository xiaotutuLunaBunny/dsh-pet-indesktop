# -*- coding: utf-8 -*-
"""X11 合成 overlay 必须绕开窗口管理器（i3 等平铺 WM 接管回归）。

现象（2026-11-02 Ubuntu 26 + i3 双显实测）：overlay 单合成窗（Qt.Tool +
Frameless + StaysOnTop）被 i3 **接管并 reparent** 进 ``i3-frame`` 容器，尺寸
4480x1600 大于任何单块输出，i3 把它摆到绝对坐标 ``+1280,-266``；Qt 收到的
ConfigureNotify 是**父容器相对坐标** (0,0)，内部几何与真实全局原点从此错位
(1280,-266)——绘制、逐像素命中、气泡跟随全部按错位坐标走，表现为「鼠标点不中
宠物、扩展屏位置错乱」。mutter（Ubuntu 24 GNOME）认 Qt 的
``_KDE_NET_WM_WINDOW_TYPE_OVERRIDE`` 提示不接管该窗，所以同代码在 GNOME 正常。

落点：X11（linux 平台）给 overlay 主窗 flags 构造处加
``Qt.WindowType.BypassWindowManagerHint``——原生窗成为 override-redirect，
任何 WM 都无法 reparent/移动它，几何假设（窗口原点 = ``_desktop_bounds``
原点）恒成立。与 macOS 阴影标志同一注入纪律（见 test_platform_mac_shadow.py
的论证）：flags 构造处注入，on_top 开关重建原生窗口后仍生效。

已知取舍：override-redirect 窗不受 WM 层序管理，X11 上 ``on_top=False``
（置底）不再能把桌宠压到普通应用之下——与「桌宠完全不可用」相比接受此退化，
其余平台语义不变。legacy ``PetWindow``（直播捕获模式，OBS 需要可枚举的
WM 管理窗）**不加**本标志，本文件同时钉住该边界。

平台分派放在 ``pet/platform_linux.py::_x11_bypass_wm_flag``：非 linux 返回
空标志（``flags |= 0`` 天然 no-op），调用点无需自行判平台。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pet import platform_linux
from pet.config import Config
from pet.overlay_window import OverlayWindow
from pet.window import PetWindow, build_window_flags
from tests.test_window_pause import FakeLibrary

app = QApplication.instance() or QApplication([])

BYPASS = Qt.WindowType.BypassWindowManagerHint


# ---------------------------------------------------------------- 平台分派（纯函数）
@pytest.mark.parametrize("platform,expected", [
    ("linux", True),
    ("win32", False),
    ("darwin", False),
])
def test_bypass_wm_flag_dispatch(monkeypatch, platform, expected):
    """只有 linux(X11) 拿到 bypass 标志；其它平台返回空标志（可直接 ``|=``）。"""
    monkeypatch.setattr(sys, "platform", platform)
    flags = platform_linux._x11_bypass_wm_flag()
    if expected:
        assert flags & BYPASS
    else:
        assert flags == Qt.WindowType(0)


# ---------------------------------------------------------------- overlay 主窗
def test_overlay_window_has_bypass_flag_on_linux(monkeypatch):
    """X11：overlay 主窗 flags 必须带 BypassWindowManagerHint（构造处注入）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    overlay = OverlayWindow()
    try:
        assert overlay.windowFlags() & BYPASS
    finally:
        overlay.close()


def test_overlay_window_no_bypass_flag_off_linux(monkeypatch):
    """非 linux：不加标志，不给 Windows/macOS 引入任何窗口管理行为变化。"""
    for platform in ("win32", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        overlay = OverlayWindow()
        try:
            assert not overlay.windowFlags() & BYPASS
        finally:
            overlay.close()


def test_overlay_bypass_flag_survives_on_top_toggle(monkeypatch):
    """on_top 开关（``setWindowFlag`` 位改版重建原生窗口）后 bypass 仍在。

    壳层 ``_apply_window_capabilities`` 只按位改 WindowStaysOnTopHint，
    构造期注入的标志必须随 flags 存活——同 macOS 阴影标志的重建语义。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    overlay = OverlayWindow()
    try:
        overlay.show()
        app.processEvents()
        assert overlay.windowFlags() & BYPASS

        overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        overlay.show()
        app.processEvents()
        assert overlay.windowFlags() & BYPASS, "关置顶重建后必须仍绕开 WM"
        assert overlay.windowHandle().flags() & BYPASS

        overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        overlay.show()
        app.processEvents()
        assert overlay.windowHandle().flags() & BYPASS
    finally:
        overlay.close()


# ---------------------------------------------------------------- legacy 窗边界
def test_legacy_petwindow_stays_wm_managed_on_linux(monkeypatch, tmp_path):
    """legacy PetWindow（含直播捕获模式）**不得**带 bypass：OBS 需要能枚举
    一个 WM 管理的真窗口，override-redirect 会让窗口捕获整条路失效。"""
    monkeypatch.setattr(sys, "platform", "linux")
    config = {"on_top": True, "mouse_through": False}
    for capture_mode in (False, True):
        assert not build_window_flags(config, False, capture_mode) & BYPASS
    win = PetWindow(FakeLibrary(), Config(base=tmp_path))
    try:
        assert not win.windowFlags() & BYPASS
        win.set_stream_capture_mode(True)
        assert not win.windowFlags() & BYPASS
    finally:
        win.close()
        app.processEvents()
