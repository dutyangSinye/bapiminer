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
    GET  /api/daily?days=30         按天收益估算（按矿池分组）
    GET  /api/kernels               挖矿内核版本/缓存
    GET  /api/pools                 矿池目录（品牌/节点/区域，供看板「更换矿池」选择）
    POST /api/kernel/check          立即检查内核最新版本
    POST /api/command               下发命令 {rig, cmd, arg}（rig 可为 "*" 广播全部）
                                    cmd: restart_miner|start_miner|stop_miner|
                                         gamemode_on|gamemode_off|update_kernel|update_client|
                                         set_pool|set_kernel
                                    set_pool   arg {"pool_url":"stratum+tcp://主机:端口"}
                                    set_kernel arg {"kernel":"SRBMiner-MULTI|BzMiner|WildRig"}
    GET  /api/alerts                告警列表
    POST /api/alerts/resolve        {id} 手动关闭
矿机端下载（需 X-Rig-Token）:
    GET  /api/kernel_dl?kernel=srbminer|bzminer   从中控缓存下载内核 zip（多镜像代下）

客户端安装包分发（放在 data/downloads/ 即可）:
    GET  /api/downloads                           安装包清单（大小/时间/sha256）
    GET  /download/<文件名>                        下载矿机端 exe / 安卓 apk（支持 Range 续传）

升级签名（矿机端只信签名清单，不信这里返回的任何裸字段）:
    GET  /api/signed_manifest                     已签名的升级清单（client + kernels 的 sha256）
    GET  /api/health                              存活 + server_version + build_commit + 清单摘要

数据: data/fleet.db (SQLite, WAL)；samples 保留 7 天。
告警: 矿机掉线 / 算力骤降(对比 1 小时均值 -25%) / GPU 高温(默认 83C)。
收益: 每 10 分钟从性价比榜服务拉 PRL 实时收益率(USDT/TH/s/天)折算。
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "fleet.db")

# ---- GPU 实测表：按【单卡】聚合各矿机最近一次上报，同一自然日只算一次 ----
# 注意：必须逐卡统计。双卡机（如 2×RTX 3080）如果按整机算，
# 会把两张卡的算力和功耗堆到一张卡上，得出假的每瓦算力。
GPU_TABLE_FILE = os.path.join(DATA_DIR, "gpu_table.json")


def _gpu_label(model):
    m = str(model or "").replace("NVIDIA GeForce ", "").strip()
    return m or "未知型号"


def _per_card_rates(d, n):
    """逐卡算力：优先 miners[].gpu_rates（长度匹配且非全零），否则按卡数均分。"""
    for m in (d.get("miners") or []):
        gr = m.get("gpu_rates") or []
        if len(gr) == n:
            try:
                vals = [float(x or 0) for x in gr]
            except (TypeError, ValueError):
                continue
            if sum(vals) > 0:
                return vals
    total = float(d.get("ths") or 0)
    return [total / n] * n if n else []


def _build_gpu_table():
    c = db()
    rows = c.execute(
        "SELECT name, last_seen, last_report FROM rigs WHERE last_seen > ? ORDER BY name",
        (time.time() - 86400,)).fetchall()
    avg, cny = rate_avg(), rate_cny()
    groups = {}
    for r in rows:
        try:
            d = json.loads(r["last_report"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        try:
            total_ths = float(d.get("ths") or 0)
        except (TypeError, ValueError):
            total_ths = 0.0
        if total_ths <= 0:
            continue
        gpus = d.get("gpus") or []
        if not gpus:
            gpus = [{"model": d.get("model"), "power": d.get("power")}]
        n = len(gpus)
        per_ths = _per_card_rates(d, n)
        kernel = str(d.get("kernel") or "")
        if not kernel:
            ms = d.get("miners") or [{}]
            kernel = str(ms[0].get("type") or "")
        for i, g in enumerate(gpus):
            try:
                ths_i = float(per_ths[i]) if i < len(per_ths) else total_ths / n
            except (TypeError, ValueError):
                ths_i = total_ths / n
            if ths_i <= 0:
                continue
            try:
                pw = float(g.get("power") or 0)
            except (TypeError, ValueError):
                pw = 0.0
            label = _gpu_label(g.get("model") or d.get("model"))
            gg = groups.setdefault(label, {"model": label, "cards": 0, "rigs": set(),
                                           "ths": [], "power": [], "kernels": {}})
            gg["cards"] += 1
            gg["rigs"].add(r["name"])
            gg["ths"].append(ths_i)
            if pw > 0:
                gg["power"].append(pw)
            if kernel:
                gg["kernels"][kernel] = gg["kernels"].get(kernel, 0) + 1

    out = []
    for label, g in groups.items():
        n = len(g["ths"])
        ths = sum(g["ths"]) / n
        pw = (sum(g["power"]) / len(g["power"])) if g["power"] else 0.0
        kernel = max(g["kernels"], key=g["kernels"].get) if g["kernels"] else ""
        out.append({
            "model": label,
            "cards": g["cards"],
            "rigs": len(g["rigs"]),
            "ths": round(ths, 1),
            "power": round(pw, 0),
            "ghw": round(ths * 1000.0 / pw, 0) if pw > 0 else None,
            "income_cny_day": round(ths * avg * cny, 2) if (avg and cny) else None,
            "kernel": kernel,
            "laptop": "Laptop" in label,
        })
    out.sort(key=lambda x: (x["ghw"] or 0), reverse=True)
    return out


def gpu_table_payload(force=False):
    """每天只算一次：同一自然日直接返回缓存，跨日重算并落盘。"""
    today = now_cn()[:10]
    if not force:
        try:
            with open(GPU_TABLE_FILE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == today and cached.get("rows") and "cards" in (cached.get("rows") or [{}])[0]:
                cached["cached"] = True
                return cached
        except (OSError, ValueError):
            pass
    rows = _build_gpu_table()
    payload = {"ok": True, "date": today, "generated_at": now_cn(), "cached": False,
               "rows": rows, "models": len(rows),
               "total_cards": sum(r["cards"] for r in rows)}
    # 机器数：机型去重后不能相加，单独按 24h 内上报的机器统计
    try:
        c = db()
        payload["total_rigs"] = c.execute(
            "SELECT COUNT(*) FROM rigs WHERE last_seen > ?", (time.time() - 86400,)).fetchone()[0]
    except Exception:  # noqa: BLE001
        payload["total_rigs"] = 0
    try:
        tmp = GPU_TABLE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, GPU_TABLE_FILE)
    except OSError:
        pass
    return payload


CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

PORT = int(os.environ.get("PORT", "8099"))
SERVER_VERSION = "3.1.0"          # 与 /api/health 一起暴露，便于确认线上跑的是哪一版
OFFLINE_SEC = int(os.environ.get("OFFLINE_SEC", "150"))
SAMPLE_KEEP_DAYS = 7
TEMP_WARN_C = float(os.environ.get("TEMP_WARN", "83"))
HASHRATE_DROP_PCT = 25.0          # 相对 1 小时均值降幅告警阈值
HASHRATE_ALERT_COOLDOWN = 3600    # 同机同因告警冷却
INCOME_SRC = os.environ.get(
    "INCOME_SRC", "https://www.vminers.com/gpu-hashrate/")

_db_lock = threading.Lock()
_admin_token = ""
_allow_register = True
_cfg = {}
_rate_lock = threading.Lock()
_rate_map = {}          # 型号 -> USDT/TH/s/天（来自 vminers PRL 实测榜）


def _load_config():
    global _admin_token, _allow_register, _cfg
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:  # noqa: BLE001
            print("[config] 读取失败: %s" % e)
    _cfg = cfg
    _admin_token = str(cfg.get("admin_token") or "")
    if not _admin_token:
        import secrets
        _admin_token = "adm_" + secrets.token_hex(8)
        cfg["admin_token"] = _admin_token
        cfg.setdefault("port", PORT)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("[config] 已生成 %s  admin_token=%s" % (CONFIG_FILE, _admin_token))
    # D2：注册开关（默认开放，不需要邀请码）
    _allow_register = bool(cfg.get("allow_register", True))


# ---------------------------------------------------------------- 数据库

def _conn():
    c = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=15,
                        isolation_level=None)   # autocommit：单语句事务，杜绝忘 commit 长持写锁
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
  offline_since REAL,
  wallet TEXT DEFAULT ''
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
    # 游戏模式标记（网吧式让卡），老库自动补列
    try:
        c.execute("ALTER TABLE rigs ADD COLUMN game_mode INTEGER DEFAULT 0")
        c.commit()
        print("[db] rigs 表新增 game_mode 列")
    except Exception:  # noqa: BLE001
        pass  # 列已存在
    # 采样加矿池字段（按矿池统计收益）
    try:
        c.execute("ALTER TABLE samples ADD COLUMN pool TEXT DEFAULT ''")
        c.commit()
        print("[db] samples 表新增 pool 列")
    except Exception:  # noqa: BLE001
        pass  # 列已存在
    # 钱包地址（矿池真实收益）
    try:
        c.execute("ALTER TABLE rigs ADD COLUMN wallet TEXT DEFAULT ''")
        c.commit()
        print("[db] rigs 表新增 wallet 列")
    except Exception:  # noqa: BLE001
        pass  # 列已存在
    # 矿机端软件版本（客户端上报 client_version）
    try:
        c.execute("ALTER TABLE rigs ADD COLUMN client_version TEXT DEFAULT ''")
        c.commit()
        print("[db] rigs 表新增 client_version 列")
    except Exception:  # noqa: BLE001
        pass
    # 当前矿池地址（客户端上报 pool_url，看板展示与「更换矿池」对照）
    try:
        c.execute("ALTER TABLE rigs ADD COLUMN pool_url TEXT DEFAULT ''")
        c.commit()
        print("[db] rigs 表新增 pool_url 列")
    except Exception:  # noqa: BLE001
        pass  # 列已存在
    # ---- 多用户体系（1.13.0.0）----
    c.executescript("""
CREATE TABLE IF NOT EXISTS users(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT UNIQUE NOT NULL,
  pwd_hash      TEXT NOT NULL,
  pwd_salt      TEXT NOT NULL,
  is_admin      INTEGER DEFAULT 0,
  status        TEXT DEFAULT 'active',
  created_at    REAL,
  last_login_at REAL
);
CREATE TABLE IF NOT EXISTS sessions(
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at REAL,
  expires_at REAL,
  ua         TEXT,
  ip         TEXT
);
CREATE TABLE IF NOT EXISTS rig_owner_log(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  rig     TEXT NOT NULL,
  old_user INTEGER,
  new_user INTEGER,
  action  TEXT,
  note    TEXT,
  ts      REAL
);
CREATE TABLE IF NOT EXISTS login_fail(
  key       TEXT PRIMARY KEY,
  fails     INTEGER DEFAULT 0,
  locked_until REAL,
  last_fail REAL
);
""")
    c.commit()
    # rigs 归属与展示名（ALTER 幂等：已存在则跳过）
    for col, ddl in (
        ("owner_id", "ALTER TABLE rigs ADD COLUMN owner_id INTEGER DEFAULT 0"),
        ("display_name", "ALTER TABLE rigs ADD COLUMN display_name TEXT DEFAULT ''"),
        ("bound_at", "ALTER TABLE rigs ADD COLUMN bound_at REAL"),
    ):
        try:
            c.execute(ddl)
            c.commit()
            print("[db] rigs 表新增 %s 列" % col)
        except Exception:  # noqa: BLE001
            pass  # 列已存在
    # 存量矿机 display_name 回填为 name（显示名与现状一致，不改机器标识）
    c.execute("UPDATE rigs SET display_name=name WHERE display_name IS NULL OR display_name=''")
    c.commit()
    # users.must_change_pwd：部署预置的管理员首次登录时强制改密
    try:
        c.execute("ALTER TABLE users ADD COLUMN must_change_pwd INTEGER DEFAULT 0")
        c.commit()
        print("[db] users 表新增 must_change_pwd 列")
    except Exception:  # noqa: BLE001
        pass  # 列已存在


# ---------------------------------------------------------------- 用户与会话

PBKDF2_ROUNDS = 200_000
SESSION_TTL = 7 * 86400          # 中控 Web 会话 7 天
LOGIN_MAX_FAIL = 5               # 连错 5 次锁 10 分钟
LOGIN_LOCK_SEC = 600
RESERVED_USERNAMES = {"admin", "root", "system", "administrator", "superuser"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,24}$")


def hash_password(password, salt_hex=None):
    """PBKDF2-HMAC-SHA256，200k 轮，随机 16 字节盐。返回 (hash_hex, salt_hex)。"""
    salt = salt_hex or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                             bytes.fromhex(salt), PBKDF2_ROUNDS)
    return dk.hex(), salt


def verify_password(password, pwd_hash, pwd_salt):
    """恒时比较校验密码。任何异常都视为失败。"""
    try:
        calc, _ = hash_password(password, pwd_salt)
        return hmac.compare_digest(calc, str(pwd_hash or ""))
    except Exception:  # noqa: BLE001
        return False


def validate_username(name, allow_reserved=False):
    """返回 (ok, err)。用户名 3~24 位，字母/数字/下划线/中文；拒绝保留词。

    allow_reserved=True 时跳过保留词检查 —— 仅供「预置/创建管理员」使用。
    保留词（admin/root/...）本意是防止普通用户冒充管理员，但管理员账号的权限
    来自 users.is_admin 字段而不是用户名，所以管理员自己用 admin 是安全的；
    反过来说，若这里不放行，生产环境永远无法用 admin 作为首个管理员。
    """
    name = str(name or "").strip()
    if not name:
        return False, "用户名不能为空"
    if not USERNAME_RE.match(name):
        return False, "用户名需 3~24 位，只能用字母、数字、下划线或中文"
    if not allow_reserved and name.lower() in RESERVED_USERNAMES:
        return False, "该用户名为系统保留，请换一个"
    return True, ""


def validate_password(pwd):
    """返回 (ok, err)。至少 6 位。"""
    pwd = str(pwd or "")
    if len(pwd) < 6:
        return False, "密码至少 6 位"
    if len(pwd) > 128:
        return False, "密码过长（最多 128 位）"
    return True, ""


def create_session(user_id, ua="", ip=""):
    """创建 Web 会话，返回 token。"""
    tok = secrets.token_hex(32)
    now = time.time()
    c = db()
    with _db_lock:
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at,ua,ip) "
                  "VALUES(?,?,?,?,?,?)",
                  (tok, user_id, now, now + SESSION_TTL, str(ua or "")[:200], str(ip or "")[:64]))
        c.commit()
    return tok


def drop_session(tok):
    if not tok:
        return
    c = db()
    with _db_lock:
        c.execute("DELETE FROM sessions WHERE token=?", (tok,))
        c.commit()


def user_by_name(username):
    return db().execute("SELECT * FROM users WHERE username=?", (str(username),)).fetchone()


def user_by_id(uid):
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_public(row):
    """对外的用户对象（绝不含 pwd_hash / pwd_salt）。"""
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"],
            "is_admin": bool(row["is_admin"]), "status": row["status"],
            "must_change_pwd": bool(_row_must_change(row)),
            "created_at": row["created_at"], "last_login_at": row["last_login_at"]}


