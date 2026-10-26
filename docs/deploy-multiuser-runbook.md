# 中控多用户模式 · 生产部署手册

> 目标：把 NAS 上的中控（Docker 容器 `minrigs`）升级到多用户模式。
> 部署脚本：`deploy/deploy_multiuser.py`

## 0. 前置确认

| 项 | 值 |
|---|---|
| 服务器 | `116.169.217.29` |
| SSH 端口 | `22` |
| SSH 用户 | `root` |
| 部署目录 | `/opt/minrigs`（自动探测，候选 `/data_7HKERMAF/minrigs`） |
| 容器名 | `minrigs` |
| 容器内端口 | `8099` |
| 生产库 | 容器内 `/app/data/fleet.db`（宿主机同路径 `/opt/minrigs/data/fleet.db`，bind-mount 同一文件） |
| 外部入口 | `http://bapimine.click:8099` |
| **不可触碰** | `8088` 端口 = 工时管理系统（`xinye-workhours-web` / `xinye-workhours-api`） |

## 1. 部署（一条命令）

```bash
cd minrigs

export SSH_HOST="116.169.217.29"
export SSH_PORT="22"
export SSH_USER="root"
export SSH_PASS="你的密码"

C:/Users/Admin/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
  deploy/deploy_multiuser.py \
  --admin-user admin --admin-password '你的临时密码' \
  --remote-dir /opt/minrigs
```

### 脚本执行链路

| 步骤 | 动作 | 成功标志 |
|---|---|---|
| ⓪ PROBE | 远端探测部署目录 | `探测到部署目录：/opt/minrigs` |
| ① UPLOAD | 上传 `server.py` / `static/index.html` / `static/bapiminer.svg` | `UPLOAD_OK` |
| ② PRESET | `config.json` 写入 `admin_user`；**已有管理员时删除明文 `admin_password`** | `PRESET_OK` |
| ③ RESTART | 备份旧文件（`.bak.<时间戳>`）→ `docker restart minrigs` → 轮询健康 | `BUILD_RUN_OK` |
| ④ SEED_ADMIN | 容器内直接写库：重置 admin 密码 + 置 `must_change_pwd=1` + 吊销旧会话 | `SEED_ADMIN_OK` |
| ⑤ VERIFY | 回读 `users` 表 / 接口健康 / 登录响应 | `VERIFY_OK` |

**为什么不用 `docker build` 重建镜像**：生产容器是 bind-mount 挂载宿主机的 `server.py`/`static/`，
替换宿主机文件后重启容器即可生效。这样 volumes、镜像、端口映射都不会丢，
回滚只需 `cp server.py.bak.<戳> server.py && docker restart minrigs`。

**为什么 SEED_ADMIN 必须在 RESTART 之后**：新 `server.py` 启动时 `init_db()` 才会建出
`users.must_change_pwd` 列；且 `ensure_admin_user()` 遇到「已有 admin」会直接返回，
预置分支根本不执行 → 标记只能由部署脚本直接写库。

## 2. ★ 客户端发布：默认不做，先本机验证

**矿机端是按 exe 的 sha256 与 `/api/client_latest` 比对，不一致就静默替换并重启。**
也就是说 —— 只要动了生产 `data/downloads/BaPiMiner.exe`，**全部矿机端会自动升级**，
不需要广播命令。

因此：

```bash
# ✅ 只部署服务端（本次采用）
python deploy/deploy_multiuser.py --admin-user admin --admin-password '***'

# ❌ 广播升级 —— 仅在本机验证通过后再用
python deploy/deploy_multiuser.py --admin-user admin --admin-password '***' --client
```

`--client` 会打印醒目提示：「★ 正在发布客户端 x.y.z —— 全部矿机端将静默自动升级！」

发布前的本机验证清单：

| 项 | 验证方式 | 状态 |
|---|---|---|
| 服务端强制改密链路 | `python tests/test_first_login_pwd_e2e.py` | 22 断言 |
| 中控看板改密卡 | `node tests/verify_pwd_change_ui.js`（需 Edge CDP + fixture） | 18 断言 |
| 矿机端改密表单 | `node tests/verify_client_pwd_ui.js`（需 Edge CDP + mock） | 22 断言 |
| 客户端源码门禁 | `python tests/preflight_client.py` | 5 关卡 |
| exe 版本资源 | `python tests/check_exe_version.py client/dist/BaPiMiner.exe` | — |

## 3. 部署后的必做动作

### 3.1 管理员首次登录 → 强制改密

