# -*- coding: utf-8 -*-
"""一次性抓取出站流，统计 metrics 推送频率与内容。"""
import json, time, urllib.request

def fetch(url):
    req = urllib.request.Request(url)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

d = fetch("http://127.0.0.1:5760/f9b1524f697635e58b699a22/api/poll?since=204")
items = d.get("items", [])
print("since204->ver", d.get("ver"), "items:", len(items))
kinds = {}
last_metrics = None
for it in items:
    kinds[it.get("t")] = kinds.get(it.get("t"), 0) + 1
    if it.get("t") == "metrics":
        last_metrics = it
print("kinds:", kinds)
if last_metrics:
    keep = {k: v for k, v in last_metrics.items() if k in (
        "ths", "gamemode", "game_proc", "alive", "uptime", "ts", "error", "accepted")}
    print("last metrics:", json.dumps(keep, ensure_ascii=False))
# 最近 3 条日志
logs = [it for it in items if it.get("t") == "log"]
for it in logs[-3:]:
    print("LOG:", str(it.get("msg"))[:150])
