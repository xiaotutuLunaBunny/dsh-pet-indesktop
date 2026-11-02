# -*- coding: utf-8 -*-
"""Linux/X11 平台窗口行为 helper（与 platform_mac 同级的分派收口）。

X11 上的桌面级透明窗不能交给窗口管理器接管：mutter 认 Qt 的
``_KDE_NET_WM_WINDOW_TYPE_OVERRIDE`` 提示会跳过管理，但 i3 等平铺 WM 不认，
会把窗 reparent 进装饰容器并重新摆放——overlay 单合成窗（尺寸 = 各屏并集，
大于任何单块输出）会被摆到错误原点，且 Qt 收到的 ConfigureNotify 是父相对
坐标，内部几何与真实全局原点从此错位，绘制/逐像素命中/气泡跟随全部偏移
（2026-11-02 Ubuntu 26 + i3 双显实测，见
tests/test_platform_linux_overlay_bypass.py 的论证）。

对策是 flags 构造处给 ``BypassWindowManagerHint``（override-redirect，WM
物理上无法接管），而不是事后与 WM 抢位置。已知取舍：override-redirect 不受
WM 层序管理，X11 上 ``on_top=False`` 不再能把桌宠压到普通应用之下。

输入法插件可用性探测（2026-11-02 Ubuntu 26 + i3 实测根因）：开发态 venv 的
PySide6 自带 Qt（本机 6.11.3）与系统 apt 的 fcitx5 Qt6 插件（按系统 Qt 私有
ABI 编译，本机 6.10.2）不兼容，dlopen 报 ``undefined symbol:
_ZN14QObjectPrivateC2E16QtPrivate_6_10_2``；Qt 对加载失败的输入法插件只往
stderr 吐一行就静默回退 compose 上下文（不支持中文），于是表现为**所有输入框
打不进中文**且用户端零可见诊断。本模块用 ``QLibrary`` 对候选插件做**实测加载**
（不预测、不比对版本声明），供启动路径把结论写进日志。实测前先把 PySide6
自己的 Qt 运行库按 soname 预热（:func:`prime_qt_runtime_libraries`）：不预热的话
插件的 ``libQt6DBus.so.6`` 会被解析到系统 apt 的同名库，报出系统库的符号错误，
结论与 Qt 真正加载插件时不一致。

为什么不做版本比对判据：成品随包的 fcitx 插件由
``scripts/build_pyside6_fcitx_plugin.sh`` 构建，它是"按系统 Qt 头编译、链接
PySide6 运行库"，插件自带的版本声明天然不等于运行时版本——按声明判会把正常工作
的成品误判为坏。实测加载没有这个失真：加载成功 = Qt 起得来，加载失败 = Qt 起不来。
修复入口：``./petctl.sh setup-im``（开发态）与随包插件（成品），详见
docs/UBUNTU-I3-ENV-SETUP-TROUBLESHOOTING.md §2.6。
"""

from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QLibrary, Qt


def _x11_bypass_wm_flag() -> Qt.WindowType:
    """X11（linux）返回 ``BypassWindowManagerHint``，其余平台返回空标志。

    空标志可直接 ``flags |= 0``，调用点无需自行判平台（同
    ``platform_mac._mac_no_window_shadow_flag`` 惯例）。Wayland 原生平台会
    忽略该标志（无 override-redirect 概念），无需额外分派；本应用的
    Wayland 会话在 ``app.py`` 已强制走 xcb/XWayland。
    非 linux 返回 ``Qt.WindowType(0)``（同 ``_mac_no_window_shadow_flag``
    惯例；PySide6 的 WindowType 枚举没有 NoFlag 成员）。
    """
    if sys.platform == "linux":
        return Qt.WindowType.BypassWindowManagerHint
    return Qt.WindowType(0)


# ---------------------------------------------------------------- 输入法插件
# 诊断专用：任何 IO/加载失败都降级为"这个候选不行"，绝不对调用方抛异常，
# 更不许改变 Qt 自己的插件选择结果。

def _bundled_plugin_root() -> Path | None:
    """PySide6 wheel / 冻结产物自带的 Qt 插件根目录。

    冻结（PyInstaller）= ``sys._MEIPASS/PySide6/Qt/plugins``；开发态 =
    site-packages 里 PySide6 包内的 ``Qt/plugins``。判不出返回 None。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "PySide6" / "Qt" / "plugins"
        if candidate.is_dir():
            return candidate
    try:
        import PySide6
        candidate = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
        if candidate.is_dir():
            return candidate
    except (ImportError, OSError):
        pass
    return None


def _system_plugin_roots() -> list[Path]:
    """发行版系统 Qt6 插件根（dpkg 多架构目录优先，回退 /usr/lib/qt6）。"""
    out: list[Path] = []
    for pattern in ("/usr/lib/*/qt6/plugins", "/usr/lib/qt6/plugins", "/usr/lib64/qt6/plugins"):
        for d in sorted(glob.glob(pattern)):
            p = Path(d)
            if p.is_dir() and p not in out:
                out.append(p)
    return out


def _bundled_qt_lib_dir() -> Path | None:
    """随进程运行的 PySide6 自带 Qt 库目录（与 :func:`_bundled_plugin_root` 同源）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "PySide6" / "Qt" / "lib"
        if candidate.is_dir():
            return candidate
    try:
        import PySide6
        candidate = Path(PySide6.__file__).resolve().parent / "Qt" / "lib"
        if candidate.is_dir():
            return candidate
    except (ImportError, OSError):
        pass
    return None