部署脚本输出的临时密码**只能用一次**。用浏览器打开 `http://bapimine.click:8099`：

1. 用临时密码登录 → 页面自动切到「设置新密码」卡片；
2. 填「当前密码 / 新密码 / 确认新密码」→ 提交；
3. 成功后自动用新密码登录并进入看板。

> 未改密前，服务端**不发 token**，所有受保护接口返回 401 —— 临时密码无法长期使用。

矿机端首次登录同样受约束：收到 `acct_pwd_required` → 改密表单 → 改完自动重登并绑定。

### 3.2 存量矿机产生归属

生产库里 7 台矿机的 `owner_id` 全是 `0`（无主）。**没有任何管理员手工指定归属的入口**
（`rig_claim` / `rig_bind` / `rig_rebind` / `unclaimed_rigs` 均已 404）。

各机主在自己的矿机上操作一次即可：

```
矿机端 GUI「中控账号」区 → 用目标账号登录
   ↓
本机自动绑定到该账号（审计记 action=bind）
   ↓
中控看板上该矿机随即出现在该账号名下
```

归属转移的唯一合法路径：**原属主先「解绑本机」→ 新用户在矿机端登录**。

### 3.3 让用户注册

中控页面顶部有「登录 / 注册」双 Tab，注册**不需要邀请码**。

### 3.4 批量升级客户端（确认后再做）

`tests/prod_upgrade_clients.py` 会广播 `update_client` 命令并轮询各矿机版本：

```bash
export MINRIGS_ADMIN_TOKEN=adm_xxxx
export TARGET_CLIENT_VERSION=1.13.0.0
python tests/prod_upgrade_clients.py
```

> 更稳的做法是直接用部署脚本的 `--client`：矿机端会自行 sha256 比对后静默更新。

## 4. 回滚

```bash
cd /opt/minrigs
ls -la server.py.bak.*              # 找最新的备份
cp server.py.bak.<戳> server.py
cp static/index.html.bak.<戳> static/index.html
docker restart minrigs
```

回滚服务端不会回滚数据库里的 `must_change_pwd` 标记。若要一并撤销：

```bash
docker exec minrigs python -c "
import sqlite3
c=sqlite3.connect('/app/data/fleet.db')
c.execute('UPDATE users SET must_change_pwd=0 WHERE username=\"admin\"')
c.commit(); print('标记已清除')"
```

## 5. 验收清单

- [ ] `docker ps` 显示 `minrigs` 为 `Up (healthy)`
- [ ] `curl http://127.0.0.1:8099/api/health` 返回 JSON
- [ ] `users` 表里 admin 且 `must_change_pwd=1`
- [ ] 外部 `curl http://bapimine.click:8099/api/health` → 200
- [ ] 用临时密码登录 → 返回 `need_password_change` 且**无 token**
- [ ] `samples` 行数与部署前一致（**零历史断裂**，部署后应持续增长）
- [ ] 7 台矿机全部在线，`owner_id` 全为 0（未绑定）
- [ ] `client_release.json` 仍是部署前的版本（**未广播升级**）
- [ ] 看板登录页能登录，改密卡能正常完成改密
- [ ] 用户 A 看不到用户 B 的矿机与钱包余额
- [ ] 矿机端能注册/登录，首次登录自动绑定本机
- [ ] 中控改名 → 矿机端**不弹窗**但静默保存（D6）
- [ ] 矿机端「中控地址 / 介入口令」为只读（D7）

生产验收脚本：

```bash
export MINRIGS_ADMIN_PASS='当前密码'
export MINRIGS_NEW_PASS='要改成的新密码'
python tests/prod_verify_multiuser.py
```

> 该脚本会**真的把管理员密码改成 `MINRIGS_NEW_PASS`**，请在跑完后用新密码登录。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `Authentication failed` | SSH 密码不对，或端口不是 22 |
| `未能在 NAS 上找到 minrigs 部署目录` | 用 `--remote-dir /opt/minrigs` 手动指定 |
| 部署后 500 | `docker logs --tail 50 minrigs` 看栈；多半是 `config.json` 权限或格式 |
| 登录后一直要改密 | `must_change_pwd` 标记没清 —— 用上面的回滚片段手工清除 |
| 矿机端没升级 | 正常 —— 本次刻意不发布 exe；`client_release.json` 未动则矿机端不会自更新 |
| 矿机端显示离线 | 客户端需升级到 1.13.0.0 才能带 `display_name` 字段；旧版本仍可用，只是没有改名功能 |
