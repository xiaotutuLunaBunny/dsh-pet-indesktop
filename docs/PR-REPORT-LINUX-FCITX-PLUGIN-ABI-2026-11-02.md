# Linux/fcitx 插件 ABI 不匹配导致「输入框打不进中文」的根因与修复

> **基线**：`203679c`（1.解决宠物修复ubuntu26的i3的窗口多屏显示问题）
> **分支**：`main`　**日期**：`2026-11-02`
> **范围**：7 个文件（实现 2、测试 2、脚本/入口 2、文档 1）
> **关联**：`docs/UBUNTU-I3-ENV-SETUP-TROUBLESHOOTING.md` §2.6.1（本报告的用户侧版本）
> **用户诉求**：「用户交互 输入文字的时候没法输入中文 ubuntu26+i3，这个项目分析下 解决下」

---

## 一、核心特性

**问题**：Ubuntu 26.04 + i3 上，fcitx5 在跑、`QT_IM_MODULE=fcitx` 与
`XMODIFIERS=@im=fcitx` 全都对，但桌宠的聊天框、快速聊天、设置页搜索框**一个中文都打不进去**，
候选词窗口根本不出现，界面侧零提示、日志侧零线索。

**根因**（本机实测，非推断）：`QT_IM_MODULE=fcitx` 只决定「用哪个前端」，真正干活的是 Qt 的
`platforminputcontexts/libfcitx5platforminputcontextplugin.so`。系统 apt 那份插件是**按系统 Qt
6.10.2 的私有 ABI**（`Qt_6_PRIVATE_API` 版本符号 `QtPrivate_6_10_2`）编译的，而开发态 venv 里跑的是
**PySide6 6.11.3 自带的 Qt 6.11.3**，dlopen 直接失败；Qt 对输入法插件加载失败**只往 stderr 吐一行就
静默回退 compose 上下文**（compose 只支持死键组合，不支持中文）。成品不受影响——
`scripts/build_linux.sh:44` 会调 `scripts/build_pyside6_fcitx_plugin.sh` 按 PySide6 自己的运行库重编
插件并 `--add-binary` 进包，缺的只是开发态那一份。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 运行期诊断（`pet/app.py` + `pet/platform_linux.py`） | 桌宠启动时把「插件到底能不能加载」写成 WARNING 进 `pet-<pid>.log`，含候选路径、Qt 原始错误文本、修复入口与文档章节；判据是 `QLibrary` **实测 dlopen** |
| 2 | 开发态一条命令修复（`./petctl.sh setup-im`） | 先用同一判据报现状（ok/broken/missing），非 ok 时体检依赖并现场构建 ABI 匹配的插件放进 venv 的 PySide6 插件目录，再复检并提示重启 |
| 3 | 构建脚本两处真缺陷修复（`scripts/build_pyside6_fcitx_plugin.sh`） | ① 配置阶段被用户环境 `CMAKE_PREFIX_PATH` 劫持导致链接没被改写；② `nm \| grep -q` 在 `pipefail` 下的 SIGPIPE 假阴性使完整性门恒假 |
| 4 | 探测正确性前提（`prime_qt_runtime_libraries`） | 实测前先把 PySide6 自己的 Core/Gui/Widgets/DBus 按 soname 映射进来，否则插件依赖会被解析到系统 apt 的同名库，报出「系统库的符号错」这种假阴性 |
| 5 | 排障手册补第四个坑 | §0 警示块 + 新增 §2.6.1（现象/版本对照/诊断/修复/验证/判据取舍）+ §4 速查表一行 + §5 两条诊断命令 |

**红线 / 不变量**：

- 诊断**只读不写**：不注册、不实例化插件，不改变 Qt 自己的插件选择结果；探测任何异常一律吞掉，
  绝不让「写日志」这件事把启动带崩。
- **不新增任何配置键**（`SETTINGS-CHANGE-GATES.md` 不适用）。
- 非 Linux、非 X11、`XMODIFIERS != @im=fcitx`、`QT_IM_MODULE` 不是 fcitx 系（fcitx/fcitx5）时
  **零行为变化**：不探测、不告警、不改环境变量。
- `_configure_linux_fcitx_input_method()` 的既有环境变量语义逐字保留（`test_bundle_encoding_check.py`
  那条既有用例未动）。

---

## 二、修改文件说明

