# -*- coding: utf-8 -*-
"""Linux 输入法插件可用性探测（platform_linux helper + 启动诊断）。

背景（2026-11-02 Ubuntu 26 + i3 实机根因）：开发态 venv 的 PySide6 自带 Qt
（本机 6.11.3）与系统 apt 的 fcitx5 Qt6 插件（按系统 Qt 6.10.2 私有 ABI 编译）
不兼容，dlopen 报 ``undefined symbol: _ZN14QObjectPrivate...QtPrivate_6_10_2``，
Qt 静默回退 compose 上下文 → **所有输入框无法输入中文**，且用户侧零可见诊断。
判据是 ``QLibrary`` 实测加载（不比对插件版本声明：随包 fcitx 插件按系统头编译、
链接 PySide6 运行库，声明天然不等于运行时，按声明判会把好产品误判为坏）。
"""
import logging

import pytest

from pet import platform_linux as pl

# ---------------------------------------------------------------- 候选筛选


def _mk(dirpath: str, *names: str) -> None:
    import os

    os.makedirs(dirpath, exist_ok=True)
    for n in names:
        with open(os.path.join(dirpath, n), "wb") as f:
            f.write(b"\x7fELF")


def test_find_plugins_matches_key_prefix_and_digits(tmp_path):
    """fcitx 键要命中 libfcitx5…so（后缀带版本号），不误命中 compose/ibus。"""
    ic = tmp_path / "platforminputcontexts"
    _mk(str(ic),
         "libfcitx5platforminputcontextplugin.so",
         "libfcitxplatforminputcontextplugin.so",
         "libcomposeplatforminputcontextplugin.so",
         "libibusplatforminputcontextplugin.so")
    ic2 = tmp_path / "other" / "platforminputcontexts"
    _mk(str(ic2), "libfcitx5platforminputcontextplugin.so")

    found = pl.find_input_method_plugins("fcitx", (ic, ic2))
    names = [p.name for p in found]
    assert "libfcitx5platforminputcontextplugin.so" in names
    assert "libfcitxplatforminputcontextplugin.so" in names
    assert "libcomposeplatforminputcontextplugin.so" not in names
    assert "libibusplatforminputcontextplugin.so" not in names


def test_find_plugins_dedups_and_keeps_priority_order(tmp_path):
    """同优先级目录顺序即返回顺序；同文件（symlink/重复目录）去重。"""
    a = tmp_path / "a" / "platforminputcontexts"
    b = tmp_path / "b" / "platforminputcontexts"
    _mk(str(a), "libfcitx5.so")
    _mk(str(b), "libfcitx5.so")
    dup = tmp_path / "dup"
    dup.symlink_to(a)
    found = pl.find_input_method_plugins("fcitx", (a, b, dup))
    assert str(found[0]) == str(a / "libfcitx5.so")
    assert len(found) == 2  # b 副本 + a 的 symlink（同一 resolve → 去重）


def test_find_plugins_tolerates_missing_dirs_and_non_so(tmp_path):
    ic = tmp_path / "platforminputcontexts"
    _mk(str(ic), "libfcitx5.so", "libfcitx5.so.bak", "notes.txt")
    found = pl.find_input_method_plugins("fcitx", (tmp_path / "does-not-exist", ic))
    assert [p.name for p in found] == ["libfcitx5.so"]


def test_search_dirs_cover_bundle_env_and_system(tmp_path, monkeypatch):
    bundled = tmp_path / "PySide6" / "Qt" / "plugins"
    monkeypatch.setattr(pl, "_bundled_plugin_root", lambda: bundled)
    extra0, extra1 = tmp_path / "qp0", tmp_path / "qp1"
    monkeypatch.setenv("QT_PLUGIN_PATH", f"{extra0}{pl.os.pathsep}{extra1}")
    dirs = pl.input_method_plugin_search_dirs()
    assert bundled / "platforminputcontexts" in dirs
    assert extra0 / "platforminputcontexts" in dirs
    assert extra1 / "platforminputcontexts" in dirs
    # 系统 Qt6 多架构目录必须覆盖（Ubuntu apt fcitx5-frontend-qt6 的落点）
    assert any(str(d).endswith("platforminputcontexts") and "qt6" in str(d) for d in dirs)


# ---------------------------------------------------------------- 运行库预热


