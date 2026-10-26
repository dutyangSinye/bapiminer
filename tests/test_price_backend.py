# -*- coding: utf-8 -*-
"""币价后端逻辑单测（不依赖外网，注入假响应）。

★ 当前契约：平台只展示 **珍珠币 PRL(PEARL)** 的币价，
  所以 PRICE_COINS 里只有 PRL，抓取器也只保留能提供 PRL 的源。

覆盖：
  1. SafeTrade 解析：last / price_change_percent(带%) / high / low / volume
  2. Gate.io 解析：last / change_percentage / high_24h / low_24h / base_volume
  3. Coinbase 停用（拿不到 PRL，不应出现在抓取器列表里）
  4. 优先级：有 PRL 的源胜出
  5. 异常源（抛异常/返回垃圾）被跳过，不影响整体
  6. price_payload 输出结构与字段（只含 PRL）
  7. 全部源失败 -> 沿用旧值，不清空

用法：python tests/test_price_backend.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

import server as srv  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s  %s" % (name, detail))


def reset():
    with srv._price_lock:
        srv._price_map.clear()
        srv._price_ts = 0.0
        srv._price_src = ""
    srv._last_urls = []


# ---------------------------------------------------------------- 假数据

ST_RESP = {
    "id": "pearlusdt", "name": "PEARL/USDT", "base_unit": "pearl",
    "quote_unit": "usdt", "avg_price": "1.85", "high": "1.93",
    "last": "1.9", "low": "1.75", "open": "1.9",
    "price_change_percent": "+2.54%", "volume": "3614901.27",
    "amount": "1951266.82",
}
ST_BTC = dict(ST_RESP, id="btcusdt", name="BTC/USDT", last="80400.1",
               high="81805.3", low="79988.4", open="80668.1",
               price_change_percent="-0.33%", volume="27921.27")

GATE_ROWS = [{
    "currency_pair": "PEARL_USDT", "last": "1.906", "lowest_ask": "1.906",
    "change_percentage": "2.54", "base_volume": "202595.7",
    "quote_volume": "375164.03", "high_24h": "1.95", "low_24h": "1.761",
}]
GATE_BTC = [{
    "currency_pair": "BTC_USDT", "last": "80550", "change_percentage": "-0.04",
    "base_volume": "2945.28", "high_24h": "81029", "low_24h": "80090.3",
}]

CB = {"data": {"amount": "80550.10"}}


def fake_get(mapping):
    """返回一个 get(url) 函数，按 URL 子串匹配。"""
    def _get(url, timeout=12):
        srv._last_urls.append(url)
        for k, v in mapping.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v
        raise OSError("no fake for %s" % url)
    return _get


# ---------------------------------------------------------------- 用例

def t_safetrade_parse():
    print("\n[1] SafeTrade 解析")
    reset()
    srv._http_get_json = fake_get({
        "pearlusdt": ST_RESP,
        "btcusdt": ST_BTC,
    })
    got = srv._fetch_prices_safetrade()
    check("拿到 PRL", "PRL" in got, str(list(got)))
    check("只拿到 PRL（PRICE_COINS 已收敛为单一币种）",
          list(got) == ["PRL"], str(list(got)))
    check("SafeTrade 请求用 market id（base+quote 小写）",
          any(u.endswith("/pearlusdt") for u in srv._last_urls), str(srv._last_urls))
    check("不会去请求 btcusdt（没在 PRICE_COINS 里）",
          not any("btcusdt" in u for u in srv._last_urls), str(srv._last_urls))
    check("PRL last=1.9", abs(got["PRL"]["last"] - 1.9) < 1e-9, str(got.get("PRL")))
    check("PRL change=2.54（去掉%号）",
          abs(got["PRL"]["change"] - 2.54) < 1e-9, str(got.get("PRL")))
    check("PRL high=1.93", abs(got["PRL"]["high"] - 1.93) < 1e-9, str(got.get("PRL")))
    check("PRL low=1.75", abs(got["PRL"]["low"] - 1.75) < 1e-9, str(got.get("PRL")))
    check("PRL volume 解析", abs(got["PRL"]["volume"] - 3614901.27) < 1e-6, str(got.get("PRL")))
    # 关键回归：/trade/public/tickers（列表端点）必须不能被当成价格源
    srv._http_get_json = fake_get({"tickers": {"pearlusdt": {"name": "PEARL/USDT"}}})
    got2 = srv._fetch_prices_safetrade()
    check("列表端点返回的名字不算价格（last<=0 被丢弃）",
          all(v["last"] > 0 for v in got2.values()), str(got2))
    check("SafeTrade 走的是 /trade/public/tickers/{market} 端点",
          all("/trade/public/tickers/" in u for u in srv._last_urls),
          str(srv._last_urls))


def t_gate_parse():
    print("\n[2] Gate.io 解析")
    reset()
    srv._http_get_json = fake_get({
        "PEARL_USDT": GATE_ROWS,
        "BTC_USDT": GATE_BTC,
    })
    got = srv._fetch_prices_gate()
    check("拿到 PRL", "PRL" in got, str(list(got)))
    check("只拿到 PRL（PRICE_COINS 已收敛为单一币种）",
          list(got) == ["PRL"], str(list(got)))
    check("不会去请求 BTC_USDT", not any("BTC_USDT" in u for u in srv._last_urls),
          str(srv._last_urls))
    check("PRL last=1.906", abs(got["PRL"]["last"] - 1.906) < 1e-9, str(got.get("PRL")))
    check("PRL change=2.54", abs(got["PRL"]["change"] - 2.54) < 1e-9, str(got.get("PRL")))
    check("PRL high_24h=1.95", abs(got["PRL"]["high"] - 1.95) < 1e-9, str(got.get("PRL")))
    check("PRL low_24h=1.761", abs(got["PRL"]["low"] - 1.761) < 1e-9, str(got.get("PRL")))
    check("PRL volume=base_volume",
          abs(got["PRL"]["volume"] - 202595.7) < 1e-6, str(got.get("PRL")))


def t_coinbase_disabled():
    print("\n[3] Coinbase 已停用")
    names = [n for n, _fn in srv._PRICE_FETCHERS]
    check("抓取器列表里没有 Coinbase", "Coinbase" not in names, str(names))
    check("抓取器列表只剩 SafeTrade + Gate.io",
          names == ["SafeTrade", "Gate.io"], str(names))
    # 函数体保留但已无引用；即便手动调用也拿不到 PRL（Coinbase 无此币）
    reset()
    srv._http_get_json = fake_get({"BTC-USD": CB})
    got = srv._fetch_prices_coinbase()
    check("Coinbase 拿不到 PRL（无此交易对）", "PRL" not in got, str(list(got)))


def t_priority():
    print("\n[4] 数据源优先级")
    reset()
    # SafeTrade 可用 -> 应选 SafeTrade
    srv._PRICE_FETCHERS = [
        ("SafeTrade", lambda: {"PRL": {"last": 1.9, "change": 2.5, "high": 1.9,
                                       "low": 1.7, "volume": 1.0}}),
        ("Gate.io", lambda: {"PRL": {"last": 9.9, "change": 9.9, "high": 9.9,
                                     "low": 9.9, "volume": 9.9}}),
    ]
    srv._fetch_prices()
    check("选中 SafeTrade", srv._price_src == "SafeTrade", srv._price_src)
    check("价格来自 SafeTrade",
          abs(srv._price_map["PRL"]["last"] - 1.9) < 1e-9, str(srv._price_map))

    # SafeTrade 挂 -> 应退到 Gate.io
    reset()
    srv._PRICE_FETCHERS = [
        ("SafeTrade", lambda: {}),
        ("Gate.io", lambda: {"PRL": {"last": 1.906, "change": 2.54, "high": 1.95,
                                     "low": 1.761, "volume": 202595.7}}),
    ]
    srv._fetch_prices()
    check("SafeTrade 无结果时退到 Gate.io", srv._price_src == "Gate.io", srv._price_src)
    check("Gate.io 的 PRL 价生效",
          abs(srv._price_map["PRL"]["last"] - 1.906) < 1e-9, str(srv._price_map))

    # 只有 SafeTrade 返回的币没有 PRL -> 不应被采纳为有效行情
    reset()
    srv._PRICE_FETCHERS = [
        ("OnlyBTC", lambda: {"BTC": {"last": 1.0, "change": 0, "high": None,
                                     "low": None, "volume": None}}),
        ("NoPRL", lambda: {"BTC": {"last": 2.0, "change": 0, "high": None,
                                   "low": None, "volume": None},
                           "ETH": {"last": 3.0, "change": 0, "high": None,
                                   "low": None, "volume": None}}),
    ]
    srv._fetch_prices()
    check("无 PRL 的源不被采纳", srv._price_src == "", srv._price_src)
    check("无 PRL 的币种不进价格表", len(srv._price_map) == 0, str(srv._price_map))


def t_errors():
    print("\n[5] 异常源处理")
    reset()

    def boom():
        raise RuntimeError("network down")

    srv._PRICE_FETCHERS = [
        ("Bad", boom),
        ("Garbage", lambda: {"PRL": {"last": "not-a-number"}}),
        ("Good", lambda: {"PRL": {"last": 1.5, "change": 1.0, "high": None,
                                  "low": None, "volume": None}}),
    ]
    srv._fetch_prices()
    check("异常源被跳过，好源生效", srv._price_src == "Good", srv._price_src)
    check("好源价格正确",
          abs(srv._price_map["PRL"]["last"] - 1.5) < 1e-9, str(srv._price_map))
    # 全部失败 -> 沿用旧值，不清空
    reset()
    with srv._price_lock:
        srv._price_map["PRL"] = {"last": 7.7, "change": 0, "high": None,
                                  "low": None, "volume": None}
        srv._price_ts = 12345.0
    srv._PRICE_FETCHERS = [("AllBad", boom)]
    srv._fetch_prices()
    check("全失败时保留旧值", abs(srv._price_map["PRL"]["last"] - 7.7) < 1e-9,
          str(srv._price_map))
    check("全失败时 ts 不被清零", srv._price_ts == 12345.0, str(srv._price_ts))


def t_payload():
    print("\n[6] price_payload 输出结构")
    reset()
    with srv._price_lock:
        srv._price_map.update({
            "PRL": {"last": 1.906, "change": 2.54, "high": 1.95, "low": 1.761,
                    "volume": 202595.7},
            "BTC": {"last": 80550, "change": -0.04, "high": 81029, "low": 80090.3,
                    "volume": 2945.28},
        })
        srv._price_ts = 1000.0
        srv._price_src = "Gate.io"
    out = srv.price_payload()
    check("ok=True", out.get("ok") is True, str(out.get("ok")))
    check("source=Gate.io", out.get("source") == "Gate.io", str(out.get("source")))
    check("coins 是列表", isinstance(out.get("coins"), list), str(type(out.get("coins"))))
    check("coins 只含 1 个币种（只要珍珠币）",
          len(out["coins"]) == 1, str(out["coins"]))
    check("唯一的币种就是 PRL", out["coins"][0]["symbol"] == "PRL", str(out["coins"][0]))
    check("BTC 不出现在 payload 里（PRICE_COINS 已无 BTC）",
          all(c["symbol"] != "BTC" for c in out["coins"]), str(out["coins"]))
    c0 = out["coins"][0]
    for f in ("symbol", "market", "name", "last", "change", "high", "low", "volume"):
        check("coin 字段 %s 存在" % f, f in c0, str(list(c0)))
    check("name 是中文名「珍珠币」", c0["name"] == "珍珠币", c0.get("name"))
    check("market 是 PEARL", c0["market"] == "PEARL", c0.get("market"))
    check("age 有值", isinstance(out.get("age"), int), str(out.get("age")))
    check("cny_rate 存在", "cny_rate" in out, str(list(out)))


def t_route():
    print("\n[7] 路由与线程")
    src = open(os.path.join(ROOT, "server", "server.py"), encoding="utf-8").read()
    check("存在 /api/price 路由", 'if p == "/api/price":' in src)
    check("路由调用 price_payload()", "return self._json(price_payload())" in src)
    check("注册了 price_fetch_loop 线程",
          "threading.Thread(target=price_fetch_loop, daemon=True).start()" in src)
    check("PRICE_COINS 只含 PRL 一项",
          '("PRL", "PEARL", "珍珠币")' in src
          and '("BTC", "BTC"' not in src
          and '("ETH", "ETH"' not in src
          and '("LTC", "LTC"' not in src
          and '("DOGE", "DOGE"' not in src)
    check("PRICE_COINS 实际长度 == 1", len(srv.PRICE_COINS) == 1, str(srv.PRICE_COINS))
    check("PRICE_COINS 里没有其它币种",
          [c[0] for c in srv.PRICE_COINS] == ["PRL"], str(srv.PRICE_COINS))


def main():
    # 保留原始 fetcher 与 http 钩子
    orig_fetchers = srv._PRICE_FETCHERS
    orig_get = srv._http_get_json
    try:
        t_safetrade_parse()
        t_gate_parse()
        t_coinbase_disabled()
        t_priority()
        t_errors()
        t_payload()
        t_route()
    finally:
        srv._PRICE_FETCHERS = orig_fetchers
        srv._http_get_json = orig_get
        reset()
    print("\n===== 结果: %d PASS / %d FAIL =====" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
