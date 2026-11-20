#!/usr/bin/env python3
# tpbackup-api.py — 面板「定时备份」本地接口（v3.1：仅备份数据库）
# 监听 127.0.0.1:8898，由面板 nginx 以 /backup/ 反向代理暴露
# 鉴权：复用面板后端 JWT（转发 Authorization 到 127.0.0.1:8081 校验）
import datetime
import email.message
import gzip
import io
import json
import os
import re
import secrets
import smtplib
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = ("127.0.0.1", 8898)
BACKEND_CHECK = "http://127.0.0.1:8081/api/account/getAccountInfo"
CONF_DIR = "/tpdata/backup"
CONF_FILE = os.path.join(CONF_DIR, "config.json")
DAILY_DIR = os.path.join(CONF_DIR, "daily")
PANEL_INI = "/tpdata/trojan-panel/config/config.ini"
MARIADB_CONTAINER = "trojan-panel-mariadb"
DB_NAME = "trojan_panel_db"

# 只备份数据库：证书/配置在重装或签发时都会自动重建，不纳入备份
BACKUP_PATHS = []

DEFAULT_CFG = {
    "enabled": True,
    "schedule": "03:30",
    "retention": 2,
    "emailEnabled": False,
    "smtpHost": "",
    "smtpPort": 465,
    "smtpUser": "",
    "smtpPass": "",
    "smtpTo": "",
    "davEnabled": False,
    "davUrl": "",
    "davUser": "",
    "davPass": "",
    "davDir": "tp-backup",
    "lastRun": "",
    "lastResult": "",
    "lastMsg": "",
    "lastFile": "",
    "pullToken": "",
    "pullEnabled": True,
}

_lock = threading.Lock()


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
    tmp = CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF_FILE)


def db_password():
    try:
        with open(PANEL_INI, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        m = re.search(r"^\s*password\s*=\s*(.+)$", txt, re.M)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def pull_token(cfg=None):
    """拉取令牌；首次调用自动生成并落盘"""
    if cfg is None:
        cfg = load_cfg()
    tok = cfg.get("pullToken") or ""
    if not tok:
        tok = secrets.token_urlsafe(32)
        cfg["pullToken"] = tok
        save_cfg(cfg)
    return tok


def public_cfg(cfg):
    out = {k: v for k, v in cfg.items() if k not in ("smtpPass", "davPass")}
    out["pullEnabled"] = bool(cfg.get("pullEnabled", True))
    out["hasSmtpPass"] = bool(cfg.get("smtpPass"))
    out["hasDavPass"] = bool(cfg.get("davPass"))
    return out


def make_dump():
    pw = db_password()
    if not pw:
        raise RuntimeError("读取数据库密码失败，请检查 %s" % PANEL_INI)
    p = subprocess.run([
        "docker", "exec", MARIADB_CONTAINER, "sh", "-c",
        "mysqldump -uroot -p'%s' --single-transaction --quick --routines --events %s | gzip -9"
        % (pw, DB_NAME),
    ], capture_output=True, timeout=600)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("mysqldump 失败: %s" % (p.stderr or b"").decode("utf-8", "replace")[:300])
    return p.stdout


def build_archive():
    buf = io.BytesIO()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dump = make_dump()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        info = tarfile.TarInfo("dump.sql.gz")
        info.size = len(dump)
        info.mtime = time.time()
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(dump))
        for path in BACKUP_PATHS:
            if os.path.exists(path):
                tar.add(path, arcname=path.lstrip("/"))
    return buf.getvalue(), "tp-backup-%s.tar.gz" % ts


