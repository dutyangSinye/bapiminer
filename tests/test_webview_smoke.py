# -*- coding: utf-8 -*-
"""pywebview + EdgeChromium 冒烟测试 v2：结果落盘 + 内置看门狗，绝不挂死。"""
import json
import os
import threading
import time

import webview

OUT = os.path.join(os.environ.get("TEMP", "."), "wv_smoke_result.json")
result = {"api_called": False, "push_ok": False, "closing_cancelled": None}

threading.Timer(25, lambda: (result.update({"hang": True}),
                             open(OUT, "w").write(json.dumps(result)),
                             os._exit(3))).start()


class Api:
    def ping(self, x):
        result["api_called"] = True
        return "pong:%s" % x


def flush():
    open(OUT, "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False))


api = Api()
win = webview.create_window("pywebview smoke", html="<h1 id=h>boot</h1>", js_api=api,
                            width=480, height=320)


def on_loaded():
    def probe():
        time.sleep(0.6)
        try:
            win.evaluate_js("document.getElementById('h').textContent='pushed'")
            result["push_ok"] = True
        except Exception as e:  # noqa: BLE001
            result["push_err"] = repr(e)
        # api 调用放在 JS 侧异步拿结果写 title，避免 evaluate_js 等 Promise 挂死
        try:
            win.evaluate_js(
                "window.pywebview.api.ping('hi').then(function(r){document.title='ret:'+r;})")
            result["api_dispatch"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["api_err"] = repr(e)
        time.sleep(0.8)
        try:
            result["title"] = win.evaluate_js("document.title")
        except Exception as e:  # noqa: BLE001
            result["title_err"] = repr(e)
        # 挂 closing 取消 handler，发 WM_CLOSE 看能否拦截
        def cancel_close():
            result["closing_cancelled"] = True
            return True
        try:
            win.events.closing += cancel_close
            result["closing_hook"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["closing_hook"] = repr(e)
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "pywebview smoke")
            ctypes.windll.user32.PostMessageW(int(hwnd), 0x0010, 0, 0)  # WM_CLOSE
            result["wm_close"] = "sent"
        except Exception as e:  # noqa: BLE001
            result["wm_close"] = repr(e)
        time.sleep(1.2)
        try:
            win.evaluate_js("1")
            result["alive_after_close"] = True   # 关闭被取消 → 窗口仍活
        except Exception:
            result["alive_after_close"] = False
        flush()
        win.destroy()   # 确定出口

    threading.Thread(target=probe, daemon=True).start()


win.events.loaded += on_loaded
webview.start(gui="edgechromium")
result["loop_ended"] = True
flush()
os._exit(0)
