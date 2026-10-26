"""A/B 对比：不同进程启动参数下 SRBMiner 能否稳定运行。

对照三组：
  A 原始：CREATE_NO_WINDOW，无 startupinfo
  B 现在：CREATE_NO_WINDOW + STARTF_USESHOWWINDOW/SW_HIDE
  C 裸跑：不加任何 flags（理论上会弹控制台窗口）
"""
import os
import subprocess
import time

EXE = r"D:\mine\kernels\SRBMiner-MULTI.exe"
ARGS = ["--algorithm", "pearlhash",
        "--pool", "stratum+tcp://prl-hk.kryptex.network:7048",
        "--wallet", os.environ.get("MINRIGS_TEST_WALLET", ""),
        "--api-enable", "--api-port", "21550", "--disable-cpu"]
CWD = os.path.dirname(EXE)

CNW = 0x08000000


def si():
    s = subprocess.STARTUPINFO()
    s.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    s.wShowWindow = 0
    return s


def trial(name, kwargs, seconds=12):
    try:
        p = subprocess.Popen([EXE] + ARGS, cwd=CWD, **kwargs)
    except Exception as e:  # noqa: BLE001
        print("%-6s 启动失败: %s: %s" % (name, type(e).__name__, e))
        return
    t0 = time.time()
    while time.time() - t0 < seconds:
        if p.poll() is not None:
            break
        time.sleep(0.5)
    rc = p.poll()
    if rc is None:
        print("%-6s 运行 %ds 仍存活  ✓" % (name, seconds))
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:  # noqa: BLE001
            p.kill()
    else:
        print("%-6s 提前退出 rc=%s (%s)  ✗" % (name, rc, hex(rc & 0xFFFFFFFF)))
    time.sleep(3)


if __name__ == "__main__":
    trial("A", dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=CNW))
    trial("B", dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=si(), creationflags=CNW))
    trial("C", dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