`git diff --numstat`（已跟踪文件）+ 新文件行数：

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/platform_linux.py` | **新增 237 行**（未跟踪 → 本次入库） | Linux 平台分派收口点。新增输入法段：`_bundled_plugin_root()` / `_bundled_qt_lib_dir()`（冻结走 `sys._MEIPASS/PySide6/Qt/{plugins,lib}`，开发态走 site-packages）、`_system_plugin_roots()`（glob `/usr/lib/*/qt6/plugins` 覆盖 dpkg 多架构）、`input_method_plugin_search_dirs()`（随包→冻结根→`QT_PLUGIN_PATH`→系统，顺序即优先级）、`_plugin_key_pattern()` + `find_input_method_plugins()`（**只按文件名筛选**、`resolve` 去重，不解析 ELF 元数据）、`prime_qt_runtime_libraries()`（预热四个 Qt 运行库，单个失败不抛）、`probe_input_method_plugin()`（`QLibrary` 实测，返回 `{module, candidates, loadable, errors}`，命中即早退）。模块文档写明根因现场与「为什么不用版本声明做判据」 |
| `pet/app.py` | +56 / −1 | 把诊断拆成**两条不能颠倒的时机**：`_configure_linux_fcitx_input_method()` 收缩为**只改环境变量**（必须跑在 `QApplication` 之前，Qt 在构造时才选型插件）；新增 `_warn_if_fcitx_plugin_unusable(module_key)`（只在 fcitx 系模块键下探测并 `logging.warning`，两类文案分别对应「一个候选都没有（打印搜索过哪些目录）」与「有候选但全都失败（打印 `path -> Qt 原始错误`）」），在 `main()` 里**紧跟 `_setup_logging(config)` 之后**调用。理由：`_setup_logging` 只挂 `RotatingFileHandler`、成品从桌面启动没有控制台，stderr 上的一行等于没写 |
| `petctl.sh` | +71 / −1 | 新增 `im_status()`（调项目自己的 `probe_input_method_plugin` 输出「状态⇥详情」，与启动诊断**同源同判据**）与 `cmd_setup_im()`（报现状→已 ok 就短路→`XMODIFIERS` 非 fcitx 时提示→体检 `cmake/git/perl` + Qt6 私有 QPA 头 + `ECMConfig.cmake`→调构建脚本产出到 venv 插件目录→复检→提示 `restart`），`usage` 的 sed 范围 5,18→5,20，`case` 加 `setup-im)` 分派。依赖清单与 `.github/workflows/build-linux.yml` 的 apt 行同口径 |
| `scripts/build_pyside6_fcitx_plugin.sh` | +22 / −1 | 三处：① 用 `find /usr/lib/*/cmake/Qt6` 定位**发行版 Qt6 CMake 包**并显式 `-DCMAKE_PREFIX_PATH=`（找不到直接 die），屏蔽用户环境里手工装的 Qt（本机 `CMAKE_PREFIX_PATH=/xiaotutu/env/qt6/6.8.3/gcc_64` 会劫持 `find_package(Qt6)`，而下面的 perl 只重写 `/usr/lib/.../libQt6*.so*`，结果链接没被改写、插件依赖落到 6.8.3）；② 完整性门改为**先把符号表读进变量再做子串判定**（原 `nm -D --demangle \| grep -q` 在 `set -euo pipefail` 下 `grep -q` 命中即退、`nm` 收 SIGPIPE、管道整体返回 141，门恒假）；③ 失败分支 `tail -n 60 compile.log`（编译阶段是 `set +e` + 重定向，且临时目录在 trap 里会被删，原来失败只剩一句「构建不完整」无从排障） |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_linux_input_method.py` | **新增 373 行 / 22 例** | 候选筛选 4 例（`libfcitx5…` 必须命中、compose/ibus 不许误命中、去重与优先级、缺目录与非 `.so` 容错）；搜索目录 1 例（随包/`QT_PLUGIN_PATH`/系统 qt6 多架构全覆盖）；预热 4 例（按序尝试四库且只报真成功的、缺文件不试且异常吞掉、无随包目录时 no-op、**probe 必须先预热再碰候选**的顺序契约）；实测加载 4 例（全失败时逐候选留原因、命中第一个即早退、无候选结构完整、单候选抛异常不中断整条探测）；启动诊断 7 例（configure **只改 env 不碰加载器**、两种告警文案各含路径/`QtPrivate_6_10_2`/`setup-im`、无候选时打印搜索目录、可加载时静默、`ibus`/`xim`/空串不探测不告警、probe 炸了不崩启动、非 fcitx-linux 会话 no-op）；实机一致性 2 例（真实目录上结构自洽且「判不出可用时每个候选都必须留下原因」、预热能把插件 `DT_NEEDED` 的那几个 Qt 库映射进来） |
| `tests/test_platform_linux_overlay_bypass.py` | **新增 128 行**（未跟踪 → 本次入库） | 上一条提交（`203679c`）的 `BypassWindowManagerHint` 用例，见本节末「顺带修复的仓库卫生缺陷」 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `docs/UBUNTU-I3-ENV-SETUP-TROUBLESHOOTING.md` | +68 / −1 | §0 标题改「i3 三大坑 + 一个中文输入坑」并加警示块；新增 §2.6.1（现象→版本对照表→`undefined symbol` 原文→为什么成品没事→诊断/修复/验证命令→为什么不拿版本声明做判据→重装 venv 后需重跑）；§4 速查表加「中文打不进去」一行；§5 诊断命令清单加 `setup-im` 与「看进程实际映射了哪个插件」 |