def save_local(data, name):
    os.makedirs(DAILY_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(DAILY_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)
    return path


def list_local():
    items = []
    try:
        for name in os.listdir(DAILY_DIR):
            if not name.endswith(".tar.gz"):
                continue
            p = os.path.join(DAILY_DIR, name)
            st = os.stat(p)
            items.append({
                "name": name,
                "size": st.st_size,
                "time": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "mtime": st.st_mtime,
            })
    except Exception:
        pass
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def prune_local(keep):
    try:
        keep = int(keep or 14)
    except Exception:
        keep = 14
    removed = 0
    for it in list_local()[keep:]:
        try:
            os.remove(os.path.join(DAILY_DIR, it["name"]))
            removed += 1
        except Exception:
            pass
    return removed



def _dav_url(cfg, name=None):
    base = (cfg.get("davUrl") or "").strip().rstrip("/")
    d = (cfg.get("davDir") or "").strip().strip("/")
    if not base:
        raise RuntimeError("WebDAV 地址未填写")
    url = base + ("/" + d if d else "")
    if name:
        url += "/" + name
    return url


def _dav_req(cfg, method, url, data=None, headers=None, timeout=25):
    import base64
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(("%s:%s" % (cfg.get("davUser") or "", cfg.get("davPass") or "")).encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def dav_ensure_dir(cfg):
    """创建目录；绿联等实现对已存在目录可能返回 405/409，统一容错"""
    url = _dav_url(cfg, None)
    code, _ = _dav_req(cfg, "MKCOL", url)
    return code in (200, 201, 204, 301, 405, 409, 207, 0)


def dav_upload(cfg, data, name):
    try:
        dav_ensure_dir(cfg)
        code, body = _dav_req(cfg, "PUT", _dav_url(cfg, name), data,
                              {"Content-Type": "application/gzip"})
    except Exception as e:
        return False, "WebDAV 上传异常: %s" % e
    if code in (200, 201, 204):
        return True, "已上传到 NAS（%.1f KB）" % (len(data) / 1024.0)
    return False, "WebDAV 上传失败 HTTP %s: %s" % (code, body.decode("utf-8", "replace")[:200])


def dav_list(cfg):
    """PROPFIND 列出目录内文件"""
    import re as _re
    body = (b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/>'
            b'<d:getlastmodified/></d:prop></d:propfind>')
    code, raw = _dav_req(cfg, "PROPFIND", _dav_url(cfg, None), body,
                         {"Depth": "1", "Content-Type": "application/xml"})
    if code not in (207, 200):
        return []
    xml = raw.decode("utf-8", "replace")
    items = []
    for m in _re.finditer(r"<[dD]:response>(.*?)</[dD]:response>", xml, _re.S):
        blk = m.group(1)
        href = _re.search(r"<[dD]:href>(.*?)</[dD]:href>", blk)
        if not href:
            continue
        u = urllib.parse.unquote(href.group(1))
        nm = u.rstrip("/").split("/")[-1]
        if not nm or not nm.startswith("tp-backup-"):
            continue
        items.append({"name": nm, "href": href.group(1)})
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


def dav_prune(cfg):
    keep = int(cfg.get("retention") or 14)
    removed = 0
    for it in dav_list(cfg)[keep:]:
        url = _dav_url(cfg, None).rstrip("/")
        base_path = urllib.parse.urlparse(url).path.rstrip("/")
        href_path = urllib.parse.urlparse(it["href"]).path if it["href"].startswith(("http", "/")) else it["href"]
        full = url if not href_path else (
            url.replace(base_path, urllib.parse.unquote(href_path)) if base_path in urllib.parse.unquote(href_path)
            else url + "/" + it["name"])
        code, _ = _dav_req(cfg, "DELETE", full)
        if code in (200, 204):
            removed += 1
    return removed


def dav_test(cfg):
    try:
        okdir = dav_ensure_dir(cfg)
        probe = "tp-backup-probe.txt"
        ok, msg = dav_upload(cfg, b"probe", probe)
        if ok:
            _dav_req(cfg, "DELETE", _dav_url(cfg, probe))
            return True, "连接成功，目录可写"
        if not okdir:
            return False, "无法创建目录，请确认地址/账号/目录权限"
        return False, msg
    except Exception as e:
        return False, "连接失败: %s" % e


def send_mail(cfg, data, name):
    host = (cfg.get("smtpHost") or "").strip()
    user = (cfg.get("smtpUser") or "").strip()
    pwd = (cfg.get("smtpPass") or "").strip()
    to = (cfg.get("smtpTo") or user).strip()
    port = int(cfg.get("smtpPort") or 465)
    if not (host and user and pwd and to):
        return False, "邮件配置不完整（需要 SMTP 服务器 / 账号 / 密码 / 收件地址）"

    msg = email.message.EmailMessage()
    msg["Subject"] = "面板备份 %s" % datetime.datetime.now().strftime("%Y-%m-%d")
    msg["From"] = user
    msg["To"] = to
    msg.set_content("附件为 trojan-panel 全量备份（数据库 + 证书 + 配置），%.1f KB。"
                    % (len(data) / 1024.0))
    msg.add_attachment(data, maintype="application", subtype="gzip", filename=name)

    try:
        ctx = ssl.create_default_context()
        if port == 587:
            s = smtplib.SMTP(host, port, timeout=25)
            s.starttls(context=ctx)
        else:
            s = smtplib.SMTP_SSL(host, port, timeout=25, context=ctx)
        s.login(user, pwd)
        s.send_message(msg)
        s.quit()
        return True, "邮件已发送到 %s" % to
    except Exception as e:
        return False, "邮件发送失败: %s" % e


# ---- 恢复（仅数据库） ----
MAX_UPLOAD = 200 * 1024 * 1024          # 上传上限 200MB


def extract_dump_from_archive(data):
    """从 tar.gz 取出 dump.sql.gz，校验 gzip 与内容，返回 SQL 字节"""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except Exception as e:
        raise ValueError("不是有效的 tar.gz 文件：%s" % e)
    member = None
    for m in tf.getmembers():
        if m.isfile() and m.name.split("/")[-1] == "dump.sql.gz":
            member = m
            break
    if member is None:
        raise ValueError("备份包里没有 dump.sql.gz")
    blob = tf.extractfile(member).read()
    try:
        sql = gzip.decompress(blob)
    except Exception:
        raise ValueError("dump.sql.gz 解压失败，文件可能损坏")
    if b"CREATE TABLE" not in sql:
        raise ValueError("SQL 中没有建表语句，不像是面板数据库备份")
    return sql


def _mysql_run(stdin_bytes, db_args, timeout=600):
    return subprocess.run(
        ["docker", "exec", "-i", MARIADB_CONTAINER, "sh", "-c",
         "mysql -uroot -p'%s' %s" % (db_password(), db_args)],
        input=stdin_bytes, capture_output=True, timeout=timeout)


TMP_DB = "tp_restore_merge"


def _mysql_text(sql_text, timeout=120):
    p = subprocess.run(
        ["docker", "exec", MARIADB_CONTAINER, "sh", "-c",
         "mysql -uroot -p'%s' -N -e \"%s\"" % (db_password(), sql_text.replace('"', '\\"'))],
        capture_output=True, timeout=timeout)
    return (p.stdout or b"").decode("utf-8", "replace").strip(), p


def merge_accounts(sql, label=""):
    """只补缺失账号：把备份导入临时库，再按 用户名+邮箱 判重插到正式库，不删不改现有数据"""
    with _lock:
        if not db_password():
            return False, "读取数据库密码失败，请检查 %s" % PANEL_INI
        try:
            u, p = _mysql_text("DROP DATABASE IF EXISTS %s; CREATE DATABASE %s "
                               "DEFAULT CHARACTER SET utf8mb4" % (TMP_DB, TMP_DB))
            if p.returncode != 0:
                raise RuntimeError("准备临时库失败：%s" % (p.stderr or b"").decode("utf-8", "replace")[:200])
            r = _mysql_run(sql, TMP_DB)
            if r.returncode != 0:
                raise RuntimeError("备份在临时库导入失败：%s" % (r.stderr or b"").decode("utf-8", "replace")[:300])

            cols, _ = _mysql_text(
                "select column_name from information_schema.columns where table_schema='%s' "
                "and table_name='account' and column_name<>'id' order by ordinal_position" % TMP_DB)
            if not cols:
                raise RuntimeError("备份里没有 account 表")
            cl = ",".join("`%s`" % c for c in cols.split())
            stmt = ("INSERT INTO trojan_panel_db.account (%s) SELECT %s FROM %s.account t "
                    "WHERE NOT EXISTS (SELECT 1 FROM trojan_panel_db.account c "
                    "WHERE c.username = t.username AND c.email = t.email)") % (cl, cl, TMP_DB)
            r = _mysql_run(stmt.encode(), "trojan_panel_db")
            if r.returncode != 0:
                raise RuntimeError("合并插入失败：%s" % (r.stderr or b"").decode("utf-8", "replace")[:300])

            total, _ = _mysql_text("select count(*) from trojan_panel_db.account")
        except Exception as e:
            _mysql_text("DROP DATABASE IF EXISTS %s" % TMP_DB)
            return False, str(e)
        _mysql_text("DROP DATABASE IF EXISTS %s" % TMP_DB)
        suffix = ("（%s）" % label) if label else ""
        return True, ("已合并%s：本次补入缺失账号，按「用户名+邮箱」判重，现有账号与其它数据未改动；"
                      "当前账号总数 %s" % (suffix, total))


def restore_database(sql, label=""):
    """用 SQL 覆盖 trojan_panel_db；恢复前自动导出当前库做回滚"""
    with _lock:
        pw = db_password()
        if not pw:
            return False, "读取数据库密码失败，请检查 %s" % PANEL_INI
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        pre = os.path.join(CONF_DIR, "pre-restore-%s.sql.gz" % ts)
        try:
            with open(pre, "wb") as f:
                f.write(make_dump())
            os.chmod(pre, 0o600)
        except Exception as e:
            return False, "恢复前快照失败，已中止：%s" % e

        stopped = []
        try:
            for c in ("trojan-panel-core", "trojan-panel"):
                if subprocess.run(["docker", "stop", c], capture_output=True).returncode == 0:
                    stopped.append(c)
            p = subprocess.run(
                ["docker", "exec", "-i", MARIADB_CONTAINER, "sh", "-c",
                 "mysql -uroot -p'%s' %s" % (pw, DB_NAME)],
                input=sql, capture_output=True, timeout=600)
            if p.returncode != 0:
                raise RuntimeError("导入失败：%s" % (p.stderr or b"").decode("utf-8", "replace")[:300])
            n = subprocess.run(
                ["docker", "exec", MARIADB_CONTAINER, "sh", "-c",
                 "mysql -uroot -p'%s' -N -e \"select count(*) from information_schema.tables "
                 "where table_schema='%s'\"" % (pw, DB_NAME)],
                capture_output=True, timeout=60)
            tables = (n.stdout or b"").decode("utf-8", "replace").strip()
        except Exception as e:
            for c in stopped:
                subprocess.run(["docker", "start", c], capture_output=True)
            return False, str(e)
        for c in stopped:
            subprocess.run(["docker", "start", c], capture_output=True)
        time.sleep(1)
        suffix = ("（%s）" % label) if label else ""
        return True, "已恢复%s，当前表数 %s；恢复前快照：%s" % (suffix, tables, pre)


def run_backup(trigger="manual"):
    with _lock:
        cfg = load_cfg()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            data, name = build_archive()
        except Exception as e:
            cfg.update({"lastRun": now, "lastResult": "error", "lastMsg": "打包失败: %s" % e})
            save_cfg(cfg)
            return False, cfg["lastMsg"]

        parts = []
        ok_any = True
        try:
            save_local(data, name)
            removed = prune_local(cfg.get("retention"))
            parts.append("已保存到本机（%.1f KB%s）" % (
                len(data) / 1024.0, "，清理 %d 份旧备份" % removed if removed else ""))
        except Exception as e:
            ok_any = False
            parts.append("本机保存失败: %s" % e)

        if cfg.get("davEnabled"):
            ok_dav, msg = dav_upload(cfg, data, name)
            parts.append(msg if ok_dav else "NAS 上传失败 → " + msg)
            ok_any = ok_any and ok_dav
            if ok_dav:
                try:
                    rm = dav_prune(cfg)
                    if rm:
                        parts.append("NAS 清理 %d 份旧备份" % rm)
                except Exception:
                    pass

        if cfg.get("emailEnabled"):
            ok_mail, msg = send_mail(cfg, data, name)
            parts.append(msg if ok_mail else "冷备失败 → " + msg)
            ok_any = ok_any and ok_mail

        cfg.update({
            "lastRun": now,
            "lastResult": "ok" if ok_any else "error",
            "lastMsg": "；".join(parts),
            "lastFile": name,
        })
        save_cfg(cfg)
        return ok_any, cfg["lastMsg"]


def next_run(cfg):
    sched = cfg.get("schedule") or ""
    if not cfg.get("enabled") or not re.match(r"^\d{2}:\d{2}$", sched):
        return ""
    now = datetime.datetime.now()
    today = now.replace(hour=int(sched[:2]), minute=int(sched[3:]), second=0, microsecond=0)
    if today <= now:
        today += datetime.timedelta(days=1)
    return today.strftime("%Y-%m-%d %H:%M")


def scheduler_loop():
    last_date = ""
    while True:
        try:
            cfg = load_cfg()
            if cfg.get("enabled"):
                sched = cfg.get("schedule") or ""
                now = datetime.datetime.now()
                if re.match(r"^\d{2}:\d{2}$", sched) and now.strftime("%H:%M") == sched \
                        and last_date != now.strftime("%Y-%m-%d"):
                    last_date = now.strftime("%Y-%m-%d")
                    run_backup("schedule")
        except Exception:
            pass
        time.sleep(30)


def panel_authed(auth_header):
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
    server_version = "tpbackup/2.0"

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, name):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        if panel_authed(self.headers.get("Authorization", "")):
            return True
        self._send(401, {"code": 401, "msg": "未登录或登录已过期"})
        return False

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def _status_payload(self):
        cfg = load_cfg()
        st = public_cfg(cfg)
        st["nextRun"] = next_run(cfg)
        items = list_local()
        st["items"] = items[:20]
        st["total"] = len(items)
        return st

    def _pull(self):
        """无 JWT、仅凭 token 的只读拉取（供 NAS 反向同步）"""
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not load_cfg().get("pullEnabled", True):
            self._send(403, {"code": 403, "msg": "拉取令牌已关闭"})
            return
        tok = (q.get("token") or [""])[0]
        expect = pull_token()
        if not tok or not secrets.compare_digest(tok, expect):
            self._send(401, {"code": 401, "msg": "令牌无效"})
            return
        if u.path.startswith("/pull/list"):
            items = list_local()
            self._send(200, {"code": 200, "data": {"items": items, "total": len(items)}})
            return
        if u.path.startswith("/pull/latest"):
            items = list_local()
            if not items:
                self._send(404, {"code": 404, "msg": "暂无备份"})
                return
            name = items[0]["name"]
            self._send_file(os.path.join(DAILY_DIR, name), name)
            return
        if u.path.startswith("/pull/file"):
            name = (q.get("name") or [""])[0]
            if not re.match(r"^tp-backup-[0-9-]+\.tar\.gz$", name):
                self._send(400, {"code": 400, "msg": "文件名不合法"})
                return
            path = os.path.join(DAILY_DIR, name)
            if not os.path.exists(path):
                self._send(404, {"code": 404, "msg": "文件不存在"})
                return
            self._send_file(path, name)
            return
        self._send(404, {"code": 404, "msg": "not found"})

    def do_GET(self):
        if self.path.startswith("/pull/"):
            self._pull()
            return
        if not self._auth():
            return
        if self.path.startswith("/status"):
            self._send(200, {"code": 200, "data": self._status_payload()})
            return
        if self.path.startswith("/download"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = (q.get("name") or [""])[0]
            if not re.match(r"^tp-backup-[0-9-]+\.tar\.gz$", name):
                self._send(400, {"code": 400, "msg": "文件名不合法"})
                return
            path = os.path.join(DAILY_DIR, name)
            if not os.path.exists(path):
                self._send(404, {"code": 404, "msg": "文件不存在"})
                return
            self._send_file(path, name)
            return
        self._send(404, {"code": 404, "msg": "not found"})

    def do_POST(self):
        if not self._auth():
            return

        if self.path.startswith("/restore-upload"):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_UPLOAD:
                self._send(400, {"code": 400, "msg": "文件为空或超过 200MB 上限"})
                return
            raw = self.rfile.read(n)
            label = urllib.parse.unquote(self.headers.get("X-Filename") or "")
            mode = "merge" if "mode=merge" in (self.path or "") else "overwrite"
            try:
                sql = extract_dump_from_archive(raw)
            except ValueError as e:
                self._send(400, {"code": 400, "msg": str(e)})
                return
            if mode == "merge":
                ok, msg = merge_accounts(sql, label)
            else:
                ok, msg = restore_database(sql, label)
            self._send(200, {"code": 200 if ok else 500, "msg": msg,
                             "data": self._status_payload() if ok else {}})
            return

        body = self._body()

        if self.path.startswith("/restore-local"):
            name = body.get("name") or ""
            if not re.match(r"^tp-backup-[0-9-]+\.tar\.gz$", name):
                self._send(400, {"code": 400, "msg": "文件名不合法"})
                return
            path = os.path.join(DAILY_DIR, name)
            if not os.path.exists(path):
                self._send(404, {"code": 404, "msg": "文件不存在"})
                return
            try:
                with open(path, "rb") as f:
                    sql = extract_dump_from_archive(f.read())
            except ValueError as e:
                self._send(400, {"code": 400, "msg": str(e)})
                return
            if body.get("mode") == "merge":
                ok, msg = merge_accounts(sql, name)
            else:
                ok, msg = restore_database(sql, name)
            self._send(200, {"code": 200 if ok else 500, "msg": msg})
            return

        if self.path.startswith("/config"):
            cfg = load_cfg()
            for k in ("enabled", "emailEnabled", "schedule", "retention", "smtpHost",
                      "smtpPort", "smtpUser", "smtpTo",
                      "davEnabled", "davUrl", "davUser", "davDir", "pullEnabled"):
                if k in body:
                    cfg[k] = body[k]
            if body.get("smtpPass"):
                cfg["smtpPass"] = body["smtpPass"].strip()
            if body.get("davPass"):
                cfg["davPass"] = body["davPass"].strip()
            for k in ("smtpHost", "smtpUser", "smtpTo", "davUrl", "davUser", "davDir"):
                if k in body and isinstance(cfg[k], str):
                    cfg[k] = cfg[k].strip()
            save_cfg(cfg)
            self._send(200, {"code": 200, "data": self._status_payload(), "msg": "已保存"})
            return

        if self.path.startswith("/pull-token"):
            cfg = load_cfg()
            cfg["pullToken"] = secrets.token_urlsafe(32)
            save_cfg(cfg)
            self._send(200, {"code": 200, "data": self._status_payload(), "msg": "拉取令牌已重置，旧链接立即失效"})
            return

        if self.path.startswith("/test-email"):
            cfg = load_cfg()
            for k in ("smtpHost", "smtpPort", "smtpUser", "smtpTo"):
                if body.get(k):
                    cfg[k] = body[k]
            if body.get("smtpPass"):
                cfg["smtpPass"] = body["smtpPass"]
            ok, msg = send_mail(cfg, b"test", "tp-backup-test.txt")
            self._send(200, {"code": 200 if ok else 500, "msg": msg, "data": {"ok": ok}})
            return

        if self.path.startswith("/test-dav"):
            cfg = load_cfg()
            for k in ("davUrl", "davUser", "davDir"):
                if body.get(k):
                    cfg[k] = body[k]
            if body.get("davPass"):
                cfg["davPass"] = body["davPass"]
            ok, msg = dav_test(cfg)
            self._send(200, {"code": 200 if ok else 500, "msg": msg, "data": {"ok": ok}})
            return

        if self.path.startswith("/run"):
            ok, msg = run_backup("manual")
            self._send(200, {"code": 200 if ok else 500, "msg": msg,
                             "data": self._status_payload()})
            return

        self._send(404, {"code": 404, "msg": "not found"})


def main():
    os.makedirs(DAILY_DIR, mode=0o700, exist_ok=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    ThreadingHTTPServer(BIND, Handler).serve_forever()


if __name__ == "__main__":
    if "--run-once" in sys.argv:
        ok, msg = run_backup("manual")
        print(("OK" if ok else "FAIL") + " " + msg)
        sys.exit(0 if ok else 1)
    main()
