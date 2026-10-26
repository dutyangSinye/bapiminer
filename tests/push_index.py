# -*- coding: utf-8 -*-
"""快速上传 index.html 到生产中控（静态文件，无需重启容器）。"""
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(__import__('os').environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()
sftp.put(r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server\static\index.html",
         "/opt/minrigs/static/index.html")
print("PUT index.html -> %d bytes" % sftp.stat("/opt/minrigs/static/index.html").st_size)
sftp.close()
cli.close()
