"""定位 SRBMiner 崩溃点：不同参数组合下的退出码。

0xC0000005 = ACCESS_VIOLATION（段错误）
"""
import os
import subprocess
import time

EXE = r"D:\mine\kernels\SRBMiner-MULTI.exe"
CWD = os.path.dirname(EXE)
POOL = "stratum+tcp://prl-hk.kryptex.network:7048"
WALLET = os.environ.get("MINRIGS_TEST_WALLET", "")

CASES = [
    ("--help", ["--help"]),
    ("--list-algorithms", ["--list-algorithms"]),
    ("--list-gpu-devices", ["--list-gpu-devices"]),
    ("--disable-gpu --help", ["--disable-gpu", "--help"]),
    ("--version", ["--version"]),
    ("mining(disable-cpu)", ["--algorithm", "pearlhash", "--pool", POOL,
                             "--wallet", WALLET, "--disable-cpu"]),
    ("mining(+api)", ["--algorithm", "pearlhash", "--pool", POOL, "--wallet", WALLET,
                      "--api-enable", "--api-port", "21550", "--disable-cpu"]),
]

for name, args in CASES:
    try:
        p = subprocess.Popen([EXE] + args, cwd=CWD,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:  # noqa: BLE001
        print("%-24s 启动异常 %s: %s" % (name, type(e).__name__, e))
        continue
    t0 = time.time()
    while time.time() - t0 < 10 and p.poll() is None:
        time.sleep(0.3)
    rc = p.poll()
    out = b""
    if rc is not None:
        try:
            out = p.stdout.read(400) or b""
        except Exception:  # noqa: BLE001
            pass
        print("%-24s rc=%-12s %s" % (name, rc, hex(rc & 0xFFFFFFFF)))
    else:
        print("%-24s 存活 → 正常" % name)
        p.kill()
        p.wait(timeout=5)
    txt = out.decode("utf-8", "replace").strip().replace("\r", "")
    if txt:
        print("     输出: %s" % txt[:200].replace("\n", " | "))
    time.sleep(2)
