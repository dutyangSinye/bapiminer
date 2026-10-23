# -*- coding: utf-8 -*-
"""监控运行实例事件流，观察游戏模式是否触发。"""
import json, time, urllib.request, sys

BASE = "http://127.0.0.1:11375/ad8f341bb0b4041ed8e81239/api/poll?since="
since = 0
deadline = time.time() + 180
last_pill = None
last_hold = None
seen_log = 0

def fetch(url):
    req = urllib.request.Request(url)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

while time.time() < deadline:
    try:
        d = fetch(BASE + str(since))
        since = d.get("ver", since)
        for it in d.get("items", []):
            t = it.get("t")
            if t == "pill":
                last_pill = it.get("text")
                print("[%s] PILL -> %s (%s)" % (time.strftime("%H:%M:%S"), it.get("text"), it.get("kind")), flush=True)
            elif t == "metrics":
                hold = it.get("gamemode")
                if hold != last_hold:
                    last_hold = hold
                    print("[%s] GAMEMODE_HOLD -> %s (game_proc=%s, ths=%s)" % (
                        time.strftime("%H:%M:%S"), hold, it.get("game_proc"), it.get("ths")), flush=True)
            elif t == "log":
                msg = it.get("msg", "")
                if "游戏模式" in msg or "暂停" in msg or "启动:" in msg or "已启动" in msg:
                    print("[%s] LOG: %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
    except Exception as e:
        print("[%s] poll err: %s" % (time.strftime("%H:%M:%S"), e), flush=True)
    time.sleep(2)

print("--- monitor end ---", flush=True)
