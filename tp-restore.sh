#!/usr/bin/env bash
#
# Trojan Panel 数据库恢复脚本   v1.1.0
# 只做一件事：把 tp-backup-*.tar.gz 里的 dump.sql.gz 导回 trojan_panel_db
# （证书与配置文件由安装/签发流程自动生成，不再从备份恢复）
#
# 用法: tp-restore.sh <备份包.tar.gz> [--dry-run] [--flush-redis] [-y]
#
set -euo pipefail

ARCHIVE=""
ASSUME_YES=0
FLUSH_REDIS=0
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: tp-restore.sh <备份包.tar.gz> [选项]
  --dry-run      只检查备份包与打印计划，不做任何修改
  --flush-redis  恢复后清空 Redis（所有登录态失效）
  -y, --yes      跳过交互确认
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY_RUN=1; shift ;;
    --flush-redis) FLUSH_REDIS=1; shift ;;
    -y|--yes)      ASSUME_YES=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    -*)            echo "未知参数: $1"; usage; exit 2 ;;
    *)             ARCHIVE="$1"; shift ;;
  esac
done

[ -n "$ARCHIVE" ] || { usage; exit 2; }
[ -f "$ARCHIVE" ] || { echo "备份包不存在: $ARCHIVE"; exit 2; }

# ---- 环境参数（按本机实际部署固定） ----
DB_CONT="trojan-panel-mariadb"
DB_HOST="127.0.0.1"
DB_PORT="9507"
DB_USER="root"
DB_PASS="83288328"
DB_NAME="trojan_panel_db"
REDIS_CONT="trojan-panel-redis"
REDIS_PORT="6378"
REDIS_PASS="83288328"
UI_CONT="trojan-panel-ui"
STACK=(trojan-panel-core trojan-panel)   # 导入时先停，避免写入冲突

TS="$(date +%Y%m%d-%H%M%S)"
WORK="/tmp/tp-restore-$TS"
ROLLBACK="/tpdata/restore-rollback/$TS"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*"; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$DB_CONT" || die "数据库容器 $DB_CONT 未运行"

say "备份包 : $ARCHIVE"

# ---- 1. 解包与自检 ----
mkdir -p "$WORK"
tar xzf "$ARCHIVE" -C "$WORK" 2>/dev/null || die "备份包解压失败"
[ -f "$WORK/dump.sql.gz" ] || die "备份包内缺少 dump.sql.gz"
gzip -t "$WORK/dump.sql.gz" || die "dump.sql.gz 校验失败（文件可能损坏）"
TABLES=$(zcat "$WORK/dump.sql.gz" | grep -c '^CREATE TABLE' || true)
[ "$TABLES" -gt 0 ] || die "dump.sql.gz 里没有任何建表语句"
say "SQL 转储: $(du -h "$WORK/dump.sql.gz" | cut -f1)，含 $TABLES 张表"

if [ "$DRY_RUN" -eq 1 ]; then
  say "dry-run：备份包与 SQL 均正常，未修改任何状态"
  rm -rf "$WORK"
  exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
  printf '将用该备份覆盖当前数据库（会先自动导出当前库做回滚）。继续？[y/N] '
  read -r ans
  case "$ans" in y|Y|yes|YES) ;; *) die "已取消" ;; esac
fi

# ---- 2. 当前库快照 ----
mkdir -p "$ROLLBACK"
say "当前数据库快照 -> $ROLLBACK/current-$DB_NAME.sql.gz"
docker exec "$DB_CONT" mysqldump -u"$DB_USER" -p"$DB_PASS" -P"$DB_PORT" -h"$DB_HOST" \
  --single-transaction --quick --databases "$DB_NAME" | gzip > "$ROLLBACK/current-$DB_NAME.sql.gz"
[ -s "$ROLLBACK/current-$DB_NAME.sql.gz" ] || die "快照生成失败，中止"

# ---- 3. 停业务容器 ----
for c in "${STACK[@]}"; do
  if docker ps --format '{{.Names}}' | grep -qx "$c"; then
    say "停止 $c"
    docker stop "$c" >/dev/null
  fi
done

# ---- 4. 导入 ----
say "导入数据库 $DB_NAME ..."
docker exec -i "$DB_CONT" mysql -u"$DB_USER" -p"$DB_PASS" -P"$DB_PORT" -h"$DB_HOST" "$DB_NAME" < <(zcat "$WORK/dump.sql.gz")
NEWTABLES=$(docker exec "$DB_CONT" mysql -u"$DB_USER" -p"$DB_PASS" -P"$DB_PORT" -h"$DB_HOST" -N \
  -e "select count(*) from information_schema.tables where table_schema='$DB_NAME';" 2>/dev/null)
say "导入完成，当前表数: $NEWTABLES"

# ---- 5. Redis（可选） ----
if [ "$FLUSH_REDIS" -eq 1 ] && docker ps --format '{{.Names}}' | grep -qx "$REDIS_CONT"; then
  say "清空 Redis（登录态全部失效）"
  docker exec "$REDIS_CONT" redis-cli -p "$REDIS_PORT" -a "$REDIS_PASS" flushall >/dev/null 2>&1 || warn "redis flush 失败"
fi

# ---- 6. 启动与健康检查 ----
say "启动业务容器 ..."
for c in "${STACK[@]}"; do docker start "$c" >/dev/null 2>&1 || true; done
OK=0
for i in $(seq 1 30); do
  CODE=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:8888/api/auth/setting || true)
  if [ "$CODE" = "200" ]; then OK=1; break; fi
  sleep 1
done
if [ "$OK" = "1" ]; then say "面板接口正常 (HTTP 200)"; else warn "30 秒内未就绪，查看: docker logs --tail 50 trojan-panel"; fi

echo
say "恢复完成（仅数据库）"
docker ps --format '  {{.Names}}\t{{.Status}}' | grep -E 'trojan-panel' || true
echo
echo "回滚到恢复前状态:"
echo "  gunzip -c $ROLLBACK/current-$DB_NAME.sql.gz | docker exec -i $DB_CONT mysql -u$DB_USER -p$DB_PASS -P$DB_PORT -h$DB_HOST"
rm -rf "$WORK"
