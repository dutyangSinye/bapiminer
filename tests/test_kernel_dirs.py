# -*- coding: utf-8 -*-
"""验证「每内核一个子目录」改造：
  1) kernel_dir_for 对各内核返回不同子目录
  2) build_cmd 指向子目录里的 exe
  3) extract_kernel_zip 解压到子目录，不污染根目录
  4) 迁移函数把旧平铺布局归位
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(HERE), "client", "miner_client.py")

spec = importlib.util.spec_from_file_location("mc", CLIENT)
mc = importlib.util.module_from_spec(spec)
sys.modules["mc"] = mc
spec.loader.exec_module(mc)

print("=== 1) kernel_dir_for ===")
root = os.path.join(tempfile.gettempdir(), "kdir_test")
for key in mc.KERNELS:
    print("  %-16s -> %s" % (key, mc.kernel_dir_for(key, root)))
subs = {mc.kernel_dir_for(k, root) for k in mc.KERNELS}
print("  子目录互不相同:", len(subs) == len(mc.KERNELS))

print("\n=== 2) 迁移：旧平铺布局 ===")
legacy = os.path.join(tempfile.gettempdir(), "kdir_legacy")
if os.path.isdir(legacy):
    shutil.rmtree(legacy)
os.makedirs(legacy)
# 模拟旧布局：三个内核的文件全平铺
for f in ["wildrig.exe", "help.txt", "license.txt", "print-devices.bat",
          "start-BitCore-zergpool.com.bat", "start-Nexa.bat",
          "SRBMiner-MULTI.exe", "WinRing0x64.sys", "list-algorithms.bat",
          "start-mining-c64.bat",
          "bzminer.exe", "bzminer.worker.log", "env-vars.txt",
          "ReadMe.txt", "config.txt"]:
    with open(os.path.join(legacy, f), "w") as fh:
        fh.write("x")
# 覆盖 _default_kernel_dir 让迁移指向 legacy
mc._default_kernel_dir = lambda: legacy
mc.log_write = lambda *a, **k: None
mc._migrate_kernel_layout()

for key in mc.KERNELS:
    sub = mc.kernel_dir_for(key, legacy)
    got = sorted(os.listdir(sub)) if os.path.isdir(sub) else []
    print("  %-16s %s" % (key, got))
root_left = sorted(os.listdir(legacy))
print("  根目录剩余:", root_left)

print("\n=== 3) extract_kernel_zip 解压到子目录 ===")
zp = os.path.join(tempfile.gettempdir(), "fake_wildrig.zip")
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("wildrig-multi-windows/wildrig.exe", "EXE")
    z.writestr("wildrig-multi-windows/help.txt", "H")
    z.writestr("wildrig-multi-windows/start-BitCore-x.bat", "B")
dest = mc.kernel_dir_for("WildRig", legacy)
err = mc.extract_kernel_zip(zp, legacy, "wildrig.exe", dest_dir=dest)
print("  err:", err)
print("  WildRig 子目录:", sorted(os.listdir(dest)))
print("  根目录未被污染:", not os.path.isfile(os.path.join(legacy, "wildrig.exe")))

print("\n=== 4) build_cmd 指向子目录 ===")
mc.KERNELS["WildRig"]["api_port"] = 17799
try:
    exe, args = mc.build_cmd("WildRig", legacy, "PRL (Pearl)",
                              "stratum+tcp://x.example.com:1234", "WALLET", "rig1")
    print("  exe:", exe)
    print("  in subdir:", os.path.dirname(exe) == dest)
except Exception as e:
    print("  (expected: need pool url valid) ->", e)

shutil.rmtree(legacy, ignore_errors=True)
shutil.rmtree(root, ignore_errors=True)
try:
    os.remove(zp)
except OSError:
    pass
print("\nDONE")
