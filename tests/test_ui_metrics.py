# -*- coding: utf-8 -*-
"""矿机端首屏快照测试：metrics 格式化 + boot 接线。

对应真实 bug：点 X 关窗后从托盘重开，新页面的 poll 游标从「当前」开始，
拿不到历史推送；而引擎 stats 是每 10 秒才 emit 一次 —— 于是算力/份额/温度
会空白最长 10 秒才冒出来。

修法：把 metrics 抽成纯函数 metrics_from_stats()，塞进 /api/boot 快照，
前端 applyBoot 立刻 apply 一次。这里守三件事：
  1. 格式化本身正确、脏数据不炸；
  2. /api/boot 的 payload 确实带了 metrics；
  3. 前端确实在 boot 时 apply 了它。

跑法：python tests/test_ui_metrics.py
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "client"))

import miner_client as mc  # noqa: E402

CLIENT_SRC = os.path.join(ROOT, "client", "miner_client.py")
APP_JS = os.path.join(ROOT, "client", "web", "app.js")


class TestMetricsFormat(unittest.TestCase):
    def test_empty_stats(self):
        m = mc.metrics_from_stats({})
        self.assertEqual(m["t"], "metrics")
        self.assertEqual(m["ths"], "0.0")
        self.assertEqual(m["acc"], "0 / 0")
        self.assertEqual(m["gpu"], "—")
        self.assertEqual(m["income"], "—")
        self.assertEqual(m["uptime"], "—")
        self.assertIn("未检测到", m["gpu_text"])

    def test_none_stats(self):
        # boot 时 engine.stats 理论上不会为 None，但别让异常把整个页面打空
        self.assertEqual(mc.metrics_from_stats(None)["ths"], "0.0")

    def test_full_stats(self):
        st = {
            "ths": 123.456, "accepted": 100, "rejected": 2, "uptime": 3661,
            "income": 12.7,
            "gpus": [
                {"model": "NVIDIA GeForce RTX 4070", "ths": 60.5,
                 "temp": 66, "fan": 70, "power": 180},
                {"model": "NVIDIA GeForce RTX 3080", "ths": 63.0,
                 "temp": 72, "fan": 65, "power": 220},
            ],
        }
        m = mc.metrics_from_stats(st)
        self.assertEqual(m["ths"], "123.5")
        self.assertEqual(m["acc"], "100 / 2")
        self.assertEqual(m["gpu"], "72°C / 70%")          # 取最高温 / 最高转速
        self.assertEqual(m["income"], "¥13")
        self.assertEqual(m["uptime"], "1:01:01")
        self.assertIn("RTX 4070", m["gpu_text"])
        self.assertNotIn("NVIDIA GeForce", m["gpu_text"])  # 前缀剥掉，省横向空间
        self.assertIn("60.5 TH/s", m["gpu_text"])

    def test_dirty_gpu_fields_do_not_crash(self):
        st = {"gpus": [{"model": None, "ths": None, "temp": None,
                        "fan": None, "power": None}]}
        m = mc.metrics_from_stats(st)
        self.assertEqual(m["gpu"], "0°C / 0%")
        self.assertIn("GPU", m["gpu_text"])

    def test_income_none_and_uptime_zero(self):
        m = mc.metrics_from_stats({"income": None, "uptime": 0})
        self.assertEqual(m["income"], "—")
        self.assertEqual(m["uptime"], "—")


class TestBootWiring(unittest.TestCase):
    """run_gui 是个巨型闭包，没法直接调 —— 这里守住「接线」不被重构掉。"""

    def test_boot_payload_includes_metrics(self):
        with open(CLIENT_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r'"metrics":\s*metrics_from_stats\(')

    def test_frontend_applies_metrics_on_boot(self):
        with open(APP_JS, encoding="utf-8") as f:
            js = f.read()
        self.assertRegex(js, r"if \(d\.metrics\)\s*apply\(d\.metrics\)")

    def test_stats_push_reuses_same_formatter(self):
        """实时推送与首屏快照必须是同一份格式化逻辑（否则会出现两套显示规则）。"""
        with open(CLIENT_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r'kind == "stats":\s*\n\s*push\(metrics_from_stats\(ev\)\)')


if __name__ == "__main__":
    unittest.main(verbosity=2)
