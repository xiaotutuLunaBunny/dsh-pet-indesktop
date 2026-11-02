# Ubuntu 桌宠环境配置与启动排障手册

> **基线（已实测）**：Ubuntu 24.04.4 LTS（Noble），X11 会话，PySide6 6.11.3。
> **目标（待迁移）**：Ubuntu 26 + **i3** 窗口管理器（纯 X11，无合成器、无系统托盘）。
> 本文只记录「把项目跑起来」所需的环境与排障，不动项目代码。所有路径以仓库根 `dsh-pet-indesktop/` 为基准。

---

## 0. 一句话结论（i3 三大坑 + 一个中文输入坑）

i3 默认 **没有合成器**（透明窗会发黑/不透明）和 **没有系统托盘**（托盘图标消失，只剩「桌宠恢复」窗）。
因此迁移到 i3 **必须**额外装两类东西：

1. **compositor**：`picom`（让透明无边框的桌宠窗正常显示）。
2. **托盘宿主**：`trayer` / `stalonetray` / `polybar`（否则 `QSystemTrayIcon.isSystemTrayAvailable()` 为 false，app 走恢复窗分支）。

再加一组 **Qt6 的 X11 系统库**（PySide6 wheel 不带这些），缺了会直接 `Could not load the Qt platform plugin "xcb"` 崩在启动最前面。

> **第四个坑（中文输入，2026-11-02 实测）**：fcitx5 在跑、三个 IM 环境变量全对，仍然可能**所有
> 输入框打不进中文**——系统 apt 的 Qt6 fcitx5 插件是按系统 Qt（本机 6.10.2）的私有 ABI 编译的，
> 而进程里跑的是 venv PySide6 自带的 Qt 6.11.3，dlopen 失败后 Qt **静默回退 compose**。用
> `./petctl.sh setup-im` 探测并现场构建匹配插件，见 §2.6.1。

---

## 1. 当前已跑通的环境快照（Ubuntu 24.04.4）

| 项 | 值 |
|---|---|
| 系统 | Ubuntu 24.04.4 LTS (Noble Numbat)，`VERSION_ID=24.04` |
| 会话 | X11（`XDG_SESSION_TYPE=x11`），`DISPLAY=:1`，`WAYLAND_DISPLAY` 空 |
| pyenv | 2.8.1-6；已装 `3.11.9` / `3.11.13` / `3.12.12`，**当前用 `3.12.12`** |
| 虚拟环境 | 仓库根 `.venv/`（由 `~/.pyenv/versions/3.12.12/bin/python -m venv` 建，`gitignore` 已忽略） |
| 关键依赖 | `PySide6 6.11.3` + Addons/Essentials/shiboken6；Qt 多媒体走 **FFmpeg 7.1.5** |
| 离线核依赖 | `Pillow 13.0.0`、`numpy 2.5.4`、`onnxruntime 1.31.1`、`opencv-python-headless 5.0.0.93`、`rapidocr 3.10.0`、`edge-tts 7.3.1`、`keyring 25.7.0`、`lunar-python 1.4.8`、`psutil 7.2.2` |
| 开发依赖 | `pytest 9.2.0`、`ruff 0.17.3` |
| 变体名 | `APP_DIR_NAME = dsh-pet-standalone`（见 `pet/config.py`） |
| 配置/数据目录 | `~/.config/dsh-pet-standalone/` |
| 应用日志 | `~/.config/dsh-pet-standalone/pet-<pid>.log`（滚动 1MB×2，超过 7 天自动清） |
| 启动脚本日志 | `.run/pet.log`（`.run/` 已被 gitignore） |
| 启动方式 | `./petctl.sh start`（`setsid nohup`，pid 存 `.run/pet.pid`） |

> Linux 版 `requirements.txt` 把 PySide6 钉在 `<6.12,>=6.5`，所以 pip 解析到 **6.11.3**（6.12.0 在 Linux 有 Py_None 引用泄漏、跑久 SIGABRT，见 requirements.txt 注释）。

### 1.1 本机两个「特殊」环境（迁移时**不要**照抄）

