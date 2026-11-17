#!/usr/bin/env bash
# db-guard.sh — MariaDB(9507) / Redis(6378) 访问控制
#   guard : 只放通白名单（本机 loopback 恒通），白名单由两部分合成：
#             /etc/db-guard.allow(.auto)  手动 + 面板服务器列表自动生成
#   open  : 完全放开（应急用）
# 用法：
#   db-guard.sh apply | status | guard | open | flush
#   db-guard.sh allow <IP|CIDR>    手动加白名单（写 .allow，不被自动同步覆盖）
#   db-guard.sh deny  <IP|CIDR>    移除手动白名单
#   db-guard.sh sync               按面板服务器列表重建自动白名单并应用
set -euo pipefail

PORTS="9507,6378"
ALLOW4="/etc/db-guard.allow"
ALLOW4_AUTO="/etc/db-guard.allow.auto"
ALLOW6="/etc/db-guard.allow6"
ALLOW6_AUTO="/etc/db-guard.allow6.auto"
MODEFILE="/etc/db-guard.mode"

mode() { cat "$MODEFILE" 2>/dev/null || echo guard; }

teardown() {
    for ipt in iptables ip6tables; do
        if "$ipt" -C INPUT -j DBGUARD 2>/dev/null; then
            "$ipt" -D INPUT -j DBGUARD
        fi
        "$ipt" -F DBGUARD 2>/dev/null || true
        "$ipt" -X DBGUARD 2>/dev/null || true
    done
}

# 把多个白名单文件合并输出（跳过空行与注释）
merged() {
    for f in "$@"; do
        if [ -f "$f" ]; then
            while IFS= read -r line || [ -n "$line" ]; do
                line="$(printf '%s' "$line" | tr -d '[:space:]')"
                [ -z "$line" ] && continue
                case "$line" in \#*) continue ;; esac
                printf '%s\n' "$line"
            done < "$f"
        fi
    done
}

ensure_chain() {
    local ipt="$1" loop="$2"; shift 2
    if "$ipt" -C INPUT -j DBGUARD 2>/dev/null; then
        "$ipt" -D INPUT -j DBGUARD
    fi
    "$ipt" -N DBGUARD 2>/dev/null || true
    "$ipt" -F DBGUARD
    "$ipt" -A DBGUARD -p tcp -m multiport --dports "$PORTS" -i lo -j RETURN
    "$ipt" -A DBGUARD -p tcp -m multiport --dports "$PORTS" -s "$loop" -j RETURN
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        "$ipt" -A DBGUARD -p tcp -m multiport --dports "$PORTS" -s "$line" -j RETURN
    done < <(merged "$@")
    "$ipt" -A DBGUARD -p tcp -m multiport --dports "$PORTS" -j DROP
    "$ipt" -A DBGUARD -j RETURN
    "$ipt" -I INPUT 1 -j DBGUARD
}

apply() {
    local m; m="$(mode)"
    if [ "$m" = "open" ]; then
        teardown
        echo "[db-guard] 模式=open：9507/6378 完全放开"
    else
        ensure_chain iptables 127.0.0.1 "$ALLOW4" "$ALLOW4_AUTO"
        ensure_chain ip6tables ::1 "$ALLOW6" "$ALLOW6_AUTO"
        echo "[db-guard] 模式=guard：仅白名单可访问 tcp/$PORTS"
    fi
}

status() {
    echo "当前模式: $(mode)"
    echo "=== iptables DBGUARD ==="
    iptables -S DBGUARD 2>/dev/null || echo "(无规则，说明处于 open 模式)"
    echo "=== 白名单 v4（手动）==="; cat "$ALLOW4" 2>/dev/null || echo "(空)"
    echo "=== 白名单 v4（服务器列表自动）==="; cat "$ALLOW4_AUTO" 2>/dev/null || echo "(空)"
    echo "=== 白名单 v6（手动）==="; cat "$ALLOW6" 2>/dev/null || echo "(空)"
    echo "=== 白名单 v6（自动）==="; cat "$ALLOW6_AUTO" 2>/dev/null || echo "(空)"
    echo "=== 监听 ==="
    ss -lntp 2>/dev/null | grep -E ':9507|:6378' || echo "(未监听)"
}

case "${1:-apply}" in
    apply) apply ;;
    status) status ;;
    open)  echo open > "$MODEFILE"; apply ;;
    guard) echo guard > "$MODEFILE"; apply ;;
    sync)
        set +e
        /usr/bin/python3 /usr/local/sbin/db-guard-sync.py
        rc=$?
        set -e
        if [ "$rc" -eq 0 ]; then
            apply
        elif [ "$rc" -eq 3 ]; then
            echo "[db-guard] 白名单与服务器列表一致，未改动（模式=$(mode)）"
        else
            echo "[db-guard] 同步失败，保持原有规则不动"
        fi
        ;;
    flush) teardown; echo "[db-guard] 规则已撤销（模式记录保留：$(mode)）" ;;
    allow)
        ip="${2:?用法: db-guard.sh allow <IP或CIDR>}"
        case "$ip" in
            *:*) touch "$ALLOW6"; grep -qxF "$ip" "$ALLOW6" || echo "$ip" >> "$ALLOW6" ;;
            *)   touch "$ALLOW4"; grep -qxF "$ip" "$ALLOW4" || echo "$ip" >> "$ALLOW4" ;;
        esac
        echo guard > "$MODEFILE"; apply; echo "[db-guard] 已放通 $ip" ;;
    deny)
        ip="${2:?用法: db-guard.sh deny <IP或CIDR>}"
        sed -i "\|^${ip}\$|d" "$ALLOW4" "$ALLOW6" 2>/dev/null || true
        apply; echo "[db-guard] 已移除 $ip" ;;
    *) echo "用法: db-guard.sh {apply|status|open|guard|sync|allow <ip>|deny <ip>|flush}"; exit 1 ;;
esac