def test_prime_loads_bundled_qt_libs_and_reports_successes(monkeypatch, tmp_path):
    """预热按依赖顺序尝试四个库，只把真正 load 成功的名字报回去。"""
    # 故意不用 fake_library fixture：它为了候选顺序把预热干掉了，本例要看的正是预热。
    _FakeLibrary.attempted = []
    _FakeLibrary.loadable = set()
    monkeypatch.setattr(pl, "QLibrary", _FakeLibrary)

    lib_dir = tmp_path / "Qt" / "lib"
    lib_dir.mkdir(parents=True)
    for name in pl._QT_RUNTIME_LIBS:
        (lib_dir / name).write_bytes(b"\x7fELF")
    _FakeLibrary.loadable = {str(lib_dir / "libQt6Core.so.6"), str(lib_dir / "libQt6DBus.so.6")}
    monkeypatch.setattr(pl, "_bundled_qt_lib_dir", lambda: lib_dir)

    assert pl.prime_qt_runtime_libraries() == ["libQt6Core.so.6", "libQt6DBus.so.6"]
    assert _FakeLibrary.attempted == [str(lib_dir / n) for n in pl._QT_RUNTIME_LIBS]


def test_prime_skips_missing_files_and_swallows_exceptions(monkeypatch, tmp_path):
    """缺文件不试；dlopen 抛异常不许把启动诊断带崩（预热不是结论）。"""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "libQt6Core.so.6").write_bytes(b"\x7fELF")

    class _Boom:
        def __init__(self, path):
            raise RuntimeError("loader blew up")

    monkeypatch.setattr(pl, "_bundled_qt_lib_dir", lambda: lib_dir)
    monkeypatch.setattr(pl, "QLibrary", _Boom)
    assert pl.prime_qt_runtime_libraries() == []


def test_prime_is_no_op_without_bundled_lib_dir(monkeypatch):
    monkeypatch.setattr(pl, "_bundled_qt_lib_dir", lambda: None)
    monkeypatch.setattr(pl, "QLibrary", lambda path: (_ for _ in ()).throw(AssertionError("不该 dlopen")))
    assert pl.prime_qt_runtime_libraries() == []


def test_probe_primes_before_touching_candidates(monkeypatch, tmp_path):
    """顺序就是这条诊断的正确性：依赖没映射就实测，报的是系统库的错（假阴性）。

    故意不用 ``fake_library`` fixture：那里把预热关掉了，本例要看的正是两者顺序。
    """
    from pathlib import Path

    calls = []
    monkeypatch.setattr(pl, "prime_qt_runtime_libraries", lambda: calls.append("prime") or [])

    def _record_loader(path):
        calls.append(f"load:{Path(path).name}")
        return _FakeLibrary(path)

    p1 = tmp_path / "libfcitx5.so"
    p1.write_bytes(b"\x7fELF")
    monkeypatch.setattr(pl, "find_input_method_plugins", lambda key, dirs=None: [p1])
    monkeypatch.setattr(pl, "QLibrary", _record_loader)

    res = pl.probe_input_method_plugin("fcitx")
    assert res["loadable"] is None
    assert calls == ["prime", f"load:{p1.name}"], calls


# ---------------------------------------------------------------- 实测加载


class _FakeLibrary:
    """假 QLibrary：只在 ``loadable`` 集合里的路径加载成功，并记录尝试顺序。"""

    attempted: list[str] = []
    loadable: set[str] = set()

    def __init__(self, path):
        self._path = path
        type(self).attempted.append(path)

    def load(self):
        return self._path in type(self).loadable

    def errorString(self):  # noqa: N802 - Qt 命名面
        return f"undefined symbol for {self._path}"


@pytest.fixture
def fake_library(monkeypatch):
    _FakeLibrary.attempted = []
    _FakeLibrary.loadable = set()
    monkeypatch.setattr(pl, "QLibrary", _FakeLibrary)
    # 预热会先 dlopen 四个 Qt 运行库，混进 attempted 就把“候选加载顺序”的断言
    # 冲淡了；预热自身有专门用例，这里当噪声关掉。
    monkeypatch.setattr(pl, "prime_qt_runtime_libraries", lambda: [])
    yield _FakeLibrary


