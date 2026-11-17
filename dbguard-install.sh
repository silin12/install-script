#!/usr/bin/env bash
# dbguard-install.sh — 一键部署/修复「数据库端口白名单 + 面板通讯防火墙」套件
#
# 幂等，可重复执行：
#   1. 从本仓库下载 3 个程序文件到 /usr/local/sbin（db-guard.sh / db-guard-sync.py / dbguard-api.py）
#   2. 写入 4 个 systemd 单元并启用
#   3. 给面板前端 nginx 配置补 /dbguard/ 反向代理（已存在则跳过）
#   4. 恢复 guard 模式并立即同步一次白名单
#
# 用法：bash dbguard-install.sh
# 换源：REPO=https://raw.githubusercontent.com/silin12/install-script/main bash dbguard-install.sh
set -e
[ "$(id -u)" = "0" ] || { echo "请用 root 执行"; exit 1; }

REPO="${REPO:-https://raw.githubusercontent.com/silin12/install-script/main}"
NGINX_CONF=/tpdata/trojan-panel-ui/nginx/default.conf
UI_CONTAINER=trojan-panel-ui

echo "==> 1/4 下载程序文件"
for f in db-guard.sh db-guard-sync.py dbguard-api.py; do
  curl -fsSL --max-time 30 "$REPO/$f" -o "/usr/local/sbin/$f"
  chmod +x "/usr/local/sbin/$f"
  echo "    /usr/local/sbin/$f"
done

echo "==> 2/4 写入 systemd 单元"
cat > /etc/systemd/system/db-guard.service <<'EOF'
[Unit]
Description=Restrict MariaDB/Redis access to whitelist (ports 9507/6378)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/db-guard.sh apply
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/db-guard-sync.service <<'EOF'
[Unit]
Description=Sync DB whitelist from panel server list
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/db-guard.sh sync
EOF

cat > /etc/systemd/system/db-guard-sync.timer <<'EOF'
[Unit]
Description=Periodic DB whitelist sync (panel server list)

[Timer]
OnBootSec=90
OnUnitActiveSec=30
AccuracySec=5

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/dbguard-api.service <<'EOF'
[Unit]
Description=DB Guard API for panel UI (127.0.0.1:8899)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/sbin/dbguard-api.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable db-guard.service dbguard-api.service db-guard-sync.timer >/dev/null 2>&1 || true
systemctl restart db-guard.service dbguard-api.service
systemctl start db-guard-sync.timer

echo "==> 3/4 检查面板 nginx 的 /dbguard/ 反向代理"
if [ ! -f "$NGINX_CONF" ]; then
  echo "    未找到 $NGINX_CONF，跳过（面板装好后可再跑一次本脚本）"
elif grep -q 'location /dbguard/' "$NGINX_CONF"; then
  echo "    已存在，跳过"
else
  cp "$NGINX_CONF" "${NGINX_CONF}.bak-dbguard-$(date +%Y%m%d%H%M%S)"
  python3 - "$NGINX_CONF" <<'PY'
import sys
p = sys.argv[1]
src = open(p, encoding='utf-8').read()
block = '''    location /dbguard/ {
        proxy_pass http://127.0.0.1:8899/;
        proxy_read_timeout 30s;
    }

'''
anchor = 'error_page   500 502 503 504'
if anchor in src:
    src = src.replace(anchor, block + anchor, 1)
else:
    src = src.replace('    location / {', block + '    location / {', 1)
open(p, 'w', encoding='utf-8').write(src)
print('    已写入 /dbguard/ 反向代理')
PY
  docker exec "$UI_CONTAINER" nginx -s reload 2>/dev/null || true
fi

echo "==> 4/4 恢复 guard 模式并同步一次白名单"
/usr/local/sbin/db-guard.sh guard
/usr/local/sbin/db-guard.sh sync || /usr/local/sbin/db-guard.sh apply

echo
echo "==> 完成，自检如下："
/usr/local/sbin/db-guard.sh status