- **OpenSSL 默认 CA 路径是坏的**：本机所有 Python（含 system 与 3 个 pyenv 版本）的 `ssl.get_default_verify_paths().openssl_cafile` 都指向不存在的 `/home/test/yx/trunk/.../ssl/cert.pem`。pip 走 HTTPS 会 `CERTIFICATE_VERIFY_FAILED`。脚本在未设置 `SSL_CERT_FILE` 时自动指到系统证书包：
  ```sh
  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
  export SSL_CERT_DIR=/etc/ssl/certs
  ```
  一台干净的 Ubuntu 26 **不会有这个坏路径**，看到 `CERTIFICATE_VERIFY_FAILED` 才需要这个 hack。
- **本机代理**：`HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:7897`。只影响 pip 安装和在线能力（聊天 edge-tts、天气、歌词等）；**不影响桌宠本体启动**。Ubuntu 26 上若没有这个代理，不要设置它，pip 直接走直连即可。

---

## 2. Ubuntu 26 + i3 迁移检查清单

按「先会话 → 再 Qt 系统库 → 再 i3 配套 → 再 Python → 最后网络」的顺序查，能最快定位。

### 2.1 会话与显示（X11 必须有）

| 检查 | 命令 | 期望 |
|---|---|---|
| 当前会话类型 | `echo "$XDG_SESSION_TYPE" / "$WAYLAND_DISPLAY"` | i3 是 X11：`x11`，且 `WAYLAND_DISPLAY` 为空 |
| DISPLAY 已设 | `echo "$DISPLAY"` | `:0`/`:1` 之类，非空 |
| X 可用 | `xrandr >/dev/null 2>&1 && echo OK` | OK |
| 真机还是无头 | 登录进 i3 则已满足；SSH/CI 需 `Xvfb`：`Xvfb :1 -screen 0 1920x1080x24 & export DISPLAY=:1` | 无 DISPLAY 时 app 启动即崩 |

> i3 只支持 X11，不要在这种机器上用 Wayland 合成器。`app.py::_default_xcb_platform_on_wayland()` 检测到 Wayland 会自动把 `QT_QPA_PLATFORM=xcb`（走 XWayland）；但 i3 本身就是 X11，无需操心。若你显式要锁平台：`export QT_QPA_PLATFORM=xcb`。

### 2.2 Qt6 的 X11 系统库（**最常被漏**，崩在最前）

PySide6 的 wheel 不含 X11 运行库。缺任何一个都会打印：
`qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.`
然后退出（exit 非 0）。

```sh
sudo apt update
sudo apt install -y \
  libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-shm0 \
  libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 \
  libxkbcommon-x11-0 libxkbcommon0 \
  libx11-xcb1 libxext6 libxrender1 libxi6 libsm6 libice6 \
  libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxrandr2 libxtst6 \
  libfontconfig1 libfreetype6 libgl1 libglib2.0-0 libdbus-1-3
```

快速验证 Qt 能加载 xcb 插件（**最省事的排障命令**）：
```sh
ldd .venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so \
  | grep "not found"
# 有输出 = 缺对应库；无输出 = xcb 插件依赖齐了
```
更彻底地看 app 为什么选不中插件：
```sh
QT_DEBUG_PLUGINS=1 QT_QPA_PLATFORM=xcb ./petctl.sh start
```

### 2.3 compositor：picom（i3 没有，透明窗必黑）

i3 自身不做合成，桌宠的透明无边框 `ShellOverlayWindow` 在没有合成器时背景会发黑/不透明。
```sh
sudo apt install -y picom
```
i3 配置（`~/.config/i3/config`）里开机启动：
```
exec --no-startup-id picom
```
> 验证：`pgrep -a picom`。透明异常（黑洞/残影）第一反应就是查 picom。

> ⚠️ **picom 全局 blur 反噬整屏（2026-11-02 已实测踩坑）**：若 picom.conf 里开了
> `blur-background = true`（常为 rofi 毛玻璃而设），它作用于**所有半透明窗**——桌宠的
> overlay 恰恰是一张铺满整屏的全透明窗（实测 4480×1600，`_NET_WM_WINDOW_TYPE_UTILITY`，
> WM_CLASS 第二段 `class_g = 'dsh-pet-standalone'`，代码里 `app.setApplicationName(APP_DIR_NAME)`
> 保证了这个类名），于是整个桌面被糊一层磨砂。修复：保留全局 blur，精准排除桌宠窗：
> ```ini
> blur-background-exclude = [
>   "class_g = 'dsh-pet-standalone'",
> ];
> ```
> 验证方法：同一区域截屏比 Laplacian 方差（换配置前后 379 → 2100+，边缘能量差 5 倍+，
> 见 §4 速查表）；rofi 等其他半透明窗毛玻璃行为不变。

