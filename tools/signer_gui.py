#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扒皮矿工 · 内核签发工具（本地 GUI）

私钥只在本机使用，中控拿不到 —— 中控被攻破也无法伪造内核包。

使用：
  1. 把 keys/publisher_ed25519.key 放到本程序同目录的 keys 目录下
  2. 填中控地址 + 管理员 token（会记住）
  3. 点「检查待签发」→ 选中内核 →「签发并上传」

签名沿用线上清单里的 client 段（客户端本身没变），只替换内核条目，
所以本机不需要放 BaPiMiner.exe。

打包：pyinstaller --onefile --windowed --name BaPiSigner \
        --add-data "tools/sign_manifest.py;." --add-data "tools/ed25519.py;." tools/signer_gui.py
"""
import json
import os
import sys
import threading
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
BUNDLE = getattr(sys, "_MEIPASS", None)
TOOLS = os.path.join(os.path.dirname(HERE), "tools")

for p in (HERE, TOOLS, BUNDLE):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

CFG = os.path.join(HERE, "signer_config.json")
KEY = os.path.join(HERE, "keys", "publisher_ed25519.key")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(base, path, token, payload=None, timeout=60):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"X-Admin-Token": token,
                                          "Content-Type": "application/json"})
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class App:
    def __init__(self, root):
        self.root = root
        root.title("扒皮矿工 · 内核签发工具")
        root.geometry("900x600")
        self.cfg = {}
        try:
            with open(CFG, encoding="utf-8") as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            pass

        top = tk.Frame(root, padx=10, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="中控地址").grid(row=0, column=0, sticky="w")
        self.server = tk.Entry(top, width=36)
        self.server.grid(row=0, column=1, padx=6)
        self.server.insert(0, self.cfg.get("server") or "http://bapimine.click:8099")
        tk.Label(top, text="管理员 token").grid(row=0, column=2, sticky="w")
        self.token = tk.Entry(top, width=24, show="*")
        self.token.grid(row=0, column=3, padx=6)
        self.token.insert(0, self.cfg.get("token") or "")

        bar = tk.Frame(root, padx=10)
        bar.pack(fill="x")
        self.btn_check = tk.Button(bar, text="① 检查待签发", width=16, command=self.check)
        self.btn_check.pack(side="left")
        self.btn_sign = tk.Button(bar, text="② 签发并上传", width=16, state="disabled",
                                  command=self.sign)
        self.btn_sign.pack(side="left", padx=8)
        tk.Button(bar, text="私钥状态", command=self.key_status).pack(side="left")
        self.keylab = tk.Label(bar, text="", fg="#666")
        self.keylab.pack(side="left", padx=10)

        self.tree = ttk.Treeview(root, columns=("kernel", "signed", "latest", "downloaded"),
                                 show="headings", height=7)
        for c, t, w in (("kernel", "内核", 150), ("signed", "已签发", 130),
                        ("latest", "上游最新", 130), ("downloaded", "包是否已下载", 200)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", padx=10, pady=8)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sel())

        tk.Label(root, text="运行日志", anchor="w").pack(fill="x", padx=10)
        self.log = tk.Text(root, height=15, bg="#111", fg="#ddd", insertbackground="#ddd")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.pending = {}
        self.refresh_key_label()
        self.write("私钥只在本机使用；中控只接收「已签名的清单」，无法伪造。")

    def write(self, msg):
        self.log.insert("end", str(msg) + "\n")
        self.log.see("end")

    def refresh_key_label(self):
        if os.path.isfile(KEY):
            self.keylab.config(text="私钥：已找到", fg="#1a7f47")
        else:
            self.keylab.config(text="未找到私钥", fg="#b3261e")

    def key_status(self):
        if os.path.isfile(KEY):
            messagebox.showinfo("私钥", "已找到：\n%s\n\n它只在本机使用，不要上传服务器。" % KEY)
        else:
            messagebox.showerror(
                "私钥缺失",
                "没有找到私钥：\n%s\n\n请把 keys\\publisher_ed25519.key 放到本程序目录下。\n"
                "（这个文件是发布身份，丢了就得重新给所有矿机换公钥）" % KEY)
        self.refresh_key_label()

    def _sel(self):
        self.btn_sign.config(state="normal" if self.tree.selection() and self.pending else "disabled")

    def save_cfg(self):
        try:
            with open(CFG, "w", encoding="utf-8") as f:
                json.dump({"server": self.server.get().strip(),
                           "token": self.token.get().strip()}, f)
        except OSError:
            pass

    def check(self):
        self.save_cfg()
        self.btn_check.config(state="disabled")
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        try:
            d = api(self.server.get().strip(), "/api/kernels", self.token.get().strip())
        except Exception as e:  # noqa: BLE001
            self.write("✗ 检查失败：%s" % e)
            self.btn_check.config(state="normal")
            return
        self.pending = d.get("pending") or {}
        signed = d.get("signed") or {}
        for i in self.tree.get_children():
            self.tree.delete(i)
        for name, meta in (d.get("catalog") or {}).items():
            p = self.pending.get(name)
            self.tree.insert("", "end", values=(
                name, signed.get(name, "-"), (meta or {}).get("version", "-"),
                ("已下载" if p and p.get("downloaded")
                 else ("未下载（等中控自动下载）" if p else "-"))))
        self.btn_check.config(state="normal")
        if not self.pending:
            self.write("✓ 没有待签发内核（清单已是最新）")
        else:
            self.write("待签发：%s" % "、".join("%s → %s" % (n, p["latest"])
                                              for n, p in self.pending.items()))
        self._sel()

    def sign(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = self.tree.item(sel[0])["values"][0]
        p = self.pending.get(name)
        if not p:
            messagebox.showinfo("提示", "%s 没有待签发版本" % name)
            return
        if not p.get("downloaded"):
            messagebox.showwarning("包还没下载",
                                   "上游 %s 的包还没下载到中控，暂时签不了。\n"
                                   "在群控点「检查最新版」触发下载，或等自动检查。" % p["latest"])
            return
        if not os.path.isfile(KEY):
            messagebox.showerror("私钥缺失", "没有找到私钥：\n%s" % KEY)
            return
        if not messagebox.askyesno("确认签发",
                                   "把 %s：%s → %s 写进签名清单？\n"
                                   "（其它内核保持不动，只签这一个）\n\n"
                                   "签发后群控点「升级内核」就会真的升到该版本。"
                                   % (name, p["signed"], p["latest"])):
            return
        self.btn_sign.config(state="disabled")
        threading.Thread(target=self._sign, daemon=True).start()

    def _sign(self, name):
        try:
            import ed25519
            import sign_manifest as sm

            base, token = self.server.get().strip(), self.token.get().strip()
            d = api(base, "/api/kernels", token)
            signed = d.get("signed") or {}
            cached = {c["file"]: c for c in (d.get("cached") or [])}
            kernels = {}
            for n, meta in (d.get("catalog") or {}).items():
                # 只签选中的那个内核；其余保持清单里已签的版本不动
                if n == name and (self.pending.get(n) or {}).get("latest"):
                    target = self.pending[n]["latest"]
                else:
                    target = signed.get(n) or (meta or {}).get("version")
                fn = "%s-%s.zip" % (n, target)
                c = cached.get(fn)
                if not c or not c.get("sha256"):
                    raise RuntimeError("%s 的包 %s 未缓存，无法签发" % (n, fn))
                kernels[n] = {"file": fn, "version": target,
                              "size": c["size"], "sha256": c["sha256"]}

            cur = api(base, "/api/signed_manifest", token)
            client = cur.get("client") or {}
            if not client.get("sha256"):
                raise RuntimeError("线上清单缺少 client 段")

            seed = sm.load_key(os.path.abspath(KEY))
            man = sm.build_manifest(
                client, kernels,
                notes="签发 " + "、".join("%s %s" % (n, c["version"]) for n, c in kernels.items()))
            man["sig"] = ed25519.sign_hex(sm.canonical(man), seed)

            res = api(base, "/api/signed_manifest", token, payload=man)
            if not res.get("ok"):
                raise RuntimeError(res.get("error") or "上传失败")
            self.write("✓ 已签发并上传：client %s / kernels %s"
                       % (res.get("client_version"), res.get("kernels")))
            self.write("现在去群控点「升级内核」即可生效。")
        except Exception as e:  # noqa: BLE001
            self.write("✗ %s" % e)
        finally:
            self.btn_sign.config(state="normal")
            self.check()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
