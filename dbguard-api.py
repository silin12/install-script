#!/usr/bin/env python3
# dbguard-api.py — 给面板 UI 提供「通讯防火墙」开关的本地接口
# 仅监听 127.0.0.1:8899，由面板 nginx 以 /dbguard/ 反向代理暴露
# 鉴权：复用面板后端 JWT（转发 Authorization 到 127.0.0.1:8081 校验）
import json
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = ("127.0.0.1", 8899)
BACKEND_CHECK = "http://127.0.0.1:8081/api/account/getAccountInfo"
GUARD = "/usr/local/sbin/db-guard.sh"
MODEFILE = "/etc/db-guard.mode"
ALLOW4 = "/etc/db-guard.allow"
ALLOW4_AUTO = "/etc/db-guard.allow.auto"
ALLOW6 = "/etc/db-guard.allow6"
ALLOW6_AUTO = "/etc/db-guard.allow6.auto"
PORTS = ("9507", "6378")


def mode():
    try:
        m = open(MODEFILE).read().strip()
    except Exception:
        m = "guard"
    return m if m in ("open", "guard") else "guard"


def allow_list(path):
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8")]
    except Exception:
        return []
    return [l for l in lines if l and not l.startswith("#")]


def merged_allow(path, auto_path):
    """手动白名单与自动同步白名单合并（自动项来自面板服务器列表）"""
    out, seen = [], set()
    for item in allow_list(path) + allow_list(auto_path):
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def guard_active():
    try:
        out = subprocess.run(["iptables", "-S", "DBGUARD"], capture_output=True, timeout=5)
        return out.returncode == 0
    except Exception:
        return False


def listening():
    res = {}
    try:
        out = subprocess.run(["ss", "-lntp"], capture_output=True, timeout=5).stdout.decode()
    except Exception:
        return res
    for p in PORTS:
        res[p] = any(":" + p in line for line in out.splitlines())
    return res


def run_guard(*args):
    p = subprocess.run([GUARD] + list(args), capture_output=True, timeout=20)
    return p.returncode == 0, (p.stdout or b"").decode() + (p.stderr or b"").decode()


def panel_authed(auth_header):
    """校验面板登录态：面板认的是裸 token（不带 Bearer），成功码是 20000"""
    tok = (auth_header or "").strip()
    if tok[:7].lower() == "bearer ":
        tok = tok[7:].strip()
    if not tok:
        return False
    try:
        req = urllib.request.Request(BACKEND_CHECK, headers={"Authorization": tok})
        with urllib.request.urlopen(req, timeout=8) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        return int(body.get("code", 0)) in (200, 20000)
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "dbguard/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if panel_authed(self.headers.get("Authorization", "")):
            return True
        self._send(401, {"code": 401, "msg": "未登录或登录已过期"})
        return False

    def _status(self):
        self._send(200, {"code": 200, "data": {
            "mode": mode(),
            "guardActive": guard_active(),
            "allow4": merged_allow(ALLOW4, ALLOW4_AUTO),
            "allow6": merged_allow(ALLOW6, ALLOW6_AUTO),
            "listening": listening(),
            "ports": list(PORTS),
        }})

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/status", ""):
            if not self._auth():
                return
            return self._status()
        self._send(404, {"code": 404, "msg": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self._auth():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return self._send(400, {"code": 400, "msg": "请求体不是合法 JSON"})

        if path == "/mode":
            m = data.get("mode")
            if m not in ("open", "guard"):
                return self._send(400, {"code": 400, "msg": "mode 只能是 open 或 guard"})
            ok, out = run_guard(m)
            if not ok:
                return self._send(500, {"code": 500, "msg": "执行失败", "detail": out[-500:]})
            return self._status()

        if path == "/sync":
            ok, out = run_guard("sync")
            if not ok:
                return self._send(500, {"code": 500, "msg": "同步失败", "detail": out[-500:]})
            return self._status()

        if path in ("/allow", "/deny"):
            ip = (data.get("ip") or "").strip()
            if not ip or len(ip) > 64 or any(c in ip for c in " ;|&$`'\"\\"):
                return self._send(400, {"code": 400, "msg": "IP 或 CIDR 格式不合法"})
            ok, out = run_guard(path[1:], ip)
            if not ok:
                return self._send(500, {"code": 500, "msg": "执行失败", "detail": out[-500:]})
            return self._status()

        self._send(404, {"code": 404, "msg": "not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(BIND, Handler).serve_forever()