### 2.4 系统托盘：trayer（i3 没有托盘，否则只剩恢复窗）

`app.py` / `overlay_shell.py` 用 `QSystemTrayIcon`。检测到无托盘时走 `_ensure_recovery_entry()`，弹一个「桌宠恢复」窗（功能可用，但没有正常托盘菜单/气泡入口）。要恢复正常托盘体验，给 i3 配一个托盘宿主：

```sh
sudo apt install -y trayer      # 轻量，推荐
# 或 stalonetray / polybar（polybar 自带 tray 模块）
```
i3 配置：
```
exec --no-startup-id trayer --edge top --align right --widthtype request \
  --expand true --height 22 --transparent true --alpha 255 \
  --tint 0x333333 --SetDockType true --SetPartialStrut true
```
> 验证：`which trayer && pgrep -a trayer`。

### 2.5 DBus 会话总线（托盘/通知/fcitx 都依赖）

Qt 的托盘、桌面通知、`QApplication` 的一些能力需要 session bus。
- 若通过显示管理器（GDM/LightDM）登录 i3，总线一般已建好：`echo "$DBUS_SESSION_BUS_ADDRESS"` 非空。
- 若手动 `startx` / `xinit` 进 i3，**必须**包一层，否则没有总线（托盘/通知会失败，严重时会让 app 起不来或异常）：
  ```sh
  # ~/.xinitrc 或启动命令
  exec dbus-run-session -- i3
  ```
  或 `exec dbus-launch i3`。

### 2.6 输入法 fcitx（只在用中文输入时需要）

`app.py::_configure_linux_fcitx_input_method()`：**只有当 `XMODIFIERS=@im=fcitx` 时**才会自动设 `QT_IM_MODULE=fcitx`；否则整段跳过（无输入法也不影响启动）。所以：
```sh
sudo apt install -y fcitx5 fcitx5-configtool   # 或 fcitx
```
并在 `~/.xprofile` / i3 启动前设好：
```sh
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
```
> 不用中文输入：完全可以不装 fcitx，app 那段逻辑自动 no-op，不影响启动。

#### 2.6.1 环境变量都对、fcitx5 在跑，但就是打不进中文：插件 ABI 不匹配（2026-11-02 本机实测）

`QT_IM_MODULE=fcitx` 只决定“用哪个前端”，真正干活的是 Qt 的
`platforminputcontexts/libfcitx5platforminputcontextplugin.so`。Qt 对该目录下的插件
**dlopen 失败时只往 stderr 吐一行就静默回退 compose 上下文**（compose 只支持
死键组合，不支持中文），于是表现为“候选词根本不出现、聊天/设置/快速聊天
所有输入框都打不进中文”，界面侧零提示——这就是本机踩到的现象。

失败形态：**开发态 venv 与系统 apt 插件的 Qt 私有 ABI 不是同一个版本**。

| 侧 | Qt 版本 | 来源 |
|---|---|---|
| 进程里实际生效的 libQt6Core/Gui | 6.11.3 | venv 里的 PySide6 wheel（`requirements.txt` 为 Linux 钉了 `<6.12`） |
| apt 的 fcitx5 Qt6 插件 | 6.10.2 | `/usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/`，链接 `Qt_6_PRIVATE_API` 版本符号 |

插件引用 `QtPrivate_6_10_2` 的私有符号，进程里的 Qt 是 6.11.3 → dlopen 直接失败
（`QT_DEBUG_PLUGINS=1` 可见，随后就是 `libcomposeplatforminputcontextplugin.so loaded library`）：

```
Cannot load library .../libfcitx5platforminputcontextplugin.so:
/usr/lib/x86_64-linux-gnu/libQt6DBus.so.6: undefined symbol:
_ZN14QObjectPrivateC2E16QtPrivate_6_10_2, version Qt_6_PRIVATE_API
```

成品不受影响：`scripts/build_linux.sh` 会调 `scripts/build_pyside6_fcitx_plugin.sh`，
按 **PySide6 自己的运行库** 编译插件并 `--add-binary` 打进包；缺的只是开发态 venv。

**诊断**（与桌宠启动用同一判据：`QLibrary` 实测加载，不比对版本声明）：