def _row_must_change(row):
    """读 users.must_change_pwd（兼容迁移前的老行，列不存在时视为 0）。"""
    if row is None:
        return False
    try:
        return int(row["must_change_pwd"] or 0)
    except (IndexError, KeyError):
        return 0


def login_fail_check(key):
    """检查是否被锁。返回剩余秒数（0 = 未锁）。"""
    row = db().execute("SELECT * FROM login_fail WHERE key=?", (key,)).fetchone()
    if row and (row["locked_until"] or 0) > time.time():
        return int(row["locked_until"] - time.time())
    return 0


def login_fail_record(key):
    """累加失败次数；超阈值则锁定。"""
    c = db()
    now = time.time()
    with _db_lock:
        row = c.execute("SELECT * FROM login_fail WHERE key=?", (key,)).fetchone()
        if row is None:
            c.execute("INSERT INTO login_fail(key,fails,locked_until,last_fail) VALUES(?,?,?,?)",
                      (key, 1, 0.0, now))
        else:
            fails = int(row["fails"] or 0) + 1
            locked = now + LOGIN_LOCK_SEC if fails >= LOGIN_MAX_FAIL else 0.0
            c.execute("UPDATE login_fail SET fails=?, locked_until=?, last_fail=? WHERE key=?",
                      (fails, locked, now, key))
        c.commit()


def login_fail_clear(key):
    c = db()
    with _db_lock:
        c.execute("DELETE FROM login_fail WHERE key=?", (key,))
        c.commit()


def auth_context(handler):
    """解析请求身份，返回 {user_id, username, is_admin, via}。

    via: 'user'  → X-User-Token 命中 sessions 表
         'admin' → X-Admin-Token 等于 config.admin_token（逃生通道 / 老看板兼容）
         None    → 未鉴权
    """
    tok = handler.headers.get("X-User-Token") or ""
    if tok:
        row = db().execute(
            "SELECT u.id, u.username, u.is_admin, u.status, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
            (tok,)).fetchone()
        if row:
            if row["status"] != "active":
                return {"user_id": 0, "username": "", "is_admin": False, "via": None}
            if (row["expires_at"] or 0) < time.time():
                drop_session(tok)
                return {"user_id": 0, "username": "", "is_admin": False, "via": None}
            return {"user_id": row["id"], "username": row["username"],
                    "is_admin": bool(row["is_admin"]), "via": "user"}
    if _admin_token and handler.headers.get("X-Admin-Token") == _admin_token:
        return {"user_id": 0, "username": "admin", "is_admin": True, "via": "admin"}
    return {"user_id": 0, "username": "", "is_admin": False, "via": None}


def require_user(auth):
    """鉴权门槛：未登录返回 False。"""
    return bool(auth and auth.get("via"))


def visible_rig_names(auth):
    """当前身份可见的矿机 name 列表。

    这是数据隔离的唯一落点——所有读写 rig 的接口都必须经过它。

    可见性规则：
      · admin    → 全部矿机（含未绑定的无主矿机）
      · 普通用户 → **仅自己名下**的矿机

    无主矿机（owner_id=0）只对 admin 可见，不对普通用户可见。
    这既保证了「无主矿机可以直接接入中控」（接入 = 允许上报，
    不受归属限制），又守住了 D5「不支持多用户只读共享」——
    若让无主矿机对所有普通用户可见，就等于开了只读共享的口子。

    归属的产生方式：矿机端首次登录（/api/rig_login）自动绑定，
    绑定后该矿机立刻从 admin 的公共视图转入该用户的私有集合。
    """
    c = db()
    if auth.get("is_admin"):
        return [r["name"] for r in c.execute("SELECT name FROM rigs ORDER BY name")]
    uid = auth.get("user_id") or 0
    if not uid:
        return []
    return [r["name"] for r in
            c.execute("SELECT name FROM rigs WHERE owner_id=? ORDER BY name", (uid,))]


def rig_visible(auth, rig):
    """单台矿机是否对当前身份可见（规则同 visible_rig_names）。"""
    if not rig:
        return False
    if auth.get("is_admin"):
        return db().execute("SELECT 1 FROM rigs WHERE name=?", (rig,)).fetchone() is not None
    uid = auth.get("user_id") or 0
    if not uid:
        return False
    return db().execute("SELECT 1 FROM rigs WHERE name=? AND owner_id=?",
                        (rig, uid)).fetchone() is not None


def rig_owner_name(uid):
    if not uid:
        return ""
    row = user_by_id(uid)
    return row["username"] if row else ""


_audit_lock = threading.Lock()


def log_owner_change(rig, old_user, new_user, action, note=""):
    """写归属变更审计。

    ★ 用独立的 _audit_lock，而不是 _db_lock：
      _db_lock 是非可重入的普通锁，若调用方已持有它再进这里 → 死锁。
      审计写入与业务写入互不冲突（不同表），用独立锁更安全。
    """
    c = db()
    with _audit_lock:
        c.execute("INSERT INTO rig_owner_log(rig,old_user,new_user,action,note,ts) "
                  "VALUES(?,?,?,?,?,?)",
                  (rig, old_user, new_user, action, note, time.time()))
        c.commit()


def _clear_cfg_admin_password():
    """预置完成后清掉 config.json 里的明文 admin_password。

    config.json 是 bind-mount :ro 挂进容器的，改它需要宿主机有写权限；
    失败不影响功能（只是明文多留一会儿），所以只打印提示不抛错。
    """
    try:
        if not _cfg.get("admin_password"):
            return
        cfg2 = dict(_cfg)
        cfg2.pop("admin_password", None)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg2, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
        _cfg.pop("admin_password", None)
        print("[auth] 已清除 config.json 中的明文初始密码", flush=True)
    except Exception as e:  # noqa: BLE001
        print("[auth] 清除 config.json 明文密码失败（不影响使用）: %s" % e, flush=True)


def ensure_admin_user():
    """D1：首次启动强制交互创建管理员。

    优先级：
    1. users 表已有 admin → 直接返回（不打扰）
    2. 环境变量 ADMIN_USER / ADMIN_PASSWORD → 预置（Docker/systemd 部署用）
    3. config.json 的 admin_user / admin_password → 预置（部署脚本写入，config.json 已是持久卷）
    4. 有 TTY → 阻塞式交互输入用户名/密码（不回显，需二次确认）
    5. 都没有 → 明确报错退出（绝不生成随机密码打印到日志）

    预置出来的账号会带上 must_change_pwd=1 标记：
    **首次登录时必须先改密**，改完才能进系统。
    这样部署脚本可以用一个一次性的临时密码完成开箱，
    而不必把长期有效的密码明文留在 config.json 里。
    """
    row = db().execute("SELECT id FROM users WHERE is_admin=1 LIMIT 1").fetchone()
    if row:
        return True
    env_u = os.environ.get("ADMIN_USER") or ""
    env_p = os.environ.get("ADMIN_PASSWORD") or ""
    cfg_u = str(_cfg.get("admin_user") or "")
    cfg_p = str(_cfg.get("admin_password") or "")
    name = env_u or cfg_u
    pwd = env_p or cfg_p
    if name and pwd:
        ok, err = validate_username(name, allow_reserved=True)
        if not ok:
            print("[auth] 预置管理员用户名不合法: %s" % err, flush=True)
            return False
        ok, err = validate_password(pwd)
        if not ok:
            print("[auth] 预置管理员密码不合法: %s" % err, flush=True)
            return False
        _insert_user(name, pwd, is_admin=1, must_change_pwd=1)
        print("[auth] 已预置管理员账号: %s（首次登录须修改密码）" % name, flush=True)
        # 预置完成后清掉 config.json 里的明文密码，避免长期留盘
        _clear_cfg_admin_password()
        return True
    # 交互式
    try:
        if not sys.stdin or not sys.stdin.isatty():
            print("[auth] ============================================================", flush=True)
            print("[auth] 首次启动：必须创建管理员账号。", flush=True)
            print("[auth] 当前环境无交互终端（Docker/systemd），请用以下任一方式预置：", flush=True)
            print("[auth]   环境变量：ADMIN_USER=xxx ADMIN_PASSWORD=xxx", flush=True)
            print("[auth]   配置文件：config.json 写入 admin_user / admin_password", flush=True)
            print("[auth] ============================================================", flush=True)
            return False
    except Exception:  # noqa: BLE001
        return False
    print("[auth] ============================================================", flush=True)
    print("[auth] 首次启动：创建管理员账号（这是唯一一次交互）", flush=True)
    print("[auth] ============================================================", flush=True)
    name = ""
    while True:
        try:
            name = input("管理员用户名 [admin]: ").strip() or "admin"
        except (EOFError, KeyboardInterrupt):
            print("\n[auth] 已取消，服务端退出。", flush=True)
            return False
        ok, err = validate_username(name, allow_reserved=True)
        if ok:
            break
        print("[auth] %s" % err, flush=True)
    while True:
        try:
            import getpass
            pwd = getpass.getpass("管理员密码 (至少 6 位，不回显): ")
        except (EOFError, KeyboardInterrupt):
            print("\n[auth] 已取消，服务端退出。", flush=True)
            return False
        ok, err = validate_password(pwd)
        if not ok:
            print("[auth] %s" % err, flush=True)
            continue
        try:
            pwd2 = getpass.getpass("确认密码: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[auth] 已取消，服务端退出。", flush=True)
            return False
        if pwd != pwd2:
            print("[auth] 两次输入不一致，请重试。", flush=True)
            continue
        break
    _insert_user(name, pwd, is_admin=1)
    print("[auth] 管理员账号已创建: %s" % name, flush=True)
    return True


def _insert_user(username, password, is_admin=0, must_change_pwd=0):
    h, s = hash_password(password)
    c = db()
    now = time.time()
    with _db_lock:
        c.execute("INSERT INTO users(username,pwd_hash,pwd_salt,is_admin,status,"
                  "created_at,last_login_at,must_change_pwd) VALUES(?,?,?,?,?,?,?,?)",
                  (username, h, s, 1 if is_admin else 0, "active", now, now,
                   1 if must_change_pwd else 0))
        c.commit()
    return db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def register_user(username, password):
    """注册。返回 (ok, payload)。payload 含 token（注册后直接登录）。"""
    if not _allow_register:
        return False, {"ok": False, "error": "本中控已关闭注册，请联系管理员开号"}
    ok, err = validate_username(username)
    if not ok:
        return False, {"ok": False, "error": err}
    ok, err = validate_password(password)
    if not ok:
        return False, {"ok": False, "error": err}
    if user_by_name(username) is not None:
        return False, {"ok": False, "error": "用户名已存在"}
    try:
        row = _insert_user(username, password, is_admin=0)
    except sqlite3.IntegrityError:
        return False, {"ok": False, "error": "用户名已存在"}
    tok = create_session(row["id"])
    return True, {"ok": True, "token": tok, "user": user_public(row)}


def login_user(username, password, ua="", ip=""):
    """登录。返回 (ok, payload)。防爆破：用户名 + IP 双维度计数。"""
    username = str(username or "").strip()
    key_u = "u:" + username.lower()
    key_i = "i:" + str(ip or "")
    for k in (key_u, key_i):
        left = login_fail_check(k)
        if left:
            return False, {"ok": False, "error": "登录失败次数过多，请 %d 秒后再试" % left}
    row = user_by_name(username)
    if row is None:
        login_fail_record(key_u)
        login_fail_record(key_i)
        return False, {"ok": False, "error": "用户名或密码错误"}
    if not verify_password(password, row["pwd_hash"], row["pwd_salt"]):
        login_fail_record(key_u)
        login_fail_record(key_i)
        return False, {"ok": False, "error": "用户名或密码错误"}
    if row["status"] != "active":
        return False, {"ok": False, "error": "该账号已被禁用"}
    login_fail_clear(key_u)
    login_fail_clear(key_i)
    c = db()
    # ★ 部署预置的管理员：首次登录必须改密。此时不发 token，
    #   要求先用旧密码换新密码，换完才能真正进系统。
    if _row_must_change(row):
        with _db_lock:
            c.execute("UPDATE users SET last_login_at=? WHERE id=?",
                      (time.time(), row["id"]))
            c.commit()
        return True, {"ok": True, "need_password_change": True,
                      "user": user_public(user_by_id(row["id"]))}
    with _db_lock:
        c.execute("UPDATE users SET last_login_at=? WHERE id=?", (time.time(), row["id"]))
        c.commit()
    tok = create_session(row["id"], ua, ip)
    fresh = user_by_id(row["id"])
    return True, {"ok": True, "token": tok, "user": user_public(fresh)}


def change_password(user_id, old_password, new_password):
    """修改密码。返回 (ok, payload)。

    用于「部署预置账号首次登录强制改密」与「用户自助改密」。
    校验旧密码 → 写新哈希 → 清 must_change_pwd 标记。
    """
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return False, {"ok": False, "error": "用户不存在"}
    if not verify_password(old_password, row["pwd_hash"], row["pwd_salt"]):
        return False, {"ok": False, "error": "原密码不正确"}
    ok, err = validate_password(new_password)
    if not ok:
        return False, {"ok": False, "error": err}
    if new_password == old_password:
        return False, {"ok": False, "error": "新密码不能与原密码相同"}
    h, s = hash_password(new_password)
    with _db_lock:
        db().execute("UPDATE users SET pwd_hash=?, pwd_salt=?, "
                     "must_change_pwd=0 WHERE id=?", (h, s, user_id))
        db().commit()
    print("[auth] 用户 %s 已修改密码" % row["username"], flush=True)
    return True, {"ok": True, "username": row["username"]}


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
    global _rate_map, _rate_ts, _rate_meta, _rate_meta_lc, _rate_avg
    while True:
        try:
            req = urllib.request.Request(
                INCOME_SRC,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) bapiminer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            m, meta = {}, {}
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
                if len(cells) < 4:
                    continue
                name = re.sub(r"<small>.*?</small>", "", cells[0], flags=re.S)
                name = re.sub(r"<[^>]+>", "", name).strip()
                ths = _cell_num(cells[1], r"([\d.]+)\s*TH/s")
                prl = _cell_num(cells[2], r"([\d.]+)\s*PRL/day")
                usdt = _cell_num(cells[3], r"([\d.]+)\s*USDT")
                pw = _cell_num(cells[4], r"([\d.]+)\s*W") if len(cells) > 4 else None
                if name and ths and usdt:
                    m[name] = usdt / ths
                    meta[name] = {"ths": ths, "prl_day": prl, "usdt_day": usdt, "power_w": pw}
            if m:
                tot_ths = sum(v["ths"] for v in meta.values())
                tot_usdt = sum(v["usdt_day"] for v in meta.values())
                with _rate_lock:
                    _rate_map = m
                    _rate_meta = meta
                    _rate_meta_lc = {k.lower(): k for k in m}
                    _rate_avg = tot_usdt / tot_ths if tot_ths else 0.0
                    _rate_ts = time.time()
                print("[rate] vminers 收益率更新：%d 个型号，均价 %.4f USDT/TH/s/天"
                      % (len(m), _rate_avg), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[rate] 拉取失败: %s" % e, flush=True)
        _fetch_cny_rate()
        time.sleep(600)


def _fetch_cny_rate():
    """动态拉取 USDT->CNY 汇率；全部失败时沿用旧值。"""
    global _cny_rate
    sources = [
        # 社区货币 API（含 USDT 兑 CNY）
        ("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usdt.json",
         lambda d: d.get("usdt", {}).get("cny")),
        # 兜底：USD 官方汇率近似 USDT
        ("https://open.er-api.com/v6/latest/USD",
         lambda d: (d.get("rates") or {}).get("CNY")),
    ]
    for url, pick in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                val = pick(json.loads(r.read().decode("utf-8")))
            if val and 4.0 < float(val) < 10.0:
                with _rate_lock:
                    globals()["_cny_rate"] = round(float(val), 4)
                print("[rate] USDT/CNY 汇率更新：%.4f" % _cny_rate, flush=True)
                return
        except Exception:  # noqa: BLE001
            continue
    print("[rate] 汇率拉取失败，沿用 %.4f" % _cny_rate, flush=True)


def rate_cny():
    with _rate_lock:
        return _cny_rate


# ---------------------------------------------------------------- 币价抓取

def _http_get_json(url, timeout=12):
    """带 UA 的 GET，返回解析后的 JSON；失败抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw)


def _num(v, default=None):
    """安全转 float：None/空串/非数字/NaN/Inf 一律返回 default。

    ★ 行情接口的字段全是字符串，且异常时可能返回 "not-a-number" 这类脏值。
      不做校验的话脏值会一路混进价格表，前端展示成 NaN。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):   # NaN / Inf
        return default
    return f


