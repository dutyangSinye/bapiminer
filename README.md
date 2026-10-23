# 扒皮矿工 BaPiMiner 珍珠币真正的不扒皮矿工 全开源 不忽悠。

PRL（pearlhash）矿场一体化系统：**中控看板 + Windows 矿机端 + Android 手机端**。

```
矿机端 BaPiMiner.exe ──HTTP 轮询/上报──▶ 中控 server.py（FastAPI 风格单文件）
        │  挖矿内核 SRBMiner / BzMiner / WildRig        │
        └─ 内核与客户端自动更新（中控代下 GitHub 包）    ├─ SQLite(WAL) fleet.db
                                                        └─ Web 看板（Element Plus 浅色）
手机端 Android ──▶ 同一套中控 API：看状态 / 收告警 / 远程启停 / 游戏模式
```

## 目录结构

| 目录 | 内容 |
|---|---|
| `server/` | 中控服务端单文件 `server.py`（HTTP API + 看板静态资源 + 内核/客户端分发） |
| `client/` | Windows 矿机端：`miner_client.py`（挖矿引擎 / Web UI 壳 / 自更新 / 托盘 / 看门狗）+ `web/` 前端 + `BaPiMiner.spec` 打包配置 |
| `android-app/` `android-uniapp/` `android-wap2app/` | Android 手机端（三套方案源码） |
| `deploy/` | 部署与发布脚本（release_client.py / docker-compose 说明 / bundle） |
| `tests/` | e2e / 冒烟 / 诊断脚本（SSH 脚本密码走环境变量 `MINRIGS_SSH_PASS`） |
| `brand/` | 品牌 LOGO 资源 |
| `tools/` `agent/` | 辅助工具 |

## 核心特性

- **中控看板**：矿机状态 / 算力曲线 / 日收益估算 / 矿池收益对账 / 告警流
- **矿机端**：Web UI（Edge app 窗口壳）+ 系统托盘；关窗收起后台挖矿，托盘退出才真正结束
- **游戏模式**：检测到游戏进程自动让出 GPU，退出游戏自动恢复挖矿
- **自更新**：客户端自替换机制（self-replace，不走 bat 换文件，规避杀软/DLL 竞态）；
  看门狗计划任务（每 3 分钟自检，wscript 静默运行）；孤儿内核自动收割
- **内核分发**：中控定时跟踪 SRBMiner / BzMiner / WildRig releases，GitHub 包缓存在服务端，
  矿机从中控直下（不依赖矿机访问 GitHub）
- **单实例互斥**：重复启动自动调出已有窗口，不产生多实例抢卡

## 快速开始

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

当前客户端 **v1.12.0.3**，中控 v3.0.x。版本历史见 git log 与中控发布 notes。