```sh
./petctl.sh setup-im      # 先报现状：ok / broken（带 Qt 原始错误）/ missing（一个候选都没有）
tail -n 50 .run/pet.log    # 桌宠启动时也会把同一结论写成 WARNING（含候选路径 + 错误 + 修复入口）
```

**修复**（开发态；产物直接放进 venv 的 PySide6 插件目录，Qt 会自己扫到）：

```sh
sudo apt install -y qt6-base-private-dev cmake git perl   # 私有 QPA 头 + 构建工具
./petctl.sh setup-im                                     # 需外网 clone qtbase + fcitx5-qt，约数分钟
./petctl.sh restart
```

验证（比日志更硬的证据：看进程真的映射了哪个插件）：

```sh
grep -o 'platforminputcontexts/[^ ]*' /proc/$(pgrep -f '\.venv/bin/python -m pet' | head -1)/maps | sort -u
# 期望 libfcitx5platforminputcontextplugin.so；只剩 libcompose… 就是还没修好
```

> 为什么不拿“插件自带的版本声明”做判据：随包插件是“按系统 Qt 头编译、只链接
> PySide6 运行库”，其声明天然不等于运行时版本，按声明比对会把正常工作的成品
> 判成坏的。实测加载没有这个失真。详见 `pet/platform_linux.py` 模块文档与
> `tests/test_linux_input_method.py`。重装 venv（`./petctl.sh install` 重建）后需重跑
> `setup-im`：插件装在 site-packages 里，随目录一起没。

### 2.7 中文字体（缺则气泡/聊天中文变方块）

README 已要求：`fonts-noto-cjk`（缺时中文显示为方块）。建议一并装 `fonts-noto-core`。
```sh
sudo apt install -y fonts-noto-cjk fonts-noto-core
```

### 2.8 Python / pyenv / venv

两种拿 Python 的方式，**任选其一**：

- **沿用 pyenv（与现环境一致）**：
  ```sh
  curl -fsSL https://pyenv.run | bash   # 或 apt 装 pyenv
  # 编译 3.12.12 需要构建依赖：
  sudo apt install -y make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev
  pyenv install 3.12.12
  ```
  然后 `PET_PY_VERSION=3.12.12 ./petctl.sh install`。
- **直接用系统 Python（更快，免编译）**：Ubuntu 26 自带 3.12/3.13。
  ```sh
  sudo apt install -y python3.12-venv python3.12-dev   # 或 python3.13-venv
  ```
  当前 `petctl.sh` 只从 pyenv 解析 `PY_VERSION`。想用系统 python，二选一：
  - 把系统 python 软链成 pyenv 形状（零改脚本，推荐）：
    ```sh
    mkdir -p ~/.pyenv/versions/3.12.12/bin
    ln -sf "$(command -v python3.12)" ~/.pyenv/versions/3.12.12/bin/python
    ./petctl.sh install
    ```
  - 或改 `petctl.sh::resolve_pyenv_python()` 让它优先读 `PYTHON_BIN` 环境变量。

装完跑：`./petctl.sh install`（脚本会自动建 venv + `pip install -r requirements.txt`）。

### 2.9 网络 / 代理 / SSL

- **本体启动不需要外网**（本地 OCR、本地动画、无托盘都离线可用）。
- 在线能力（AI 聊天、天气、歌词、更新检查）需要外网；若有企业代理再设 `HTTPS_PROXY`。
- 只有在 `pip install` 报 `CERTIFICATE_VERIFY_FAILED` 时才需要 `SSL_CERT_FILE`（见 1.1，干净 Ubuntu 26 不需要）。

### 2.10 配置文件迁移（可选）

`~/.config/dsh-pet-standalone/` 跨机器直接拷即可复用角色/设置/活跃宠物。全新机器不拷也行，首次启动自动建。
注意：应用日志是 `pet-<pid>.log`，每个实例一份，拷不拷无所谓。

---

## 3. i3 配置最小片段（汇总 2.3 / 2.4）

`~/.config/i3/config`：
```
# 合成器：透明桌宠窗正常显示
exec --no-startup-id picom

# 系统托盘宿主（否则桌宠只剩"恢复"窗）
exec --no-startup-id trayer --edge top --align right --widthtype request \
  --expand true --height 22 --transparent true --alpha 255 \
  --tint 0x333333 --SetDockType true --SetPartialStrut true
```