### 未改动（明确说明，避免评审误以为漏了）

- `scripts/build_linux.sh`：成品链路本来就是对的（L41-44 构建、L146 `--add-binary`），只是过去
  被 `build_pyside6_fcitx_plugin.sh` 的两处缺陷削弱；本轮**不动它**。
- `.github/workflows/build-linux.yml`：L171-176 的产物门仍只判「文件存在」。改成判「能加载」需要
  在 runner 上起 X + 真实 dlopen，属另一条改动，本轮不做（登记在 §七）。
- `pet/__main__.py` 的 `--settings` 独立进程路径：不 import `pet.app`，因此不会自动设
  `QT_IM_MODULE`，本轮**未改**（见 §七）。
- `requirements.txt`：Linux 侧 `PySide6>=6.5,<6.12` 的封顶理由（6.12.0 的 `Py_None` 引用泄漏）与本问题
  无关，未动版本。

### 顺带修复的仓库卫生缺陷（必须在合并前处理）

`git show HEAD:pet/overlay_window.py` 第 49 行已提交 `from .platform_linux import _x11_bypass_wm_flag`，
但 `pet/platform_linux.py` 与 `tests/test_platform_linux_overlay_bypass.py` 当时**没被 `git add`**（仍是
未跟踪文件）。新鲜检出 HEAD 后 `import pet.overlay_window` 直接崩：

```
$ rm -rf /tmp/im_headclone && mkdir -p /tmp/im_headclone && git archive HEAD | tar -x -C /tmp/im_headclone
$ cd /tmp/im_headclone && QT_QPA_PLATFORM=offscreen <venv>/bin/python -c "import pet.overlay_window"
  File "/tmp/im_headclone/pet/overlay_window.py", line 49, in <module>
    from .platform_linux import _x11_bypass_wm_flag
ModuleNotFoundError: No module named 'pet.platform_linux'
```

本次提交把这两个文件一并入库即可收口（`platform_linux.py` 正是本改动的载体，二者天然同批）。

---

## 三、实现要点

**判据选型：实测加载，不比对版本声明。** 三个候选判据：

1. 读插件的 Qt 版本声明 / `DT_NEEDED` 版本号——**否决**：随包插件是「按系统 Qt 头编译、只链接
   PySide6 运行库」（`build_pyside6_fcitx_plugin.sh` 的 header-overlay + perl 链接替换），其声明天然
   不等于运行时版本，按声明判会把**正常工作的成品判成坏的**。
2. 比对 `qVersion()` 与 apt 包版本——**否决**：同上失真，且开发态根本没有「插件应该匹配哪个版本」
   的权威来源。
3. `QLibrary(...).load()` 实测——**采纳**：加载成功 = Qt 起得来，失败 = Qt 起不来，没有中间态；
   dlopen 失败不留残留，成功则与 Qt 随后自己要做的事复用同一份映射，因此探测是**幂等的、无副作用的**。

