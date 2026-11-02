#!/usr/bin/env bash
# ============================================================
#  dsh-pet 开发用启停脚本（Linux / X11）
#
#    ./petctl.sh install        创建 pyenv 虚拟环境并安装依赖
#    ./petctl.sh start          后台启动桌宠
#    ./petctl.sh stop           停止桌宠（含设置页 / OCR 子进程）
#    ./petctl.sh restart        重启
#    ./petctl.sh status         查看运行状态
#    ./petctl.sh log [-f]       查看启动日志（-f 跟随）
#    ./petctl.sh setup-im       为开发态 venv 构建 ABI 匹配的 Fcitx5 输入法插件
#                               （修复“输入框打不进中文”，见 docs/UBUNTU-I3 手册 §2.6）
#
#  可用环境变量覆盖默认值：
#    PET_PY_VERSION   pyenv 里的版本号（默认 3.12.12，需已 pyenv install）
#    PET_VENV         虚拟环境目录（默认 <仓库>/.venv）
#    PET_LOG          日志文件（默认 <仓库>/.run/pet.log）
#    PET_STOP_TIMEOUT 优雅退出等待秒数（默认 10）
#
#  额外参数会原样传给 `python -m pet`，例如：./petctl.sh start --slot 1
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${PET_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
RUN_DIR="$ROOT/.run"
PID_FILE="$RUN_DIR/pet.pid"
LOG_FILE="${PET_LOG:-$RUN_DIR/pet.log}"
PY_VERSION="${PET_PY_VERSION:-3.12.12}"
STOP_TIMEOUT="${PET_STOP_TIMEOUT:-10}"
# 进程匹配串：同一 venv 的 python 跑 -m pet（含 --settings / --ocr-worker 子进程）
NEEDLE="$VENV/bin/python -m pet"

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

# 本机 Python/OpenSSL 编译期钉的默认 CA 路径指向不存在的目录
# （ssl.get_default_verify_paths() 报 /home/test/...，pip 走 HTTPS 会
# CERTIFICATE_VERIFY_FAILED），有系统 CA 包时显式指过去。
if [ -z "${SSL_CERT_FILE:-}" ] && [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
    export SSL_CERT_DIR=/etc/ssl/certs
fi

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

read_pid() {
    [ -f "$PID_FILE" ] || return 0
    tr -dc '0-9' <"$PID_FILE"
}

# 脚本外启动的同类进程（例如旧的终端里手动跑的 python -m pet）。
# 只认「命令行以 <venv>/bin/python -m pet 开头」的进程：单纯包含这段文字的
# shell / grep / 编辑器不算，避免误判、误杀。
find_stray_pids() {
    ps -eo pid=,args= 2>/dev/null |
        awk -v needle="$NEEDLE" '{ pid = $1; sub(/^[^ ]+[ ]+/, ""); if (index($0, needle) == 1) print pid }'
}

# ---------- Python 环境 ----------

resolve_pyenv_python() {
    local root="${PYENV_ROOT:-$HOME/.pyenv}" direct resolved
    direct="$root/versions/$PY_VERSION/bin/python"
    if [ -x "$direct" ]; then
        printf '%s\n' "$direct"
        return 0
    fi
    if command -v pyenv >/dev/null 2>&1; then
        resolved="$(PYENV_VERSION="$PY_VERSION" pyenv which python 2>/dev/null || true)"
        if [ -n "$resolved" ] && [ -x "$resolved" ]; then
            printf '%s\n' "$resolved"
            return 0
        fi
    fi
    return 1
}

ensure_venv() {
    if [ -x "$PY" ] && "$PY" -c 'import sys' >/dev/null 2>&1; then
        return 0
    fi
    local base
    base="$(resolve_pyenv_python)" || die "pyenv 里没有 Python $PY_VERSION（先 pyenv install $PY_VERSION，或用 PET_PY_VERSION=已有版本 ./petctl.sh install）"
    printf '创建虚拟环境 %s（Python %s）\n' "$VENV" "$PY_VERSION"
    "$base" -m venv "$VENV" || die "创建虚拟环境失败"
}

deps_ready() { "$PY" -c 'import PySide6' >/dev/null 2>&1; }

install_deps() {
    ensure_venv
    printf '安装依赖：%s\n' "$ROOT/requirements.txt"
    "$PY" -m pip install --upgrade pip >/dev/null
    "$PY" -m pip install -r "$ROOT/requirements.txt" || die "依赖安装失败"
    printf '环境就绪：%s\n' "$("$PY" -c 'import sys, PySide6; print("Python", sys.version.split()[0], "/ PySide6", PySide6.__version__)')"
}

# ---------- 生命周期 ----------

cmd_start() {
    local pid stray
    pid="$(read_pid)"
    if pid_alive "$pid"; then
        printf '已在运行：pid=%s\n' "$pid"
        return 0
    fi
    stray="$(find_stray_pids | head -n1)"
    if [ -n "$stray" ]; then
        printf '已在运行：pid=%s（不是本脚本记录的进程）\n' "$stray"
        return 0
    fi

    ensure_venv
    deps_ready || die "依赖未安装，先执行：./petctl.sh install"

    mkdir -p "$RUN_DIR" || die "无法创建 $RUN_DIR"
    printf '\n===== %s 启动 =====\n' "$(date '+%F %T')" >>"$LOG_FILE"
    cd "$ROOT" || die "无法进入 $ROOT"
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$PY" -m pet "$@" >>"$LOG_FILE" 2>&1 </dev/null &
    else
        nohup "$PY" -m pet "$@" >>"$LOG_FILE" 2>&1 </dev/null &
    fi
    pid=$!
    printf '%s\n' "$pid" >"$PID_FILE"

    sleep 2
    if pid_alive "$pid"; then
        printf '已启动：pid=%s\n日志：%s\n' "$pid" "$LOG_FILE"
        return 0
    fi
    rm -f "$PID_FILE"
    printf '启动失败，日志末尾：\n' >&2
    tail -n 25 "$LOG_FILE" >&2
    return 1
}

kill_one() {
    local sig="$1" pid="$2" pgid pgid_self
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    pgid_self="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"
    # 只有「自己是进程组长且不是本脚本所在组」时才整组发信号，
    # 否则误杀会把当前终端里的其它进程一起带走。
    if [ -n "$pgid" ] && [ "$pgid" = "$pid" ] && [ "$pgid" != "$pgid_self" ]; then
        kill -"$sig" -- "-$pgid" 2>/dev/null || kill -"$sig" "$pid" 2>/dev/null
    else
        kill -"$sig" "$pid" 2>/dev/null
    fi
}

cmd_stop() {
    local pid p listed=() targets=()
    pid="$(read_pid)"
    pid_alive "$pid" && targets+=("$pid")
    while read -r p; do
        [ -n "$p" ] && targets+=("$p")
    done < <(find_stray_pids)

    if [ ${#targets[@]} -eq 0 ]; then
        rm -f "$PID_FILE"
        printf '未在运行\n'
        return 0
    fi

    printf '停止中（%s）…\n' "${targets[*]}"
    for p in "${targets[@]}"; do
        listed+=("$p")
        kill_one TERM "$p"
    done

    local waited=0
    while [ "$waited" -lt "$((STOP_TIMEOUT * 5))" ]; do
        local alive=0
        for p in "${listed[@]}"; do
            pid_alive "$p" && alive=1
        done
        [ "$alive" -eq 0 ] && break
        sleep 0.2
        waited=$((waited + 1))
    done

    for p in "${listed[@]}"; do
        if pid_alive "$p"; then
            printf '进程 %s 未响应 SIGTERM，强制结束\n' "$p"
            kill_one KILL "$p"
        fi
    done
    sleep 0.3

    rm -f "$PID_FILE"
    printf '已停止\n'
}

cmd_status() {
    local pid stray
    pid="$(read_pid)"
    if pid_alive "$pid"; then
        printf '运行中：pid=%s\n' "$pid"
        ps -o pid=,etime=,rss=,args= -p "$pid" | sed 's/^/  /'
        stray="$(find_stray_pids | grep -vx "$pid" | tr '\n' ' ')"
        [ -n "${stray// /}" ] && printf '  其它相关进程：%s\n' "$stray"
        return 0
    fi
    if [ -f "$PID_FILE" ]; then
        rm -f "$PID_FILE"
    fi
    stray="$(find_stray_pids | tr '\n' ' ')"
    if [ -n "${stray// /}" ]; then
        printf '运行中（非脚本记录进程）：%s\n' "$stray"
        return 0
    fi
    printf '未运行\n'
    return 0
}

cmd_log() {
    [ -f "$LOG_FILE" ] || { printf '暂无日志（%s）\n' "$LOG_FILE"; return 0; }
    if [ "${1:-}" = "-f" ]; then
        tail -n 100 -f "$LOG_FILE"
    else
        tail -n 100 "$LOG_FILE"
    fi
}

# ---------- 输入法插件（开发态） ----------

# 用项目自己的探测函数（与桌宠启动诊断同一判据：QLibrary 实测加载）报告现状，
# 输出「状态<TAB>详情」：ok / missing（一个候选都没有）/ broken（全都 dlopen 失败）。
im_status() {
    ( cd "$ROOT" && "$PY" - "${1:-fcitx}" <<'PY'
import sys

from pet import platform_linux as pl

r = pl.probe_input_method_plugin(sys.argv[1])
if r["loadable"]:
    print("ok", r["loadable"], sep="\t")
elif not r["candidates"]:
    print("missing", "已搜索: " + ", ".join(str(d) for d in pl.input_method_plugin_search_dirs()), sep="\t")
else:
    print("broken", "; ".join(f"{p} -> {e}" for p, e in r["errors"].items()), sep="\t")
PY
    )
}

cmd_setup_im() {
    local module="${1:-fcitx}" plugin_dir target verdict status detail
    # 依赖清单与成品构建同口径（.github/workflows/build-linux.yml 的 apt 行）：
    # fcitx5-qt 的 CMake 要 ECM + Qt6 私有 QPA 头 + xcb/xkb 开发头。
    local apt_hint="sudo apt install -y cmake extra-cmake-modules qt6-base-private-dev libxkbcommon-dev libxcb1-dev libx11-dev git perl"
    deps_ready || die "依赖未安装，先执行：./petctl.sh install"

    plugin_dir="$("$PY" -c 'import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).resolve().parent / "Qt" / "plugins" / "platforminputcontexts")')" \
        || die "无法定位 venv 内的 PySide6 插件目录"
    target="$plugin_dir/libfcitx5platforminputcontextplugin.so"

    verdict="$(im_status "$module")" || die "输入法探测失败（venv 里的 pet 包或 PySide6 不可用？）"
    status="${verdict%%$'\t'*}"; detail="${verdict#*$'\t'}"
    printf '现状（模块键 %s）：%s\n' "$module" "$status"
    [ -n "$detail" ] && printf '  %s\n' "$detail"
    if [ "$status" = "ok" ]; then
        printf '已存在可加载的插件，无需构建。\n'
        return 0
    fi
    if [ "${XMODIFIERS:-}" != "@im=fcitx" ]; then
        printf '提示：当前会话 XMODIFIERS=%s 未声明 fcitx，桌宠不会选择 fcitx 输入法前端。\n' "${XMODIFIERS:-（未设置）}"
        printf '      请先启动 fcitx5 并在桌面环境里把输入法切到 fcitx，再执行本命令。\n'
    fi

    for tool in cmake git perl; do
        command -v "$tool" >/dev/null 2>&1 || die "缺少构建工具 $tool：$apt_hint"
    done
    if [ -z "$(find /usr/include -type f -path '*/QtGui/*/QtGui/qpa/qplatforminputcontext.h' -print -quit 2>/dev/null)" ]; then
        die "缺少 Qt6 私有开发头文件：$apt_hint"
    fi
    if [ -z "$(find /usr/share/ECM /usr/lib -maxdepth 6 -name 'ECMConfig.cmake' -print -quit 2>/dev/null)" ]; then
        die "缺少 Extra-CMake-Modules（fcitx5-qt 的 CMake 配置需要）：$apt_hint"
    fi

    printf '构建中（首次会 clone qtbase + fcitx5-qt，需要外网，约数分钟）…\n'
    "$ROOT/scripts/build_pyside6_fcitx_plugin.sh" "$PY" "$target" || die "插件构建失败（见上方输出）"

    verdict="$(im_status "$module")" || die "构建后探测失败"
    status="${verdict%%$'\t'*}"; detail="${verdict#*$'\t'}"
    if [ "$status" != "ok" ]; then
        printf '插件已生成但 Qt 仍无法加载（%s）：%s\n' "$target" "$detail" >&2
        return 1
    fi
    printf '已就绪：%s\n重启桌宠生效：./petctl.sh restart\n' "$detail"
}

usage() {
    sed -n '5,20p' "$0" | sed 's/^#  \{0,1\}//' | grep -v '^#$'
}

action="${1:-}"
shift || true
case "$action" in
    install) install_deps ;;
    start) cmd_start "$@" ;;
    stop) cmd_stop ;;
    restart) cmd_stop && cmd_start "$@" ;;
    status) cmd_status ;;
    log) cmd_log "${1:-}" ;;
    setup-im) cmd_setup_im "${1:-}" ;;
    ""|-h|--help|help) usage ;;
    *) usage; exit 2 ;;
esac
