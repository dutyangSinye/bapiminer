# -*- coding: utf-8 -*-
"""把 minrigs/server 代码上传到新服务器（paramiko SFTP）

用法：设置环境变量 SSH_HOST/SSH_PORT/SSH_USER/SSH_PASS 后直接运行。
只传代码与配置，不传 data（data 走 NAS rsync / HTTP 拉取）。
"""
import os
import sys
import posixpath

import paramiko

LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server"
REMOTE = "/opt/minrigs"

FILES = [
    ("server.py", "/opt/minrigs/server.py"),
    ("config.json", "/opt/minrigs/config.json"),
    (r"static\index.html", "/opt/minrigs/static/index.html"),
    (r"static\bapiminer.svg", "/opt/minrigs/static/bapiminer.svg"),
]

DOCKERFILE = """FROM docker.m.daocloud.io/library/python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
COPY server.py .
COPY static/ ./static/
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8099/api/downloads',timeout=3)"
CMD ["python", "-u", "server.py"]
"""


def sftp_connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(os.environ["SSH_HOST"], port=int(os.environ.get("SSH_PORT", "22")),
                username=os.environ["SSH_USER"], password=os.environ["SSH_PASS"],
                timeout=25, banner_timeout=30, auth_timeout=30,
                look_for_keys=False, allow_agent=False)
    return cli, cli.open_sftp()


def ensure_dir(sftp, path):
    parts = path.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def main():
    cli, sftp = sftp_connect()
    for d in (REMOTE, REMOTE + "/static", REMOTE + "/data",
              REMOTE + "/data/kernels", REMOTE + "/data/downloads"):
        ensure_dir(sftp, d)

    for src_rel, dst in FILES:
        src = os.path.join(LOCAL, src_rel)
        ensure_dir(sftp, posixpath.dirname(dst))
        sftp.put(src, dst)
        print("PUT %-28s -> %s (%d bytes)" % (src_rel, dst, os.path.getsize(src)))

    with sftp.open(REMOTE + "/Dockerfile", "w") as f:
        f.write(DOCKERFILE)
    print("PUT Dockerfile")

    sftp.close()
    cli.close()
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