**探测自身的一个假阴性（本轮踩到并修掉）**：启动诊断跑在 `QApplication` 之前，此时 PySide6 的
`libQt6DBus.so.6` 往往还没被映射，ld.so 按 **soname** 把插件的 `DT_NEEDED` 解析到系统 apt 的同名库上，
于是报出来的是**系统库自己的** undefined symbol——把「其实能用」的随包/venv 插件误判为坏。实测产物
的 RUNPATH 是一串空冒号（`::::::::::::::`），帮不上解析。修法 = `prime_qt_runtime_libraries()`：探测前
把 PySide6 自带的 Core/Gui/Widgets/DBus 按路径显式 `QLibrary().load()`，**复现 Qt 真正加载插件时的映射
状态**。顺序本身就是契约，有 `test_probe_primes_before_touching_candidates` 守着（该例故意不用
`fake_library` fixture，因为那个 fixture 为了断言候选顺序把预热关掉了）。

**两条时机不能合并**：改环境变量必须在 `QApplication` 之前（Qt 构造时才选型插件）；写日志必须在
`_setup_logging` 之后（成品的唯一可见通道是 `pet-<pid>.log`）。早期版本把两件事写在一个函数里，
诊断实际落在 stderr 上等于没写——拆成两个入口并由
`test_configure_sets_env_only_and_never_touches_the_loader`（QApplication 之前不许 dlopen，否则启动路径
变成不确定 IO）与 `main()` 的调用位置共同守住。

**`setup-im` 与启动诊断共用一个判据**：`im_status()` 直接调 `pet.platform_linux.probe_input_method_plugin`，
所以「命令行说 broken」和「日志说 broken」永远是同一个结论，不会出现两套口径互相打脸。

**构建脚本的 `pipefail` 陷阱值得记进先例**：`set -euo pipefail` 下 `producer | grep -q pattern` 是
**假阴性结构**——`grep -q` 命中即退，`producer` 收到 SIGPIPE，管道整体退出码 141。本机实测该插件有
688 个动态符号、目标符号在第 234 行命中，门照样判假。判定类门必须先**完整消费**生产者输出（读进变量
/数组）再做子串判定。

---

## 四、性能分析

**方法（可复现）**：`PYTHONPATH=. .venv/bin/python /tmp/im_perf_final.py`（`perf_counter` 圈复杂度为零的
计时包装，`QT_QPA_PLATFORM=offscreen`；脚本内容=对下列函数各跑 n 次取 min/max）
环境：本机 Ubuntu 26.04.1 LTS / Python 3.12.11 / PySide6 6.11.3（Qt 6.11.3）/ 开发态 venv，
**输入法插件在场且可加载**（走的是「静默早退」这条最常见路径）。

| 指标 | 实测 | 归属 |
|---|---|---|
| `input_method_plugin_search_dirs()` | 0.791 – 1.141 ms（n=10，命中 3 个目录） | 新增（启动一次） |
| `prime_qt_runtime_libraries()`（库已映射，warm） | 0.184 – 0.232 ms（n=10，4 个库全回报成功） | 新增（启动一次） |
| `probe_input_method_plugin("fcitx")` 首调用（冷，含预热） | 2.119 ms（n=1） | 新增（启动一次） |
| `probe_input_method_plugin("fcitx")` 重复调用 | 1.334 – 2.366 ms，10 次合计 19.2 ms（n=10） | 新增 |
| `_warn_if_fcitx_plugin_unusable("fcitx")` 整条诊断 | **1.353 – 1.909 ms**（n=5） | 新增（**每进程一次**） |
| `./petctl.sh setup-im` 已 ok 时的短路路径 | 0.355 s wall（含一次解释器启动 + 探测） | 新增（手动，非常态） |
| `./petctl.sh setup-im` 首次真构建 | **3:41.09 total**（55.88 s user / 9.85 s system / 29% CPU），rc=0 | 新增（手动，一次性） |
| 插件产物体积 | 2,502,816 字节 | 新增（开发态 venv 内；成品侧本来就随包） |

**结论**（逐条回答四问）：

1. **稳态开销**：唯一新增开销是启动路径上约 **1.4–2.1 ms 的一次性诊断**（每进程一次，不在任何 tick /
   事件循环 / 采样路径上）。作为量级参照：本机一次 `Config()` 读约 0.5 ms 级、设置窗构造约 1 s 级，
   这 2 ms 落在启动噪声带内且**不随宠物数量、窗口数量、tick 频率增长**。稳态（启动完成后）零成本。