# 输入法插件的 DT_NEEDED 里会出现的 Qt 运行库（本机对成品插件 ``readelf -d`` 实测）。
_QT_RUNTIME_LIBS = ("libQt6Core.so.6", "libQt6Gui.so.6", "libQt6Widgets.so.6", "libQt6DBus.so.6")


def prime_qt_runtime_libraries() -> list[str]:
    """把输入法插件依赖的 Qt 运行库按 soname 提前映射进来，返回成功的文件名。

    为什么必需：ld.so 按 soname 复用**已映射**的库。成品进程里
    ``import PySide6.QtWidgets`` 已带入 PySide6 自己的 Core/Gui/Widgets，xcb 平台插件
    又带入 QtDBus；但启动诊断跑在 QApplication 之前，QtDBus 往往还没映射——此时
    实测插件会把 DT_NEEDED 的 ``libQt6DBus.so.6`` 解析到**系统 apt 的那一份**，
    报出来的是系统库自己的 undefined symbol（本机 2026-11-02 实测），把“其实能用”
    的插件误判为坏。预热 = 让探测复现 Qt 自己加载插件时的映射状态。
    单个库加载失败不抛：缺了就缺了，后面的实测会说它自己的答案。
    """
    lib_dir = _bundled_qt_lib_dir()
    if lib_dir is None:
        return []
    loaded: list[str] = []
    for name in _QT_RUNTIME_LIBS:
        path = lib_dir / name
        if not path.is_file():
            continue
        try:
            if QLibrary(str(path)).load():
                loaded.append(name)
        except Exception:  # noqa: BLE001 - 预热不是结论，交给实测
            pass
    return loaded


def input_method_plugin_search_dirs() -> list[Path]:
    """Qt 会扫描 ``platforminputcontexts`` 的候选目录（顺序即优先级，去重）：
    随包目录 → 冻结根 → ``QT_PLUGIN_PATH`` → 系统 Qt6 多架构目录。

    不预先检查存在性：缺目录由 :func:`find_input_method_plugins` 的 iterdir
    容错吞掉；这里过早过滤会让开发态（目录尚未装插件）连"找过哪里"都答不出。
    """
    roots: list[Path] = []
    bundled = _bundled_plugin_root()
    if bundled is not None:
        roots.append(bundled)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    for item in os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()))
    roots.extend(_system_plugin_roots())
    out: list[Path] = []
    for root in roots:
        candidate = root / "platforminputcontexts"
        if candidate not in out:
            out.append(candidate)
    return out


def _plugin_key_pattern(module_key: str) -> re.Pattern:
    """插件文件名的模块键匹配（从词首起算，允许 ``lib`` 前缀与版本号后缀）：
    ``fcitx`` 要命中 ``libfcitx5platforminputcontextplugin.so``，
    但不能命中 ``libcompose…so``/``libibus…so``。"""
    return re.compile(rf"^(?:lib)?{re.escape(module_key)}\d*", re.IGNORECASE)


def find_input_method_plugins(module_key: str, dirs=None) -> list[Path]:
    """按命名惯例列出候选插件文件（``dirs=None`` = 用 Qt 的搜索目录）。

    只做文件名筛选，不解析 ELF 元数据：判据来自实测加载，这里只需要
    "该试哪些文件"。同一目录内排序稳定，跨目录按优先级顺序。
    """
    if dirs is None:
        dirs = input_method_plugin_search_dirs()
    pattern = _plugin_key_pattern(module_key)
    out: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for so in entries:
            if so.suffix != ".so" or not so.is_file():
                continue
            if not pattern.match(so.name):
                continue
            key = str(so.resolve() if so.exists() else so)
            if key in seen:
                continue
            seen.add(key)
            out.append(so)
    return out


def probe_input_method_plugin(module_key: str, dirs=None) -> dict:
    """实测 ``module_key`` 的输入法插件能不能被 Qt 加载（``QLibrary`` dlopen）。

    返回 ``{"module": str, "candidates": [str…], "loadable": str | None,
    "errors": {path: 错误文本}}``；``loadable`` 非 None = 至少一个候选加载成功，
    即运行期能拿到输入法上下文。探测会 dlopen 命中的文件（与 Qt 随后自己要做的
    事完全同类，失败不留残留；成功则与 Qt 复用同一份已映射库），但**不注册、
    不实例化、不改变 Qt 的插件选择**。调用方只在 fcitx 会话下用它写日志。

    先 :func:`prime_qt_runtime_libraries` 再实测：不预热会把依赖解析到系统 Qt 的
    同名库上，结果与 Qt 真正加载时不一致（假阴性）。
    """
    prime_qt_runtime_libraries()
    candidates = find_input_method_plugins(module_key, dirs)
    result: dict = {
        "module": module_key,
        "candidates": [str(p) for p in candidates],
        "loadable": None,
        "errors": {},
    }
    for path in candidates:
        try:
            lib = QLibrary(str(path))
            ok = bool(lib.load())
            if ok:
                result["loadable"] = str(path)
                return result
            result["errors"][str(path)] = (lib.errorString() or "unknown error").strip()
        except Exception as exc:  # noqa: BLE001 - 诊断路径：单候选异常不许中断探测
            result["errors"][str(path)] = f"{type(exc).__name__}: {exc}"
    return result