def test_probe_collects_errors_when_nothing_loads(fake_library, monkeypatch, tmp_path):
    p1 = tmp_path / "libfcitx5-a.so"
    p2 = tmp_path / "libfcitx5-b.so"
    for p in (p1, p2):
        p.write_bytes(b"\x7fELF")
    monkeypatch.setattr(pl, "find_input_method_plugins", lambda key, dirs=None: [p1, p2])
    res = pl.probe_input_method_plugin("fcitx")
    assert res["loadable"] is None
    assert res["module"] == "fcitx"
    assert set(res["candidates"]) == {str(p1), str(p2)}
    assert set(res["errors"]) == {str(p1), str(p2)}
    assert fake_library.attempted == [str(p1), str(p2)]


def test_probe_stops_at_first_loadable(fake_library, monkeypatch, tmp_path):
    p1 = tmp_path / "libfcitx5-a.so"
    p2 = tmp_path / "libfcitx5-b.so"
    for p in (p1, p2):
        p.write_bytes(b"\x7fELF")
    fake_library.loadable = {str(p1)}
    monkeypatch.setattr(pl, "find_input_method_plugins", lambda key, dirs=None: [p1, p2])
    res = pl.probe_input_method_plugin("fcitx")
    assert res["loadable"] == str(p1)
    assert fake_library.attempted == [str(p1)]  # 成功即早退，不再试后面


def test_probe_no_candidates_reports_empty(fake_library, monkeypatch):
    monkeypatch.setattr(pl, "find_input_method_plugins", lambda key, dirs=None: [])
    res = pl.probe_input_method_plugin("fcitx")
    assert res == {"module": "fcitx", "candidates": [], "loadable": None, "errors": {}}
    assert fake_library.attempted == []


def test_probe_survives_loader_exception(fake_library, monkeypatch, tmp_path):
    """单个候选抛异常（非 Qt 正常失败路径）不许中断整轮探测。"""
    boom = tmp_path / "libfcitx5-boom.so"
    boom.write_bytes(b"\x7fELF")

    def _raise(path):
        raise RuntimeError("loader blew up")

    monkeypatch.setattr(pl, "QLibrary", _raise)
    monkeypatch.setattr(pl, "find_input_method_plugins", lambda key, dirs=None: [boom])
    res = pl.probe_input_method_plugin("fcitx")
    assert res["loadable"] is None
    assert "loader blew up" in res["errors"][str(boom)]


# ---------------------------------------------------------------- 启动诊断
# 时机分工：`_configure_linux_fcitx_input_method()` 只改环境变量，跑在 QApplication
# 之前；`_warn_if_fcitx_plugin_unusable()` 才做探测与告警，跑在 `_setup_logging` 之后
#（成品从桌面启动没有控制台，stderr 上的一行等于没写）。两条时机都要有断言守着。


def test_configure_sets_env_only_and_never_touches_the_loader(monkeypatch):
    """QApplication 之前的那段只定模块键：在那儿 dlopen 会把启动路径变成不确定 IO。"""
    from pet import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    monkeypatch.setenv("XMODIFIERS", "@im=fcitx")
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    probed = []
    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin",
                        lambda *a, **k: probed.append(a))
    app_mod._configure_linux_fcitx_input_method()
    assert app_mod.os.environ["QT_IM_MODULE"] == "fcitx"
    assert probed == []


@pytest.mark.parametrize("module", ["fcitx", "fcitx5"])
def test_diagnosis_warns_when_nothing_loadable(monkeypatch, caplog, module):
    from pet import app as app_mod

    calls = []

    def _probe(key, dirs=None):
        calls.append(key)
        return {"module": key, "candidates": ["/x/libfcitx5.so"], "loadable": None,
                "errors": {"/x/libfcitx5.so": "undefined symbol: QtPrivate_6_10_2"}}

    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin", _probe)
    with caplog.at_level(logging.WARNING):
        app_mod._warn_if_fcitx_plugin_unusable(module)

    assert calls == [module]
    messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("中文" in m for m in messages), messages
    # 告警必须带上可操作的线索：失败的插件路径 + Qt 原始错误 + 修复入口
    joined = " ".join(messages)
    assert "/x/libfcitx5.so" in joined
    assert "QtPrivate_6_10_2" in joined
    assert "setup-im" in joined


