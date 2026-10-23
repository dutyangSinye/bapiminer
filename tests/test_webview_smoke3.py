# -*- coding: utf-8 -*-
"""聚焦测试：js_api 桥的可用形态（void 前缀避免 evaluate_js 等 Promise）。"""
import json
import os
import threading
import time

import webview

OUT = os.path.join(os.environ.get("TEMP", "."), "wv_smoke3.json")
result = {}
threading.Timer(20, lambda: (result.update({"hang": True}),
                             open(OUT, "w").write(json.dumps(result)), os._exit(3))).start()


class Api:
    def ping(self, x):
        result["api_called"] = True
        return "pong:%s" % x


def flush():
    open(OUT, "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False))


api = Api()
win = webview.create_window("pywebview smoke3", html="<h1>boot</h1>", js_api=api,
                            width=420, height=280)


def on_loaded():
    def probe():
        result["loaded_fired"] = True
        flush()
        time.sleep(1.0)   # 关键差异：smoke2 有 0.6s 延时成功，立即 eval 会死锁
        for label, expr in [
            ("typeof_pywebview", "String(typeof window.pywebview)"),
            ("typeof_api", "String(typeof (window.pywebview && window.pywebview.api))"),
        ]:
            try:
                result[label] = win.evaluate_js(expr)
            except Exception as e:  # noqa: BLE001
                result[label] = "ERR " + repr(e)
            flush()
        try:
            # void 前缀：表达式完成值为 undefined，不让 evaluate_js 等 Promise
            win.evaluate_js(
                "void window.pywebview.api.ping('hi').then("
                "function(r){document.title='ret:'+r;})")
            result["dispatch"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["dispatch"] = "ERR " + repr(e)
        flush()
        time.sleep(1.0)
        try:
            result["title"] = win.evaluate_js("document.title")
        except Exception as e:  # noqa: BLE001
            result["title"] = "ERR " + repr(e)
        result["api_called_flag"] = result.get("api_called", False)
        flush()
        win.destroy()
    threading.Thread(target=probe, daemon=True).start()


win.events.loaded += on_loaded
webview.start(gui="edgechromium")
result["loop_ended"] = True
flush()
os._exit(0)
