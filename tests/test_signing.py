# -*- coding: utf-8 -*-
"""升级签名链路测试：Ed25519 向量 / 清单验签 / 降级拦截 / 内核包校验 / 服务端端点。

跑法（任意 Python 3.9+，零依赖）：
    python tests/test_signing.py            # 或 python -m unittest tests.test_signing

覆盖的核心承诺（也就是「不扒皮」可验证的部分）：
  1. 工具与客户端两份 Ed25519 实现都对 RFC 8032 向量逐字节吻合；
  2. 清单任意字段被改 -> 验签失败 -> 不升级、不装内核（fail closed）；
  3. 签名版本不高于本机 -> 拒绝（防重放旧清单把矿机降级到有问题的版本）；
  4. 内核 zip 的哈希必须与清单一致，否则丢弃；
  5. 中控 /api/signed_manifest 原样转发、/api/health 能查到版本与 commit。
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "client", "server"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import ed25519                    # noqa: E402
import sign_manifest as sm        # noqa: E402
import miner_client as mc         # noqa: E402
import server as srv              # noqa: E402

# 导入时先记下内置公钥：后面的用例会临时把它换成测试密钥，
# 「内置公钥是否与本地私钥一致」这条只能拿这份原始值比。
EMBEDDED_PUBKEY = mc.PUBLISHER_PUBKEY

# RFC 8032 §7.1 的 3 组向量：(私钥, 公钥, 消息, 签名)
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- Ed25519


class TestEd25519(unittest.TestCase):
    def test_rfc8032_vectors_tools_impl(self):
        for sk_hex, pk_hex, msg_hex, sig_hex in RFC_VECTORS:
            seed, message = bytes.fromhex(sk_hex), bytes.fromhex(msg_hex)
            self.assertEqual(ed25519.public_key_hex(seed), pk_hex)
            self.assertEqual(ed25519.sign_hex(message, seed), sig_hex)
            self.assertTrue(ed25519.verify_hex(message, sig_hex, pk_hex))

    def test_rfc8032_vectors_client_impl(self):
        """客户端内嵌的验签实现必须与工具实现同结果（防两边漂移）。"""
        for sk_hex, pk_hex, msg_hex, sig_hex in RFC_VECTORS:
            message = bytes.fromhex(msg_hex)
            self.assertTrue(mc.ed25519_verify(message, bytes.fromhex(sig_hex),
                                              bytes.fromhex(pk_hex)))
            # 改一个字节的消息必须失败
            self.assertFalse(mc.ed25519_verify(message + b"!", bytes.fromhex(sig_hex),
                                               bytes.fromhex(pk_hex)))

    def test_client_cross_checks_tool_signature(self):
        seed = ed25519.new_seed()
        pk = ed25519.public_key(seed)
        message = "内核 v1.0 已签名".encode("utf-8")
        sig = ed25519.sign(message, seed)
        self.assertTrue(mc.ed25519_verify(message, sig, pk))

    def test_rejects_malformed_inputs(self):
        seed = ed25519.new_seed()
        pk = ed25519.public_key(seed)
        sig = ed25519.sign(b"x", seed)
        self.assertFalse(mc.ed25519_verify(b"x", sig[:-1], pk))          # 签名截断
        self.assertFalse(mc.ed25519_verify(b"x", sig, pk[:-1]))          # 公钥截断
        bad_sig = bytearray(sig)
        bad_sig[0] ^= 0xFF
        self.assertFalse(mc.ed25519_verify(b"x", bytes(bad_sig), pk))    # 篡改 R
        bad_pk = bytearray(pk)
        bad_pk[0] ^= 0xFF
        self.assertFalse(mc.ed25519_verify(b"x", sig, bytes(bad_pk)))     # 非曲线点


# ---------------------------------------------------------------- 清单


class ManifestCase(unittest.TestCase):
    """公共夹具：临时密钥对 + 把客户端内置公钥替换成它。"""

    def setUp(self):
        self.seed = ed25519.new_seed()
        self.pubkey = ed25519.public_key_hex(self.seed)
        self._old_pubkey = mc.PUBLISHER_PUBKEY
        mc.PUBLISHER_PUBKEY = self.pubkey

    def tearDown(self):
        mc.PUBLISHER_PUBKEY = self._old_pubkey

    def sign(self, manifest):
        manifest = dict(manifest)
        manifest.pop("sig", None)
        manifest["sig"] = ed25519.sign_hex(sm.canonical(manifest), self.seed)
        return manifest

    def client_manifest(self, version="1.13.0.1", sha=None, kernels=None):
        return self.sign(sm.build_manifest(
            {"version": version, "file": "BaPiMiner.exe", "size": 3,
             "sha256": sha or sha256_bytes(b"new")},
            kernels or {},
            notes="测试"))


class TestManifest(ManifestCase):
    def test_canonical_form_identical_between_tool_and_client(self):
        m = sm.build_manifest(
            {"version": "1.13.0.1", "file": "a.exe", "size": 1, "sha256": "0" * 64},
            {"srbminer": {"version": "v1", "file": "a.zip", "size": 2,
                          "sha256": "1" * 64}},
            notes="中文备注 / ascii")
        self.assertEqual(sm.canonical(m), mc._manifest_canonical(m))

    def test_valid_manifest_passes(self):
        manifest = self.client_manifest()
        parsed, err = mc.parse_signed_manifest(json.dumps(manifest).encode())
        self.assertIsNotNone(parsed, err)
        self.assertEqual(parsed["client"]["version"], "1.13.0.1")

    def test_bytes_and_bom_tolerated(self):
        manifest = self.client_manifest()
        raw = b"\xef\xbb\xbf" + json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        parsed, err = mc.parse_signed_manifest(raw)
        self.assertIsNotNone(parsed, err)

    def test_every_tampered_field_is_rejected(self):
        base = self.client_manifest()
        mutations = {
            "client.sha256": lambda m: m["client"].__setitem__("sha256", "f" * 64),
            "client.version": lambda m: m["client"].__setitem__("version", "9.9.9.9"),
            "client.size": lambda m: m["client"].__setitem__("size", 123456),
            "issued_at": lambda m: m.__setitem__("issued_at", m["issued_at"] + 1),
            "notes": lambda m: m.__setitem__("notes", "被改过的说明"),
            "extra_kernel": lambda m: m["kernels"].__setitem__(
                "bzminer", {"version": "v0", "file": "x.zip", "size": 1,
                            "sha256": "a" * 64}),
            "drop_sig": lambda m: m.pop("sig"),
        }
        for name, mutate in mutations.items():
            manifest = json.loads(json.dumps(base))
            mutate(manifest)
            parsed, _err = mc.parse_signed_manifest(json.dumps(manifest).encode())
            self.assertIsNone(parsed, "篡改 %s 之后仍然验签通过" % name)

    def test_wrong_pubkey_rejected(self):
        other = ed25519.new_seed()
        mc.PUBLISHER_PUBKEY = ed25519.public_key_hex(other)
        manifest = self.client_manifest()
        parsed, err = mc.parse_signed_manifest(json.dumps(manifest).encode())
        self.assertIsNone(parsed)
        self.assertIn("验签失败", err)

    def test_bad_schema_and_shape_rejected(self):
        for bad in ({"schema": 2, "client": {}},
                    {"schema": 1},
                    {"schema": 1, "client": {}, "sig": "zzzz"},
                    "not-a-dict",
                    b"{not json"):
            parsed, _err = mc.parse_signed_manifest(
                bad if isinstance(bad, bytes) else json.dumps(bad).encode())
            self.assertIsNone(parsed)

    def test_shipped_pubkey_matches_key_file(self):
        """如果本机存着发布私钥，内置公钥必须就是它（忘同步会立刻被抓到）。"""
        key_path = os.path.join(ROOT, "keys", "publisher_ed25519.key")
        if not os.path.isfile(key_path):
            self.skipTest("没有本地发布私钥（正常：私钥不该出现在工作区）")
        with open(key_path, encoding="utf-8") as f:
            seed = bytes.fromhex(f.read().strip())
        self.assertEqual(ed25519.public_key_hex(seed), EMBEDDED_PUBKEY)


# ---------------------------------------------------------------- 版本比较 / 升级决策


class TestVersionCompare(unittest.TestCase):
    def test_ordering(self):
        self.assertTrue(mc._ver_gt("1.13.0.1", "1.13.0.0"))
        self.assertTrue(mc._ver_gt("1.14", "1.13.9.9"))
        self.assertTrue(mc._ver_gt("2.0.0.0", "1.99.99.99"))
        self.assertFalse(mc._ver_gt("1.13.0.0", "1.13.0.0"))
        self.assertFalse(mc._ver_gt("1.12.9.9", "1.13.0.0"))
        self.assertFalse(mc._ver_gt("", "0.0.0.1"))

    def test_non_numeric_segments(self):
        # 'v1.13.0.1-beta' 按点切段，最后一段只取数字 -> 1
        self.assertEqual(mc._ver_tuple("v1.13.0.1-beta"), (1, 13, 0, 1))


class TestClientUpdateDecision(ManifestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.exe = os.path.join(self.tmp, "BaPiMiner.exe")
        with open(self.exe, "wb") as f:
            f.write(b"old-build")
        self._old_exe = mc.EXE_PATH
        self._old_fetch = mc.fetch_signed_manifest
        mc.EXE_PATH = self.exe

    def tearDown(self):
        mc.EXE_PATH = self._old_exe
        mc.fetch_signed_manifest = self._old_fetch
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use(self, manifest):
        mc.fetch_signed_manifest = lambda server, timeout=12: (manifest, "")

    def test_newer_signed_version_triggers_update(self):
        self.use(self.client_manifest(version="9.9.9.9", sha="a" * 64))
        need, _local, latest = mc.client_update_status("http://unused")
        self.assertTrue(need)
        self.assertTrue(latest["signed"])
        self.assertEqual(latest["version"], "9.9.9.9")

    def test_same_version_does_not_reinstall(self):
        self.use(self.client_manifest(version=mc.CLIENT_VERSION, sha="a" * 64))
        need, _local, _latest = mc.client_update_status("http://unused")
        self.assertFalse(need)

    def test_older_version_refused_as_downgrade(self):
        self.use(self.client_manifest(version="1.0.0.0", sha="a" * 64))
        need, _local, _latest = mc.client_update_status("http://unused")
        self.assertFalse(need, "旧签名版本不应该把矿机降级")

    def test_manifest_failure_returns_reason_not_silent(self):
        mc.fetch_signed_manifest = lambda server, timeout=12: (None, "清单验签失败")
        need, _local, latest = mc.client_update_status("http://unused")
        self.assertFalse(need)
        self.assertFalse(latest["ok"])
        self.assertIn("验签失败", latest["error"])

    def test_apply_refuses_unsigned_payload(self):
        old_lock = mc._UPDATE_LOCK_FILE
        mc._UPDATE_LOCK_FILE = os.path.join(self.tmp, ".update.lock")
        try:
            ok, note = mc.apply_client_update(
                "http://unused", {"sha256": "a" * 64, "version": "9.9.9.9"})
            self.assertFalse(ok)
            self.assertIn("未经签名", note)
        finally:
            mc._UPDATE_LOCK_FILE = old_lock


# ---------------------------------------------------------------- 内核安装（真 HTTP）


class _StubServer:
    """极简 HTTP 服务：按路径返回固定字节，用来验证客户端下载后的校验行为。"""

    def __init__(self, routes):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                data = outer.routes.get(path)
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):   # 静音
                pass

        self.routes = routes
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestKernelInstall(ManifestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.kernel_dir = os.path.join(self.tmp, "kernels")
        self.zip_path = os.path.join(self.tmp, "srbminer-v9.9.zip")
        with zipfile.ZipFile(self.zip_path, "w") as z:
            z.writestr("SRBMiner-MULTI.exe", b"MZ fake kernel bytes")
        with open(self.zip_path, "rb") as f:
            self.zip_bytes = f.read()
        self.zip_sha = sha256_bytes(self.zip_bytes)
        self.stub = None

    def tearDown(self):
        if self.stub:
            self.stub.close()
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def serve(self, kernels, zip_bytes=None):
        manifest = self.client_manifest(kernels=kernels)
        self.stub = _StubServer({
            "/api/signed_manifest": json.dumps(manifest).encode("utf-8"),
            "/api/kernel_dl": zip_bytes if zip_bytes is not None else self.zip_bytes,
        })
        return self.stub.url

    def kernel_entry(self, sha=None, version="v9.9"):
        return {"version": version, "file": "srbminer-v9.9.zip",
                "size": len(self.zip_bytes), "sha256": sha or self.zip_sha}

    def test_install_with_valid_signature(self):
        url = self.serve({"srbminer": self.kernel_entry()})
        err, ver = mc.ensure_kernel("SRBMiner-MULTI", self.kernel_dir, url, "rig-token",
                                    rig_name="rig-1")
        self.assertIsNone(err)
        self.assertEqual(ver, "v9.9")
        exe = os.path.join(self.kernel_dir, "SRBMiner-MULTI", "SRBMiner-MULTI.exe")
        self.assertTrue(os.path.isfile(exe), "内核应已解压到位")
        # 版本号以签名清单为准，落盘可查
        self.assertEqual(mc.kernel_local_version("SRBMiner-MULTI", self.kernel_dir), "v9.9")

    def test_hash_mismatch_is_discarded(self):
        url = self.serve({"srbminer": self.kernel_entry(sha="0" * 64)})
        err, _ver = mc.ensure_kernel("SRBMiner-MULTI", self.kernel_dir, url, "rig-token")
        self.assertIsNotNone(err)
        self.assertIn("不一致", err)
        exe = os.path.join(self.kernel_dir, "SRBMiner-MULTI", "SRBMiner-MULTI.exe")
        self.assertFalse(os.path.isfile(exe), "校验失败绝不能留下可执行文件")
        leftovers = [f for f in os.listdir(os.path.join(self.kernel_dir, "SRBMiner-MULTI"))
                     if f.endswith(".zip")]
        self.assertEqual(leftovers, [], "校验失败应删掉下载的 zip")

    def test_unsigned_kernel_refused(self):
        url = self.serve({"bzminer": dict(self.kernel_entry(), file="bzminer.zip")})
        err, _ver = mc.ensure_kernel("SRBMiner-MULTI", self.kernel_dir, url, "rig-token")
        self.assertIsNotNone(err)
        self.assertIn("不在签名清单", err)

    def test_missing_manifest_refused(self):
        self.stub = _StubServer({})            # 一切 404
        err, _ver = mc.ensure_kernel("SRBMiner-MULTI", self.kernel_dir,
                                     self.stub.url, "rig-token")
        self.assertIsNotNone(err)
        self.assertIn("无法取得升级清单", err)

    def test_tampered_manifest_blocks_install(self):
        manifest = json.loads(json.dumps(
            self.client_manifest(kernels={"srbminer": self.kernel_entry()})))
        manifest["kernels"]["srbminer"]["version"] = "v0.0.1"    # 签名后篡改
        self.stub = _StubServer({
            "/api/signed_manifest": json.dumps(manifest).encode("utf-8"),
            "/api/kernel_dl": self.zip_bytes,
        })
        err, _ver = mc.ensure_kernel("SRBMiner-MULTI", self.kernel_dir,
                                     self.stub.url, "rig-token")
        self.assertIsNotNone(err)
        self.assertIn("验签失败", err)


# ---------------------------------------------------------------- 服务端端点


class TestServerEndpoints(ManifestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self._saved = (srv.DATA_DIR, srv.DB_FILE, srv.SIGNED_MANIFEST)
        srv.DATA_DIR = self.tmp
        srv.DB_FILE = os.path.join(self.tmp, "fleet.db")
        srv.SIGNED_MANIFEST = os.path.join(self.tmp, "signed_manifest.json")
        srv._conn_local = threading.local()      # 别复用上一个用例的 sqlite 连接
        srv.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        srv.DATA_DIR, srv.DB_FILE, srv.SIGNED_MANIFEST = self._saved
        srv._conn_local = threading.local()
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path),
                                    timeout=5) as r:
            return r.status, r.read()

    def test_manifest_absent_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/signed_manifest")
        self.assertEqual(cm.exception.code, 404)

    def test_manifest_served_byte_exact(self):
        manifest = self.client_manifest()
        raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        with open(srv.SIGNED_MANIFEST, "wb") as f:
            f.write(raw)
        status, body = self.get("/api/signed_manifest")
        self.assertEqual(status, 200)
        self.assertEqual(body, raw, "必须原样转发，否则签名会对不上")
        # 客户端拿到服务端给的字节也能验签通过
        parsed, err = mc.parse_signed_manifest(body)
        self.assertIsNotNone(parsed, err)

    def test_health_reports_version_commit_and_manifest(self):
        manifest = self.client_manifest(version="1.13.0.1")
        with open(srv.SIGNED_MANIFEST, "wb") as f:
            f.write(json.dumps(manifest).encode("utf-8"))
        with open(os.path.join(self.tmp, "build_info.json"), "w", encoding="utf-8") as f:
            json.dump({"commit": "abc1234", "built_at": 111}, f)

        _status, body = self.get("/api/health")
        d = json.loads(body)
        self.assertTrue(d["ok"])
        self.assertEqual(d["server_version"], srv.SERVER_VERSION)
        self.assertEqual(d["build_commit"], "abc1234")
        self.assertTrue(d["signed_manifest"]["present"])
        self.assertEqual(d["signed_manifest"]["client_version"], "1.13.0.1")
        self.assertEqual(d["signed_manifest"]["kernels"], [])

    def test_health_without_manifest(self):
        _status, body = self.get("/api/health")
        d = json.loads(body)
        self.assertTrue(d["ok"])
        self.assertFalse(d["signed_manifest"]["present"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
