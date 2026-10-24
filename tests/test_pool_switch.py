# -*- coding: utf-8 -*-
"""更换矿池(set_pool) / 切换内核(set_kernel) 全链路测试。

A. 服务端（临时目录跑 server.py 副本，随机端口）：
   - /api/pools 矿池目录结构
   - 上报 pool_url 落库 + /api/fleet 输出
   - /api/command 白名单与 set_pool/set_kernel 的 arg 校验
   - poll 原样带回 arg（JSON 字符串）
B. 矿机端（import miner_client，不起 GUI）：
   - _handle_cmd set_pool：格式校验 / 幂等 / 改配置 / 落盘 / 配置版本号
   - _handle_cmd set_kernel：未知内核拒绝 / 相同幂等 / 切换（ensure_kernel 免下载）
   - arg=None（旧中控不带参数）不炸
"""
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_DIR = os.path.join(ROOT, "server")
CLIENT_DIR = os.path.join(ROOT, "client")

PY = sys.executable
# 本机系统代理会劫持 127.0.0.1，所有本地请求强制直连
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

fails = []
_stats = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if cond:
        _stats["pass"] += 1
    else:
        _stats["fail"] += 1
        fails.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------- A. 服务端

def start_server(workdir):
    port = free_port()
    env = dict(os.environ, PORT=str(port))
    proc = subprocess.Popen([PY, os.path.join(workdir, "server.py")], cwd=workdir,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(60):
        try:
            with opener.open(base + "/api/health", timeout=3) as r:
                if r.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    else:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        raise RuntimeError("server 未启动:\n" + out[-2000:])
    with open(os.path.join(workdir, "config.json"), encoding="utf-8") as f:
        admin = json.load(f)["admin_token"]
    return proc, base, admin


def req(base, method, path, body=None, headers=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def server_suite():
    tmp = tempfile.mkdtemp(prefix="minrigs_pool_")
    shutil.copy(os.path.join(SERVER_DIR, "server.py"), tmp)
    proc, base, admin = start_server(tmp)
    AH = {"X-Admin-Token": admin}
    RIG, TOK = "pooltest-rig", "tok-pool-001"
    RH = {"X-Rig-Token": TOK}
    try:
        # ---- A1 /api/pools 目录 ----
        st, pools = req(base, "GET", "/api/pools", headers=AH)
        check("pools ok", st == 200 and pools.get("ok") is True, st)
        ids = [b["id"] for b in pools.get("brands") or []]
        check("pools brands", ids == ["kryptex", "pearlhash", "k1pool",
                                      "f2pool", "herominers", "alphapool"], ids)
        kx = pools["brands"][0]
        urls = [n["url"] for n in kx["nodes"]]
        check("pools node urls", "stratum+tcp://prl-hk.kryptex.network:7048" in urls, urls[:2])
        check("pools region_tag", kx["nodes"][0].get("region_tag") == "中国港澳台",
              kx["nodes"][0])
        check("pools custom", pools.get("custom", {}).get("id") == "custom")
        st, e = req(base, "GET", "/api/pools")
        check("pools need admin", st == 401, st)

        # ---- A2 上报 pool_url 落库 + fleet 输出 ----
        st, r = req(base, "POST", "/api/report", headers=RH, body={
            "name": RIG, "token": TOK, "hostname": "h", "ts": time.time(),
            "kernel": "SRBMiner-MULTI", "kernel_ver": "3.1.1", "pool_url": POOL_A,
            "gpus": [{"index": 0, "model": "RTX 4070", "temp": 60, "fan": 60, "power": 150}],
            "miners": [{"type": "srbminer", "alive": True, "ths": 120.0, "accepted": 10,
                        "rejected": 0, "uptime": 60}],
        })
        check("report with pool_url", r.get("ok") is True, r)
        st, fleet = req(base, "GET", "/api/fleet", headers=AH)
        rig0 = next((x for x in fleet["rigs"] if x["name"] == RIG), None)
        check("fleet pool_url", rig0 and rig0.get("pool_url") == POOL_A,
              rig0 and rig0.get("pool_url"))
        check("fleet snapshot pool_url", rig0 and rig0["snapshot"].get("pool_url") == POOL_A)
        # 空 pool_url 不清空已存值（CASE WHEN 保护）
        req(base, "POST", "/api/report", headers=RH, body={
            "name": RIG, "token": TOK, "hostname": "h", "ts": time.time(),
            "pool_url": "", "gpus": [], "miners": []})
        st, fleet = req(base, "GET", "/api/fleet", headers=AH)
        rig0 = next((x for x in fleet["rigs"] if x["name"] == RIG), None)
        check("empty pool_url keeps old", rig0 and rig0.get("pool_url") == POOL_A)

        # ---- A3 /api/command 白名单与 arg 校验 ----
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "set_pool", "arg": {"pool_url": POOL_B}})
        check("cmd set_pool ok", st == 200 and r.get("ok") is True, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "set_pool", "arg": {"pool_url": "prl-hk:7048"}})
        check("cmd set_pool bad url", st == 400 and r.get("ok") is False, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "set_pool", "arg": {}})
        check("cmd set_pool missing arg", st == 400, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "set_kernel", "arg": {"kernel": "WildRig"}})
        check("cmd set_kernel ok", st == 200 and r.get("ok") is True, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "set_kernel", "arg": {"kernel": "NopeMiner"}})
        check("cmd set_kernel bad key", st == 400, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": RIG, "cmd": "hack_the_planet"})
        check("cmd whitelist", st == 400, (st, r))
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": "no-such-rig", "cmd": "restart_miner"})
        check("cmd unknown rig", st == 400, (st, r))
        st, r = req(base, "POST", "/api/command",
                    body={"rig": RIG, "cmd": "set_pool", "arg": {"pool_url": POOL_B}},
                    headers={"X-Admin-Token": "wrong"})
        check("cmd need admin", st == 401, (st, r))

        # ---- A4 poll 原样带回 arg（客户端按 JSON 字符串解析）----
        st, poll = req(base, "GET", "/api/poll?name=" + RIG, headers=RH)
        cmds = poll.get("commands") or []
        check("poll count", len(cmds) == 2, cmds)
        by_cmd = {c["cmd"]: c for c in cmds}
        a1 = by_cmd.get("set_pool", {}).get("arg")
        a2 = by_cmd.get("set_kernel", {}).get("arg")
        check("poll arg is json str", isinstance(a1, str) and json.loads(a1) == {"pool_url": POOL_B}, a1)
        check("poll arg kernel", isinstance(a2, str) and json.loads(a2) == {"kernel": "WildRig"}, a2)
        # 广播：rig="*" 给所有在册机器
        req(base, "POST", "/api/report", headers={"X-Rig-Token": "tok-2"}, body={
            "name": "pooltest-rig2", "token": "tok-2", "hostname": "h2", "ts": time.time(),
            "gpus": [], "miners": []})
        st, r = req(base, "POST", "/api/command", headers=AH,
                    body={"rig": "*", "cmd": "set_pool", "arg": {"pool_url": POOL_B}})
        check("cmd broadcast", st == 200 and len(r.get("targets") or []) == 2, r)
        st, p2 = req(base, "GET", "/api/poll?name=pooltest-rig2", headers={"X-Rig-Token": "tok-2"})
        got = [c for c in (p2.get("commands") or []) if c["cmd"] == "set_pool"]
        check("poll broadcast arg", len(got) == 1 and json.loads(got[0]["arg"]) == {"pool_url": POOL_B})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- B. 矿机端

def client_suite():
    sys.path.insert(0, CLIENT_DIR)
    import miner_client as mc  # noqa: E402

    # 假内核目录：三个内核的 exe 都在，ensure_kernel 免下载直接用本地版
    kdir = tempfile.mkdtemp(prefix="fake_kernels_")
    try:
        for exe in ("SRBMiner-MULTI.exe", "bzminer.exe", "wildrig.exe"):
            open(os.path.join(kdir, exe), "wb").close()
        mc.save_kernel_version("SRBMiner-MULTI", kdir, "3.1.1")
        mc.save_kernel_version("WildRig", kdir, "0.51.2")
        mc.save_kernel_version("BzMiner", kdir, "10.0.0")

        def new_engine(**over):
            cfg = dict(mc.DEFAULT_CFG)
            cfg.update({"kernel_dir": kdir, "server": "", "token": "",
                        "pool_url": POOL_A, "rig_name": "ut-rig"})
            cfg.update(over)
            eng = mc.MiningEngine(cfg, queue.Queue())
            persisted = []
            eng.persist = lambda: persisted.append(dict(eng.cfg))
            eng._persisted = persisted      # 测试观察用
            return eng

        # ---- B1 set_pool ----
        eng = new_engine()
        ok, note = eng._handle_cmd("set_pool", {"pool_url": POOL_B})
        check("set_pool ok", ok is True, note)
        check("set_pool cfg", eng.cfg["pool_url"] == POOL_B, eng.cfg["pool_url"])
        check("set_pool persisted", eng._persisted and eng._persisted[-1]["pool_url"] == POOL_B)
        check("set_pool cfg_ver", eng._cfg_ver == 1, eng._cfg_ver)
        ok, note = eng._handle_cmd("set_pool", {"pool_url": POOL_B})
        check("set_pool idempotent", ok is True and "未变化" in note, note)
        check("set_pool idempotent no persist", len(eng._persisted) == 1, len(eng._persisted))
        ok, note = eng._handle_cmd("set_pool", {"pool_url": "1.2.3.4:7048"})
        check("set_pool bad url", ok is False and "stratum" in note, note)
        ok, note = eng._handle_cmd("set_pool", {})
        check("set_pool missing", ok is False, note)
        ok, note = eng._handle_cmd("set_pool", None)
        check("set_pool arg None", ok is False, note)

        # ---- B2 set_kernel ----
        eng = new_engine()   # DEFAULT kernel = SRBMiner-MULTI（已安装）
        ok, note = eng._handle_cmd("set_kernel", {"kernel": "SRBMiner-MULTI"})
        check("set_kernel same", ok is True and "已在使用" in note, note)
        check("set_kernel same no persist", not eng._persisted)
        ok, note = eng._handle_cmd("set_kernel", {"kernel": "Nope"})
        check("set_kernel unknown", ok is False and "未知内核" in note, note)
        ok, note = eng._handle_cmd("set_kernel", {"kernel": "WildRig"})
        check("set_kernel switch", ok is True and "WildRig" in note, note)
        check("set_kernel cfg", eng.cfg["kernel"] == "WildRig", eng.cfg["kernel"])
        check("set_kernel persisted", eng._persisted and eng._persisted[-1]["kernel"] == "WildRig")
        check("set_kernel cfg_ver", eng._cfg_ver == 1)
        # 内核 exe 缺失且无中控 → ensure_kernel 失败 → 配置不动（回滚）
        eng2 = new_engine(kernel_dir=os.path.join(kdir, "no_such_subdir"))
        ok, note = eng2._handle_cmd("set_kernel", {"kernel": "BzMiner"})
        check("set_kernel rollback", ok is False and eng2.cfg["kernel"] == "SRBMiner-MULTI",
              (ok, note, eng2.cfg["kernel"]))
        check("set_kernel rollback no persist", not eng2._persisted)

        # ---- B3 纯函数 ----
        check("valid_pool_url ok", mc.valid_pool_url("stratum+tcp://prl-hk.kryptex.network:7048"))
        check("valid_pool_url ssl", mc.valid_pool_url("stratum+ssl://pool.pearlhash.xyz:9000"))
        check("valid_pool_url bad", not mc.valid_pool_url("tcp://x:1") and
              not mc.valid_pool_url("stratum+tcp://host") and
              not mc.valid_pool_url("stratum+tcp://host:99999") and
              not mc.valid_pool_url(""))
        b, n = mc.brand_of_url(POOL_A)
        check("brand_of_url", b and b["id"] == "kryptex" and n["label"] == "中国香港",
              b and b["id"])
        check("pool_display", mc.pool_display(POOL_A) == "Kryptex · 中国香港",
              mc.pool_display(POOL_A))

        # ---- B4 服务端返回的 arg（JSON 字符串）按 _poll_cmds 同款方式解析 ----
        raw = json.dumps({"pool_url": POOL_B})
        arg = json.loads(raw) if isinstance(raw, str) else raw
        eng3 = new_engine()
        ok, note = eng3._handle_cmd("set_pool", arg)
        check("poll-arg style", ok is True and eng3.cfg["pool_url"] == POOL_B, note)
    finally:
        shutil.rmtree(kdir, ignore_errors=True)


# ---------------------------------------------------------------- C. 服务端纯函数

def unit_suite():
    sys.path.insert(0, SERVER_DIR)
    import server as srv  # noqa: E402

    check("srv valid_pool_url", srv.valid_pool_url("stratum+tcp://cn.pearl.k1pool.com:3360")
          and not srv.valid_pool_url("kryptex:7048")
          and not srv.valid_pool_url("stratum+tcp://h:0"))
    cat = srv.pool_catalog_payload()
    check("srv catalog", cat["ok"] and len(cat["brands"]) == 6 and
          cat["brands"][0]["nodes"][0]["url"] == "stratum+tcp://prl-hk.kryptex.network:7048")
    check("srv kernel keys", srv.KERNEL_KEYS == ("SRBMiner-MULTI", "BzMiner", "WildRig"))

    # ---- 链上打款来源池识别（prlscan type_label）----
    check("srv tx_pool", srv.tx_pool({"type_label": "PearlHash payout"})[0] == "pearlhash"
          and srv.tx_pool({"type_label": "Kryptex payout"})[0] == "kryptex"
          and srv.tx_pool({"type_label": "LuckyPool payout"})[0] == "luckypool"
          and srv.tx_pool({"type_label": None})[0] == "other"
          and srv.tx_pool({})[0] == "other")

    # ---- 矿池品牌判定 ----
    check("srv pool_brand_of", srv.pool_brand_of(POOL_A) == "kryptex"
          and srv.pool_brand_of("stratum+tcp://pool.pearlhash.xyz:9000") == "pearlhash"
          and srv.pool_brand_of("stratum+tcp://pool.example.com:3333") == ""
          and srv.pool_brand_of("") == "")

    # ---- 待付按品牌分发：无公开余额 API 的池明确返回不可查 ----
    w = srv.wallet_fetch_one("prl", "prl1abc", "stratum+tcp://pool.pearlhash.xyz:9000")
    check("srv unpaid pearlhash", w["unpaid_supported"] is False
          and w["total"] is None and w["pool_brand"] == "pearlhash", w)

    class _FakeResp(object):
        def __init__(self, d):
            self._d = d

        def read(self):
            return json.dumps(self._d).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _orig = srv.urllib.request.urlopen
    srv.urllib.request.urlopen = lambda req, timeout=20: _FakeResp(
        {"total": 3.52, "confirmed": 3.0, "unconfirmed": 0.52,
         "threshold": 10, "reached_pct": 35})
    try:
        w = srv.wallet_fetch_one("prl", "prl1abc", POOL_A)
        check("srv unpaid kryptex", w["unpaid_supported"] is True
              and abs(w["total"] - 3.52) < 1e-9 and w["pool_brand"] == "kryptex", w)
    finally:
        srv.urllib.request.urlopen = _orig

    # ---- wallet_stub：钱包循环未就绪时的最小对象（pending=True）----
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE rigs(name TEXT, wallet TEXT, pool_url TEXT, last_report TEXT)")
    conn.execute("INSERT INTO rigs VALUES('r1','prl1abc',?, '')",
                 ("stratum+tcp://pool.pearlhash.xyz:9000",))
    row = conn.execute("SELECT * FROM rigs").fetchone()
    st = srv.wallet_stub(row)
    check("srv wallet_stub", bool(st) and st["pending"] is True
          and st["total"] is None and st["wallet"] == "prl1abc", st)
    conn.close()

    # ---- chain_income_stats 按池归集（ pools / days_pool ）----
    now = time.time()
    TZ8 = 8 * 3600
    today0 = int((now + TZ8) // 86400) * 86400 - TZ8

    def _tx(day0, hh, grains, label):
        import datetime as _dt
        t = _dt.datetime.utcfromtimestamp(day0 + hh * 3600)
        return {"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "received_grains": grains, "type_label": label}

    txs = [_tx(today0, 2, int(1.0e8), "PearlHash payout"),
           _tx(today0, 6, int(0.5e8), "Kryptex payout"),
           _tx(today0 - 86400, 20, int(2.0e8), "LuckyPool payout")]
    inc = srv.chain_income_stats(txs)
    check("srv inc pools", abs(inc["today"] - 1.5) < 1e-6
          and abs(inc["pools"]["pearlhash"]["today"] - 1.0) < 1e-6
          and abs(inc["pools"]["kryptex"]["today"] - 0.5) < 1e-6
          and abs(inc["pools"]["luckypool"]["yesterday"] - 2.0) < 1e-6
          and inc["pools"]["pearlhash"]["name"] == "PearlHash", inc["pools"])
    day = time.strftime("%Y-%m-%d", time.gmtime(today0 + TZ8))
    dp = inc["days_pool"].get(day) or {}
    check("srv inc days_pool", abs(dp.get("pearlhash", 0) - 1.0) < 1e-6
          and abs(dp.get("kryptex", 0) - 0.5) < 1e-6, dp)


POOL_A = "stratum+tcp://prl-hk.kryptex.network:7048"
POOL_B = "stratum+tcp://cn.pearl.k1pool.com:3360"

if __name__ == "__main__":
    unit_suite()
    server_suite()
    client_suite()
    print("\n%d passed, %d failed" % (_stats["pass"], _stats["fail"]))
    if fails:
        print("FAILED: %s" % "、".join(fails))
    sys.exit(0 if not fails else 1)
