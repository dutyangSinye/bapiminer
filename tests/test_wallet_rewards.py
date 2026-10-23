# -*- coding: utf-8 -*-
"""中控「今日实际收入」逻辑离线验证：链上浏览器口径。

数据源为 prlscan 链上交易（wallet_chain_txs，时间倒序），
chain_income_stats 按北京时间归集：收到即计入，转出（received_grains<=0）不计。
"""
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "server", "server.py")

spec = importlib.util.spec_from_file_location("srv", SRC)
srv = importlib.util.module_from_spec(spec)
sys.modules["srv"] = srv
spec.loader.exec_module(srv)

ok_n = 0
fail = []


def check(name, cond, detail=""):
    global ok_n
    if cond:
        ok_n += 1
        print("PASS  %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        fail.append(name)
        print("FAIL  %s  %s" % (name, detail))


TZ8 = 8 * 3600
now = time.time()
today0 = int((now + TZ8) // 86400) * 86400 - TZ8  # 北京时间当天零点（UTC 时间戳）
today_str = time.strftime("%Y-%m-%d", time.gmtime(now + TZ8))


def iso(ts):
    """UTC 时间戳 -> explorer API 的 time 字符串（ISO UTC，秒级）。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def tx(ts, amount):
    """构造一笔链上交易：amount 为 PRL 币数（负数表示转出/归集）。

    链上以 grains 计价（1 PRL = 1e8 grains），这里按真实口径取整到 grains。
    """
    return {"time": iso(ts), "received_grains": int(round(amount * 1e8)),
            "type_label": "PearlHash payout"}


# 1. 今日归集：复现用户截图场景（今天 5 笔打款，另有一笔转出）
# 截图 5 笔相加 = 7.984996142 PRL；链上按 grains 取整后合计 798499615 grains
# = 7.98499615，统一取 6 位小数 -> 7.984996（展示到 4 位即 7.9850）
today_pay = [1.263362516, 1.918742216, 1.12462807, 1.99928932, 1.67897402]
elapsed = max(now - today0, 1)  # 北京时间今天已过去的秒数
txs = [tx(today0 + elapsed * f, a) for f, a in
       zip((0.1, 0.3, 0.5, 0.7, 0.9), today_pay)]
txs.append(tx(today0 + elapsed * 0.95, -10.77064676))  # 转出，不计收入
st = srv.chain_income_stats(txs)
check("今日 5 笔打款合计 = 7.984996", abs(st["today"] - 7.984996) < 1e-6, str(st["today"]))
check("转出不计入收入笔数", st["count"] == 5, str(st["count"]))
check("总额只算收到（不限窗口）", abs(st["total"] - 7.984996) < 1e-6, str(st["total"]))
check("days 今日键为北京日期", abs(st["days"].get(today_str, 0) - 7.984996) < 1e-6,
      json.dumps(st["days"]))

# 2. 北京日期边界：00:00:00 算今天，昨天 23:59:59 算昨天
stb = srv.chain_income_stats([tx(today0, 1.0), tx(today0 - 1, 2.0),
                              tx(today0 + 86399, 4.0)])
check("北京当天 00:00:00 计入今日", abs(stb["today"] - 5.0) < 1e-9, str(stb["today"]))
check("昨天 23:59:59 计入昨日", stb["yesterday"] == 2.0, str(stb["yesterday"]))

# 3. 滚动窗口：h24 / d7 / d30（严格大于窗口起点才计入；total 不限窗口）
win = [tx(now - 3600, 1.0),              # 1 小时前：全窗口
       tx(now - 90000, 2.0),             # 25 小时前：出 h24，留 d7
       tx(now - 7 * 86400 + 60, 4.0),    # 7 天前 1 分钟：卡在 d7 内
       tx(now - 7 * 86400 - 60, 8.0),    # 7 天前 1 分外：出 d7，留 d30
       tx(now - 30 * 86400 + 60, 16.0),  # 30 天前 1 分钟：卡在 d30 内
       tx(now - 30 * 86400 - 60, 32.0)]  # 30 天前 1 分外：出全部窗口
stw = srv.chain_income_stats(win)
check("h24 只含 24 小时内", abs(stw["h24"] - 1.0) < 1e-9, str(stw["h24"]))
check("d7 含近 7 天（不含第 8 天）", abs(stw["d7"] - 7.0) < 1e-9, str(stw["d7"]))
check("d30 含近 30 天（不含第 31 天）", abs(stw["d30"] - 31.0) < 1e-9, str(stw["d30"]))
check("total 为全部收到（不限窗口）", abs(stw["total"] - 63.0) < 1e-9, str(stw["total"]))

# 4. 按天归集：近 30 天每天一笔 -> 30 个北京日期键，days 合计 = total
many = [tx(now - i * 86400, 2.0) for i in range(30)]
many.append(tx(now - 40 * 86400, 999.0))  # 更早的一笔也进 days（total 口径）
stm = srv.chain_income_stats(many)
check("近30日 = 今日起 30 天合计", abs(stm["d30"] - 60.0) < 1e-9, str(stm["d30"]))
check("近7日 = 今日起 7 天合计", abs(stm["d7"] - 14.0) < 1e-9, str(stm["d7"]))
check("今日取当日值", abs(stm["today"] - 2.0) < 1e-9, str(stm["today"]))
check("days 覆盖全部有交易的日期", len(stm["days"]) == 31, str(len(stm["days"])))
check("days 合计 = total", abs(sum(stm["days"].values()) - stm["total"]) < 1e-6,
      "%s vs %s" % (sum(stm["days"].values()), stm["total"]))

# 5. 脏数据容错：不炸、不计入（零额/转出/坏时间/缺字段/非 dict/None）
st_bad = srv.chain_income_stats([
    {"time": iso(today0 + 60), "received_grains": 0},          # 0 额
    {"time": iso(today0 + 60), "received_grains": -1077064676},  # 转出
    {"time": "not-a-time", "received_grains": 100000000},
    {"time": None, "received_grains": 100000000},
    {"received_grains": 100000000},                              # 缺 time
    "not-a-dict",                                                # 非 dict
    None,                                                        # None
    {"time": "", "received_grains": 100000000},                  # 空 time
])
check("脏数据全部忽略", st_bad["today"] == 0.0 and st_bad["count"] == 0,
      json.dumps(st_bad["days"]))
check("空列表不炸", srv.chain_income_stats([])["today"] == 0.0)
check("None 不炸", srv.chain_income_stats(None)["d30"] == 0.0)
# 格式变体：字符串 grains / 带微秒的 time 仍可解析
st_fmt = srv.chain_income_stats([
    {"time": iso(today0 + 120), "received_grains": "500000000"},
    {"time": iso(today0 + 180) + ".123Z", "received_grains": 100000000},
])
check("字符串 grains 与微秒 time 可解析", abs(st_fmt["today"] - 6.0) < 1e-9, str(st_fmt["today"]))

# 6. wallet_chain_txs：URL 拼接 + cursor 翻页 + 上限 + 非 dict 兜底
captured = {"urls": [], "pages": [], "page": 0}


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return json.dumps(self._d).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    captured["urls"].append(req.full_url)
    if captured["page"] < len(captured["pages"]):
        d = captured["pages"][captured["page"]]
        captured["page"] += 1
        return _FakeResp(d)
    return _FakeResp({"items": [], "next_cursor": ""})


_orig_urlopen = srv.urllib.request.urlopen
srv.urllib.request.urlopen = _fake_urlopen
try:
    captured["urls"], captured["page"] = [], 0
    captured["pages"] = [
        {"items": [{"time": "x"}], "next_cursor": "CURSOR1"},
        {"items": [{"time": "y"}], "next_cursor": ""},
    ]
    out = srv.wallet_chain_txs("prl", "prl1walletaddr")
    check("浏览器 URL 正确",
          captured["urls"][0] == "https://api.prlscan.com/v1/addresses/prl1walletaddr/txs?limit=100",
          captured["urls"][0])
    check("next_cursor 翻页", captured["urls"][1].endswith("&cursor=CURSOR1"),
          captured["urls"][1])
    check("翻页合并条数", len(out) == 2, str(len(out)))
    check("无 next_cursor 时停止", len(captured["urls"]) == 2, str(len(captured["urls"])))

    captured["urls"], captured["page"] = [], 0
    captured["pages"] = [{"items": [{"i": 0}], "next_cursor": "C%d" % i} for i in range(10)]
    out2 = srv.wallet_chain_txs("prl", "w", pages=2)
    check("pages 上限生效（不无限翻页）", len(captured["urls"]) == 2, str(len(captured["urls"])))
    check("pages=2 取满 2 页", len(out2) == 2, str(len(out2)))

    captured["urls"], captured["page"] = [], 0
    captured["pages"] = [{"items": []}]
    srv.wallet_chain_txs("prl", "  prl1walletaddr  ")
    check("钱包地址 strip", captured["urls"][0].endswith("/prl1walletaddr/txs?limit=100"),
          captured["urls"][0])

    captured["urls"], captured["page"] = [], 0
    captured["pages"] = [[]]  # 顶层非 dict（异常响应）
    try:
        srv.wallet_chain_txs("prl", "w")
        check("非 dict 响应兜底不炸", True)
    except Exception as e:  # noqa: BLE001
        check("非 dict 响应兜底不炸", False, repr(e))
finally:
    srv.urllib.request.urlopen = _orig_urlopen

# 7. pool_coin 推断币种（余额/链上交易都用它拼 URL）
check("prl-hk 节点推断 prl（区属后缀不是币种，否则 API 502）",
      srv.pool_coin("stratum+tcp://prl-hk.kryptex.network:7048") == "prl",
      srv.pool_coin("stratum+tcp://prl-hk.kryptex.network:7048"))
check("prl 节点推断 prl", srv.pool_coin("stratum+tcp://prl.kryptex.network:7048") == "prl")
check("未知池返回空", srv.pool_coin("stratum+tcp://pool.example.com:1234") == "")

# 8. fleet_payload：同一钱包被多台矿机共用（各自上报同一地址）时聚合不翻倍
srv._wallet_map = {
    "rigA": {"coin": "prl", "wallet": "W1", "total": 2.0,
             "paid": {"h24": 0.5, "yesterday": 1.0, "d7": 3.0, "d30": 5.0, "total": 9.0},
             "rewards": {"today": 0.25, "yesterday": 1.0, "d7": 3.0, "d30": 5.0}},
    "rigB": {"coin": "prl", "wallet": "W1", "total": 2.0,
             "paid": {"h24": 0.5, "yesterday": 1.0, "d7": 3.0, "d30": 5.0, "total": 9.0},
             "rewards": {"today": 0.25, "yesterday": 1.0, "d7": 3.0, "d30": 5.0}},
    "rigC": {"coin": "prl", "wallet": "W2", "total": 1.0,
             "paid": {"h24": 0.1, "yesterday": 0.2, "d7": 0.6, "d30": 1.0, "total": 2.0},
             "rewards": {"today": 0.1, "yesterday": 0.2, "d7": 0.6, "d30": 1.0}},
}
fp = srv.fleet_payload()
tt = fp["total"]
check("fleet ok", fp.get("ok") is True)
check("共用钱包不重复计余额", abs(tt["wallet_total"] - 3.0) < 1e-9, str(tt["wallet_total"]))
check("共用钱包不重复计实收", abs(tt["wallet_paid"]["yesterday"] - 1.2) < 1e-9,
      str(tt["wallet_paid"]["yesterday"]))
check("今日实际收入跨钱包合计", abs(tt["wallet_reward"]["today"] - 0.35) < 1e-9,
      str(tt["wallet_reward"]["today"]))
check("今日实际收入近7日合计", abs(tt["wallet_reward"]["d7"] - 3.6) < 1e-9,
      str(tt["wallet_reward"]["d7"]))
check("收益币种标签", tt["wallet_reward_coin"] == "PRL", tt["wallet_reward_coin"])
check("余额币种标签", tt["wallet_coin"] == "PRL", tt["wallet_coin"])
srv._wallet_map = {}

# 9. daily_payload：打款记录按天并入时同样去重（共用钱包不翻倍）
srv._wallet_map = {
    "rigA": {"coin": "prl", "wallet": "W1", "total": 2.0,
             "paid": {"days": {"2026-10-20": 5.606644}, "total": 5.606644}},
    "rigB": {"coin": "prl", "wallet": "W1", "total": 2.0,
             "paid": {"days": {"2026-10-20": 5.606644}, "total": 5.606644}},
}
dp = srv.daily_payload(30)
day20 = [d for d in dp.get("days") or [] if d["day"] == "2026-10-20"]
check("按天明细打款不翻倍", bool(day20) and abs(day20[0]["paid"] - 5.606644) < 1e-6,
      str(day20[0]["paid"] if day20 else None))
srv._wallet_map = {}

# 10. daily_payload：估算按北京日期归集（曾因 gmtime(dkey*86400-28800) 整表提前
# 一天，导致「估算属于第二天、实收属于当天」错行，且今日行永不存在、kt 估算兜底失效）
import sqlite3
import tempfile

d2_ago = time.strftime("%Y-%m-%d", time.gmtime(now + TZ8 - 2 * 86400))
yesterday = time.strftime("%Y-%m-%d", time.gmtime(now + TZ8 - 86400))
_tmp = tempfile.mktemp(suffix=".db", prefix="daily_label_")
_tc = sqlite3.connect(_tmp)
_tc.row_factory = sqlite3.Row
_tc.execute("CREATE TABLE samples(rig TEXT NOT NULL, ts REAL NOT NULL, ths REAL, "
            "power REAL, temp REAL, fan REAL, accepted INTEGER, rejected INTEGER, "
            "uptime INTEGER)")   # 故意不建 pool 列，顺带覆盖老库兼容分支
for _i, _v in enumerate((100.0, 200.0, 300.0)):
    _tc.execute("INSERT INTO samples(rig, ts, ths) VALUES ('rigX', ?, ?)",
                (today0 + 3600 - _i * 86400, _v))
_tc.commit()

_orig_conn = getattr(srv._conn_local, "c", None)
srv._conn_local.c = _tc
try:
    srv._wallet_map = {
        "rigX": {"coin": "prl", "wallet": "W1", "total": 1.5,
                 "paid": {"days": {today_str: 1.5}, "total": 1.5}},
    }
    dp = srv.daily_payload(7)
    got = {d["day"]: d for d in dp.get("days") or []}
    check("估算行恰好覆盖今/昨/前三天（无跨日偏移）",
          set(got) == {today_str, yesterday, d2_ago}, str(sorted(got)))
    check("今日行含今日样本（ths=100）",
          bool((got.get(today_str) or {}).get("rigs")) and
          abs(got[today_str]["rigs"][0]["ths"] - 100.0) < 0.01,
          json.dumps(got.get(today_str)))
    check("昨日行含昨日样本（ths=200）",
          bool((got.get(yesterday) or {}).get("rigs")) and
          abs(got[yesterday]["rigs"][0]["ths"] - 200.0) < 0.01)
    check("实收与估算同日对齐（今日 paid=1.5）",
          abs((got.get(today_str) or {}).get("paid", 0) - 1.5) < 1e-9,
          str((got.get(today_str) or {}).get("paid")))
finally:
    srv._wallet_map = {}
    srv._conn_local.c = _orig_conn
    _tc.close()
    os.remove(_tmp)

print("\n%d passed, %d failed" % (ok_n, len(fail)))
if fail:
    print("失败项：" + "、".join(fail))
sys.exit(1 if fail else 0)
