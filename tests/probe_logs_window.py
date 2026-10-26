# -*- coding: utf-8 -*-
"""查容器日志 01:25-01:50 的 wallet/cmd 相关行，定位截图时段的空窗原因。"""
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(__import__('os').environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=60):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    return out + (("\n[ERR] " + err) if err.strip() else "")


print(run("docker logs -t --since 2026-10-23T17:25:00 minrigs 2>&1 | "
          "grep -iE 'wallet|链上|cmd|Traceback|Error' | tail -40"))
cli.close()