> **桌宠窗不需要任何 `for_window` 规则（2026-11-02 起）**：X11 上 overlay 单合成窗
> 已带 `Qt.BypassWindowManagerHint`（override-redirect，`pet/platform_linux.py::
> _x11_bypass_wm_flag`），i3 物理上无法接管/移动它。旧版本文建议的
> `for_window [class="pet"] floating enable` 是无效规则（真实 WM_CLASS 是
> `dsh-pet-standalone`，且浮动也救不了坐标错位，见 §4）。
> 历史背景：i3 接管 overlay 会把它 reparent 进 i3-frame 并摆到错误原点
>（实测 4480×1600 的窗被放到 `+1280,-266`），而 Qt 收到的 ConfigureNotify 是
> 父相对坐标，从此绘制/命中/气泡跟随全部错位——表现为「鼠标点不中宠物、
> 扩展屏位置错乱」。mutter（GNOME）认 Qt 的 `_KDE_NET_WM_WINDOW_TYPE_OVERRIDE`
> 提示不接管该窗，所以同代码在 Ubuntu 24 GNOME 上正常。
> 已知取舍：override-redirect 不受 WM 层序管理，X11 上 `on_top=False`（置底）
> 不再能把桌宠压到普通应用之下。气泡等小窗仍由 i3 管理（实测 i3 会尊重其
> 客户端请求位置，不偏移）。验证：`xwininfo -root -tree` 里 overlay 窗应是
> **root 直属子窗**（不在任何 `i3-frame` 容器里）且绝对坐标 = 虚拟桌面原点。

---

## 4. 排障速查表

| 现象 | 根因 | 命令 / 验证 | 修复 |
|---|---|---|---|
| 启动即崩，`Could not load the Qt platform plugin "xcb"` | 缺 Qt 的 X11 系统库 | `ldd .../plugins/platforms/libqxcb.so \| grep "not found"` | 装 §2.2 的 apt 包 |
| 启动即崩，`DISPLAY` 相关 / 无窗口 | 没有 X 会话 | `echo $DISPLAY`；`xrandr` | 登录进 i3，或 `Xvfb :1 ...; export DISPLAY=:1` |
| 桌宠窗背景全黑 / 不透明 / 拖影 | i3 无合成器 | `pgrep -a picom` | 装并自启 `picom`（§2.3） |
| 启动桌宠后**整个屏幕蒙一层模糊/磨砂**（项目代码内无任何 blur 实现，是 picom 全局 `blur-background` 命中桌宠的全屏透明 overlay 窗） | picom blur 反噬 | `kill $(pgrep picom)` 看模糊是否立刻消失；`xprop -id <桌宠窗>` 看 WM_CLASS/窗类型（桌宠窗不在 `_NET_CLIENT_LIST` 时用 `xwininfo -root -tree` 找全屏那张） | picom.conf 加 `blur-background-exclude = [ "class_g = 'dsh-pet-standalone'" ];`（§2.3 警示块） |
| 没有托盘图标，只弹出「桌宠恢复」窗 | i3 无系统托盘 | `which trayer`；app 日志有 `isSystemTrayAvailable` 相关 | 装并自启 `trayer`（§2.4） |
| 托盘/通知/气泡失灵，或启动异常 | 缺 DBus session bus | `echo $DBUS_SESSION_BUS_ADDRESS` 为空 | `exec dbus-run-session -- i3`（§2.5） |
| 中文气泡/聊天显示成方块 | 缺 noto-cjk | `fc-list \| grep -i noto-cjk` | `apt install fonts-noto-cjk`（§2.7） |
| `pip install` 报 `CERTIFICATE_VERIFY_FAILED` | SSL CA 路径坏（本机特有） | 看 `ssl.get_default_verify_paths()` | `export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`（§1.1） |
| `start` 立即回 `已在运行` 但看不到窗 | 旧实例残留 / pid 文件陈旧 | `./petctl.sh status`；`ps -eo pid=,args=\| grep '\.venv/bin/python -m pet'` | `./petctl.sh stop` 清掉；仍残留则杀掉对应 pid 再启动 |
| 双击拖动不动 / 移动被忽略 | 误跑在原生 Wayland | `echo $WAYLAND_DISPLAY` | `export QT_QPA_PLATFORM=xcb`（app 也会自动设） |
| **鼠标点不中宠物 / 扩展屏后位置错乱**（旧代码 + i3；现行代码已内置修复，出现即检查代码回退） | i3 接管 overlay 窗 reparent 到错误原点，Qt 几何与真实全局坐标错位（§3 说明） | `xwininfo -root -tree`：overlay 窗若在 `i3-frame` 容器里、绝对坐标 ≠ 虚拟桌面原点即命中 | 升级到含 `BypassWindowManagerHint` 的版本（`pet/platform_linux.py`）；临时救场 `i3-msg '[class="^dsh-pet-standalone$"] floating enable, border none, move absolute position 0 0'` |
| 中文打不进去（候选窗不出现），但 fcitx5 在跑、三个 IM 环境变量都对 | Qt 的 fcitx5 插件 dlopen 失败（插件与进程内 Qt 的私有 ABI 版本不同），Qt 静默回退 compose 上下文 | `./petctl.sh setup-im` 报 broken 并打印 `undefined symbol: …QtPrivate_6_10_2`；`grep platforminputcontexts /proc/<pid>/maps` 只有 compose | 开发态：`sudo apt install qt6-base-private-dev && ./petctl.sh setup-im` 后 `./petctl.sh restart`（§2.6.1）；成品侧检查随包 `platforminputcontexts` 目录是否齐 |
| 在线功能（聊天/天气/歌词）失败 | 无外网 / 代理 | `curl -sS -o /dev/null -w "%{http_code}\n" https://pypi.org` | 设 `HTTPS_PROXY` 或直连 |

