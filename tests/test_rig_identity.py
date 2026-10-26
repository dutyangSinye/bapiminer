"""复制安装 / 重装后的身份自愈回归测试。

覆盖：
1. machine_fingerprint 稳定且格式正确
2. ensure_machine_identity：新配置不重置；来自别的机器则重置成本机身份
3. save_config 原子落盘可回读
4. _heal_identity：第一次只换接入码，第二次才给名字加后缀
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "client"))
import miner_client as mc  # noqa: E402


class FingerprintTest(unittest.TestCase):
    def test_stable_and_hex(self):
        a = mc.machine_fingerprint()
        b = mc.machine_fingerprint()
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)
        int(a, 16)


class EnsureIdentityTest(unittest.TestCase):
    def test_fresh_config_keeps_identity(self):
        cfg = {"token": "rig-abc", "rig_name": "mine-1"}
        self.assertFalse(mc.ensure_machine_identity(cfg))
        self.assertEqual(cfg["token"], "rig-abc")
        self.assertEqual(cfg["rig_name"], "mine-1")
        self.assertEqual(cfg["machine_id"], mc.machine_fingerprint())

    def test_copied_config_is_reset(self):
        cfg = {"token": "rig-someone-else", "rig_name": "other-pc",
               "display_name": "别人的机器", "rig_bound": True, "user_id": 6,
               "username": "alice", "user_token": "t", "machine_id": "deadbeefdeadbeef"}
        self.assertTrue(mc.ensure_machine_identity(cfg))
        self.assertEqual(cfg["token"], "")             # 交给启动流程重新生成
        self.assertEqual(cfg["rig_name"], mc.socket.gethostname().lower()[:20])
        self.assertEqual(cfg["display_name"], "")
        self.assertFalse(cfg["rig_bound"])
        self.assertEqual(cfg["user_id"], 0)
        self.assertEqual(cfg["machine_id"], mc.machine_fingerprint())


class SaveConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = mc.CONFIG_FILE
        mc.CONFIG_FILE = os.path.join(self.tmp, "client_config.json")

    def tearDown(self):
        mc.CONFIG_FILE = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        cfg = {"token": "rig-x", "rig_name": "pc-1", "机器": "中文"}
        self.assertTrue(mc.save_config(cfg))
        with open(mc.CONFIG_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f), cfg)
        self.assertFalse(os.path.exists(mc.CONFIG_FILE + ".tmp"))


class HealIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = mc.CONFIG_FILE
        mc.CONFIG_FILE = os.path.join(self.tmp, "client_config.json")

    def tearDown(self):
        mc.CONFIG_FILE = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _engine(self, cfg):
        eng = mc.MiningEngine(cfg, queue.Queue())
        eng.cfg = dict(cfg)
        return eng

    def test_first_heal_keeps_name_second_adds_suffix(self):
        eng = self._engine({"token": "rig-old", "rig_name": "pc-9",
                            "display_name": "pc-9"})
        eng._heal_identity(gap=0)
        self.assertEqual(eng.cfg["rig_name"], "pc-9")          # 第一次不改名
        self.assertTrue(eng.cfg["token"].startswith("rig-"))
        self.assertNotEqual(eng.cfg["token"], "rig-old")
        first = eng.cfg["token"]

        eng._heal_identity(gap=0)
        self.assertTrue(eng.cfg["rig_name"].startswith("pc-9-"))  # 第二次加后缀
        self.assertLessEqual(len(eng.cfg["rig_name"]), 20)
        self.assertNotEqual(eng.cfg["token"], first)

    def test_rate_limited(self):
        eng = self._engine({"token": "rig-old", "rig_name": "pc-9"})
        self.assertTrue(eng._heal_identity(gap=90))
        self.assertFalse(eng._heal_identity(gap=90))   # 冷却期内不重复折腾

    def test_on_identity_callback(self):
        eng = self._engine({"token": "rig-old", "rig_name": "pc-9"})
        seen = {}
        eng.on_identity = lambda name, tok: seen.update(name=name, token=tok)
        eng._heal_identity(gap=0)
        self.assertEqual(seen["name"], "pc-9")
        self.assertTrue(seen["token"].startswith("rig-"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
