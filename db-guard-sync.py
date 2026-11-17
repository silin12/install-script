#!/usr/bin/env python3
# db-guard-sync.py — 把面板「服务器列表」里的 IP 同步成 DB 白名单
# 数据源：trojan_panel_db.node_server.ip + trojan_panel_db.node.node_server_ip
# 输出：/etc/db-guard.allow.auto（v4）、/etc/db-guard.allow6.auto（v6）
# 退出码：0=有变化需要 apply  3=无变化（调用方可跳过重建）  1=失败（保持现状）
# 安全：查询失败或结果为空时不动现有文件，绝不把白名单清空
import ipaddress
import os
import subprocess
import sys
import time

DB_CONTAINER = "trojan-panel-mariadb"
DB_USER = "root"
DB_PASS = "05320532"
DB_NAME = "trojan_panel_db"
OUT4 = "/etc/db-guard.allow.auto"
OUT6 = "/etc/db-guard.allow6.auto"
LOG = "/var/log/db-guard-sync.log"

SQL = (
    "select distinct ip from {db}.node_server where ip is not null and ip<>'';"
    "select distinct node_server_ip from {db}.node where node_server_ip is not null and node_server_ip<>'';"
).format(db=DB_NAME)


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG) > 512 * 1024:
            with open(LOG, encoding="utf-8") as f:
                tail = f.readlines()[-2000:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass
    print(line)


def query_ips():
    cmd = ["docker", "exec", DB_CONTAINER, "mysql", "-u" + DB_USER, "-p" + DB_PASS,
           "-N", "-B", "-e", SQL]
    p = subprocess.run(cmd, capture_output=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace")[:300])
    return p.stdout.decode("utf-8", "replace")


def current(path):
    try:
        with open(path, encoding="utf-8") as f:
            items = set()
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    items.add(s)
            return items
    except FileNotFoundError:
        return None


def write(path, items):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# 由 db-guard-sync.py 自动生成，勿手改（手动项写到 db-guard.allow）\n")
        for it in sorted(items):
            f.write(it + "\n")
    os.replace(tmp, path)


def main():
    try:
        raw = query_ips()
    except Exception as e:
        log("FAIL 查询数据库出错，保持现有白名单不动: " + str(e))
        return 1

    v4, v6, skipped = set(), set(), []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            net = ipaddress.ip_network(s, strict=False)
        except ValueError:
            skipped.append(s)
            continue
        if net.prefixlen == net.max_prefixlen:
            (v4 if net.version == 4 else v6).add(str(net.network_address))
        else:
            (v4 if net.version == 4 else v6).add(str(net))

    for s in skipped:
        log("SKIP 服务器列表中的非 IP 值（域名/别名不会被放通）: " + s)

    if not v4 and not v6:
        log("FAIL 服务器列表解析出 0 条地址，保持现有白名单不动（避免误锁）")
        return 1

    old4, old6 = current(OUT4), current(OUT6)
    if old4 == v4 and old6 == v6:
        return 3

    write(OUT4, v4)
    write(OUT6, v6)
    log("UPDATE v4=%d v6=%d -> %s" % (len(v4), len(v6), ",".join(sorted(v4)) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