2. **新增路径的绝对成本与触发频率**：`setup-im` 是**人手一次**的开发态动作（首次 3:41，其中 clone
   qtbase 22,900 个文件占主导、29% CPU 说明瓶颈是 IO/网络而非本地算力；已 ok 时短路 0.355 s）。
   构建脚本的三处改动只作用于构建期，成品运行期一行未执行。
3. **新的系统调用 / 网络 / 磁盘 / 线程**：诊断路径新增 `glob`（3 个目录）+ `iterdir`（候选目录）+
   `dlopen`（最多 N 个候选，命中即早退），**无线程、无网络、无写盘**。构建路径的新增网络（clone
   qtbase/fcitx5-qt）是既有行为，本轮未扩大；`find /usr/lib/*/cmake/Qt6` 与 `nm -D` 各一次，都在构建期。
4. **内存 / 缓存**：预热额外映射 4 个 PySide6 自己的 Qt 库——这 4 个库在 `import PySide6.QtWidgets`
   之后**本来就已映射**（`libQt6DBus` 除外，它由 xcb 平台插件带入），因此不引入新常驻内存；dlopen
   失败的候选不驻留。无新增缓存结构。

---

## 五、实机运行记录

全部在**本机真实环境**（Ubuntu 26.04.1 LTS + i3 + X11，非 CI、非 mock）。版本事实：

```
$ .venv/bin/python -c "import PySide6; from PySide6.QtCore import qVersion; print('PySide6', PySide6.__version__, 'Qt', qVersion())"
PySide6 6.11.3 Qt 6.11.3
$ dpkg-query -W -f='${Package} ${Version}\n' libqt6core6t64
libqt6core6t64 6.10.2+dfsg-7
$ dpkg -S /usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so
fcitx5-frontend-qt6: /usr/lib/.../libfcitx5platforminputcontextplugin.so
$ pgrep -a fcitx5 | head -1 ; echo "QT_IM_MODULE=$QT_IM_MODULE XMODIFIERS=$XMODIFIERS"
6352 /usr/bin/fcitx5 -d
QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx
```

### 1. 根因的现场复现（修复前，`setup-im` 报出的 Qt 原始错误）

```
$ ./petctl.sh setup-im
现状（模块键 fcitx）：broken
  /usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so ->
  Cannot load library .../libfcitx5platforminputcontextplugin.so:
  /usr/lib/x86_64-linux-gnu/libQt6WaylandClient.so.6: undefined symbol:
  _ZN14QObjectPrivateC2E16QtPrivate_6_10_2, version Qt_6_PRIVATE_API
```

即「fcitx5 在跑、环境变量全对、插件文件存在，但加载不了」。同一条判据在修好之后：

```
$ ./petctl.sh setup-im
现状（模块键 fcitx）：ok
  /xiaotutu/.../.venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so
已存在可加载的插件，无需构建。
```

### 2. 修复动作与真实输出

按 `setup-im` 的依赖提示装齐构建依赖（`sudo apt install -y qt6-base-private-dev libfcitx5-qt-dev`
连带 29 个包，6.10.2；另补 `extra-cmake-modules libxkbcommon-dev libxcb1-dev libx11-dev pkg-config`
——`ECMConfig.cmake` 缺失时 fcitx5-qt 的 `find_package(ECM)` 直接失败，这条也是体检项扩到 CI 同口径的原因），
然后真跑一遍端到端：

```
$ time ./petctl.sh setup-im
… Cloning into '/tmp/dsh-pet-fcitx-plugin.cKJMbT/qtbase'…（切到 v6.11.3）
… Cloning into '…/fcitx5-qt'…（切到 5.1.14）
-- Found XCB … Found XKBCommon … -- The following REQUIRED packages have been found: * ECM * XCB * XKBCommon * X11 * Qt6 * Qt6GuiPrivate
-- Configuring done (1.7s)
已就绪：/xiaotutu/.../.venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so
重启桌宠生效：./petctl.sh restart
rc=0
./petctl.sh setup-im > /tmp/setup_im_e2e_v1.txt 2>&1  55.88s user 9.85s system 29% cpu 3:41.09 total
```

### 3. 用户可见行为（本轮改动后重跑）

```
$ ./petctl.sh restart
未在运行
已启动：pid=400233
$ tr '\0' '\n' < /proc/400233/maps | grep -oE 'platforminputcontexts/[^ ]*\.so' | sort -u
platforminputcontexts/libfcitx5platforminputcontextplugin.so
$ grep -c "输入法插件" ~/.config/dsh-pet-standalone/pet-400233.log
0
```

