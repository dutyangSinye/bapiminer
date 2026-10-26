# 扒皮矿工 BaPiMiner 珍珠币真正的不扒皮矿工 全开源 不忽悠。

PRL（pearlhash）矿场一体化系统：**中控看板 + Windows 矿机端 + Android 手机端**。

```
矿机端 BaPiMiner.exe ──HTTP 轮询/上报──▶ 中控 server.py（标准库单文件）
        │  挖矿内核 SRBMiner / BzMiner / WildRig        │
        └─ 内核与客户端自动更新（中控代下 GitHub 包）    ├─ SQLite(WAL) fleet.db
                                                        └─ Web 看板（手写 CSS/JS 单页）
手机端 Android ──▶ 同一套中控 API：看状态 / 收告警 / 远程启停 / 游戏模式
```

## 目录结构

| 目录 | 内容 |
|---|---|
| `server/` | 中控服务端单文件 `server.py`（HTTP API + 看板静态资源 + 内核/客户端分发） |
| `client/` | Windows 矿机端：`miner_client.py`（挖矿引擎 / Web UI 壳 / 自更新 / 托盘 / 看门狗）+ `web/` 前端 + `BaPiMiner.spec` 打包配置 |
| `android-app/` `android-uniapp/` `android-wap2app/` | Android 手机端（三套方案源码） |
| `deploy/` | 部署与发布脚本（release_client.py / docker-compose 说明 / bundle） |
| `tests/` | e2e / 冒烟 / 诊断脚本（SSH 脚本密码走环境变量 `MINRIGS_SSH_PASS`）；`test_signing.py` 是升级链路的回归测试 |
| `tools/` | 发布签名工具：`ed25519.py`、`sign_manifest.py`、`stamp_build.py` |
| `brand/` | 品牌 LOGO 资源 |
| `agent/` | 轻量采集 Agent（已有矿工环境用，纯标准库） |

## 核心特性

- **中控看板**：矿机状态 / 算力曲线 / 日收益估算 / 矿池收益对账 / 告警流
- **矿机端**：Web UI（Edge app 窗口壳）+ 系统托盘；关窗收起后台挖矿，托盘退出才真正结束
- **游戏模式**：检测到游戏进程自动让出 GPU，退出游戏自动恢复挖矿
- **自更新**：客户端自替换机制（self-replace，不走 bat 换文件，规避杀软/DLL 竞态）；
  看门狗计划任务（每 3 分钟自检，wscript 静默运行）；孤儿内核自动收割
- **内核分发**：中控定时跟踪 SRBMiner / BzMiner / WildRig releases，GitHub 包缓存在服务端，
  矿机从中控直下（不依赖矿机访问 GitHub）
- **升级签名**：客户端与内核的升级目标由离线私钥签名的清单决定，矿机端内置公钥验签；
  未签名 / 验签失败 / 版本降级一律拒绝——中控被拿下也推不了任意 exe
- **单实例互斥**：重复启动自动调出已有窗口，不产生多实例抢卡

## 升级签名（供应链）

矿机端**只信任签名清单**，不信任中控返回的裸字段：文件字节和它的 sha256 如果来自同一个
未鉴权响应，只能防传输损坏、防不了替换。改这条链路之前，任何拿到中控的人都能给全群推
任意 exe——对一个卖点是「不扒皮」的产品，这等于把可信度交给运气。

### 密钥

```bash
python tools/sign_manifest.py keygen      # 生成 keys/publisher_ed25519.key（只做一次）
python tools/sign_manifest.py pubkey      # 打印公钥，填进 client/miner_client.py 的 PUBLISHER_PUBKEY
```

- 私钥是整条链路的信任根：**离线保管、不进仓库（已 gitignore）、不上中控**。
- 换公钥 = 所有旧矿机再也升不动，只能手动重装。丢了私钥同理。

### 客户端发版

```bash
pyinstaller --clean --noconfirm client/BaPiMiner.spec

# 取中控缓存的各内核 zip 哈希（需要 admin token）
curl -s -H "X-Admin-Token: $TOK" http://host:8099/api/kernels > catalog.json

python tools/sign_manifest.py sign --exe client/dist/BaPiMiner.exe \
    --version 1.13.0.1 --catalog catalog.json \
    --out server/data/signed_manifest.json

python deploy/release_client.py --version 1.13.0.1 \
    --manifest server/data/signed_manifest.json --notes "本次更新内容"
```

`release_client.py` 上传前会本地验签并核对 exe 指纹（防「换了 exe 忘了重签」），上传后
再读 `/api/health` 确认中控已经读到清单。

### 内核签发

内核版本仍由中控自动跟踪（每 6 小时查 GitHub、缓存 zip，并记下 sha256），但**装不装**
由签名清单决定：清单里没有的内核，矿机端拒绝安装并提示发布者去签。发现内核有新版本后，
重跑一次上面的 `sign` 命令即可。

### 中控端点

- `GET /api/signed_manifest` — 原样转发清单字节（中控改一个字段签名就失效）
- `GET /api/health` — `server_version` / `build_commit` / 清单摘要，用来确认线上跑的是哪份代码

跑一次 `python tools/stamp_build.py` 会写 `server/data/build_info.json`（commit + 构建时间），
跟 `server.py` 一起部署即可。

### 引导期（重要）

- 已装 **1.13.0.0** 的机器仍走旧的 `/api/client_latest` 升到 **1.13.0.1**：这条老路本身
  没有验签能力，是历史遗留，升上来之后就只认清单了。
- **1.13.0.1 起**，没有签名清单 = 不升级、不装内核（fail closed）。所以上线前先把清单
  签好传上去，否则全新机器会停在「缺少内核」。

## 快速开始

### 域名与入口

| 用途 | 地址 |
|---|---|
| 中控地址 | `http://bapimine.click:8099` |
| 旧入口 | 仍由服务器侧保留（**不写入本仓库**），老矿机靠它不间断 |

旧入口**不能撤**：已经装好的矿机把中控地址写在各自的 `client_config.json` 里，
撤了 DNS 它们就集体失联。新装客户端默认用新域名，启动时会先探活
（见 `client/miner_client.py::pick_default_server`）；备选入口用环境变量
`BAPIMINER_SERVER_FALLBACK` 注入，仓库里不写死任何备选域名。

已有矿机切到新域名有两条路：

1. 等它升到 ≥1.13.0.4，在中控对该矿机下发 `set_server`（参数 `{"server": "http://bapimine.click:8099"}`）；
2. 直接改那台机器的 `client_config.json` 里的 `server` 字段后重启客户端。

### 中控（Linux + Docker）

```bash
# 见 deploy/ 内 compose 与部署脚本；代码是挂载进容器的：
#   升级 = 替换 /opt/minrigs/server.py 或 static/ 后 docker restart minrigs
cp server/config.example.json server/config.json   # 修改 admin_token
```

### 矿机端（Windows 10/11 x64）

```
pyinstaller client/BaPiMiner.spec --distpath dist --workpath build --noconfirm
```

双击 `BaPiMiner.exe`，填钱包地址、选矿池即可；内核在「系统设置」里一键安装（中控代下）。

### 环境变量约定

| 变量 | 用途 |
|---|---|
| `MINRIGS_SSH_PASS` | tests/deploy 里 SSH 脚本的服务器密码（不硬编码进仓库） |
| `MINRIGS_ADMIN_TOKEN` | e2e 脚本的中控 admin token |

## 版本

当前客户端 **v1.13.0.1**（自该版起升级链路强制验签），中控 v3.1.x。版本历史见 git log 与中控发布 notes。