def _fetch_prices_safetrade():
    """SafeTrade 公开行情 API（无需鉴权）。

    单市场端点：GET {base}/trade/public/tickers/{market}
    返回：{"id","name","base_unit","quote_unit","avg_price","high","last",
           "low","open","price_change_percent","volume","amount"}
    ★ /trade/public/tickers（不带 market）只返回市场名列表，没有价格，别用。
    """
    base = os.environ.get("SAFETRADE_BASE", "https://safe.trade/api/v2")
    out = {}
    for sym, market, _cn in PRICE_COINS:
        # ★ SafeTrade 的 market id = 基础币+计价币 小写拼接，如 PEARL -> "pearlusdt"
        mid = "%susdt" % market.lower()
        try:
            d = _http_get_json("%s/trade/public/tickers/%s" % (base, mid))
            last = _num(d.get("last"), 0)
            if last <= 0:
                continue
            chg = str(d.get("price_change_percent") or "0%").replace("%", "").strip()
            out[sym] = {
                "last": last,
                "change": _num(chg, 0.0),
                "high": _num(d.get("high")) or None,
                "low": _num(d.get("low")) or None,
                "volume": _num(d.get("volume")) or None,
            }
        except Exception:  # noqa: BLE001
            continue
    return out


def _fetch_prices_gate():
    """Gate.io 公开行情 API（允许跨域，生产服务器可直连）。

    GET /api/v4/spot/tickers?currency_pair=BTC_USDT
    返回：{"currency_pair","last","change_percentage","high_24h","low_24h",
           "base_volume","quote_volume",...}
    """
    out = {}
    for sym, market, _cn in PRICE_COINS:
        try:
            rows = _http_get_json(
                "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=%s_USDT" % market)
            if not isinstance(rows, list) or not rows:
                continue
            d = rows[0]
            last = _num(d.get("last"), 0)
            if last <= 0:
                continue
            out[sym] = {
                "last": last,
                "change": _num(d.get("change_percentage"), 0.0),
                "high": _num(d.get("high_24h")) or None,
                "low": _num(d.get("low_24h")) or None,
                "volume": _num(d.get("base_volume")) or None,
            }
        except Exception:  # noqa: BLE001
            continue
    return out


def _fetch_prices_coinbase():
    """Coinbase 现货价兜底 —— ★ 当前已停用。

    Coinbase 没有 Pearl(PRL) 交易对，拿不到平台收益币种，
    而 _fetch_prices 只认「拿到了 PRL」的源，所以这个兜底永远不会被选中，
    留着只是白打一次 HTTP。保留函数体仅作历史参考，_PRICE_FETCHERS 中不再引用。
    """
    out = {}
    for sym, _market, _cn in PRICE_COINS:
        if sym == "PRL":
            continue
        try:
            d = _http_get_json("https://api.coinbase.com/v2/prices/%s-USD/spot" % sym)
            last = _num((d.get("data") or {}).get("amount"), 0)
            if last > 0:
                out[sym] = {"last": last, "change": 0.0, "high": None,
                            "low": None, "volume": None}
        except Exception:  # noqa: BLE001
            continue
    return out


# 按优先级排列的抓取器：SafeTrade 为首选（用户指定），Gate.io 为可靠兜底
# ★ 只保留能提供 PRL(珍珠币) 的源 —— 平台收益口径就是 PRL，其他币种无业务意义。
_PRICE_FETCHERS = [
    ("SafeTrade", _fetch_prices_safetrade),
    ("Gate.io", _fetch_prices_gate),
]


def _fetch_prices():
    """依次尝试各数据源，取第一个能拿到 PRL（珍珠币）的结果。

    ★ 只有拿到 PRL 才算成功 —— 平台只展示珍珠币的币价，
      没有 PRL 的源一律不采纳（哪怕它返回了别的币种），
      否则页面会显示与业务无关的币价。
    """
    global _price_ts, _price_src
    best, best_src = {}, ""
    for name, fn in _PRICE_FETCHERS:
        try:
            got = fn()
        except Exception as e:  # noqa: BLE001
            print("[price] %s 抓取异常: %s" % (name, e), flush=True)
            continue
        if not got:
            continue
        # ★ 聚合层再校验一次：各 fetcher 内部已做 _num 转换，
        #   但这里是最后一道闸 —— 脏值（"not-a-number"/NaN/<=0）绝不能进价格表。
        clean = {}
        for sym, d in got.items():
            if not isinstance(d, dict):
                continue
            last = _num(d.get("last"))
            if not last or last <= 0:
                continue
            clean[sym] = {
                "last": last,
                "change": _num(d.get("change"), 0.0),
                "high": _num(d.get("high")),
                "low": _num(d.get("low")),
                "volume": _num(d.get("volume")),
            }
        if not clean:
            continue
        # ★ 只认 PRL。数据源拿不到珍珠币 → 跳过，继续试下一个源。
        if "PRL" not in clean:
            continue
        best, best_src = clean, name
        break
    if best:
        with _price_lock:
            _price_map.clear()
            _price_map.update(best)
            _price_ts = time.time()
            _price_src = best_src
        print("[price] 珍珠币价更新：PRL %s，来源 %s"
              % (best.get("PRL", {}).get("last"), best_src), flush=True)
    else:
        print("[price] 珍珠币价抓取失败，沿用旧值", flush=True)


def price_payload():
    """对外返回的币价数据结构。"""
    with _price_lock:
        coins = []
        for sym, market, cn in PRICE_COINS:
            d = _price_map.get(sym)
            if not d:
                continue
            last = _num(d.get("last"))
            if not last or last <= 0:
                continue
            coins.append({
                "symbol": sym,
                "market": market,
                "name": cn,
                "last": last,
                "change": _num(d.get("change"), 0.0),
                "high": _num(d.get("high")),
                "low": _num(d.get("low")),
                "volume": _num(d.get("volume")),
            })
        return {
            "ok": True,
            "coins": coins,
            "source": _price_src,
            "ts": _price_ts,
            "age": int(time.time() - _price_ts) if _price_ts else None,
            "cny_rate": rate_cny(),
        }


def price_fetch_loop():
    """币价后台刷新线程：启动时立刻拉一次，之后每 5 分钟一次。"""
    while True:
        try:
            _fetch_prices()
        except Exception as e:  # noqa: BLE001
            print("[price] 刷新循环异常: %s" % e, flush=True)
        time.sleep(300)


def _cell_num(cell, pattern):
    mt = re.search(pattern, cell)
    return float(mt.group(1)) if mt else None


def _norm_model(model):
    """统一显卡型号写法，便于与 vminers 收益率表比对。"""
    s = (model or "").strip()
    for p in ("NVIDIA GeForce ", "NVIDIA ", "AMD Radeon ", "AMD ", "Intel Arc ", "Intel "):
        if s.startswith(p):
            s = s[len(p):].strip()
            break
    # 笔记本卡写法归一：RTX 5080 Laptop GPU -> RTX 5080 Laptop（vminers 独立条目）
    if s.endswith(" Laptop GPU"):
        s = s[: -len(" GPU")]
    return s


def _rate_key(model):
    """型号 -> 收益率表键（精确/小写/后缀变体/词边界前缀）。未命中返回 None。"""
    s = _norm_model(model)
    with _rate_lock:
        if not _rate_map or not s:
            return None
        for cand in (s, (model or "").strip()):  # 规范名 + 原始名（AMD 全名条目）
            if cand in _rate_map:
                return cand
            lc = _rate_meta_lc.get(cand.lower())
            if lc:
                return lc
        # 笔记本卡降级到桌面卡：RTX 5080 Laptop -> RTX 5080
        for suf in (" Laptop", " (Laptop)", " GPU"):
            base = s[: -len(suf)].strip()
            if s.endswith(suf) and base in _rate_map:
                return base
        # 词边界前缀兜底（长键优先，避免 RTX 5090 误吞 RTX 5090 D）
        for k in sorted(_rate_map, key=len, reverse=True):
            if s.startswith(k + " "):
                return k
    return None


def rate_for(model):
    k = _rate_key(model)
    if k:
        with _rate_lock:
            return _rate_map[k]
    return None


def rate_avg():
    """vminers 全表加权均价 USDT/TH/s/天（收益计算用，与显卡型号无关）。"""
    with _rate_lock:
        return _rate_avg


def rate_meta_for(model):
    k = _rate_key(model)
    if k:
        with _rate_lock:
            return dict(_rate_meta.get(k) or {}, ref_model=k)
    return None


# ---------------------------------------------------------------- 矿池钱包
# 未支付余额：矿池 API（实时）
#   Kryptex：GET /{coin}/api/v1/miner/balance/{wallet}（prl.kryptex.network 子域即币种代码）
# 真实收入（今日实际收入/实收/近7日/近30日/按天明细）：链上交易，Pearl 浏览器 API
#   GET https://api.prlscan.com/v1/addresses/{wallet}/txs?limit=100[&cursor=...]
#   -> [{time:"2026-10-23T11:02:01Z", received_grains, sent_grains, type_label}]
#   1 PRL = 1e8 grains；收到即计入（type_label 为 *payout 的池打款 + 其它转入）。
# 为什么不用 pool.kryptex.com 的 payouts / reward-chart：该池 2026-10-20 迁移后
# 这两个接口停止更新（payouts 最后一笔停在 10-20 10:19 UTC，reward-chart 10-21 起
# 恒为 0），而矿机一直全速在挖、链上每 ~4 小时准时打款。以链口为准，与钱包页面
# （prlscan.com）看到的数字一致；balance 接口仍然实时，继续用于「矿池未支付」。

_wallet_lock = threading.Lock()
_wallet_map = {}   # rig -> {coin, wallet, total, confirmed, unconfirmed, threshold, pct, last_active, ts}
_wallet_ts = 0.0


def pool_coin(pool_url):
    """从矿池地址推断币种代码：prl.kryptex.network:7048 / prl-hk.kryptex.network:7048 -> prl
    （区属后缀 -hk/-eu 等不是币种，pool.kryptex.com 只认纯币种代码，带后缀会 502）"""
    host = (pool_url or "").replace("stratum+tcp://", "").replace("stratum+ssl://", "")
    host = host.split("/")[0].split(":")[0].lower()
    if host.endswith("kryptex.network") or host.endswith("kryptex.com"):
        return host.split(".")[0].split("-")[0]
    return ""


def wallet_fetch_one(coin, wallet, pool_url=""):
    """按 rig 当前所在矿池拉「未支付余额」。

    pool_url 为空时按 Kryptex 兼容旧调用；该池没有公开余额 API 时返回
    unpaid_supported=False（total=None）——看板标注「待付不可查」，
    而不是把别的池（甚至已停更的旧池）数字冒充成当前池的待付。
    """
    brand = pool_brand_of(pool_url) or ("kryptex" if not pool_url else "")
    base = {"coin": coin, "pool_brand": brand, "ts": time.time()}
    tpl = POOL_BALANCE_API.get(brand)
    if not tpl:
        base.update({"unpaid_supported": False, "total": None, "confirmed": None,
                     "unconfirmed": None, "threshold": None, "pct": None,
                     "last_active": 0})
        return base
    url = tpl.format(coin=coin, wallet=quote(wallet.strip()))
    req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    base.update({
        "unpaid_supported": True,
        "total": round(float(d.get("total") or 0), 6),
        "confirmed": round(float(d.get("confirmed") or 0), 6),
        "unconfirmed": round(float(d.get("unconfirmed") or 0), 6),
        "threshold": float(d.get("threshold") or 0),
        "pct": float(d.get("reached_pct") or 0),
        "last_active": d.get("last_active") or 0,
    })
    return base


def _rig_coin(row):
    """按矿机最近上报的矿池推断币种，推断不出默认 prl。"""
    try:
        if row["last_report"]:
            snap = json.loads(row["last_report"]) or {}
            miners = snap.get("miners") or []
            c = pool_coin((miners[0].get("pool") if miners else "") or "")
            if c:
                return c
    except Exception:  # noqa: BLE001
        pass
    return "prl"


