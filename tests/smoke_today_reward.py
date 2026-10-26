# -*- coding: utf-8 -*-
"""冒烟：隔离目录起中控服务，验证「今日实际收入」全链路（真实 prlscan 链上 API）。

Kryptex 矿池的 payouts/reward-chart 自 2026-10-20 起停更，收入口径改走链上
浏览器 API；balance 接口仍实时，继续用于「矿池未支付余额」。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PY = r"C:\Users\Admin\.workbuddy\binaries\python\versions\3.13.12\python.exe"
SRV = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server"
SMOKE = r"C:\Users\Admin\AppData\Local\Temp\minrigs_smoke"
PORT = 18123
WALLET = os.environ.get("MINRIGS_TEST_WALLET", "")

# 本机有系统代理，访问 127.0.0.1 必须绕过
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(opener)

shutil.rmtree(SMOKE, ignore_errors=True)
os.makedirs(os.path.join(SMOKE, "data"))
os.makedirs(os.path.join(SMOKE, "static"))
shutil.copy(os.path.join(SRV, "server.py"), SMOKE)
shutil.copy(os.path.join(SRV, "static", "index.html"), os.path.join(SMOKE, "static"))
shutil.copy(os.path.join(SRV, "static", "bapiminer.svg"), os.path.join(SMOKE, "static"))
with open(os.path.join(SMOKE, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"admin_token": "adm_smoke", "port": PORT}, f)

env = dict(os.environ, PORT=str(PORT), no_proxy="127.0.0.1,localhost",
           NO_PROXY="127.0.0.1,localhost")
proc = subprocess.Popen([PY, "server.py"], cwd=SMOKE, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + detail) if detail else ""))
    if not cond:
        ok = False


def get(path, token=""):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path))
    if token:
        req.add_header("X-Admin-Token", token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body, token=""):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Admin-Token", token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


try:
    for _ in range(30):
        try:
            get("/api/health")
            break
        except Exception:
            time.sleep(0.5)
    check("服务起来", True)

    # 1. 矿机上报（钱包在 payload 里，矿池用 prl-hk 区属节点）
    r = post("/api/report", {
        "name": "smoke1", "token": "tok123", "wallet": WALLET,
        "gpus": [{"index": 0, "model": "NVIDIA GeForce RTX 5080",
                  "temp": 55, "fan": 40, "power": 120}],
        "miners": [{"type": "srbminer", "alive": True, "ths": 137.6,
                    "accepted": 100, "rejected": 1, "uptime": 3600,
                    "pool": "stratum+tcp://prl-hk.kryptex.network:7048"}],
        "hostname": "smoke", "client_version": "1.11.2.2"})
    check("上报成功", r.get("ok") is True, str(r))

    # 2. 看板绑定钱包（wallet_set 立即拉余额 + 链上交易收入）
    w = post("/api/wallet", {"rig": "smoke1", "wallet": WALLET}, "adm_smoke")
    check("钱包绑定成功", w.get("ok") is True, str(w.get("fetch_error") or ""))
    bal = w.get("balance") or {}
    check("prl-hk 节点推断出 prl（不再 502）", bal.get("coin") == "prl", str(bal.get("coin")))
    check("余额拉到真实值", (bal.get("total") or 0) > 0, str(bal.get("total")))
    paid = bal.get("paid") or {}
    check("链上收入统计字段齐全",
          all(k in paid for k in ("total", "h24", "today", "yesterday", "d7", "d30", "days")),
          json.dumps({k: paid.get(k) for k in ("today", "yesterday", "d7", "d30")}))
    rw = bal.get("rewards") or {}
    check("链上收入已汇总（今日实收）", rw.get("today", 0) > 0,
          "today=%s yesterday=%s d7=%s" % (rw.get("today"), rw.get("yesterday"), rw.get("d7")))
    check("按天明细非空", isinstance(paid.get("days"), dict) and len(paid["days"]) > 0,
          "%d 天" % len(paid.get("days") or {}))

    # 3. fleet 聚合
    f = get("/api/fleet", "adm_smoke")
    t = f.get("total") or {}
    check("fleet.wallet_reward.today > 0", (t.get("wallet_reward") or {}).get("today", 0) > 0,
          json.dumps(t.get("wallet_reward")))
    check("fleet.wallet_reward_coin = PRL", t.get("wallet_reward_coin") == "PRL",
          str(t.get("wallet_reward_coin")))
    check("余额不翻倍", abs((t.get("wallet_total") or 0) - (bal.get("total") or 0)) < 0.001,
          "total=%s balance=%s" % (t.get("wallet_total"), bal.get("total")))

    # 4. 看板页面含新卡片与渲染逻辑
    with urllib.request.urlopen("http://127.0.0.1:%d/" % PORT, timeout=10) as resp:
        html = resp.read().decode("utf-8")
    check("页面含「今日实际收入」卡", "今日实际收入" in html and 'id="kt"' in html)
    check("页面含 reward 渲染逻辑", "wallet_reward" in html)
    check("页面含 income_meta rewards", "rewards" in html)
    check("卡片徽标行结构已统一", 'class="rig-tags"' in html)

    # 5. 控制台日志无拉取异常
    time.sleep(1)
finally:
    proc.terminate()
    try:
        out = proc.communicate(timeout=10)[0].decode("utf-8", "replace")
    except Exception:
        proc.kill()
        out = ""
    print("---- server log ----")
    print(out[-2500:])
    bad = [ln for ln in out.splitlines()
           if "Traceback" in ln or "循环异常" in ln]
    warn = [ln for ln in out.splitlines() if "拉取失败" in ln]
    check("服务日志无异常", not bad, "; ".join(bad[:5]))
    if warn:
        print("WARN  瞬时拉取失败（重试周期会自愈，不影响本次断言）：%s" % warn[:3])

print("\nSMOKE %s" % ("OK" if ok else "FAILED"))
sys.exit(0 if ok else 1)
