# -*- coding: utf-8 -*-
"""
挖矿群控 · 中控服务（纯标准库，零依赖）

用法:
    python server.py                          # 默认端口 8099
    PORT=8099 python server.py
首次启动自动生成 server/config.json（含 admin_token），控制台会打印。

矿机侧接口（Agent 调用，需 header X-Rig-Token 与 rigs 表一致）:
    POST /api/report        上报一次快照
    GET  /api/poll?name=    拉取待执行命令（并标记 sent）
    POST /api/cmd_result    命令执行回执 {rig, id, ok, result}

看板接口（需 header X-Admin-Token，看板页会引导输入一次）:
    GET  /api/fleet                 全群聚合
    GET  /api/history?rig=&hours=   单机时序
    POST /api/command               下发命令 {rig, cmd:"restart_miner"}
    GET  /api/alerts                告警列表
    POST /api/alerts/resolve        {id} 手动关闭

数据: data/fleet.db (SQLite, WAL)；samples 保留 7 天。
告警: 矿机掉线 / 算力骤降(对比 1 小时均值 -25%) / GPU 高温(默认 83C)。
收益: 每 10 分钟从性价比榜服务拉 PRL 实时收益率(USDT/TH/s/天)折算。
"""

import json
import os
import sqlite3
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "fleet.db")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

PORT = int(os.environ.get("PORT", "8099"))
OFFLINE_SEC = int(os.environ.get("OFFLINE_SEC", "150"))
SAMPLE_KEEP_DAYS = 7
TEMP_WARN_C = float(os.environ.get("TEMP_WARN", "83"))
HASHRATE_DROP_PCT = 25.0          # 相对 1 小时均值降幅告警阈值
HASHRATE_ALERT_COOLDOWN = 3600    # 同机同因告警冷却
INCOME_SRC = os.environ.get(
    "INCOME_SRC", "https://gpu-value-rank.app.workbuddy.host/api/rank")

_db_lock = threading.Lock()
_admin_token = ""
_rate_lock = threading.Lock()
_rate_map = {}          # model -> usdt/TH/s/天
_rate_ts = 0.0

# ---------------------------------------------------------------- 配置


def _load_config():
    global _admin_token
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:  # noqa: BLE001
            print("[config] 读取失败: %s" % e)
    _admin_token = str(cfg.get("admin_token") or "")
    if not _admin_token:
        import secrets
        _admin_token = "adm_" + secrets.token_hex(8)
        cfg["admin_token"] = _admin_token
        cfg.setdefault("port", PORT)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("[config] 已生成 %s  admin_token=%s" % (CONFIG_FILE, _admin_token))


# ---------------------------------------------------------------- 数据库

def _conn():
    c = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=15)
    c.row_factory = sqlite3.Row
    return c

_conn_local = threading.local()


def db():
    c = getattr(_conn_local, "c", None)
    if c is None:
        c = _conn()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
        _conn_local.c = c
    return c


def init_db():
    c = db()
    c.executescript("""
CREATE TABLE IF NOT EXISTS rigs(
  name TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  first_seen REAL, last_seen REAL,
  last_report TEXT,
  status TEXT DEFAULT 'offline',
  offline_since REAL
);
CREATE TABLE IF NOT EXISTS samples(
  rig TEXT NOT NULL, ts REAL NOT NULL,
  ths REAL, power REAL, temp REAL, fan REAL,
  accepted INTEGER, rejected INTEGER, uptime INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples ON samples(rig, ts);
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rig TEXT NOT NULL, cmd TEXT NOT NULL, arg TEXT,
  status TEXT DEFAULT 'pending',
  created REAL, picked REAL, finished REAL, result TEXT
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, rig TEXT, level TEXT, kind TEXT, msg TEXT,
  resolved INTEGER DEFAULT 0, resolved_ts REAL
);
""")
    c.commit()


# ---------------------------------------------------------------- 工具

