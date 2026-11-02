# Ubuntu 桌宠环境配置与启动排障手册

> **基线（已实测）**：Ubuntu 24.04.4 LTS（Noble），X11 会话，PySide6 6.11.3。
> **目标（待迁移）**：Ubuntu 26 + **i3** 窗口管理器（纯 X11，无合成器、无系统托盘）。
> 本文只记录「把项目跑起来」所需的环境与排障，不动项目代码。所有路径以仓库根 `dsh-pet-indesktop/` 为基准。

---

## 0. 一句话结论（i3 三大坑）

i3 默认 **没有合成器**（透明窗会发黑/不透明）和 **没有系统托盘**（托盘图标消失，只剩「桌宠恢复」窗）。
因此迁移到 i3 **必须**额外装两类东西：

1. **compositor**：`picom`（让透明无边框的桌宠窗正常显示）。
2. **托盘宿主**：`trayer` / `stalonetray` / `polybar`（否则 `QSystemTrayIcon.isSystemTrayAvailable()` 为 false，app 走恢复窗分支）。

再加一组 **Qt6 的 X11 系统库**（PySide6 wheel 不带这些），缺了会直接 `Could not load the Qt platform plugin "xcb"` 崩在启动最前面。

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

# 让桌宠的 overlay 窗不被 i3 平铺（WM_CLASS 用 xprop 实测后填，见 §5）
for_window [instance="pet"] floating enable
for_window [class="pet"] floating enable
```

---

## 4. 排障速查表

| 现象 | 根因 | 命令 / 验证 | 修复 |
|---|---|---|---|
| 启动即崩，`Could not load the Qt platform plugin "xcb"` | 缺 Qt 的 X11 系统库 | `ldd .../plugins/platforms/libqxcb.so \| grep "not found"` | 装 §2.2 的 apt 包 |
| 启动即崩，`DISPLAY` 相关 / 无窗口 | 没有 X 会话 | `echo $DISPLAY`；`xrandr` | 登录进 i3，或 `Xvfb :1 ...; export DISPLAY=:1` |
| 桌宠窗背景全黑 / 不透明 / 拖影 | i3 无合成器 | `pgrep -a picom` | 装并自启 `picom`（§2.3） |
| 没有托盘图标，只弹出「桌宠恢复」窗 | i3 无系统托盘 | `which trayer`；app 日志有 `isSystemTrayAvailable` 相关 | 装并自启 `trayer`（§2.4） |
| 托盘/通知/气泡失灵，或启动异常 | 缺 DBus session bus | `echo $DBUS_SESSION_BUS_ADDRESS` 为空 | `exec dbus-run-session -- i3`（§2.5） |
| 中文气泡/聊天显示成方块 | 缺 noto-cjk | `fc-list \| grep -i noto-cjk` | `apt install fonts-noto-cjk`（§2.7） |
| `pip install` 报 `CERTIFICATE_VERIFY_FAILED` | SSL CA 路径坏（本机特有） | 看 `ssl.get_default_verify_paths()` | `export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`（§1.1） |
| `start` 立即回 `已在运行` 但看不到窗 | 旧实例残留 / pid 文件陈旧 | `./petctl.sh status`；`ps -eo pid=,args=\| grep '\.venv/bin/python -m pet'` | `./petctl.sh stop` 清掉；仍残留则杀掉对应 pid 再启动 |
| 双击拖动不动 / 移动被忽略 | 误跑在原生 Wayland | `echo $WAYLAND_DISPLAY` | `export QT_QPA_PLATFORM=xcb`（app 也会自动设） |
| 窗被 i3 平铺占半屏 | i3 默认平铺 overlay 窗 | `xprop \| grep WM_CLASS` 找类名 | 加 §3 的 `for_window ... floating enable` |
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

# 启动状态与日志
./petctl.sh status
tail -n 50 .run/pet.log
tail -n 50 ~/.config/dsh-pet-standalone/pet-*.log

# 找桌宠窗的 WM_CLASS（用于 i3 浮动规则）
xprop | grep -E "WM_CLASS|WM_NAME"

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