---

## 5. 关键诊断命令清单

```sh
# 会话 / 显示
echo "$XDG_SESSION_TYPE / $DISPLAY / $WAYLAND_DISPLAY"
xrandr >/dev/null 2>&1 && echo "X OK" || echo "NO X"
echo "$DBUS_SESSION_BUS_ADDRESS"

# Qt 平台插件依赖是否齐
ldd .venv/lib/python3.12/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"

# 插件选择过程（最详细）
QT_DEBUG_PLUGINS=1 QT_QPA_PLATFORM=xcb ./petctl.sh start

# 配套进程
pgrep -a picom ; which trayer && pgrep -a trayer ; pgrep -a fcitx5

# 输入法插件能不能加载（判据 = Qt 实测 dlopen，与启动诊断同源）
./petctl.sh setup-im

# 桌宠进程实际映射了哪个输入法插件（只剩 compose = fcitx 插件没加载上，中文必挂）
grep -o 'platforminputcontexts/[^ ]*' /proc/$(pgrep -f '\.venv/bin/python -m pet' | head -1)/maps | sort -u

# 启动状态与日志
./petctl.sh status
tail -n 50 .run/pet.log
tail -n 50 ~/.config/dsh-pet-standalone/pet-*.log

# 找桌宠窗的 WM_CLASS / 父子关系（排障用；现行代码下 overlay 应为 root 直属，见 §3）
xprop | grep -E "WM_CLASS|WM_NAME"
xwininfo -root -tree | grep -B1 dsh-pet-standalone

# 找本机残留的 pet 进程
ps -eo pid=,args= | grep '\.venv/bin/python -m pet' | grep -v grep

# SSL 路径（仅在怀疑 pip 报 CERTIFICATE_VERIFY_FAILED 时用）
.venv/bin/python -c "import ssl;print(ssl.get_default_verify_paths())"

# 验证依赖装全没
.venv/bin/python -c "import PySide6, PIL, numpy, onnxruntime, cv2, rapidocr, edge_tts, keyring; print('deps OK')"
```

---

## 6. 不要带过去的「本机专属」坑（提醒）

1. **OpenSSL 坏 CA 路径**（§1.1）：只在本机编译的 Python 上出现，干净 Ubuntu 26 不该有；别把 `SSL_CERT_FILE` 写死进通用脚本。
2. **本机 127.0.0.1:7897 代理**（§1.1）：仅本机联网用；迁移后按真实网络决定要不要设 `HTTPS_PROXY`。
3. **`DISPLAY=:1`**：本机测试用的显示号；新机器通常是 `:0`，别写死。

---

## 7. 关联文档

- 启动/停止脚本本身：`petctl.sh`（仓库根），用法见脚本头部与 `README.md` 的「测试与验证」「打包发布」。
- 平台行为基线：`README.md`、`.agents/skills/desktop-pet-ui-style/`、`.agents/skills/qt-ui-review/`。
- 打包/依赖口径：`docs/ONEDIR_PACKAGING.md`、`requirements.txt`（PySide6 `<6.12` 封顶与 Linux 引用泄漏说明）。