进程实际映射的是 **fcitx5** 插件（不是 compose），启动日志零告警。

**「打进中文字」这一最终观感为何不能自动验证**：候选词窗口由 **另一个进程**（fcitx5 守护，pid 6352）
绘制，注入合成按键（XTEST/xdotool）会抢走用户此刻桌面的键盘焦点，且「看到候选窗出现」本质是人工目视
判定。替代证据链取两层：① 上面的 `/proc/<pid>/maps`——Qt 只有真的把 fcitx5 选为 platform input
context 才会映射该文件；② 不含任何本仓库探测代码的**裸 PySide6 应用** A/B（排除「映射是探测自己
dlopen 造成的」这一干扰）：

```
# 裸应用（QT_QPA_PLATFORM=xcb，只 import QtWidgets + 一个 QLineEdit，不调用任何探测函数）
$ python - <<'PY' | …   # 打印本进程映射的 platforminputcontexts 插件
mapped IM plugins by Qt itself: ['platforminputcontexts/libfcitx5platforminputcontextplugin.so']
# 把该插件临时移走（回到修复前状态）：
mapped IM plugins by Qt itself: ['platforminputcontexts/libcomposeplatforminputcontextplugin.so']
```

插件在场→Qt 选 fcitx5；插件移走→Qt 回退 compose。**这正是「中文打不进去」与「能打进去」的分界**。
仍需用户在本机聊天框实际打一次拼音确认最终观感（见 §七）。

### 4. 边界与失败路径的实机观察

- **修复失败必须报红**：`setup-im` 在缺 `qt6-base-private-dev` 时 die 并打印 apt 命令；补装后仍缺 ECM 时
  第二条 die 命中 `ECMConfig.cmake`——两条都在真机跑出来过，不是纸面分支。
- **完整性门曾经假阴**：修复前 `build_pyside6_fcitx_plugin.sh` 对**已经正确构建**的插件报
  「Fcitx5 Qt6 插件构建不完整」（`nm|grep -q` 的 141），且临时目录被 trap 删除导致 `compile.log` 看不到；
  修完后同一条流水线 rc=0。
- **`CMAKE_PREFIX_PATH` 劫持**：本机环境变量指向 `/xiaotutu/env/qt6/6.8.3/gcc_64` 时，`readelf -d` 显示产物
  的 `DT_NEEDED` 落在 6.8.3 路径上（perl 只重写 `/usr/lib/...`），脚本固定发行版 CMake 包后链接替换才真的生效。
- **探测假阴性**：把插件放进 venv 后探测仍 `loadable=None`，报
  `libQt6DBus.so.6: undefined symbol …`——那是**系统 apt 库自己的**符号错（未预热时依赖被解析到 apt 副本）；
  预热后 `loadable=<venv>/…/libfcitx5platforminputcontextplugin.so`。

### 5. HEAD 树可复现缺陷

见 §二末尾：`git archive HEAD` 新鲜检出后 `import pet.overlay_window` 抛
`ModuleNotFoundError: No module named 'pet.platform_linux'`（原始 traceback 已贴）。

