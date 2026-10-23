# -*- coding: utf-8 -*-
"""smoke4：① 内联 JS 调 api（不经 evaluate_js）② closing 拦截 ③ 单次 evaluate_js 读结果。"""
import json
import os
import threading
import time

import webview

OUT = os.path.join(os.environ.get("TEMP", "."), "wv_smoke4.json")
result = {"ping_called": False}
LOCK = threading.Lock()
OUTBOX = []          # Python → JS 轮询通道
VER = [0]

threading.Timer(25, lambda: (result.update({"hang": True}), flush(), os._exit(3))).start()


def flush():
    with LOCK:
        open(OUT, "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False))


class Api:
    def ping(self, x):
        result["ping_called"] = True
        return "pong:%s" % x

    def poll(self, since):
        with LOCK:
            items = OUTBOX[max(int(since or 0) - 0, 0):]
        return {"ver": VER[0], "items": items}


def emit(obj):
    with LOCK:
        OUTBOX.append(obj)
        VER[0] += 1


HTML = """<h1>boot</h1><script>
window.addEventListener('pywebviewready', function(){
  document.title = 'bridge-ready';
  window.pywebview.api.poll(0).then(function(r){
    document.title = 'poll:' + r.items.length;
  }).catch(function(e){ document.title = 'poll-err:' + e; });
});
</script>"""

api = Api()
win = webview.create_window("pywebview smoke4", html=HTML, js_api=api,
                            width=420, height=280)

emit({"t": "hello", "n": 1})


def on_loaded():
    def probe():
        result["loaded_fired"] = True
        flush()
        # 等内联脚本完成 bridge-ready + poll（pywebviewready 事件在 loaded 后触发）
        for _ in range(40):
            try:
                t = win.evaluate_js("document.title")
            except Exception as e:  # noqa: BLE001
                result["title_err"] = repr(e)
                break
            result["title"] = t
            flush()
            if t and t.startswith("poll"):
                break
            time.sleep(0.3)
        # closing 拦截：挂 handler 后发 WM_CLOSE
        def cancel_close():
            result["closing_cancelled"] = True
            flush()
            return True
        try:
            win.events.closing += cancel_close
            result["closing_hook"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["closing_hook"] = repr(e)
            flush()
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "pywebview smoke4")
            ctypes.windll.user32.PostMessageW(int(hwnd), 0x0010, 0, 0)
            result["wm_close"] = "sent"
        except Exception as e:  # noqa: BLE001
            result["wm_close"] = repr(e)
        flush()
        time.sleep(2.5)   # 若拦截成功：webview.start 仍在跑，loop_ended 不会出现
        result["still_running"] = True
        flush()
        win.destroy()
    threading.Thread(target=probe, daemon=True).start()


win.events.loaded += on_loaded
webview.start(gui="edgechromium")
result["loop_ended"] = True
flush()
os._exit(0)