def now_cn():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def add_alert(rig, level, kind, msg):
    c = db()
    row = c.execute(
        "SELECT id FROM alerts WHERE rig=? AND kind=? AND resolved=0", (rig, kind)
    ).fetchone()
    if row:
        return
    c.execute("INSERT INTO alerts(ts,rig,level,kind,msg) VALUES(?,?,?,?,?)",
              (time.time(), rig, level, kind, msg))
    c.commit()
    print("[alert] %s [%s] %s: %s" % (now_cn(), level, rig, msg))


def resolve_alert(rig, kind):
    c = db()
    c.execute(
        "UPDATE alerts SET resolved=1, resolved_ts=? WHERE rig=? AND kind=? AND resolved=0",
        (time.time(), rig, kind))
    c.commit()


def rig_avg_ths(rig, window_sec):
    c = db()
    row = c.execute(
        "SELECT AVG(ths) a FROM samples WHERE rig=? AND ts>? AND ths>0",
        (rig, time.time() - window_sec)).fetchone()
    return row["a"] or 0.0


# ---------------------------------------------------------------- 收益率

def rate_fetch_loop():
    global _rate_map, _rate_ts
    while True:
        try:
            req = urllib.request.Request(INCOME_SRC, headers={"User-Agent": "minrigs/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            m = {}
            for it in data.get("items", []):
                ths, usdt = it.get("ths"), it.get("usdt_day")
                model = it.get("model")
                if model and ths and usdt and it.get("price_cny"):
                    m[model] = usdt / ths
            if m:
                with _rate_lock:
                    _rate_map = m
                    _rate_ts = time.time()
                print("[rate] 收益率更新：%d 个型号" % len(m), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[rate] 拉取失败: %s" % e, flush=True)
        time.sleep(600)


def rate_for(model):
    with _rate_lock:
        if model in _rate_map:
            return _rate_map[model]
        # 模糊匹配：NVIDIA GeForce RTX 4090 -> RTX 4090
        short = (model or "").replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()
        if short in _rate_map:
            return _rate_map[short]
    return None


# ---------------------------------------------------------------- 上报处理

def handle_report(payload):
    name = str(payload.get("name") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not name or not token:
        return {"ok": False, "error": "缺少 name/token"}
    c = db()
    row = c.execute("SELECT token FROM rigs WHERE name=?", (name,)).fetchone()
    if row is None:
        c.execute("INSERT INTO rigs(name,token,first_seen,last_seen,status) VALUES(?,?,?,?,?)",
                  (name, token, time.time(), time.time(), "online"))
        c.commit()
        add_alert(name, "info", "rig_new", "新矿机接入：%s" % name)
        print("[rig] 新矿机注册：%s" % name)
    elif row["token"] != token:
        return {"ok": False, "error": "token 不匹配"}

    gpus = payload.get("gpus") or []
    miners = payload.get("miners") or []
    ths = 0.0
    accepted = rejected = 0
    uptime = None
    for m in miners:
        ths += float(m.get("ths") or 0)
        accepted += int(m.get("accepted") or 0)
        rejected += int(m.get("rejected") or 0)
        if m.get("uptime"):
            uptime = m["uptime"]
    power = sum(float(g.get("power") or 0) for g in gpus)
    temp = max([float(g.get("temp") or 0) for g in gpus], default=0)
    fan = max([float(g.get("fan") or 0) for g in gpus], default=0)

    model = (gpus[0].get("model") if gpus else "") or ""
    income = None
    rate = rate_for(model)
    if rate:
        income = round(ths * rate * 7.10, 2)  # USDT->CNY 固定折算，看板可感知

    snap = {
        "gpus": gpus, "miners": miners,
        "ths": round(ths, 2), "power": round(power, 1),
        "temp": temp, "fan": fan,
        "accepted": accepted, "rejected": rejected,
        "uptime": uptime, "model": model, "income_cny_day": income,
        "hostname": payload.get("hostname") or "",
    }
    with _db_lock:
        c.execute(
            "UPDATE rigs SET last_seen=?, last_report=?, status='online', offline_since=NULL WHERE name=?",
            (time.time(), json.dumps(snap, ensure_ascii=False), name))
        c.execute(
            "INSERT INTO samples(rig,ts,ths,power,temp,fan,accepted,rejected,uptime) VALUES(?,?,?,?,?,?,?,?,?)",
            (name, time.time(), ths, power, temp, fan, accepted, rejected, uptime))
        c.commit()

    resolve_alert(name, "rig_offline")
    # 高温告警
    if temp >= TEMP_WARN_C:
        add_alert(name, "warn", "temp_high",
                  "GPU 最高温度 %.0fC ≥ %.0fC" % (temp, TEMP_WARN_C))
    else:
        resolve_alert(name, "temp_high")
    # 算力骤降
    if ths > 0.1:
        avg = rig_avg_ths(name, 3600)
        if avg > 0.1 and ths < avg * (1 - HASHRATE_DROP_PCT / 100.0):
            add_alert(name, "warn", "hashrate_drop",
                      "算力 %.1f TH/s 较 1 小时均值 %.1f 下降 %.0f%%"
                      % (ths, avg, (1 - ths / avg) * 100))
    return {"ok": True}


def sweep_loop():
    while True:
        try:
            c = db()
            rows = c.execute("SELECT name,last_seen,status FROM rigs").fetchall()
            for r in rows:
                if time.time() - (r["last_seen"] or 0) > OFFLINE_SEC:
                    if r["status"] != "offline":
                        c.execute("UPDATE rigs SET status='offline', offline_since=? WHERE name=?",
                                  (time.time(), r["name"]))
                        c.commit()
                        add_alert(r["name"], "crit", "rig_offline",
                                  "矿机掉线：%s（%d 秒无心跳）"
                                  % (r["name"], int(time.time() - (r["last_seen"] or 0))))
            c.execute("DELETE FROM samples WHERE ts < ?", (time.time() - SAMPLE_KEEP_DAYS * 86400,))
            c.commit()
        except Exception:  # noqa: BLE001
            traceback.print_exc(limit=1)
        time.sleep(30)


# ---------------------------------------------------------------- 聚合

def fleet_payload():
    c = db()
    rigs = c.execute("SELECT * FROM rigs ORDER BY name").fetchall()
    out = []
    total = {"online": 0, "ths": 0.0, "power": 0.0, "income": 0.0, "rate_age": int(time.time() - _rate_ts) if _rate_ts else None}
    for r in rigs:
        snap = json.loads(r["last_report"]) if r["last_report"] else None
        online = r["status"] == "online"
        if online:
            total["online"] += 1
            if snap:
                total["ths"] += snap.get("ths") or 0
                total["power"] += snap.get("power") or 0
                total["income"] += snap.get("income_cny_day") or 0
        # 近 12 小时采样（用于迷你曲线，10 分钟粒度抽点）
        hist = c.execute(
            "SELECT ts,ths,power,temp FROM samples WHERE rig=? AND ts>? ORDER BY ts",
            (r["name"], time.time() - 12 * 3600)).fetchall()
        out.append({
            "name": r["name"], "status": r["status"],
            "last_seen": r["last_seen"],
            "offline_since": r["offline_since"],
            "snapshot": snap, "history": [dict(h) for h in hist],
        })
    total["ths"] = round(total["ths"], 1)
    total["power"] = round(total["power"], 0)
    total["income"] = round(total["income"], 0)
    return {"ok": True, "now": now_cn(), "total": total, "rigs": out}


def history_payload(rig, hours):
    c = db()
    rows = c.execute(
        "SELECT ts,ths,power,temp,fan,accepted,rejected FROM samples WHERE rig=? AND ts>? ORDER BY ts",
        (rig, time.time() - hours * 3600)).fetchall()
    return {"ok": True, "rig": rig, "samples": [dict(x) for x in rows]}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "MinRigs/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Rig-Token,X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"__error__": str(e)}

    def _rig_auth(self):
        return self.headers.get("X-Rig-Token") or ""

    def _admin_auth(self):
        return self.headers.get("X-Admin-Token") or ""

    def do_OPTIONS(self):
        self._send(200, "{}")

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/api/poll":
            name = (q.get("name") or [""])[0]
            c = db()
            row = c.execute("SELECT token FROM rigs WHERE name=?", (name,)).fetchone()
            if not row or row["token"] != self._rig_auth():
                return self._json({"ok": False, "error": "auth failed"}, 401)
            with _db_lock:
                pend = c.execute(
                    "SELECT id,cmd,arg FROM commands WHERE rig=? AND status='pending' ORDER BY id",
                    (name,)).fetchall()
                if pend:
                    c.execute("UPDATE commands SET status='sent', picked=? WHERE rig=? AND status='pending'",
                              (time.time(), name))
                c.commit()
            return self._json({"ok": True, "commands": [dict(x) for x in pend]})

        if p == "/api/fleet":
            if self._admin_auth() != _admin_token:
                return self._json({"ok": False, "error": "admin token required"}, 401)
            return self._json(fleet_payload())

        if p == "/api/history":
            if self._admin_auth() != _admin_token:
                return self._json({"ok": False, "error": "admin token required"}, 401)
            rig = (q.get("rig") or [""])[0]
            hours = min(168, max(1, int((q.get("hours") or ["24"])[0])))
            return self._json(history_payload(rig, hours))

        if p == "/api/alerts":
            if self._admin_auth() != _admin_token:
                return self._json({"ok": False, "error": "admin token required"}, 401)
            c = db()
            rows = c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 80").fetchall()
            return self._json({"ok": True, "alerts": [dict(x) for x in rows]})

        if p == "/api/health":
            c = db()
            n = c.execute("SELECT COUNT(*) n FROM rigs").fetchone()["n"]
            return self._json({"ok": True, "rigs": n, "now": now_cn()})

        if p in ("/", "/index.html"):
            try:
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(404, "dashboard missing", "text/plain")

        return self._send(404, "Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/api/report":
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            return self._json(handle_report(body))

        if p == "/api/cmd_result":
            body = self._read_json()
            c = db()
            row = c.execute("SELECT token FROM rigs WHERE name=?", (body.get("rig") or "",)).fetchone()
            if not row or row["token"] != self._rig_auth():
                return self._json({"ok": False, "error": "auth failed"}, 401)
            with _db_lock:
                c.execute("UPDATE commands SET status=?, finished=?, result=? WHERE id=?",
                          ("done" if body.get("ok") else "failed", time.time(),
                           str(body.get("result") or "")[:500], body.get("id")))
                c.commit()
            return self._json({"ok": True})

        if p == "/api/command":
            body = self._read_json()
            if self._admin_auth() != _admin_token:
                return self._json({"ok": False, "error": "admin token required"}, 401)
            rig, cmd = body.get("rig") or "", body.get("cmd") or ""
            if not rig or not cmd:
                return self._json({"ok": False, "error": "rig/cmd 必填"}, 400)
            c = db()
            with _db_lock:
                c.execute("INSERT INTO commands(rig,cmd,arg,created) VALUES(?,?,?,?)",
                          (rig, cmd, json.dumps(body.get("arg") or {}), time.time()))
                c.commit()
            print("[cmd] 已下发 %s -> %s" % (cmd, rig))
            return self._json({"ok": True})

        if p == "/api/alerts/resolve":
            body = self._read_json()
            if self._admin_auth() != _admin_token:
                return self._json({"ok": False, "error": "admin token required"}, 401)
            c = db()
            c.execute("UPDATE alerts SET resolved=1, resolved_ts=? WHERE id=?",
                      (time.time(), body.get("id")))
            c.commit()
            return self._json({"ok": True})

        return self._send(404, "Not Found", "text/plain; charset=utf-8")


def main():
    _load_config()
    init_db()
    threading.Thread(target=sweep_loop, daemon=True).start()
    threading.Thread(target=rate_fetch_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print("[server] 挖矿群控中控已启动  http://0.0.0.0:%d" % PORT, flush=True)
    print("[server] admin_token=%s  （看板首次打开时输入一次）" % _admin_token, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