def test_diagnosis_warns_with_search_dirs_when_no_candidates(monkeypatch, caplog):
    """一个候选都没有时，告警要说出找过哪里——否则用户无从判断是装漏还是路径错。"""
    from pet import app as app_mod

    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin",
                        lambda key, dirs=None: {"module": key, "candidates": [],
                                                "loadable": None, "errors": {}})
    monkeypatch.setattr(app_mod.platform_linux, "input_method_plugin_search_dirs",
                        lambda: ["/opt/petsite/PySide6/Qt/plugins/platforminputcontexts"])
    with caplog.at_level(logging.WARNING):
        app_mod._warn_if_fcitx_plugin_unusable("fcitx")
    joined = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
    assert "/opt/petsite/PySide6/Qt/plugins/platforminputcontexts" in joined
    assert "setup-im" in joined


def test_diagnosis_quiet_when_plugin_loadable(monkeypatch, caplog):
    from pet import app as app_mod

    monkeypatch.setattr(
        app_mod.platform_linux, "probe_input_method_plugin",
        lambda key, dirs=None: {"module": key, "candidates": ["/ok"], "loadable": "/ok", "errors": {}})
    with caplog.at_level(logging.WARNING):
        app_mod._warn_if_fcitx_plugin_unusable("fcitx")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("module", ["ibus", "xim", ""])
def test_diagnosis_does_not_probe_non_fcitx_modules(monkeypatch, caplog, module):
    """ibus 等其它前端有自己的插件生态，不适用这条判据：不许探测、不许告警。"""
    from pet import app as app_mod

    called = []
    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin",
                        lambda *a, **k: called.append(a))
    with caplog.at_level(logging.WARNING):
        app_mod._warn_if_fcitx_plugin_unusable(module)
    assert called == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_diagnosis_probe_failure_never_breaks_startup(monkeypatch, caplog):
    """探测自身炸了（例如 unexpected env）只留 debug 痕迹，绝不抛出。"""
    from pet import app as app_mod

    def _boom(key, dirs=None):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin", _boom)
    with caplog.at_level(logging.WARNING):
        app_mod._warn_if_fcitx_plugin_unusable("fcitx")  # 不抛
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("platform,modifiers", [
    ("win32", "@im=fcitx"),
    ("linux", "@im=ibus"),
    ("linux", ""),
])
def test_configure_unchanged_outside_fcitx_linux_session(monkeypatch, platform, modifiers):
    from pet import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", platform)
    monkeypatch.setenv("XMODIFIERS", modifiers)
    monkeypatch.delenv("QT_IM_MODULE", raising=False)
    probed = []
    monkeypatch.setattr(app_mod.platform_linux, "probe_input_method_plugin",
                        lambda *a, **k: probed.append(a))
    app_mod._configure_linux_fcitx_input_method()
    assert "QT_IM_MODULE" not in app_mod.os.environ
    assert probed == []


# ---------------------------------------------------------------- 实机一致性


def test_probe_on_real_search_dirs_is_well_formed():
    """本机的真实目录/文件上跑一遍：结构完整、候选都是已存在的 .so。"""
    if not pl.sys.platform.startswith("linux"):
        pytest.skip("Linux 输入法插件探测")
    res = pl.probe_input_method_plugin("fcitx")
    assert set(res) == {"module", "candidates", "loadable", "errors"}
    from pathlib import Path
    for cand in res["candidates"]:
        assert Path(cand).is_file()
    if res["loadable"] is None:
        # 判不出可用插件时，每个候选都必须留下原因（诊断不许沉默）
        assert set(res["errors"]) == set(res["candidates"])
        assert all(v.strip() for v in res["errors"].values())


def test_prime_on_real_machine_maps_the_libs_the_plugin_needs():
    """预热的前提：PySide6 wheel 自带插件 DT_NEEDED 的那几个 Qt 库。

    这三个（Core/Gui/Widgets）是任何 PySide6 Linux 安装都有的；不预热时它们
    已经被 ``import PySide6.QtWidgets`` 映射，但 libQt6DBus 往往没有——那正是
    假阴性的来源（见 ``prime_qt_runtime_libraries`` 文档）。
    """
    if not pl.sys.platform.startswith("linux"):
        pytest.skip("Linux 输入法插件探测")
    loaded = set(pl.prime_qt_runtime_libraries())
    assert {"libQt6Core.so.6", "libQt6Gui.so.6", "libQt6Widgets.so.6"} <= loaded, loaded