def _rig_pool_url(row):
    """rig 当前矿池地址：优先 rigs.pool_url 列（set_pool 即时生效），
    其次上报快照里的 pool_url，最后退回矿工 API 回报的池地址。"""
    try:
        u = (row["pool_url"] if "pool_url" in row.keys() else "") or ""
    except (IndexError, KeyError):
        u = ""
    if u.strip():
        return u.strip()
    try:
        if row["last_report"]:
            snap = json.loads(row["last_report"]) or {}
            u = (snap.get("pool_url") or "").strip()
            if u:
                return u
            miners = snap.get("miners") or []
            if miners:
                return (miners[0].get("pool") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


_EXPLORER_API = {"prl": "https://api.prlscan.com"}  # 币种 -> 链上浏览器 API 基址


def wallet_chain_txs(coin, wallet, pages=3):
    """钱包链上交易（时间倒序，最多 pages*100 条，覆盖 30 天足够）。

    浏览器 API 单页上限 100 条，带 next_cursor 翻页。
    """
    base = _EXPLORER_API.get((coin or "").lower(), "https://api.prlscan.com")
    items, cursor = [], ""
    for _ in range(max(1, pages)):
        url = "%s/v1/addresses/%s/txs?limit=100" % (base, quote(wallet.strip()))
        if cursor:
            url += "&cursor=" + quote(cursor)
        req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
        batch = (d.get("items") or []) if isinstance(d, dict) else []
        items.extend(batch)
        cursor = (d.get("next_cursor") or "") if isinstance(d, dict) else ""
        if not cursor or not batch:
            break
    return items


_TX_POOL_NAMES = (("pearlhash", "PearlHash"), ("kryptex", "Kryptex"),
                  ("luckypool", "LuckyPool"))


def tx_pool(t):
    """链上打款交易的来源池：prlscan 的 type_label 形如「PearlHash payout」。
    识别不出归「其它」（自定义池/直接转账）。"""
    lab = (t.get("type_label") or "").lower() if isinstance(t, dict) else ""
    for pid, name in _TX_POOL_NAMES:
        if pid in lab:
            return pid, name
    return "other", "其它"


def chain_income_stats(txs):
    """链上交易 -> 收入统计（按北京时间归集，收到即计入）。

    返回 {total, h24, today, yesterday, d7, d30, count, days,
          pools, days_pool}：
    today 即看板「今日实际收入」（钱包今天实际收到的币数，含矿池打款）；
    yesterday/d7/d30 同口径；days 为 {北京日期: 金额}；
    pools 为 {池id: {name, total, h24, today, yesterday, d7, d30}}（按打款来源
    池拆分——同一钱包在不同池挖，收益按池标注，参考 vminers 的着色口径）；
    days_pool 为 {日期: {池id: 金额}}，供按天明细展示各池贡献。
    """
    now = time.time()
    TZ8 = 8 * 3600
    today0 = int((now + TZ8) // 86400) * 86400 - TZ8  # 北京时间当天零点
    y0 = today0 - 86400
    stats = {"total": 0.0, "h24": 0.0, "today": 0.0, "yesterday": 0.0,
             "d7": 0.0, "d30": 0.0, "count": 0, "days": {},
             "pools": {}, "days_pool": {}}
    for t in txs or []:
        try:
            amt = float(t.get("received_grains") or 0) / 1e8
            dt = datetime.strptime((t.get("time") or "")[:19], "%Y-%m-%dT%H:%M:%S")
            ts = dt.replace(tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError, AttributeError):
            continue
        if amt <= 0 or ts <= 0:
            continue
        stats["count"] += 1
        stats["total"] += amt
        if ts > now - 86400:
            stats["h24"] += amt
        if ts >= today0:
            stats["today"] += amt
        if y0 <= ts < today0:
            stats["yesterday"] += amt
        if ts > now - 7 * 86400:
            stats["d7"] += amt
        if ts > now - 30 * 86400:
            stats["d30"] += amt
        day = time.strftime("%Y-%m-%d", time.gmtime(ts + TZ8))
        stats["days"][day] = stats["days"].get(day, 0.0) + amt
        # ---- 按打款来源池归集（vminers 式分池口径）----
        pid, pname = tx_pool(t)
        p = stats["pools"].setdefault(pid, {"name": pname, "total": 0.0, "h24": 0.0,
                                            "today": 0.0, "yesterday": 0.0,
                                            "d7": 0.0, "d30": 0.0})
        p["total"] += amt
        if ts > now - 86400:
            p["h24"] += amt
        if ts >= today0:
            p["today"] += amt
        if y0 <= ts < today0:
            p["yesterday"] += amt
        if ts > now - 7 * 86400:
            p["d7"] += amt
        if ts > now - 30 * 86400:
            p["d30"] += amt
        dp = stats["days_pool"].setdefault(day, {})
        dp[pid] = dp.get(pid, 0.0) + amt
    # 累计过程不做四舍五入（每步 round 会累积误差），返回前统一取 6 位
    for k in ("total", "h24", "today", "yesterday", "d7", "d30"):
        stats[k] = round(stats[k], 6)
    stats["days"] = {d: round(v, 6) for d, v in stats["days"].items()}
    stats["pools"] = {pid: {k: (round(v, 6) if isinstance(v, float) else v)
                            for k, v in p.items()}
                      for pid, p in stats["pools"].items()}
    stats["days_pool"] = {d: {pid: round(v, 6) for pid, v in dp.items()}
                          for d, dp in stats["days_pool"].items()}
    return stats


def wallet_loop():
    global _wallet_map, _wallet_ts
    while True:
        try:
            rows = db().execute(
                "SELECT name, wallet, pool_url, last_report FROM rigs "
                "WHERE wallet IS NOT NULL AND wallet != ''").fetchall()
            # 同一钱包可能被多台矿机共用，且这些矿机可能分处不同矿池：
            # 待付余额按「钱包 + 所在池」分组，各拉各的（不同池余额不互通）。
            # 只按钱包分组会拿任意一台的池去查所有台——可查池的余额因此永远拉不到。
            uniq = {}
            for r in rows:
                coin = _rig_coin(r)
                brand = pool_brand_of(_rig_pool_url(r)) or ""
                uniq.setdefault((coin, r["wallet"].strip(), brand), []).append(r["name"])
            got = {}
            chain_cache = {}
            for (coin, wallet, brand), names in uniq.items():
                try:
                    purl = _rig_pool_url(next(r for r in rows if r["name"] == names[0]))
                    w = wallet_fetch_one(coin, wallet, purl)
                    w["wallet"] = wallet
                    # 链上收益/打款与池无关：同一钱包整轮只拉一次，多池共用
                    if wallet not in chain_cache:
                        try:
                            chain_cache[wallet] = chain_income_stats(
                                wallet_chain_txs(coin, wallet))
                        except Exception as e:  # noqa: BLE001
                            chain_cache[wallet] = None
                            print("[wallet] %s 链上交易拉取失败: %s" % (wallet[:12], e), flush=True)
                    inc = chain_cache.get(wallet)
                    if inc:
                        w["paid"] = inc
                        w["rewards"] = {k: inc[k] for k in
                                        ("today", "yesterday", "d7", "d30",
                                         "days", "pools", "days_pool")}
                    for n in names:
                        got[n] = dict(w)
                except Exception as e:  # noqa: BLE001
                    print("[wallet] %s@%s 拉取失败: %s" % (wallet[:12], brand or "-", e), flush=True)
            if got:
                with _wallet_lock:
                    _wallet_map = got
                    _wallet_ts = time.time()
                print("[wallet] 钱包余额更新：%d 台" % len(got), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[wallet] 循环异常: %s" % e, flush=True)
        time.sleep(300)


def wallet_for(rig):
    with _wallet_lock:
        v = _wallet_map.get(rig)
        return dict(v) if v else None


def wallet_stub(row):
    """钱包循环尚未拉到数据时，用 rigs.wallet 列给前端一个最小对象。

    否则服务重启后/首轮拉取前 KPI 会误显示「等待矿机上报钱包地址」——
    钱包其实早就上报了，只是中控还没来得及拉链上数据。
    pending=True 让前端显示「正在拉取」而不是 0 或误导文案。
    """
    try:
        w = (row["wallet"] if "wallet" in row.keys() else "") or ""
    except (IndexError, KeyError):
        return None
    if not w.strip():
        return None
    return {"wallet": w.strip(), "coin": _rig_coin(row), "total": None,
            "pending": True, "rewards": None, "paid": None}


def unique_wallets(with_pool=False, rig_names=None):
    """_wallet_map 快照去重：同一钱包可能被多台矿机共用（各自上报同一地址）。

    with_pool=False：按 (币种,钱包) 去重——打款/收益是链上的，同一钱包只能算一次。
    with_pool=True ：按 (币种,钱包,所在池) 去重——待付余额按池各算各的，
                     同一钱包挂在两个池上是两份独立余额，合成一条会把其中一个池抹掉。
    rig_names：限定只统计这些矿机的钱包（多用户隔离用）。None = 不限（admin 视角）。
    """
    allow = set(rig_names) if rig_names is not None else None
    with _wallet_lock:
        seen, out = set(), []
        for rig, v in _wallet_map.items():
            if allow is not None and rig not in allow:
                continue
            w = (v.get("wallet") or "").strip()
            if not w:
                continue
            coin = v.get("coin") or ""
            if with_pool:
                wk = (coin, w, v.get("pool_brand") or "")
            else:
                wk = (coin, w)
            if wk in seen:
                continue
            seen.add(wk)
            out.append(v)
        return out


def wallet_set(rig, wallet):
    """看板手工绑定钱包（客户端上报也会写同列）。立即拉一次余额。"""
    wallet = (wallet or "").strip()
    c = db()
    row = c.execute("SELECT name FROM rigs WHERE name=?", (rig,)).fetchone()
    if row is None:
        return {"ok": False, "error": "矿机不存在：%s" % rig}
    with _db_lock:
        c.execute("UPDATE rigs SET wallet=? WHERE name=?", (wallet, rig))
        c.commit()
    out = {"ok": True, "rig": rig, "wallet": wallet}
    if wallet:
        try:
            r = c.execute("SELECT pool_url, last_report FROM rigs WHERE name=?", (rig,)).fetchone()
            coin = _rig_coin(r)
            w = wallet_fetch_one(coin, wallet, _rig_pool_url(r))
            w["wallet"] = wallet
            try:
                inc = chain_income_stats(wallet_chain_txs(coin, wallet))
                w["paid"] = inc
                w["rewards"] = {k: inc[k] for k in
                                ("today", "yesterday", "d7", "d30",
                                 "days", "pools", "days_pool")}
            except Exception as e:  # noqa: BLE001
                print("[wallet] %s 链上交易拉取失败: %s" % (rig, e), flush=True)
            with _wallet_lock:
                _wallet_map[rig] = w
                globals()["_wallet_ts"] = time.time()
            out["balance"] = w
        except Exception as e:  # noqa: BLE001
            out["fetch_error"] = str(e)
    return out


# ---------------------------------------------------------------- 上报处理

def rig_auth_check(name, tk, source=""):
    """矿机接入码校验。返回 None 表示通过。

    口令为空（管理员执行过『重置接入码』）时，允许第一个来报到的客户端
    用自己的口令重新绑定，解决重装/更换目录后 401 的问题。
    """
    if not name or not tk:
        return "auth failed"
    c = db()
    row = c.execute("SELECT token FROM rigs WHERE name=?", (name,)).fetchone()
    if row is None:
        return "auth failed"
    stored = row["token"] or ""
    if stored and stored != tk:
        return "token mismatch"
    if not stored:
        with _db_lock:
            c.execute("UPDATE rigs SET token=?, last_seen=? WHERE name=?",
                      (tk, time.time(), name))
            c.commit()
        add_alert(name, "info", "rig_rebind", "矿机 %s 已重新绑定接入码（%s）" % (name, source or "认领"))
        print("[rig] 重新绑定接入码：%s（%s）" % (name, source or "认领"))
    return None


def handle_report(payload):
    name = str(payload.get("name") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not name or not token:
        return {"ok": False, "error": "缺少 name/token"}
    c = db()
    row = c.execute("SELECT token, game_mode FROM rigs WHERE name=?", (name,)).fetchone()
    # display_name：中控里的展示名（可改）。rig_name/name 是机器身份，不可变。
    # 客户端改显示名后随 report 同步；这里只更新展示名，绝不动 name。
    disp = str(payload.get("display_name") or "").strip()
    if disp and len(disp) > 64:
        disp = disp[:64]
    if row is None:
        c.execute("INSERT INTO rigs(name,token,first_seen,last_seen,status,wallet,display_name) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (name, token, time.time(), time.time(), "online",
                   str(payload.get("wallet") or "").strip(), disp or name))
        c.commit()
        add_alert(name, "info", "rig_new", "新矿机接入：%s" % name)
        print("[rig] 新矿机注册：%s" % name)
    elif row["token"] and row["token"] != token:
        return {"ok": False, "error": "token mismatch（本机接入码与中控登记不一致，"
                                      "请在中控该矿机卡片点『重置接入码』后重试）"}
    elif not row["token"]:
        with _db_lock:
            c.execute("UPDATE rigs SET token=? WHERE name=?", (token, name))
            c.commit()
        add_alert(name, "info", "rig_rebind", "矿机 %s 已重新绑定接入码（上报认领）" % name)
        print("[rig] 重新绑定接入码：%s（上报认领）" % name)
    old_gm = int((row["game_mode"] if row is not None else 0) or 0)
    gm = 1 if payload.get("game_mode") else 0

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
    # 收益 = 总算力 × vminers 全表均价(USDT/TH/s/天) × 实时汇率（型号仅做参考展示，不参与计算）
    income = None
    avg = rate_avg()
    if avg:
        income = round(ths * avg * rate_cny(), 2)
    ref = rate_meta_for(model)  # 参考信息：该卡在 vminers 榜上的对应条目

    snap = {
        "gpus": gpus, "miners": miners,
        "ths": round(ths, 2), "power": round(power, 1),
        "temp": temp, "fan": fan,
        "accepted": accepted, "rejected": rejected,
        "uptime": uptime, "model": model, "income_cny_day": income,
        "income_usdt_day": round(ths * avg, 2) if avg else None,
        "income_ref_model": (ref or {}).get("ref_model"),
        "income_ref_ths": (ref or {}).get("ths"),
        "hostname": payload.get("hostname") or "",
        "wallet": str(payload.get("wallet") or "").strip(),
        "client_version": payload.get("client_version") or "",
        "game_proc": payload.get("game_proc") or None,
        "kernel": payload.get("kernel") or "",
        "kernel_ver": payload.get("kernel_ver") or "",
        "pool_url": payload.get("pool_url") or "",
        "conflicts": payload.get("conflicts") or [],
    }
    with _db_lock:
        c.execute(
            "UPDATE rigs SET last_seen=?, last_report=?, status='online', offline_since=NULL, game_mode=?, "
            "wallet=CASE WHEN ?<>'' THEN ? ELSE wallet END, "
            "client_version=CASE WHEN ?<>'' THEN ? ELSE client_version END, "
            "pool_url=CASE WHEN ?<>'' THEN ? ELSE pool_url END, "
            "display_name=CASE WHEN ?<>'' THEN ? ELSE display_name END WHERE name=?",
            (time.time(), json.dumps(snap, ensure_ascii=False), gm,
             str(payload.get("wallet") or "").strip(), str(payload.get("wallet") or "").strip(),
             str(payload.get("client_version") or "").strip(),
             str(payload.get("client_version") or "").strip(),
             str(payload.get("pool_url") or "").strip(),
             str(payload.get("pool_url") or "").strip(),
             disp, disp, name))
        c.execute(
            "INSERT INTO samples(rig,ts,ths,power,temp,fan,accepted,rejected,uptime,pool) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, time.time(), ths, power, temp, fan, accepted, rejected, uptime,
             (miners[0].get("pool") if miners else "") or ""))
        c.commit()

    # 游戏模式进出告警（info 级，退出自动消除）
    if gm and not old_gm:
        gproc = payload.get("game_proc")
        add_alert(name, "info", "gamemode",
                  "🎮 游戏模式：检测到游戏进程 %s，已暂停挖矿让出显卡" % gproc
                  if gproc else "🎮 游戏模式：检测到游戏进程，已暂停挖矿让出显卡")
    elif not gm and old_gm:
        resolve_alert(name, "gamemode")

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
    # 本机其他挖矿程序（会抢显卡，是算力下降的常见原因）
    cf = payload.get("conflicts") or []
    if cf:
        add_alert(name, "warn", "foreign_miner",
                  "本机在跑其他挖矿程序：%s，会抢占显卡拉低算力，建议关闭"
                  % "、".join(str(x) for x in cf[:4]))
    else:
        resolve_alert(name, "foreign_miner")
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

# ---- 看板迷你曲线：固定窗口 + 等间隔抽点 ----
HIST_WINDOW_H = 12        # 曲线窗口（小时）
HIST_STEP_S = 600         # 抽点粒度（秒）→ 12h / 10min = 73 个点


def rig_history_series(rig, window_h=None, step=None):
    """返回固定窗口的等间隔序列；没有采样的桶为 None（前端据此断线）。

    为什么必须在服务端抽点：矿机端每 10 秒上报一次，12 小时就是 4300 个点，
    11 台机整个 /api/fleet 会到 2.3MB；而曲线只有 350px 宽，挤在一起后
    任何一次短暂停顿都会变成一根满高竖线（看起来像算力突然掉到底）。
    抽成 10 分钟均值后，短暂抖动被平均掉，真正的停机仍会留下缺口。
    """
    window_h = HIST_WINDOW_H if window_h is None else window_h
    step = HIST_STEP_S if step is None else step
    now = time.time()
    start = now - window_h * 3600
    c = db()
    rows = c.execute(
        "SELECT CAST(ts / ? AS INTEGER) AS b, AVG(ths) AS ths, AVG(power) AS power, "
        "AVG(temp) AS temp FROM samples WHERE rig=? AND ts>? GROUP BY b ORDER BY b",
        (step, rig, start)).fetchall()
    got = {int(x["b"]): x for x in rows}
    out = []
    for b in range(int(start // step), int(now // step) + 1):
        x = got.get(b)
        out.append({
            "ts": b * step,
            "ths": (round(x["ths"], 2) if x is not None and x["ths"] is not None else None),
            "power": (round(x["power"], 1) if x is not None and x["power"] is not None else None),
            "temp": (round(x["temp"], 1) if x is not None and x["temp"] is not None else None),
        })
    return out


def fleet_payload(auth=None):
    """全群聚合。auth 传入时按可见矿机集合过滤（多用户隔离）。

    auth=None 表示 admin 视角（不过滤）——只在内部调用使用。
    """
    c = db()
    if auth is not None:
        vis = set(visible_rig_names(auth))
    else:
        vis = None
    rigs = c.execute("SELECT * FROM rigs ORDER BY name").fetchall()
    out = []
    total = {"online": 0, "ths": 0.0, "power": 0.0, "income": 0.0, "gamemode": 0,
             "rate_age": int(time.time() - _rate_ts) if _rate_ts else None,
             "rate_avg_usdt": round(rate_avg(), 5),
             "cny_rate": rate_cny(),
             "client_latest": client_release().get("version") or ""}
    for r in rigs:
        if vis is not None and r["name"] not in vis:
            continue
        snap = json.loads(r["last_report"]) if r["last_report"] else None
        online = r["status"] == "online"
        r_gm = bool(r["game_mode"]) if "game_mode" in r.keys() else False
        if online:
            total["online"] += 1
            if r_gm:
                total["gamemode"] += 1
            if snap:
                total["ths"] += snap.get("ths") or 0
                total["power"] += snap.get("power") or 0
                total["income"] += snap.get("income_cny_day") or 0
        hist = rig_history_series(r["name"])
        out.append({
            "name": r["name"], "status": r["status"],
            "display_name": (r["display_name"] if "display_name" in r.keys() else "") or r["name"],
            "owner_id": (r["owner_id"] if "owner_id" in r.keys() else 0) or 0,
            "owner_name": rig_owner_name((r["owner_id"] if "owner_id" in r.keys() else 0) or 0),
            "last_seen": r["last_seen"],
            "offline_since": r["offline_since"],
            "game_mode": r_gm,
            "client_version": (r["client_version"] if "client_version" in r.keys()
                               else "") or (snap or {}).get("client_version") or "",
            "pool_url": (r["pool_url"] if "pool_url" in r.keys()
                         else "") or (snap or {}).get("pool_url") or "",
            "wallet": wallet_for(r["name"]) or wallet_stub(r),
            "snapshot": snap, "history": [dict(h) for h in hist],
        })
    # 同一钱包可能被多台矿机共用（各自上报同一地址），两套口径各算各的：
    #   打款/收益是链上的 → 按 (币种,钱包) 去重，否则按矿机台数翻倍
    #   待付余额是分池的   → 按 (币种,钱包,池) 去重，同一钱包挂两池各算各的
    # 多用户隔离：钱包聚合只能统计可见矿机的（D3：自己名下矿机的钱包明文展示）
    _uniq = unique_wallets(rig_names=vis)
    _uniq_pool = unique_wallets(with_pool=True, rig_names=vis)
    _coins = {v["coin"].upper() for v in _uniq}
    # 注意 total 可能为 None（所在池不提供余额查询）——按 0 计入合计，
    # 具体每台的「待付不可查」由 rig.wallet.unpaid_supported 标注
    total["wallet_total"] = round(sum((v.get("total") or 0) for v in _uniq_pool), 4)
    _paid = {"h24": 0.0, "yesterday": 0.0, "d7": 0.0, "d30": 0.0, "total": 0.0}
    for v in _uniq:
        p = v.get("paid") or {}
        for k in _paid:
            _paid[k] = round(_paid[k] + (p.get(k) or 0.0), 6)
    # 今日实际收入（链上真实到账，按北京时间归日）
    _reward = {"today": 0.0, "yesterday": 0.0, "d7": 0.0, "d30": 0.0}
    for v in _uniq:
        rs = v.get("rewards") or {}
        for k in _reward:
            _reward[k] = round(_reward[k] + (rs.get(k) or 0.0), 6)
    total["wallet_paid"] = _paid
    total["wallet_reward"] = _reward
    # ---- 按矿池拆分（对齐 vminers：Kryptex / PearlHash / LuckyPool / 其它 分色）----
    # wallet_pools[pid] = {name, unpaid, unpaid_supported, reward:{today..d30}}
    # unpaid 为该池账户的未支付余额（pool_brand 取自 rig 当前所在池）；
    # 无公开余额 API 的池 unpaid=None、unpaid_supported=False。
    _brand_names = {b["id"]: b["name"] for b in POOL_BRANDS}
    _wpools = {}
    for v in _uniq:
        for pid, p in ((v.get("rewards") or {}).get("pools") or {}).items():
            e = _wpools.setdefault(pid, {
                "name": p.get("name") or _brand_names.get(pid, pid),
                "unpaid": None, "unpaid_supported": None,
                "reward": {"today": 0.0, "yesterday": 0.0, "d7": 0.0, "d30": 0.0}})
            for k in e["reward"]:
                e["reward"][k] = round(e["reward"][k] + (p.get(k) or 0.0), 6)
    # 待付余额：按池各归各的（同一钱包挂在 Kryptex 与 PearlHash 是两份独立余额）
    for v in _uniq_pool:
        pid = v.get("pool_brand") or ""
        if not pid:
            continue
        e = _wpools.setdefault(pid, {
            "name": _brand_names.get(pid, pid),
            "unpaid": None, "unpaid_supported": None,
            "reward": {"today": 0.0, "yesterday": 0.0, "d7": 0.0, "d30": 0.0}})
        e["unpaid"] = v.get("total")
        e["unpaid_supported"] = v.get("unpaid_supported", True)
    total["wallet_pools"] = _wpools
    total["wallet_reward_coin"] = (next(iter(_coins)) if len(_coins) == 1
                                   else ("MIX" if _coins else ""))
    total["wallet_coin"] = _coins.pop() if len(_coins) == 1 else ("MIX" if _coins else "")
    total["wallet_age"] = int(time.time() - _wallet_ts) if _wallet_ts else None
    total["ths"] = round(total["ths"], 1)
    total["power"] = round(total["power"], 0)
    total["income"] = round(total["income"], 0)
    return {"ok": True, "now": now_cn(), "total": total, "rigs": out, "history_hours": HIST_WINDOW_H}


def history_payload(rig, hours):
    c = db()
    rows = c.execute(
        "SELECT ts,ths,power,temp,fan,accepted,rejected FROM samples WHERE rig=? AND ts>? ORDER BY ts",
        (rig, time.time() - hours * 3600)).fetchall()
    return {"ok": True, "rig": rig, "samples": [dict(x) for x in rows]}


# ---------------------------------------------------------------- 挖矿内核仓库
# 矿机端可能连不上境外源（GitHub/官方站），由中控做下载代理：
# 检查版本（GitHub API）→ 多镜像下载 zip 缓存到 NAS → 矿机端从中控直下。

KERNEL_REPO = {
    "srbminer": {"repo": "doktor83/SRBMiner-Multi", "exe": "SRBMiner-MULTI.exe", "label": "SRBMiner-MULTI"},
    "bzminer": {"repo": "bzminer/bzminer", "exe": "bzminer.exe", "label": "BzMiner"},
    "wildrig": {"repo": "andru-kun/wildrig-multi", "exe": "wildrig.exe", "label": "WildRig"},
}
# 矿机端可选内核（miner_client.KERNELS 的键，中控仅做白名单校验；
# 切换由矿机端按需从中控下载内核 zip 后重启矿工生效）
KERNEL_KEYS = ("SRBMiner-MULTI", "BzMiner", "WildRig")

# ---------------------------------------------------------------- 矿池目录
# 与矿机端 miner_client.py 的 POOL_BRANDS 保持一致（改一边必须同步另一边）：
# 矿机端本地界面用自己那份，中控看板用这份。二者允许短暂不一致——
# set_pool 只校验 URL 格式，客户端接受任意合法 stratum 地址。
# region 分级口径见 miner_client.py：cn 大陆 / hmt 港澳台 / abroad 海外。
POOL_BRANDS = [
    {
        "id": "kryptex", "name": "Kryptex", "fee": "1%",
        "note": "多币种平台，PRL 节点覆盖最广，支持 TLS（同主机 8048）",
        "nodes": [
            {"label": "中国香港", "region": "hmt", "geo": "香港 · 九龙",
             "host": "prl-hk.kryptex.network", "port": 7048},
            {"label": "新加坡", "region": "abroad", "geo": "新加坡",
             "host": "prl-sg.kryptex.network", "port": 7048},
            {"label": "欧洲", "region": "abroad", "geo": "德国 · 法尔肯施泰因",
             "host": "prl.kryptex.network", "port": 7048},
            {"label": "北美", "region": "abroad", "geo": "美国 · 弗吉尼亚",
             "host": "prl-us.kryptex.network", "port": 7048},
            {"label": "俄罗斯", "region": "abroad", "geo": "俄罗斯 · 莫斯科",
             "host": "prl-ru.kryptex.network", "port": 7048},
        ],
    },
    {
        "id": "pearlhash", "name": "PearlHash", "fee": "1%",
        "note": "Pearl 官方专用池；配 WildRig 内核 0 开发费。全球 Anycast 单入口自动就近，无大陆机房",
        "nodes": [
            {"label": "全球（Anycast）", "region": "abroad",
             "geo": "AWS Global Accelerator · 实测 ~111ms",
             "host": "pool.pearlhash.xyz", "port": 9000},
        ],
    },
    {
        "id": "k1pool", "name": "K1Pool", "fee": "0%",
        "note": "PPLNS 零费率；「中国线路」实际落地香港，非大陆机房",
        "nodes": [
            {"label": "中国线路", "region": "hmt", "geo": "香港 · AWS",
             "host": "cn.pearl.k1pool.com", "port": 3360},
            {"label": "欧洲", "region": "abroad", "geo": "法国 · 格拉沃利讷",
             "host": "eu.pearl.k1pool.com", "port": 3360},
            {"label": "美国", "region": "abroad", "geo": "加拿大 · 博阿努瓦",
             "host": "us.pearl.k1pool.com", "port": 3360},
            {"label": "欧洲 SOLO", "region": "abroad", "geo": "法国 · 格拉沃利讷",
             "host": "eu.pearlsolo.k1pool.com", "port": 3362},
        ],
    },
    {
        "id": "f2pool", "name": "F2Pool 飞鱼", "fee": "1%",
        "note": "PPLNS；钱包位填 F2Pool 账户名而非 PRL 地址",
        "nodes": [
            {"label": "全球", "region": "abroad", "geo": "美国 · 旧金山",
             "host": "pearl.f2pool.com", "port": 5500},
        ],
    },
    {
        "id": "herominers", "name": "HeroMiners", "fee": "1%",
        "note": "PPLNS，界面与统计做得细",
        "nodes": [
            {"label": "全球", "region": "abroad", "geo": "美国 · 旧金山",
             "host": "pearl.herominers.com", "port": 1200},
        ],
    },
    {
        "id": "alphapool", "name": "AlphaPool", "fee": "1%",
        "note": "新加坡单节点，亚太线路",
        "nodes": [
            {"label": "新加坡", "region": "abroad", "geo": "新加坡",
             "host": "sg1.alphapool.tech", "port": 5571},
        ],
    },
]
POOL_REGION_TAG = {"cn": "大陆直连", "hmt": "中国港澳台", "abroad": "海外"}
_POOL_URL_RE = re.compile(r"^stratum\+(tcp|ssl)://[A-Za-z0-9._\-]+:\d{1,5}$")


def pool_node_url(n):
    return "stratum+tcp://%s:%d" % (n["host"], int(n["port"]))


def pool_brand_of(url):
    """stratum URL -> 矿池品牌 id（kryptex/pearlhash/...）。

    先按 POOL_BRANDS 节点 host 精确匹配；匹配不到（自定义矿池/换了域名）
    再按域名关键词兜底；都失败返回 ""（看板按「自定义池」处理）。
    """
    u = (url or "").strip().lower()
    if not u:
        return ""
    host = re.sub(r"^stratum\+(tcp|ssl)://", "", u).split(":")[0]
    for b in POOL_BRANDS:
        for n in b["nodes"]:
            if host == str(n["host"]).lower():
                return b["id"]
    for b in POOL_BRANDS:
        if b["id"] in host:
            return b["id"]
    for kw, bid in (("kryptex", "kryptex"), ("pearlhash", "pearlhash"),
                    ("k1pool", "k1pool"), ("f2pool", "f2pool"),
                    ("herominers", "herominers"), ("alphapool", "alphapool")):
        if kw in host:
            return bid
    return ""


# 矿池「未支付余额」查询 API（按品牌）。只有提供公开按钱包查询接口的池才列在这里；
# 未列入的池（如 PearlHash——无公开余额 API 且中控网络不可达）返回
# unpaid_supported=False，看板标注「待付不可查」，而不是拿别的池的数字冒充。
POOL_BALANCE_API = {
    "kryptex": "https://pool.kryptex.com/{coin}/api/v1/miner/balance/{wallet}",
}


def valid_pool_url(u):
    """stratum 地址格式校验（自定义矿池也走同一规则）。"""
    u = (u or "").strip()
    if not _POOL_URL_RE.match(u):
        return False
    try:
        return 0 < int(u.rsplit(":", 1)[1]) <= 65535
    except ValueError:
        return False


def pool_catalog_payload():
    """看板「更换矿池」用的目录：品牌 + 节点（带 url 与区域标注）。"""
    brands = []
    for b in POOL_BRANDS:
        brands.append({
            "id": b["id"], "name": b["name"], "fee": b["fee"], "note": b["note"],
            "nodes": [dict(n, url=pool_node_url(n),
                           region_tag=POOL_REGION_TAG.get(n["region"], n["region"]))
                      for n in b["nodes"]],
        })
    return {"ok": True, "brands": brands,
            "custom": {"id": "custom", "name": "自定义矿池…",
                       "hint": "手动填写 stratum+tcp://主机:端口"},
            "region_tag": POOL_REGION_TAG}
# 下载镜像（逐个尝试，空串=直连）
GH_MIRRORS = ["", "https://ghfast.top/", "https://gh-proxy.com/"]
KERNEL_DIR = os.path.join(DATA_DIR, "kernels")
_kernel_meta = {"checked": 0, "catalog": {}}  # name -> {version,url,size,file,checked}

# ---------------------------------------------------------------- 客户端安装包分发
# 把矿机端 exe / 安卓 apk 放到 data/downloads/ 即可在看板一键下载（部署脚本负责同步）
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
CLIENT_FILES = [
    {"key": "exe", "file": "BaPiMiner.exe", "icon": "🖥",
     "title": "矿机端 · Windows", "plat": "Windows 10 / 11 · x64",
     "desc": "双击即用，中控地址与令牌已内置；首次启动自动下载挖矿内核，无需手工配置。"},
    {"key": "apk", "file": "BaPiMiner-android.apk", "icon": "📱",
     "title": "手机端 · Android", "plat": "Android 6.0 以上 · arm64 / armeabi-v7a",
     "desc": "手机随时看矿机状态、收告警、远程启停与切换游戏模式。"},
]
_sha_cache = {}  # path -> (size, mtime, sha256)


def _sha256_of(path):
    st = os.stat(path)
    key = (st.st_size, int(st.st_mtime))
    hit = _sha_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    digest = h.hexdigest()
    _sha_cache[path] = (key, digest)
    return digest


def downloads_payload():
    """可下载的客户端安装包（不存在也返回条目，前端提示「待上传」）。"""
    items = []
    for it in CLIENT_FILES:
        path = os.path.join(DOWNLOAD_DIR, it["file"])
        ok = os.path.isfile(path)
        size = os.path.getsize(path) if ok else 0
        item = {
            "key": it["key"], "icon": it["icon"], "title": it["title"],
            "plat": it["plat"], "desc": it["desc"],
            "name": it["file"], "url": "/download/" + it["file"],
            "ready": ok, "size": size,
            "version": (client_release().get("version") or "") if it["key"] == "exe" else "",
            "mtime": int(os.path.getmtime(path)) if ok else 0,
            "sha256": _sha256_of(path) if ok else "",
        }
        items.append(item)
    return {"ok": True, "items": items,
            "total": sum(i["size"] for i in items)}


# ---------------------------------------------------------------- 客户端自动更新
# 上传 exe 时一并写 data/client_release.json：{"version":"1.7.0.0","notes":"...","uploaded":ts}
# 矿机端拿自身 exe 的 sha256 与 /api/client_latest 比对，不一致即静默自更新。
CLIENT_RELEASE = os.path.join(DATA_DIR, "client_release.json")


def client_release():
    try:
        with open(CLIENT_RELEASE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def client_latest_payload():
    """矿机端自更新用的最新客户端信息（不含 admin 鉴权，新装机也能查）。

    client_release.json 里 "update_enabled": false 可全局冻结自动升级
    （ready=false → 矿机端视为无更新），用于灰度/定向推送期间冻住其他矿机。
    """
    path = os.path.join(DOWNLOAD_DIR, "BaPiMiner.exe")
    ok = os.path.isfile(path)
    rel = client_release()
    enabled = rel.get("update_enabled", True)
    return {
        "ok": True,
        "name": "BaPiMiner.exe",
        "url": "/download/BaPiMiner.exe",
        "ready": ok and enabled,
        "size": os.path.getsize(path) if ok else 0,
        "sha256": _sha256_of(path) if ok else "",
        "mtime": int(os.path.getmtime(path)) if ok else 0,
        "version": rel.get("version") or "",
        "notes": rel.get("notes") or "",
        "uploaded": rel.get("uploaded") or 0,
    }


# ---------------------------------------------------------------- 签名升级清单
# 矿机端只接受「用内置公钥验签通过」的清单。这个文件由发布机用离线私钥签发，
# 中控只负责原样转发：绝不在这里解析后重新拼装 —— 多一个字段或少一个字段，
# 签名就对不上，整条升级链路会 fail closed。
SIGNED_MANIFEST = os.path.join(DATA_DIR, "signed_manifest.json")


def signed_manifest_bytes():
    """原样返回清单字节；文件缺失 / 不是合法 JSON / 缺关键字段时返回 None。"""
    try:
        with open(SIGNED_MANIFEST, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not (isinstance(doc, dict) and "sig" in doc and "client" in doc):
        return None
    return raw


def signed_manifest_meta():
    """给健康检查/看板用的摘要（不含签名内容本身）。"""
    raw = signed_manifest_bytes()
    if raw is None:
        return {"present": False}
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return {"present": False, "error": "invalid json"}
    return {
        "present": True,
        "schema": doc.get("schema"),
        "issued_at": doc.get("issued_at") or 0,
        "client_version": (doc.get("client") or {}).get("version") or "",
        "kernels": sorted((doc.get("kernels") or {}).keys()),
    }


def build_info():
    """线上跑的是哪份代码：优先 data/build_info.json（部署时写入），其次环境变量。"""
    info = {}
    try:
        with open(os.path.join(DATA_DIR, "build_info.json"), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            info.update({k: d[k] for k in ("commit", "version", "built_at") if k in d})
    except (OSError, ValueError):
        pass
    if not info.get("commit"):
        info["commit"] = os.environ.get("BUILD_COMMIT", "")
    info.setdefault("version", SERVER_VERSION)
    info.setdefault("built_at", 0)
    return info


def _gh_api(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "bapiminer-server/1.0",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def kernel_check():
    """检查两个内核的最新版本（单仓库失败不影响其它）。"""
    for name, info in KERNEL_REPO.items():
        try:
            rel = _gh_api("https://api.github.com/repos/%s/releases/latest" % info["repo"])
            asset = None
            for a in rel.get("assets") or []:
                n = a.get("name", "").lower()
                if n.endswith(".zip") and ("windows" in n or "win64" in n or "x64" in n) \
                        and "linux" not in n and "macos" not in n:
                    asset = a
                    if "x64" in n:
                        break
            if asset:
                _kernel_meta["catalog"][name] = {
                    "version": rel.get("tag_name") or rel.get("name") or "",
                    "url": asset["browser_download_url"],
                    "size": asset.get("size"),
                    "file": asset.get("name"),
                    "label": info["label"], "exe": info["exe"],
                    "checked": time.time(),
                }
                print("[kernel] %s 最新版 %s (%s)" % (name, rel.get("tag_name"), asset["name"]))
        except Exception as e:  # noqa: BLE001
            print("[kernel] 检查 %s 失败: %s" % (name, e))
    _kernel_meta["checked"] = time.time()
    _save_kernel_meta()


def kernel_loop():
    while True:
        try:
            kernel_check()
        except Exception:  # noqa: BLE001
            traceback.print_exc(limit=1)
        time.sleep(6 * 3600)


def load_kernel_meta():
    """启动时把上次检查到的版本读回内存，避免重启后看板/矿机端看不到版本。"""
    try:
        with open(os.path.join(DATA_DIR, "kernel_meta.json"), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            _kernel_meta["checked"] = d.get("checked") or 0
            _kernel_meta["catalog"] = d.get("catalog") or {}
            if _kernel_meta["catalog"]:
                print("[kernel] 已载入 %d 个内核版本信息" % len(_kernel_meta["catalog"]),
                      flush=True)
    except (OSError, ValueError):
        pass
    _backfill_cached_sha()


def _save_kernel_meta():
    try:
        with open(os.path.join(DATA_DIR, "kernel_meta.json"), "w", encoding="utf-8") as f:
            json.dump(_kernel_meta, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _backfill_cached_sha():
    """给已缓存的内核 zip 补 sha256/size。

    老版本只记了版本号；签发升级清单必须知道 zip 的哈希，没有它就签不了，
    所以启动时按磁盘上的实际文件补一次（只补缺失的，不覆盖已有值）。
    """
    changed = False
    for name, meta in (_kernel_meta.get("catalog") or {}).items():
        if not isinstance(meta, dict) or meta.get("sha256"):
            continue
        path = os.path.join(KERNEL_DIR, "%s-%s.zip" % (name, meta.get("version")))
        if os.path.isfile(path):
            meta["sha256"] = _sha256_of(path)
            meta["size"] = os.path.getsize(path)
            changed = True
            print("[kernel] 已补 %s 的 sha256（%s…）" % (name, meta["sha256"][:12]),
                  flush=True)
    if changed:
        _save_kernel_meta()


def kernel_download(name):
    """下载（带镜像重试）并缓存，返回 (本地路径, 错误)。"""
    meta = _kernel_meta["catalog"].get(name)
    if not meta:
        return None, "尚无 %s 版本信息，请先点「检查更新」" % name
    os.makedirs(KERNEL_DIR, exist_ok=True)
    dest = os.path.join(KERNEL_DIR, "%s-%s.zip" % (name, meta["version"]))
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return dest, None
    err = None
    for m in GH_MIRRORS:
        url = m + meta["url"] if m else meta["url"]
        try:
            print("[kernel] 下载 %s (%s)" % (meta["file"], url[:70] or "direct"))
            req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0"})
            with urllib.request.urlopen(req, timeout=900) as r, open(dest + ".part", "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(dest + ".part", dest)
            # 记录哈希：发布者签发升级清单时要用它（矿机端只认清单里的 sha256）
            meta["sha256"] = _sha256_of(dest)
            meta["size"] = os.path.getsize(dest)
            _save_kernel_meta()
            return dest, None
        except Exception as e:  # noqa: BLE001
            err = e
            print("[kernel] 源失败 %s: %s" % (url[:70] or "direct", e))
    return None, "所有下载源均失败: %s" % err


def kernel_payload():
    cached = []
    try:
        for f in sorted(os.listdir(KERNEL_DIR)):
            if f.endswith(".zip"):
                cached.append({"file": f, "size": os.path.getsize(os.path.join(KERNEL_DIR, f))})
    except OSError:
        pass
    return {"ok": True, "checked": _kernel_meta["checked"], "catalog": _kernel_meta["catalog"],
            "cached": cached}


def daily_payload(days=30, auth=None):
    """按天收益估算（按矿池分组）：日内样本均值算力 × 收益率。

    auth 传入时按可见矿机集合过滤 samples 与钱包打款记录（多用户隔离）。
    """
    c = db()
    since = time.time() - days * 86400
    vis = set(visible_rig_names(auth)) if auth is not None else None
    if vis is not None and not vis:
        # 没有可见矿机 → 直接返回空，绝不能回落到全表
        return {"ok": True, "days": []}
    if vis is None:
        try:
            rows = c.execute(
                "SELECT CAST((ts+28800)/86400 AS INT) dkey, rig, pool, AVG(ths) aths, MAX(temp) mtemp "
                "FROM samples WHERE ts>? AND ths>0 GROUP BY dkey,rig,pool ORDER BY dkey DESC",
                (since,)).fetchall()
        except sqlite3.OperationalError:  # 老库无 pool 列
            rows = c.execute(
                "SELECT CAST((ts+28800)/86400 AS INT) dkey, rig, '' pool, AVG(ths) aths, MAX(temp) mtemp "
                "FROM samples WHERE ts>? AND ths>0 GROUP BY dkey,rig ORDER BY dkey DESC",
                (since,)).fetchall()
    else:
        qs = ",".join("?" * len(vis))
        args = [since] + list(vis)
        try:
            rows = c.execute(
                "SELECT CAST((ts+28800)/86400 AS INT) dkey, rig, pool, AVG(ths) aths, MAX(temp) mtemp "
                "FROM samples WHERE ts>? AND ths>0 AND rig IN (%s) GROUP BY dkey,rig,pool ORDER BY dkey DESC" % qs,
                args).fetchall()
        except sqlite3.OperationalError:  # 老库无 pool 列
            rows = c.execute(
                "SELECT CAST((ts+28800)/86400 AS INT) dkey, rig, '' pool, AVG(ths) aths, MAX(temp) mtemp "
                "FROM samples WHERE ts>? AND ths>0 AND rig IN (%s) GROUP BY dkey,rig ORDER BY dkey DESC" % qs,
                args).fetchall()
    days_out = {}
    avg = rate_avg()
    for x in rows:
        # dkey = floor((ts+28800)/86400) 即北京日序；dkey*86400 正是该北京日
        # 00:00 的 UTC 时间戳，直接 gmtime 取日期即北京日期（不能再 -28800，
        # 否则整表提前一天，估算与实收错行）
        day = time.strftime("%Y-%m-%d", time.gmtime(int(x["dkey"]) * 86400))
        usdt = (x["aths"] or 0) * avg
        d = days_out.setdefault(day, {"day": day, "usdt": 0.0, "rigs": {}})
        e = d["rigs"].setdefault(x["rig"], {"rig": x["rig"], "ths": 0.0, "usdt": 0.0, "pools": {}})
        e["ths"] += x["aths"] or 0
        e["usdt"] += usdt
        pool = x["pool"] or "未知矿池"
        e["pools"][pool] = round(e["pools"].get(pool, 0.0) + usdt, 4)
        d["usdt"] += usdt
    # 矿池打款记录（真实收入，攒起付线批量到账）按天并入（钱包去重，防共用地址翻倍）
    paid_days = {}
    for v in unique_wallets(rig_names=vis):
        p = v.get("paid") or {}
        for day, amt in (p.get("days") or {}).items():
            paid_days[day] = round(paid_days.get(day, 0.0) + amt, 6)
    out = []
    for day in sorted(days_out, reverse=True):
        d = days_out[day]
        for e in d["rigs"].values():
            e["ths"] = round(e["ths"], 2)
            e["usdt"] = round(e["usdt"], 4)
            e["cny"] = round(e["usdt"] * rate_cny(), 2)
        d["usdt"] = round(d["usdt"], 4)
        d["cny"] = round(d["usdt"] * rate_cny(), 2)
        out.append({"day": day, "usdt": d["usdt"], "cny": d["cny"],
                    "paid": paid_days.get(day, 0.0), "paid_cny": round(paid_days.get(day, 0.0) * rate_cny(), 2),
                    "rigs": sorted(d["rigs"].values(), key=lambda x: -x["usdt"])})
    # 打款发生在估算无数据的日子（离线日到账）也要展示
    known = {o["day"] for o in out}
    for day, amt in sorted(paid_days.items(), reverse=True):
        if day not in known:
            out.append({"day": day, "usdt": 0.0, "cny": 0.0,
                        "paid": amt, "paid_cny": round(amt * rate_cny(), 2), "rigs": []})
    out.sort(key=lambda o: o["day"], reverse=True)
    return {"ok": True, "days": out}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "BaPiMiner/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def parse_request(self):
        """每个请求开始时重置请求体缓存。

        keep-alive 下同一个 handler 实例会被复用，若不重置，
        第二个请求会拿到第一个请求的 body。
        """
        self._raw_body = None
        return BaseHTTPRequestHandler.parse_request(self)

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Rig-Token,X-Admin-Token,X-User-Token")
        self.send_header("Access-Control-Expose-Headers", "X-Kernel-Version,X-Kernel-Name,X-Kernel-Key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        for hk, hv in (extra or {}).items():
            # HTTP 头只能放 latin-1，非 ASCII 一律剔除，避免 send_header 抛错
            self.send_header(hk, "".join(ch for ch in str(hv) if ord(ch) < 128))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _send_file(self, path, fname, ctype="application/octet-stream"):
        """分块发送大文件，支持 Range（浏览器/下载器可续传）。"""
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=600")
        self.send_header("Access-Control-Allow-Origin", "*")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_raw_body(self):
        """读取并缓存请求体。

        HTTP/1.1 keep-alive 下，若某个 POST 路由没有消费请求体，
        残留字节会与下一个请求行拼接（如 '{}' + 'POST /x HTTP/1.1'），
        导致 BaseHTTPRequestHandler 解析出非法 method 并回 501 HTML 错误页。
        因此请求体必须被完整读取且只读取一次（此处缓存）。
        """
        if getattr(self, "_raw_body", None) is None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                n = 0
            self._raw_body = self.rfile.read(n) if n > 0 else b""
        return self._raw_body

    def _read_json(self):
        try:
            raw = self._read_raw_body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"__error__": str(e)}

    def _rig_auth(self):
        return self.headers.get("X-Rig-Token") or ""

    def _admin_auth(self):
        return self.headers.get("X-Admin-Token") or ""

    def _client_ip(self):
        try:
            return self.client_address[0] if self.client_address else ""
        except Exception:  # noqa: BLE001
            return ""

    def _is_app_webview(self):
        """判断请求是否来自已安装的安卓 App 内嵌 WebView。

        ★ 背景：uniapp 的 <web-view> 与 wap2app 的 launch_path 都硬编码指向
          http://mine.sinyerobot.com:8099/（根路径）。官网首页占用了根路径后，
          老 App 打开会变成营销页 —— 所以这里识别出 App 流量，仍返回群控台。

        识别依据（命中任一即认为是 App）：
          - HTML5+ 运行时标记：DCloud 系的 WebView UA 里含 "Html5plus" / "dcloud"
          - uniapp / wap2app 的自定义 UA 关键字
          - plusready 之前运行时还会带 "UCBrowser"（部分定制 WebView）

        注意：这是「App 优先」的启发式判断，宁可多给群控台也不要让 App 用户
        看到营销页 —— 因为营销页与群控台本来就有入口互通，误判代价极小。
        """
        ua = (self.headers.get("User-Agent") or "").lower()
        if not ua:
            return False
        markers = (
            "html5plus",   # DCloud HTML5+ 运行时
            "dcloud",      # DCloud 官方标识
            "uniapp",      # uni-app 自定义 UA
            "minrigs",     # 本项目 App 自定义标识
            "wap2app",     # wap2app 打包器
            "plusruntime",
        )
        return any(m in ua for m in markers)

    def _auth(self):
        """解析身份。未登录返回 None。"""
        a = auth_context(self)
        return a if require_user(a) else None

    def _deny(self, msg="需要登录"):
        return self._json({"ok": False, "error": msg}, 401)

    def do_OPTIONS(self):
        self._read_raw_body()
        self._send(200, "{}")

    def do_GET(self):
        # 同样消费可能存在的请求体，避免 keep-alive 连接串包
        self._read_raw_body()
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/api/poll":
            name = (q.get("name") or [""])[0]
            err = rig_auth_check(name, self._rig_auth(), "轮询")
            if err:
                return self._json({"ok": False, "error": err}, 401)
            c = db()
            with _db_lock:
                pend = c.execute(
                    "SELECT id,cmd,arg FROM commands WHERE rig=? AND status='pending' ORDER BY id",
                    (name,)).fetchall()
                if pend:
                    c.execute("UPDATE commands SET status='sent', picked=? WHERE rig=? AND status='pending'",
                              (time.time(), name))
                c.commit()
            return self._json({"ok": True, "commands": [dict(x) for x in pend]})

        if p == "/api/auth/me":
            auth = self._auth()
            if not auth:
                return self._deny()
            c = db()
            if auth.get("via") == "admin" and not auth.get("user_id"):
                # 逃生通道（X-Admin-Token）：没有真实用户身份
                return self._json({"ok": True, "user": {"id": 0, "username": "admin",
                                                        "is_admin": True, "status": "active",
                                                        "created_at": None, "last_login_at": None},
                                   "rig_count": len(visible_rig_names(auth)), "via": "admin"})
            row = user_by_id(auth["user_id"])
            n = c.execute("SELECT COUNT(*) n FROM rigs WHERE owner_id=?", (auth["user_id"],)).fetchone()["n"]
            return self._json({"ok": True, "user": user_public(row), "rig_count": n, "via": "user"})

        if p == "/api/fleet":
            auth = self._auth()
            if not auth:
                return self._deny()
            return self._json(fleet_payload(auth))

        if p == "/api/history":
            auth = self._auth()
            if not auth:
                return self._deny()
            rig = (q.get("rig") or [""])[0]
            if not rig_visible(auth, rig):
                return self._json({"ok": False, "error": "无权查看该矿机"}, 403)
            hours = min(168, max(1, int((q.get("hours") or ["24"])[0])))
            return self._json(history_payload(rig, hours))

        if p == "/api/alerts":
            auth = self._auth()
            if not auth:
                return self._deny()
            vis = visible_rig_names(auth)
            if not vis:
                return self._json({"ok": True, "alerts": []})
            qs = ",".join("?" * len(vis))
            rows = db().execute(
                "SELECT * FROM alerts WHERE rig IN (%s) ORDER BY id DESC LIMIT 80" % qs,
                list(vis)).fetchall()
            return self._json({"ok": True, "alerts": [dict(x) for x in rows]})

        if p == "/api/gpu_table":
            # 官网「GPU 算力与日收益实测表」：按型号聚合，每天自动刷新一次
            return self._json(gpu_table_payload())

        if p == "/api/health":
            c = db()
            n = c.execute("SELECT COUNT(*) n FROM rigs").fetchone()["n"]
            bi = build_info()
            return self._json({"ok": True, "rigs": n, "now": now_cn(),
                               "server_version": SERVER_VERSION,
                               "build_commit": bi.get("commit") or "",
                               "built_at": bi.get("built_at") or 0,
                               "signed_manifest": signed_manifest_meta()})

        if p == "/api/price":
            # 币价行情：公开只读，无需登录（页面顶部展示用）
            return self._json(price_payload())

        if p == "/api/daily":
            auth = self._auth()
            if not auth:
                return self._deny()
            days = min(90, max(1, int((q.get("days") or ["30"])[0])))
            return self._json(daily_payload(days, auth))

        if p == "/api/kernels":
            auth = self._auth()
            if not auth:
                return self._deny()
            return self._json(kernel_payload())

        if p == "/api/pools":
            # 矿池目录：看板「更换矿池」选择用（静态参考数据，无敏感信息）
            auth = self._auth()
            if not auth:
                return self._deny()
            return self._json(pool_catalog_payload())

        if p == "/api/users":
            # 仅管理员：用户列表 + 各自矿机数
            auth = self._auth()
            if not auth:
                return self._deny()
            if not auth.get("is_admin"):
                return self._json({"ok": False, "error": "仅管理员可查看"}, 403)
            c = db()
            rows = c.execute(
                "SELECT u.id,u.username,u.is_admin,u.status,u.created_at,u.last_login_at,"
                " (SELECT COUNT(*) FROM rigs r WHERE r.owner_id=u.id) rig_count "
                "FROM users u ORDER BY u.id").fetchall()
            return self._json({"ok": True, "users": [user_public(r) | {"rig_count": r["rig_count"]}
                                                     for r in rows]})

        if p == "/api/rigs":
            # 矿机列表（按权限过滤）
            auth = self._auth()
            if not auth:
                return self._deny()
            c = db()
            vis = visible_rig_names(auth)
            if not vis:
                return self._json({"ok": True, "rigs": []})
            qs = ",".join("?" * len(vis))
            rows = c.execute(
                "SELECT name,display_name,owner_id,status,last_seen,client_version,pool_url "
                "FROM rigs WHERE name IN (%s) ORDER BY name" % qs, list(vis)).fetchall()
            return self._json({"ok": True, "rigs": [
                {"name": r["name"],
                 "display_name": (r["display_name"] or r["name"]),
                 "owner_id": r["owner_id"] or 0,
                 "owner_name": rig_owner_name(r["owner_id"] or 0),
                 "status": r["status"], "last_seen": r["last_seen"],
                 "client_version": r["client_version"] or "",
                 "pool_url": r["pool_url"] or ""} for r in rows]})

        if p == "/api/owner_log":
            # 仅管理员：归属变更审计
            auth = self._auth()
            if not auth:
                return self._deny()
            if not auth.get("is_admin"):
                return self._json({"ok": False, "error": "仅管理员可查看"}, 403)
            rows = db().execute(
                "SELECT * FROM rig_owner_log ORDER BY id DESC LIMIT 200").fetchall()
            return self._json({"ok": True, "log": [dict(x) for x in rows]})

        if p == "/api/downloads":
            # 公开：机主拿到链接就能下，清单里没有敏感信息
            return self._json(downloads_payload())

        if p == "/api/signed_manifest":
            # 公开：矿机端升级前必读。内容由发布私钥签名，中控改不动
            #（改了签名就失效，矿机端会拒绝升级而不是装上一个坏包）。
            raw = signed_manifest_bytes()
            if raw is None:
                return self._json({"ok": False, "error": "no signed manifest"}, 404)
            return self._send(200, raw, "application/json; charset=utf-8")

        if p == "/api/client_latest":
            # 公开：老版本矿机端仍在读这个接口（新版本改走 /api/signed_manifest）。
            # 保留是为了让存量机器还能升上来，但它的 sha256 不可信、不参与校验。
            return self._json(client_latest_payload())

        if p.startswith("/download/"):
            fname = unquote(p[len("/download/"):])
            if fname not in {it["file"] for it in CLIENT_FILES}:
                return self._json({"ok": False, "error": "not found"}, 404)
            fpath = os.path.join(DOWNLOAD_DIR, fname)
            if not os.path.isfile(fpath):
                return self._json({"ok": False, "error": "安装包还没上传到中控"}, 404)
            ctype = ("application/vnd.android.package-archive" if fname.endswith(".apk")
                     else "application/octet-stream")
            return self._send_file(fpath, fname, ctype)

        if p == "/api/kernel_catalog":
            # 矿机端只读版本信息（客户端上显示「最新 vX」），不需要 admin 权限
            return self._json(kernel_payload())

        if p == "/api/income_meta":
            rig = (q.get("rig") or [""])[0].strip()
            err = rig_auth_check(rig, self._rig_auth(), "收益口径")
            if err:
                return self._json({"ok": False, "error": err}, 401)
            out = {
                "ok": True,
                "rate_avg_usdt": round(rate_avg(), 6),
                "cny_rate": rate_cny(),
                "rate_age": int(time.time() - _rate_ts) if _rate_ts else None,
                "source": INCOME_SRC,
            }
            with _wallet_lock:
                w = dict(_wallet_map.get(rig) or {})
            if w:
                out["wallet"] = {
                    "total": w.get("total"), "confirmed": w.get("confirmed"),
                    "unconfirmed": w.get("unconfirmed"), "coin": w.get("coin"),
                    "threshold": w.get("threshold"), "pct": w.get("pct"),
                    "paid": w.get("paid"), "rewards": w.get("rewards"),
                }
            return self._json(out)

        if p == "/api/kernel_dl":
            tk = self._rig_auth()
            rig_name = (q.get("rig") or [""])[0].strip()
            c = db()
            row = c.execute("SELECT token FROM rigs WHERE name=?", (rig_name,)).fetchone() if rig_name else None
            if row is not None:
                stored = row["token"] or ""
                if stored and stored != tk:
                    return self._json({"ok": False, "error": "token mismatch"}, 401)
                if not stored:
                    # 接入码被管理员重置过 → 本次下载自动重新绑定
                    if not tk:
                        return self._json({"ok": False, "error": "auth failed"}, 401)
                    with _db_lock:
                        c.execute("UPDATE rigs SET token=?, last_seen=? WHERE name=?",
                                  (tk, time.time(), rig_name))
                        c.commit()
                    add_alert(rig_name, "info", "rig_rebind", "矿机 %s 已重新绑定接入码（内核下载认领）" % rig_name)
                    print("[rig] 重新绑定接入码：%s（内核下载认领）" % rig_name)
            if row is None:
                # 未注册矿机下载内核时自动注册（与首次上报同规则）
                if not rig_name or not tk:
                    return self._json({"ok": False, "error": "auth failed"}, 401)
                with _db_lock:
                    c.execute("INSERT INTO rigs(name,token,first_seen,last_seen,status) VALUES(?,?,?,?,?)",
                              (rig_name, tk, time.time(), time.time(), "online"))
                    c.commit()
                add_alert(rig_name, "info", "rig_new", "新矿机接入（内核下载自动注册）：%s" % rig_name)
                print("[rig] 新矿机注册（kernel_dl）：%s" % rig_name)
            name = (q.get("kernel") or [""])[0]
            if name not in KERNEL_REPO:
                return self._json({"ok": False, "error": "unknown kernel"}, 400)
            path, err = kernel_download(name)
            if err:
                return self._json({"ok": False, "error": err}, 502)
            meta = _kernel_meta["catalog"].get(name) or {}
            with open(path, "rb") as f:
                data = f.read()
            return self._send(200, data, "application/zip", extra={
                "X-Kernel-Version": meta.get("version") or "",
                "X-Kernel-Name": meta.get("file") or "",
                "X-Kernel-Key": name,
            })

        # ---------------- 官网首页 / 群控台 ----------------
        # ★ 根路径 "/" = 对外官网首页（营销页），群控台移到 "/group"。
        #
        #   为什么这样分：官网要给外部人看，登录页不是首页。
        #   但已安装的 uniapp APK 里 <web-view> 硬编码指向
        #   http://mine.sinyerobot.com:8099/（根路径），老 APK 打开会变成营销页。
        #   对策是双保险：
        #     ① 服务端按 UA 识别 HTML5+ 运行时（uniapp/wap2app 的 WebView），
        #        仍把根路径返回群控台 —— 老 App 完全不受影响；
        #     ② 首页前端再按 plus 对象做一次跳转兜底（防 UA 伪装/自定义 UA）。
        #   两个条件都不满足时，根路径返回官网首页。
        if p in ("/", "/index.html"):
            if p == "/index.html" or self._is_app_webview():
                try:
                    with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except FileNotFoundError:
                    return self._send(404, "dashboard missing", "text/plain")
            try:
                with open(os.path.join(STATIC_DIR, "home.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(404, "home missing", "text/plain")

        # 群控台（正式路径）。/index.html 之外的别名：/group、/group/、/dash。
        if p in ("/group", "/group/", "/group.html", "/dash", "/dash/"):
            try:
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(404, "dashboard missing", "text/plain")

        if p in ("/home", "/home.html"):
            try:
                with open(os.path.join(STATIC_DIR, "home.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(404, "home missing", "text/plain")

        # 首页插图（产品截图）。只放行 static/img/ 下的 png/jpg/svg/webp，
        # 并做规范化路径校验，防目录穿越。
        if p.startswith("/img/"):
            rel = unquote(p[len("/img/"):])
            fname = os.path.normpath(os.path.join(STATIC_DIR, "img", rel))
            img_dir = os.path.normpath(os.path.join(STATIC_DIR, "img"))
            if not fname.startswith(img_dir + os.sep) or not os.path.isfile(fname):
                return self._send(404, "Not Found", "text/plain")
            ext = os.path.splitext(fname)[1].lower()
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".svg": "image/svg+xml", ".webp": "image/webp",
                     ".gif": "image/gif"}.get(ext)
            if not ctype:
                return self._send(404, "Not Found", "text/plain")
            try:
                with open(fname, "rb") as f:
                    return self._send(200, f.read(), ctype,
                                      extra={"Cache-Control": "public, max-age=3600"})
            except OSError:
                return self._send(404, "Not Found", "text/plain")

        if p == "/bapiminer.svg":
            try:
                with open(os.path.join(STATIC_DIR, "bapiminer.svg"), "rb") as f:
                    return self._send(200, f.read(), "image/svg+xml",
                                      extra={"Cache-Control": "public, max-age=86400"})
            except FileNotFoundError:
                return self._send(404, "Not Found", "text/plain")

        return self._send(404, "Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path

        # ★ 统一消费请求体：keep-alive 连接上若残留未读字节，
        #   会与下一个请求行拼接，产生非法 method 并回 501 HTML。
        #   这里先把 body 读进缓存，各路由再用 _read_json() 取（只解析一次）。
        self._read_raw_body()

        # ---------------- 账号体系 ----------------
        if p == "/api/auth/register":
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            ok, out = register_user(body.get("username"), body.get("password"))
            return self._json(out, 200 if ok else 400)

        if p == "/api/auth/login":
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            ok, out = login_user(body.get("username"), body.get("password"),
                                 self.headers.get("User-Agent") or "", self._client_ip())
            return self._json(out, 200 if ok else 401)

        if p == "/api/auth/change_password":
            # 修改密码。两种调用方式：
            #  ① 首次登录强制改密：未登录态，带 username + old_password + new_password
            #  ② 已登录用户自助改密：带 X-User-Token + old_password + new_password
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            old_pwd = str(body.get("old_password") or "")
            new_pwd = str(body.get("new_password") or "")
            tok = self.headers.get("X-User-Token") or ""
            username = str(body.get("username") or "").strip()
            uid = 0
            if tok:
                row = db().execute("SELECT user_id FROM sessions WHERE token=?",
                                   (tok,)).fetchone()
                if row is None:
                    return self._deny()
                uid = int(row["user_id"])
            elif username:
                # 未登录态：必须能通过旧密码证明身份
                urow = user_by_name(username)
                if urow is None or not verify_password(old_pwd, urow["pwd_hash"],
                                                      urow["pwd_salt"]):
                    return self._json({"ok": False, "error": "原密码不正确"}, 401)
                uid = int(urow["id"])
            else:
                return self._json({"ok": False, "error": "缺少身份信息"}, 401)
            ok, out = change_password(uid, old_pwd, new_pwd)
            return self._json(out, 200 if ok else 400)

        if p == "/api/auth/logout":
            tok = self.headers.get("X-User-Token") or ""
            if not tok:
                return self._json({"ok": True})
            drop_session(tok)
            return self._json({"ok": True})

        if p == "/api/rig_login":
            # 矿机端登录 + 首次自动挂载（R2/R3）
            # 机器身份：X-Rig-Token + rig name；人的身份：用户名密码
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            rig = str(body.get("rig") or body.get("name") or "").strip()
            tk = self._rig_auth()
            if not rig or not tk:
                return self._json({"ok": False, "error": "缺少机器标识或接入码"}, 400)
            err = rig_auth_check(rig, tk, "矿机端登录")
            if err:
                return self._json({"ok": False, "error": err}, 401)
            username = str(body.get("username") or "").strip()
            pwd = str(body.get("password") or "")
            key_u = "u:" + username.lower()
            left = login_fail_check(key_u)
            if left:
                return self._json({"ok": False, "error": "登录失败次数过多，请 %d 秒后再试" % left}, 429)
            row = user_by_name(username)
            if row is None or not verify_password(pwd, row["pwd_hash"], row["pwd_salt"]):
                login_fail_record(key_u)
                return self._json({"ok": False, "error": "用户名或密码错误"}, 401)
            if row["status"] != "active":
                return self._json({"ok": False, "error": "该账号已被禁用"}, 403)
            login_fail_clear(key_u)
            uid = row["id"]
            # ★ 部署预置账号：首次登录必须先改密。此时不放行 token，
            #   也不做归属绑定 —— 改完密重新登录才走下面的绑定流程。
            if _row_must_change(row):
                return self._json({"ok": True, "need_password_change": True,
                                   "user": user_public(user_by_id(uid))})
            c = db()
            r = c.execute("SELECT owner_id, display_name FROM rigs WHERE name=?", (rig,)).fetchone()
            cur_owner = int((r["owner_id"] if r else 0) or 0)
            newly_bound = False
            if cur_owner == 0:
                # ★ 无主矿机 → 矿机端首次登录自动绑定（唯一的归属产生方式）
                with _db_lock:
                    c.execute("UPDATE rigs SET owner_id=?, bound_at=? WHERE name=?",
                              (uid, time.time(), rig))
                    c.commit()
                log_owner_change(rig, 0, uid, "bind", "矿机端首次登录自动绑定")
                print("[auth] 矿机 %s 已绑定到用户 %s" % (rig, username), flush=True)
                newly_bound = True
            elif cur_owner == uid:
                pass  # 幂等：已属自己，直接放行
            else:
                # 已属他人 → 拒绝。归属不再由管理员手工指定/强制转移，
                # 必须先由原属主在矿机端解绑（变回无主），才能被新用户绑定。
                owner_name = rig_owner_name(cur_owner) or ("用户#%d" % cur_owner)
                return self._json({"ok": False,
                                   "error": "该矿机已绑定账号「%s」，"
                                            "请先在原账号解绑后再登录"
                                            % owner_name}, 409)
            tok = create_session(uid, self.headers.get("User-Agent") or "", self._client_ip())
            fresh = user_by_id(uid)
            rr = c.execute("SELECT name,display_name,owner_id FROM rigs WHERE name=?", (rig,)).fetchone()
            return self._json({"ok": True, "token": tok, "user": user_public(fresh),
                               "bound": newly_bound,
                               "rig": {"name": rig,
                                       "display_name": (rr["display_name"] or rig) if rr else rig,
                                       "owner_id": uid}})

        if p == "/api/rig_logout":
            # 客户端登出：只清本地登录态，不解绑机器（矿机仍归原账号）
            tok = self.headers.get("X-User-Token") or ""
            if tok:
                drop_session(tok)
            return self._json({"ok": True})

        if p == "/api/rig_rename":
            # 远程改中控中的名字（R6）—— 只改 display_name，不动机器标识 name
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            rig = str(body.get("rig") or "").strip()
            disp = str(body.get("display_name") or "").strip()
            if not rig:
                return self._json({"ok": False, "error": "缺少 rig"}, 400)
            if not disp:
                return self._json({"ok": False, "error": "显示名不能为空"}, 400)
            if len(disp) > 32:
                return self._json({"ok": False, "error": "显示名最长 32 个字符"}, 400)
            if any(ord(ch) < 32 for ch in disp):
                return self._json({"ok": False, "error": "显示名含非法控制字符"}, 400)
            if not rig_visible(auth, rig):
                return self._json({"ok": False, "error": "无权修改该矿机"}, 403)
            c = db()
            old = c.execute("SELECT display_name FROM rigs WHERE name=?", (rig,)).fetchone()
            old_disp = (old["display_name"] or rig) if old else rig
            with _db_lock:
                c.execute("UPDATE rigs SET display_name=? WHERE name=?", (disp, rig))
                c.commit()
            # 下发命令让矿机端同步改名（D6：不弹窗，静默保存）
            with _db_lock:
                c.execute("INSERT INTO commands(rig,cmd,arg,created) VALUES(?,?,?,?)",
                          (rig, "set_display_name", json.dumps({"display_name": disp}), time.time()))
                c.commit()
            print("[cmd] 中控改名 %s: %s → %s（by %s）" % (rig, old_disp, disp, auth.get("username")), flush=True)
            return self._json({"ok": True, "rig": rig, "display_name": disp, "old": old_disp})

        if p == "/api/rig_unbind":
            # 管理员解绑（或 owner 自解绑）
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            rig = str(body.get("rig") or "").strip()
            if not rig:
                return self._json({"ok": False, "error": "缺少 rig"}, 400)
            c = db()
            r = c.execute("SELECT owner_id FROM rigs WHERE name=?", (rig,)).fetchone()
            if r is None:
                return self._json({"ok": False, "error": "矿机不存在：%s" % rig}, 400)
            old = int(r["owner_id"] or 0)
            if not auth.get("is_admin") and old != (auth.get("user_id") or 0):
                return self._json({"ok": False, "error": "无权解绑该矿机"}, 403)
            with _db_lock:
                c.execute("UPDATE rigs SET owner_id=0, bound_at=NULL WHERE name=?", (rig,))
                c.commit()
            log_owner_change(rig, old, 0, "unbind", "解绑")
            return self._json({"ok": True, "rig": rig})

        if p == "/api/report":
            body = self._read_json()
            if "__error__" in body:
                return self._json({"ok": False, "error": body["__error__"]}, 400)
            rep = handle_report(body)
            # 接入码不匹配用真实 401，便于矿机端识别并给出明确指引
            if not rep.get("ok") and "mismatch" in str(rep.get("error") or ""):
                return self._json(rep, 401)
            return self._json(rep)

        if p == "/api/cmd_result":
            body = self._read_json()
            err = rig_auth_check(str(body.get("rig") or ""), self._rig_auth(), "命令回执")
            if err:
                return self._json({"ok": False, "error": err}, 401)
            c = db()
            with _db_lock:
                c.execute("UPDATE commands SET status=?, finished=?, result=? WHERE id=?",
                          ("done" if body.get("ok") else "failed", time.time(),
                           str(body.get("result") or "")[:500], body.get("id")))
                c.commit()
            return self._json({"ok": True})

        if p == "/api/rig_reset_token":
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            rig = str(body.get("rig") or "").strip()
            if not rig:
                return self._json({"ok": False, "error": "缺少 rig"}, 400)
            if not rig_visible(auth, rig):
                return self._json({"ok": False, "error": "无权操作该矿机"}, 403)
            c = db()
            row = c.execute("SELECT name FROM rigs WHERE name=?", (rig,)).fetchone()
            if row is None:
                return self._json({"ok": False, "error": "矿机不存在：%s" % rig})
            with _db_lock:
                c.execute("UPDATE rigs SET token='' WHERE name=?", (rig,))
                c.commit()
            add_alert(rig, "warn", "rig_token_reset",
                      "矿机 %s 接入码已重置，等待矿机端重新绑定" % rig)
            print("[rig] 接入码已重置：%s" % rig)
            return self._json({"ok": True, "rig": rig})

        if p == "/api/kernel/check":
            auth = self._auth()
            if not auth:
                return self._deny()
            kernel_check()
            return self._json(kernel_payload())

        if p == "/api/command":
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            rig, cmd = body.get("rig") or "", body.get("cmd") or ""
            arg = body.get("arg") or {}
            if not isinstance(arg, dict):
                return self._json({"ok": False, "error": "arg 必须是对象"}, 400)
            if not rig or not cmd:
                return self._json({"ok": False, "error": "rig/cmd 必填"}, 400)
            if cmd not in {"restart_miner", "start_miner", "stop_miner",
                           "gamemode_on", "gamemode_off", "update_kernel",
                           "update_client", "set_pool", "set_kernel",
                           "set_display_name"}:
                return self._json({"ok": False, "error": "未知命令: %s" % cmd}, 400)
            if cmd == "set_pool":
                if not valid_pool_url(str(arg.get("pool_url") or "")):
                    return self._json({"ok": False,
                                       "error": "矿池地址格式应为 stratum+tcp://主机:端口"}, 400)
            elif cmd == "set_kernel":
                if str(arg.get("kernel") or "") not in KERNEL_KEYS:
                    return self._json({"ok": False, "error": "内核必须是 %s 之一"
                                       % " / ".join(KERNEL_KEYS)}, 400)
            elif cmd == "set_display_name":
                disp = str(arg.get("display_name") or "").strip()
                if not disp or len(disp) > 32:
                    return self._json({"ok": False, "error": "显示名需 1~32 字符"}, 400)
            c = db()
            # 广播只作用于当前身份可见的矿机（D5：严格单一归属，user 只能命令自己的）
            names = visible_rig_names(auth)
            if rig == "*":
                targets = names
            else:
                if not rig_visible(auth, rig):
                    return self._json({"ok": False, "error": "矿机不存在或无权操作：%s" % rig}, 403)
                targets = [rig]
            if not targets:
                return self._json({"ok": False, "error": "没有可操作的矿机"}, 400)
            with _db_lock:
                for t in targets:
                    c.execute("INSERT INTO commands(rig,cmd,arg,created) VALUES(?,?,?,?)",
                              (t, cmd, json.dumps(arg), time.time()))
                c.commit()
            print("[cmd] 已下发 %s -> %s" % (cmd, "全部(%d台)" % len(targets) if rig == "*" else rig))
            return self._json({"ok": True, "targets": targets})

        if p == "/api/alerts/resolve":
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            c = db()
            row = c.execute("SELECT rig FROM alerts WHERE id=?", (body.get("id"),)).fetchone()
            if row is not None and not rig_visible(auth, row["rig"]):
                return self._json({"ok": False, "error": "无权操作该告警"}, 403)
            c.execute("UPDATE alerts SET resolved=1, resolved_ts=? WHERE id=?",
                      (time.time(), body.get("id")))
            c.commit()
            return self._json({"ok": True})

        if p == "/api/wallet":
            body = self._read_json()
            auth = self._auth()
            if not auth:
                return self._deny()
            rig = (body.get("rig") or "").strip()
            if not rig:
                return self._json({"ok": False, "error": "rig 必填"}, 400)
            if not rig_visible(auth, rig):
                return self._json({"ok": False, "error": "无权操作该矿机"}, 403)
            return self._json(wallet_set(rig, str(body.get("wallet") or "")))

        return self._send(404, "Not Found", "text/plain; charset=utf-8")


def main():
    _load_config()
    init_db()
    # D1：首次启动强制交互创建管理员（已有 admin 则跳过，不打扰）
    if not ensure_admin_user():
        print("[auth] 未创建管理员账号，服务端拒绝启动。", flush=True)
        sys.exit(2)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    load_kernel_meta()
    threading.Thread(target=sweep_loop, daemon=True).start()
    threading.Thread(target=rate_fetch_loop, daemon=True).start()
    threading.Thread(target=price_fetch_loop, daemon=True).start()
    threading.Thread(target=wallet_loop, daemon=True).start()
    threading.Thread(target=kernel_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print("[server] 扒皮矿工中控已启动  http://0.0.0.0:%d" % PORT, flush=True)
    print("[server] 管理员逃生通道 admin_token=%s（仅应急用）" % _admin_token, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