---

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv/bin/ruff check pet tests scripts` | **All checks passed!** |
| Shell 语法 | `bash -n petctl.sh`；`bash -n scripts/build_pyside6_fcitx_plugin.sh` | 双双通过（无输出，rc=0） |
| 聚焦 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_linux_input_method.py tests/test_bundle_encoding_check.py tests/test_platform_linux_overlay_bypass.py -q` | **42 passed in 0.88s** |
| 先红后绿 | 新用例提交前首跑 `3 failed / 14 passed`（失败全在候选文件名匹配：`(?:^\|[^a-z0-9])fcitx\d*` + `search` 匹配不到 `libfcitx5…`，因为 `fcitx` 前面是字母 `b`），改锚定词首 + `match` 后转绿 | 已取证 |
| 全量 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` | **9 failed, 9013 passed, 86 skipped in 2173.07s (36:13)** |
| 全量红的归因 | 独立 worktree 跑基线（`git worktree add --detach /tmp/im_baseline HEAD` + 只补进 HEAD 漏加的 `pet/platform_linux.py`，不含本次任何改动）跑同 9 条 | **同样 9 failed in 32.21s** ⇒ 本机 Linux 既有环境红，与本改动无关 |
| 实机端到端 | `./petctl.sh setup-im`（rc=0）→ `./petctl.sh restart` → `/proc/<pid>/maps` → 日志告警计数 | 见 §五 |

9 条既有红的名单（逐条在基线复现）：
`test_animation_prewarm_phase2::test_enable_prewarm_reschedules_when_visible`、
`test_chat_subsystem::test_resize_keeps_following_reader_at_bottom`、
`test_click_talk_dialog::test_builtin_character_discovers_real_names`、
`test_island_alert_colors::{test_folded_alert_survives_the_sampling_blackout, test_chip_dot_color_follows_the_highest_level_and_disappears_on_restore}`、
`test_settings_page_guide::test_anchor_bar_{does_not_stick_shown_inside_its_own_gap, converges_from_both_initial_states_inside_the_gap, holds_across_jump_resize_and_domain_navigation}`、
`test_windows_node_env::TestConfiguredPnpmBin::test_broken_config_falls_back_to_discovery`。

**断言有效性（消融）**：把 `_plugin_key_pattern` 退回旧写法 → 候选筛选 3 例转红；把
`probe_input_method_plugin` 开头的 `prime_qt_runtime_libraries()` 调用删掉 →
`test_probe_primes_before_touching_candidates` 转红；把 `_configure_linux_fcitx_input_method()` 里的诊断
调用塞回去 → `test_configure_sets_env_only_and_never_touches_the_loader` 转红。

---

## 七、已知限制与后续

1. **最终「打出中文」的观感仍待用户确认**：候选窗由 fcitx5 守护进程绘制，自动注入按键会抢用户桌面
   焦点，故以 `/proc/<pid>/maps` + 裸应用 A/B 作为替代证据（§五 3）。
2. **`--settings` 独立进程不受益**：`pet/__main__.py::_run_settings` 不 import `pet.app`，因此**不会**自动设
   `QT_IM_MODULE`。本机桌面环境已导出该变量（§五 版本事实行），故不构成现行故障；若某桌面没设，
   独立设置页仍会回退 compose。触发条件：真有用户报告「只有设置页打不进中文」时，把这两条时机
   同样移植进 `_run_settings`。
3. **CI 仍只验证「文件存在」**：`build-linux.yml:171-176` 判 `libfcitx5platforminputcontextplugin.so` 在不在包里，
   **从不验证它能不能加载**——本轮 3 号缺陷（构建脚本被环境劫持、门恒假）正是这个缺口的后果。建议后续
   加一步「在 runner 上对包内插件跑 `probe_input_method_plugin`」的产物门（需要 xvfb + 真实 dlopen）。
4. **插件装在 site-packages 里**：`./petctl.sh install` 重建 venv 后需重跑 `setup-im`（文档 §2.6.1 已写明）。
5. **不做的事**：不改 `requirements.txt` 的 PySide6 版本策略去「凑」apt 版本（Linux 封顶 `<6.12` 的理由是
   6.12.0 的引用泄漏，与本问题无关，且 apt 侧版本不归本项目控制）；不加任何设置键。

---

## 八、风险与回滚

- **影响面**：Linux/X11 + `XMODIFIERS=@im=fcitx` 会话的启动路径（新增 2 ms 级一次性诊断 + 可能的
  一条 WARNING 日志）；开发态新增一条手动命令；构建脚本仅影响 Linux 成品构建。Windows/macOS
  零行为变化（`sys.platform` 早退，且 `petctl.sh` 只在 Linux 开发态用）。
- **没有开关**：诊断是无副作用的只读路径，刻意不加设置键（加了反而多一条「为什么日志没出现」的
  排障线）。若某环境因探测本身出问题，回滚方式就是 revert 本片。
- **配置迁移**：无新增/删除任何持久化键。
- **回滚残留**：`setup-im` 会往 `.venv/…/PySide6/Qt/plugins/platforminputcontexts/` 写一个
  `libfcitx5platforminputcontextplugin.so`（2,502,816 字节）。`git revert` 代码不会删除它，但它只是让
  中文输入**继续能用**；要完全回到回滚前状态需手工删该文件（`site-packages` 内的产物，不在版本控制里，
  重建 venv 亦自动清除）。构建脚本的三处修复属健壮性加固，回滚会让「构建不完整」的假阴性门重新生效。
