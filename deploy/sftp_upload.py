# -*- coding: utf-8 -*-
"""SFTP 上传 minrigs 部署包到 NAS。凭据走环境变量。"""
import os
import sys
import paramiko

HOST = os.environ["SSH_HOST"]
PORT = int(os.environ.get("SSH_PORT", "22"))
USER = os.environ["SSH_USER"]
PW = os.environ["SSH_PASS"]
LOCAL = sys.argv[1]
REMOTE = sys.argv[2]

t = paramiko.Transport((HOST, PORT))
t.connect(username=USER, password=PW)
sftp = paramiko.SFTPClient.from_transport(t)

def mkdirs(path):
    parts = path.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            try:
                sftp.mkdir(cur)
            except PermissionError:
                print("SKIP mkdir (will sudo later):", cur)
        except PermissionError:
            print("stat denied, continue:", cur)

def put_dir(local, remote):
    mkdirs(remote)
    for name in os.listdir(local):
        lp = os.path.join(local, name)
        rp = remote + "/" + name
        if os.path.isdir(lp):
            put_dir(lp, rp)
        else:
            try:
                sftp.put(lp, rp)
            except PermissionError:
                print("PUT DENIED:", rp)
                raise
            print("put", rp, os.path.getsize(lp), "bytes")

put_dir(LOCAL, REMOTE)
sftp.close()
t.close()
print("UPLOAD OK")
