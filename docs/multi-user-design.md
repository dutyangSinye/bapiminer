# 中控多用户模式设计

> 目标版本：客户端 1.13.0.0 / 中控服务端 1.13.0.0
> 状态：**设计已定稿**（决策已由需求方拍板，见附录 A）
> 日期：2026-10-24

---

## 一、需求与角色

| 编号 | 需求 | 落地要点 |
|---|---|---|
| R1 | 中控改多用户模式，用户注册后在自己账号下绑定自己的矿机 | 新增 `users` 表 + 矿机归属列 |
| R2 | 挂载动作必须在**矿机端**做用户登录 | 新增 `POST /api/rig_login`，矿机端 GUI 提供登录入口 |
| R3 | 矿机端**第一次登录自动绑定**该矿机到用户名下 | 首次登录且矿机无归属 → 自动绑定 |
| R4 | 管理员可看到联机到中控的**所有**矿机 | `is_admin` 角色，绕过归属过滤 |
| R5 | 矿机端和中控端**都能注册和登录** | 同一套账号体系，两个入口 |
| R6 | 可**远程给矿机改中控中的名字** | 拆「机器身份 `name`」与「展示名 `display_name`」，双向可改 |

### 角色定义

| 角色 | 说明 | 能力 |
|---|---|---|
| **admin** | 平台管理员 | 看全部矿机/全部用户；强制改绑；重置接入码；改任意矿机名 |
| **user** | 注册用户 | 看**自己名下**矿机；改自己矿机的名/池/钱包；对自己的矿机下发命令 |
| **guest / 未登录** | —— | 看板显示登录页；无数据访问权 |

---

## 二、现状硬约束（决定了设计走向）

### 2.1 `rigs.name` 是三重身份合一

```
rigs(name TEXT PRIMARY KEY, token, wallet, game_mode, client_version, pool_url, ...)
   ↑                    ↑
   │                    └── 矿机接入码（X-Rig-Token 比对）
   │
   ├── samples.rig  ──┐
   ├── commands.rig ──┼── 三张表全部按 name 关联
   └── alerts.rig   ──┘
```

**结论**：`name` 一旦可改，`samples`/`commands`/`alerts` 的历史关联全部断裂。
→ 必须拆分：**`name` = 不可变机器标识**（继续当主键和 FK），**新增 `display_name` = 可变展示名**。

### 2.2 现有「改名字 = 矿机掉线」缺陷

客户端 GUI 的 `rig_name` 字段可编辑，上报时作为 `name` 发出：

```python
# client/miner_client.py:2245
"name": self.cfg.get("rig_name") or socket.gethostname().lower(),
```

服务端 `handle_report` 用 `WHERE name=?` 更新：

```python
# server/server.py:800
"UPDATE rigs SET ... WHERE name=?", (..., name)
```

用户在矿机端改 `rig_name` → 上报的 name 变了 → 匹配不到旧行 → 落入
`elif row["token"] and row["token"] != token` → 返回 **"token mismatch"** → 矿机从看板上消失。

→ R6 的实现必须先修掉这个既有缺陷。

### 2.3 鉴权是单点全局令牌

```python
_admin_token = cfg.get("admin_token")   # 全局一个
# 看板所有接口：fleet / history / daily / alerts / kernels / pools
#              / rig_reset_token / kernel_check / command / alerts_resolve / wallet
```

### 2.4 收益与钱包是全局聚合 —— 多用户下的泄露点

```python
# server/server.py:644
def unique_wallets(with_pool=False):
    """_wallet_map 快照去重……"""   # ← 遍历全部 rig，不区分归属
# server/server.py:912
_uniq = unique_wallets()             # ← 直接进 fleet_payload 的 total
```

`fleet_payload` 的 `total` 段（总算力/毛收益/钱包余额）直接来自全量聚合。
→ **普通用户看到别人的钱包余额**。这是本次改造必须堵死的漏洞。

---

## 三、数据模型

### 3.1 新增表

```sql
-- 用户
CREATE TABLE IF NOT EXISTS users(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT UNIQUE NOT NULL,
  pwd_hash      TEXT NOT NULL,      -- PBKDF2-HMAC-SHA256, hex
  pwd_salt      TEXT NOT NULL,      -- 16 字节随机, hex
  is_admin      INTEGER DEFAULT 0,
  status        TEXT DEFAULT 'active',   -- active | disabled
  created_at    REAL,
  last_login_at REAL
);

-- Web 会话（中控浏览器登录态）
CREATE TABLE IF NOT EXISTS sessions(
  token      TEXT PRIMARY KEY,        -- 随机 32 字节 hex
  user_id    INTEGER NOT NULL,
  created_at REAL,
  expires_at REAL,
  ua         TEXT,
  ip         TEXT
);

-- 归属变更审计（**只有归属变更才记；改名不留历史**，见 D4）
CREATE TABLE IF NOT EXISTS rig_owner_log(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  rig     TEXT NOT NULL,
  old_user INTEGER,
  new_user INTEGER,
  action  TEXT,       -- bind | rebind | unbind | admin_force
  note    TEXT,
  ts      REAL
);

-- 登录失败计数（防爆破）
CREATE TABLE IF NOT EXISTS login_fail(
  key       TEXT PRIMARY KEY,   -- username 或 ip
  fails     INTEGER DEFAULT 0,
  locked_until REAL,
  last_fail REAL
);
```

### 3.2 `rigs` 表增列（沿用现有 `ALTER TABLE ADD COLUMN` 幂等模式）

```sql
ALTER TABLE rigs ADD COLUMN owner_id    INTEGER DEFAULT 0;   -- 0 = 无主（未绑定）
ALTER TABLE rigs ADD COLUMN display_name TEXT DEFAULT '';   -- 空则回退显示 name
ALTER TABLE rigs ADD COLUMN bound_at    REAL;
```

**不动的表结构**：`samples` / `commands` / `alerts` 全部保持按 `name` 关联 —— 零迁移成本、零历史断裂风险。

### 3.3 字段语义最终定义

| 字段 | 可变性 | 含义 | 谁能看到 |
|---|---|---|---|
| `rigs.name` | **不可变** | 机器标识 / 主键 / FK 键 / 接入码绑定键 | 内部 |
| `rigs.display_name` | **可变** | 中控界面展示名 | 用户（仅自己名下）|
| `rigs.token` | 可重置 | 机器接入码（`X-Rig-Token`）| 内部 |
| `rigs.owner_id` | 可变 | 归属用户 id，0 = 无主（未绑定） | 内部 |

---

## 四、认证：三个正交域

多用户化最容易出错的地方是**把人的身份和机器的身份混在一起**。本设计明确拆成三个互不干涉的域：

```
┌─ 域 A：机器身份（机器 → 中控）─────────────────────────┐
│  凭证：X-Rig-Token header + rig name                   │
│  用途：/api/report  /api/poll  /api/cmd_result         │
│        /api/income_meta  /api/kernel_dl                │
│  语义：证明"我确实是这台矿机"                            │
│  变更影响：完全不受多用户改造影响，现有 7 台矿机零改动   │
└─────────────────────────────────────────────────────────┘

┌─ 域 B：人的身份（中控 Web → 中控）─────────────────────┐
│  凭证：X-User-Token header（sessions 表）              │
│  用途：/api/fleet /api/history /api/daily /api/alerts   │
│        /api/command /api/wallet /api/rig_rename ...     │
│  语义：证明"我是哪个用户"，数据按 owner_id 过滤          │
└─────────────────────────────────────────────────────────┘

┌─ 域 C：人的身份（矿机端 → 中控）──────────────────────┐
│  凭证：X-Rig-Token（证明机器） + 用户名/密码（证明人）  │
│  用途：/api/rig_login  /api/rig_logout  /api/rig_rebind│
│  语义：人在矿机现场登录 → 触发首次自动绑定               │
└─────────────────────────────────────────────────────────┘
```

### 4.1 密码存储

纯标准库（服务端零依赖原则不破）：

```python
import hashlib, hmac, os

PBKDF2_ROUNDS = 200_000

def hash_password(password: str, salt_hex: str = None) -> tuple:
    salt = salt_hex or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return dk.hex(), salt

def verify_password(password: str, pwd_hash: str, salt_hex: str) -> bool:
    calc, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(calc, pwd_hash)
```

### 4.2 令牌

| 令牌 | 长度 | 有效期 | 存储位置 |
|---|---|---|---|
| Web session | 32 字节 hex | 7 天（滑动续期）| 浏览器 localStorage |
| 客户端绑定态 | 32 字节 hex | 90 天 | `client_config.json` |
| 机器接入码 | 16 字节 hex | 永久（可重置）| `client_config.json` + `rigs.token` |

三个令牌**互不复用**，泄露一个不影响另外两个。

### 4.3 鉴权统一入口

```python
def auth_context(handler) -> dict:
    """解析请求身份，返回 {user_id, username, is_admin, via}。

    via: 'user'  → X-User-Token 命中 sessions
         'admin' → X-Admin-Token 等于 config.admin_token（逃生通道 / 老看板兼容）
         None    → 未鉴权
    """
    tok = handler.headers.get("X-User-Token") or ""
    if tok:
        row = db().execute(
            "SELECT u.id, u.username, u.is_admin, u.status, s.expires_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
            (tok,)).fetchone()
        if row and row["expires_at"] > time.time() and row["status"] == "active":
            return {"user_id": row["id"], "username": row["username"],
                    "is_admin": bool(row["is_admin"]), "via": "user"}
    if handler.headers.get("X-Admin-Token") == _admin_token:
        return {"user_id": 0, "username": "admin", "is_admin": True, "via": "admin"}
    return {"user_id": 0, "username": "", "is_admin": False, "via": None}
```

`X-Admin-Token` **保留**（逃生通道 + 旧版看板继续可用 + 迁移期不中断）。

---

## 五、关键流程

### 5.1 注册

**中控端**（Web 看板）：
```
POST /api/auth/register  {username, password}
  → 校验用户名格式/重名/密码强度
  → 建 users 行（is_admin=0）
  → 立即建 session 并返回 token
  → 该账号目前 0 台矿机（空的账号）
```

**矿机端**（客户端 GUI）：
```
POST /api/auth/register  {username, password}
  → 同上，返回 token
  → 客户端自动接着调 /api/rig_login 完成绑定（见 5.2）
```

> 开放注册（D2：**不要邀请码**）：`config.json` 增加 `allow_register`（默认 true）。
> 若管理员把它设为 false，则注册接口返回 403，仅管理员可在看板建号。
> **不设 `invite_codes` 白名单**。

### 5.2 矿机端登录 → 首次自动绑定（R2 / R3）

```
矿机端 GUI「中控账号」区：
  用户名 [________]  密码 [________]  [登录] [注册]

客户端：
  POST /api/rig_login
    Headers: X-Rig-Token: <本机接入码>
    Body:    {name: rig_name, username, password}

服务端判定矩阵：
  ① 机器身份校验  rig_auth_check(name, token)  —— 失败即 401
  ② 用户密码校验  verify_password(...)         —— 失败计 login_fail，5 次锁 10 分钟
  ③ 归属决策：
     owner_id == 0        → ★ 自动绑定（R3）：UPDATE rigs SET owner_id=?, bound_at=?
                             写 rig_owner_log(action='bind')
     owner_id == 自己      → 幂等，直接放行
     owner_id == 别人 且非 admin → 409「该矿机已绑定账号 X，请先在原账号解绑或联系管理员」
     owner_id == 别人 且是 admin   → 强制转移，写 rig_owner_log(action='admin_force')
  ④ 返回 {ok, token, user, rig:{name, display_name, owner}}
```

客户端收到成功 → 写入 `client_config.json`：
```json
{ "user_token": "...", "username": "...", "user_id": 3 }
```

**降级设计（重要）**：客户端登录态过期或被解绑，**不影响挖矿和上报** ——
上报走的是域 A（机器接入码），与用户登录态完全解耦。用户看到的是
「登录已过期，请重新登录以恢复远程管理」，而不是「矿机掉线」。

### 5.3 远程改名（R6）—— 双向

#### 方向一：中控端改 → 远程下发到矿机

```
看板（用户对自己矿机 / 管理员对任意矿机）
  POST /api/rig_rename  {rig: name, display_name: "客厅矿机"}
    ① 鉴权 + 归属校验（owner 或 admin）
    ② display_name 合法性：1~32 字符，去首尾空白，禁止控制字符
    ③ UPDATE rigs SET display_name=?
    ④ 下发命令 commands(rig, cmd='set_display_name', arg={display_name})
         → 矿机端 /api/poll 拉到 → 写入本地 client_config.json
         → 矿机端 UI 立即同步显示新名
         → 客户端回执 cmd_result
    ⑤ 返回新名
```

#### 方向二：矿机端改 → 上报时同步到中控

```
矿机端 GUI「中控显示名称」输入框（可编辑，与「机器标识」分开）
  保存 → 写本地 display_name
  → 下次 /api/report 带上 display_name
  → 服务端 UPDATE rigs SET display_name=? WHERE name=? AND token 匹配
    （只能改自己的，天然安全）
```

#### 客户端 GUI 的两个名字字段（必须分清，否则重现「改名即掉线」）

| 字段 | GUI 标签 | 可编辑 | 说明 |
|---|---|---|---|
| `rig_name` | 机器标识（勿改） | **只读** | 机器身份，改了会掉线；首次运行锁定 |
| `display_name` | 中控显示名称 | ✅ 可编辑 | 中控 / 看板里看到的名字 |

客户端首次运行：`rig_name` = 现有值（保持不变，兼容老矿机），
`display_name` 初始 = `rig_name`。

另外（D7）：**中控地址 `server`** 与 **接入口令 `token`** 也一并改为只读，
客户端 `set_field` 层面拒绝写入这两个 key（见附录 A）。

#### 改名不留历史（D4）

- 不建改名历史表；`display_name` **就地覆盖**。
- 看板端改名交互：**输入新名 + 确认**（二次确认，防误触）。
- 唯一留痕是矿机端本地日志一行 `中控重命名：旧名 → 新名`（不入库）。
- 归属变更审计仍保留（`rig_owner_log`）——那是安全事件，与改名不是一回事。

#### 端上改名不弹窗（D6）

中控下发 `set_display_name` → 矿机端**静默写入本地配置并落盘**，
不弹窗、不打断用户，仅日志区追加一行。保存必须做，弹窗不要。

### 5.4 管理员视图（R4）

- `GET /api/fleet`：admin → 返回**全部**矿机；user → 只返回 `owner_id = 自己` 的
- `GET /api/users`（admin）：用户列表 + 各自矿机数
- `POST /api/rig_bind`（admin）：手动把矿机挂到某用户名下
- `POST /api/rig_unbind`（admin）：解绑
- `POST /api/rig_rebind`（原 owner 或 admin）：换绑
- 看板新增「用户管理」页（仅 admin 可见）

---

## 六、权限矩阵

| 接口 | 未登录 | user | admin | 说明 |
|---|:--:|:--:|:--:|---|
| `GET /api/fleet` | ❌ | 仅自己 | 全部 | **含 total 聚合按可见集过滤** |
| `GET /api/history?rig=` | ❌ | 仅自己 | 任意 | 校验 rig 归属 |
| `GET /api/daily` | ❌ | 仅自己 | 全部 | samples 查询加 rig 过滤 |
| `GET /api/alerts` | ❌ | 仅自己 | 全部 | 按 rig 过滤 |
| `POST /api/alerts/resolve` | ❌ | 仅自己 | 全部 | |
| `POST /api/command` | ❌ | 仅自己（`*` 只广播自己的）| 任意 | |
| `POST /api/wallet` | ❌ | 仅自己 | 任意 | |
| `POST /api/rig_reset_token` | ❌ | 仅自己 | 任意 | |
| `POST /api/rig_rename` | ❌ | 仅自己 | 任意 | |
| `GET /api/users` | ❌ | ❌ | ✅ | 用户管理 |
| `POST /api/rig_bind/unbind/rebind` | ❌ | rebind 仅自己 | ✅ | |
| `POST /api/auth/register` | ✅ | ✅ | ✅ | 受 `allow_register` 控制 |
| `POST /api/auth/login` | ✅ | ✅ | ✅ | 防爆破 |
| `POST /api/rig_login` | ✅ | ✅ | ✅ | 需 X-Rig-Token |
| `POST /api/report` `poll` `cmd_result` | X-Rig-Token | 同左 | 同左 | 机器域，不变 |
| `GET /api/kernel_dl` `income_meta` | X-Rig-Token | 同左 | 同左 | 机器域，不变 |
| `GET /api/downloads` `client_latest` `kernel_catalog` `health` | ✅ 公开 | ✅ | ✅ | 不变 |

### 6.1 过滤实现要点（隔离的真正落点）

```python
def visible_rig_names(auth) -> list:
    """当前身份可见的矿机 name 列表。admin 可见全部。"""
    c = db()
    if auth.get("is_admin"):
        return [r["name"] for r in c.execute("SELECT name FROM rigs")]
    uid = auth.get("user_id") or 0
    return [r["name"] for r in
            c.execute("SELECT name FROM rigs WHERE owner_id=?", (uid,))]

def visible_rig_filter(auth, params, column="rig"):
    """给 SQL 加归属过滤条件 + 参数。"""
    names = visible_rig_names(auth)
    if not names:
        return "1=0", []
    qs = ",".join("?" * len(names))
    return "%s IN (%s)" % (column, qs), list(names)
```

**必须改的三处聚合**（否则跨用户泄露）：

| 位置 | 现状 | 改造 |
|---|---|---|
| `fleet_payload()` → `total` 段 | 全量 `rigs` 遍历 + 全局 `unique_wallets()` | 只遍历可见 rig；钱包聚合只统计可见 rig 的 wallet |
| `daily_payload(days)` | `samples` 全表 GROUP BY | 加 `rig IN (可见集)` |
| `alerts` 列表 | `SELECT * FROM alerts` | 加 `rig IN (可见集)` |

`unique_wallets()` 需加 `rigs` 参数限定来源矿机集合。

> D3：用户对自己名下矿机的钱包地址**明文展示**（不掩码）。
> 掩码只用于「列表里一眼扫过」的场合；点开详情必须是完整地址。
> 跨用户的钱包地址依然完全不可见 —— 隔离边界是「可见矿机集合」，不是「字段级脱敏」。

---

## 七、API 清单

### 7.1 新增

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/auth/register` | 公开 | 注册（`allow_register` / 邀请码） |
| POST | `/api/auth/login` | 公开 | 登录，返回 session token；命中 `must_change_pwd` 时**只返回 `need_password_change`、不发 token** |
| POST | `/api/auth/change_password` | 公开（带旧密码）或 user（token 态） | **首次登录强制改密 / 自助改密** |
| POST | `/api/auth/logout` | user | 注销当前 session |
| GET | `/api/auth/me` | user | 当前用户信息 + 矿机数 |
| POST | `/api/rig_login` | 机器 + 账号密码 | **矿机端登录 + 无主矿机自动绑定** |
| POST | `/api/rig_logout` | user | 客户端登出（仅清本地态，不解绑） |
| POST | `/api/rig_unbind` | owner/admin | 解绑（回到无主，可被新账号绑定） |
| POST | `/api/rig_rename` | user/admin | **远程改中控中的名字** |
| GET | `/api/users` | admin | 用户列表 |
| GET | `/api/rigs` | user/admin | 矿机列表（按权限） |

### 7.2 改造（行为变化）

`fleet` `history` `daily` `alerts` `alerts/resolve` `command` `wallet`
`rig_reset_token` —— 全部从「全局 admin_token」改为「user session 或 admin_token + 归属过滤」。

### 7.3 命令白名单新增

```python
ALLOWED_CMDS = {
    "restart_miner", "start_miner", "stop_miner",
    "gamemode_on", "gamemode_off", "update_kernel", "update_client",
    "set_pool", "set_kernel",
    "set_display_name",   # ★ 新增：远程改中控中的名字
}
```

---

## 八、客户端改造（1.13.0.0）

### 8.1 配置新增字段

```json
{
  "rig_name": "biu",              // 机器标识，只读展示
  "display_name": "客厅矿机",      // 中控显示名，可编辑
  "user_token": "...",            // 客户端绑定态
  "username": "zhangsan",
  "user_id": 3
}
```

### 8.2 GUI 改造

在「中控」页（`page-fleet`）新增「中控账号」区块：

```
┌─ 中控账号 ─────────────────────────────────┐
│ 状态：● 已登录为 zhangsan（绑定 3 台矿机） │
│                                            │
│ [未登录时]                                  │
│   用户名 [________]  密码 [________]        │
│   [登录]  [注册]                           │
│                                            │
│ [已登录时]                                  │
│   [退出登录]  [解绑本机]                   │
│   ⚠ 解绑会解除本机与账号的归属关系          │
└────────────────────────────────────────────┘

┌─ 矿机标识 ─────────────────────────────────┐
│  机器标识  biu            （只读，勿改）    │
│  中控显示名称 [客厅矿机]      [保存]        │
└────────────────────────────────────────────┘
```

### 8.3 上报/轮询变更

```python
# /api/report 增加 display_name
payload = {
    "name": self.cfg["rig_name"],              # 身份，不变
    "display_name": self.cfg.get("display_name") or "",   # ★ 新增
    "token": self.cfg["token"],
    ...
}
```

```python
# /api/poll 收到 set_display_name 时
if cmd == "set_display_name":
    self.cfg["display_name"] = arg.get("display_name") or ""
    save_cfg(quiet=True)
    self.emit(event="rig", display_name=self.cfg["display_name"])
```

### 8.4 客户端本地指令新增

```python
"user_login", "user_register", "user_logout", "user_unbind", "set_display_name"
```

---

## 九、看板改造（中控 Web）

### 9.1 登录页

现有登录卡（输入 admin_token）改为：

```
┌─ 登录 / 注册 ──────────────────────────┐
│  [登录] [注册]                          │
│  用户名 [________]                      │
│  密码   [________]                      │
│  [确认]                                 │
│                                        │
│  管理员口令登录（高级）                 │
└────────────────────────────────────────┘
```

token 存储键 `bapiminer_admin_token` → `bapiminer_user_token`。
**保留** `#token=adm_xxx` 书签直开（管理员逃生通道）。

### 9.2 数据隔离

`api()` 统一带 `X-User-Token`；`fleet` 返回的 rigs 已按权限过滤，前端无需改动逻辑，
但 `total` 段语义变为「我的矿机合计」。

### 9.3 新增页面（仅 admin）

- 「用户管理」：用户列表、矿机数、强制解绑/改绑
- 「全部矿机」（admin 视角）已在 fleet 页天然满足

### 9.4 改名交互

矿机卡片上加 ✏️ 按钮 → inline input → 保存调 `/api/rig_rename` → 卡片名即时刷新。
下发命令后矿机端也会同步改名。

### 9.5 卡片信息分层：管理员精简 / 个人全展开

需求方要求：**管理员侧默认只保留矿机主信息，卡片其余信息收起；个人侧卡片全部放开。**

实现方式是把卡片拆成两层：

```
┌─ 卡片（精简态，管理员默认）─────────────────┐
│ [☑] ● rig-客厅矿机   [机器标识tag] ✏️改名 🎮模式 │  ← rig-hd：身份 + 高频操作
│ 1.5 TH/s · RTX 5080    ≈¥12/天  🏦0.8 PRL   ▼详情 │  ← rig-sum：主信息，常驻
├─ rig-detail（默认 hidden，点▼详情展开）─────┤
│ [srbminer] [内核版本] [客户端版本]           │  ← rig-tags
│ 功耗200W │ 温度50°C │ 有效10/0 │ 运行1分钟   │  ← metrics
│ [算力曲线 sparkline]                         │
│ [#0 50°C 40% · 200W]                         │  ← gpu-row
│ [↻重启] [🔑重置] [⚙]                        │  ← rig-ops（仅精简态放这里）
│ 心跳 3秒前 · ⛏Kryptex                       │  ← rig-ft
└──────────────────────────────────────────────┘
```

| 层 | 内容 | 精简态 | 全展开态 |
|---|---|---|---|
| `rig-hd` | 选择框、在线点、显示名、机器标识、✏️改名、🎮模式 | 常驻 | 常驻 |
| `rig-sum` | 算力大数字、收益徽标、钱包徽标、▼详情按钮 | 常驻 | 常驻（无 ▼详情） |
| `rig-detail` | 内核/客户端版本、指标四宫格、曲线、GPU 温度、重启/重置/⚙、心跳矿池 | 默认收起 | 常驻展开 |

规则：

- **默认状态由身份决定**：管理员（`me.is_admin`）→ 精简；个人用户 → 全展开
- **偏好按角色分开存**：`bapiminer_compact_admin` / `bapiminer_compact_user`，
  同一浏览器换账号不会串味
- **工具栏有「精简卡片」开关**，管理员可随时切成全展开（反向亦然）
- **手动展开状态按矿机记住**（`RIG_EXPANDED`），点「▼详情」→ 「▲收起」就地切换，不整页重绘
- **精简态卡片高度从 ~376px 降到 ~98px**，管理员一屏能扫完全部矿机
- **次要操作入口不丢**：精简态把「重启 / 重置 / ⚙」移进明细区（头部仍留 ✏️改名、🎮模式）
- **退出登录清空展开状态**，避免共享浏览器下换账号看到上一账号的展开态

### 9.6 归属产生方式：无主矿机接入 + 矿机端首次登录绑定

**需求方决策（2026-10-24 追加）**：
> 把认领矿机这一步去掉。可以无主矿机接入，只有在矿机端登录的第一次自动绑定。

因此**不存在任何管理员手工指定归属的入口**。归属的产生与转移规则如下：

#### 归属的产生

| 场景 | 结果 |
|---|---|
| 矿机上报（`/api/report`）但从未在矿机端登录 | `owner_id=0`，**无主矿机**，接入不受任何限制 |
| 矿机端首次登录（`/api/rig_login`）且该矿机无主 | **自动绑定**到该账号，写 `owner_id` + `bound_at`，记审计 `action=bind` |
| 同一账号再次登录同一矿机 | 幂等放行，归属不变 |
| 登录一个**已属他人**的矿机 | **409 拒绝**，提示原属主账号；admin 也不例外 |

> admin 也不能在矿机端抢走他人已绑定的机器 —— 归属是「谁在这台机器上登录」的事实，
> 不是可以由管理员转授的权限。

#### 归属的转移（唯一合法路径）

```
原属主在矿机端点「解绑本机」  →  owner_id 归 0（回到无主）
   ↓
新用户在矿机端登录            →  自动绑定到新账号
```

`/api/rig_unbind` 同时支持「owner 自解绑」与「admin 应急解绑」，两者都写审计。

#### 已移除的接口

| 接口 | 原用途 | 现状 |
|---|---|---|
| `POST /api/rig_claim` | admin 批量认领无主矿机 | **404 已移除** |
| `POST /api/rig_bind` | admin 手动把矿机挂到某账号 | **404 已移除** |
| `POST /api/rig_rebind` | 换绑 / admin 强制改绑 | **404 已移除** |
| `GET /api/unclaimed_rigs` | 列出待认领矿机 | **404 已移除** |

#### 可见性

| 身份 | 能看到的矿机 |
|---|---|
| admin | **全部**（含无主矿机） |
| 普通用户 | **仅自己名下**的矿机 |

无主矿机**只对 admin 可见**，不对普通用户可见 —— 否则就等于开了 D5 明令禁止的
「多用户只读共享」口子。矿机接入（上报）不受归属限制，但**归属产生前**
普通用户看不到它；一旦绑定完成，它立刻从 admin 的视图转入该用户的私有集合。

#### 看板表现

管理员的矿机卡片上，未绑定矿机显示 `🔗 未绑定` 标签（tooltip 提示去矿机端登录），
已绑定矿机显示 `👤 <账号名>` 标签。不再有「认领」面板。

#### 存量矿机（生产 7 台）的归属产生路径

生产库迁移后 7 台矿机 `owner_id` 均为 0（无主）。按新设计，
它们**不会**自动归属 admin —— 必须由各机主在自己的矿机上登录一次：

```
矿机端升级到 1.13.0.0（带账号功能）
   ↓
在矿机端 GUI「中控账号」区用目标账号登录
   ↓
本机自动绑定到该账号，中控看板上随即出现在该账号名下
```

### 9.7 管理员初始密码：部署后首次登录强制修改

**需求方决策（2026-10-24 追加）**：
> 管理员密码的模式改成 —— 部署好之后，第一次登录时进行修改。

#### 为什么不能只靠 `config.json` 预置

`server.ensure_admin_user()` 的第一行就是「`users` 表已有 admin → 直接返回」。
生产库里 admin 早就存在，所以：

- 改完 `server.py` 重启后，**预置逻辑根本不会跑**；
- `must_change_pwd` 标记也就永远不会被打上；
- 结果是初始密码变成**长期有效**的凭据 —— 正是要消除的风险。

因此部署流程必须**直接写库**，而不是指望服务端预置。

#### 机制

| 环节 | 行为 |
|---|---|
| `users.must_change_pwd` | 新增列（`INTEGER DEFAULT 0`），幂等迁移 |
| `login_user()` 命中标记 | 返回 `{ok:true, need_password_change:true, user:{...}}`，**不发 token** |
| `/api/rig_login` 命中标记 | 同样返回 `need_password_change`，且**不做归属绑定**（改完密重登才绑） |
| `/api/auth/change_password` | 校验旧密码 → 写新哈希（PBKDF2 200k）→ 清 `must_change_pwd` |
| 改密成功后 | 用新密码重新登录 → 拿到 token → 正常进系统 |
| 旧密码 | 改密后立即失效 |
| 旧会话 | 部署重置密码时**一并吊销**（否则已登录浏览器的 token 仍可用） |

#### 部署流程（`deploy/deploy_multiuser.py`）

```
⓪ PROBE      探测部署目录
① UPLOAD      替换 server.py / static/index.html / static/bapiminer.svg
② PRESET      config.json 写入 admin_user
              ★ 库里已有管理员时**删除**明文 admin_password（不留盘）
③ RESTART     docker restart → init_db() 建出 must_change_pwd 列
④ SEED_ADMIN  在容器内直接写库：
              - 重置 admin 的 pwd_hash / pwd_salt（与服务端同一套 PBKDF2 参数）
              - 置 must_change_pwd = 1
              - 删除该用户的全部 sessions
⑤ VERIFY      回读 users 表 / 接口健康 / 登录响应应含 need_password_change 且无 token
```

`--admin-password` 留空时自动生成随机临时密码（`T` + 11 位字母数字），
部署结束后在终端打印，并注明「首次登录须修改」。

#### 三端表现

| 端 | 表现 |
|---|---|
| 中控看板 | 登录卡 → 检测到 `need_password_change` → 切到独立的改密卡（旧密码 / 新密码 / 确认新密码）→ 成功后自动用新密码登录并进系统 |
| 矿机端 | 收到 `acct_pwd_required` 事件 → 账号区切成改密表单 → 提交 `user_change_password` → 成功后自动重登并绑定本机 |
| 服务端 API | 未改密前**拿不到 token**，所有受保护资源返回 401 |

#### 安全性要点

- 临时密码只用于「开箱用一次」，不是长期凭据；
- `config.json` 里不再保留明文 `admin_password`；
- 密码哈希参数与服务端完全一致（PBKDF2-HMAC-SHA256 / 200k 轮 / 16 字节盐 / `hmac.compare_digest`）；
- 弱密码（<6 位）、新旧密码相同、旧密码错误，全部拒绝。

---

### 9.8 矿机卡片头信息分层（UI 重构）

> 起因：生产截图暴露卡片头信息架构失衡 —— 矿机名被压成 `dutyang...`，
> 而次要信息（归属账号）却完整显示；归属标签与矿机名同色同字重，无法区分。

#### 9.8.1 诊断（算得出来的硬数据）

卡片内容宽 354px（`minmax(392px)` − 两侧 padding 19px），而重构前头部一行
的固定宽度合计约 **361px**：

| 元素 | 宽度 |
|---|---|
| 复选框 + 状态点 | 23px |
| 机器标识 tag（改名后才出现） | ~90px |
| 归属 tag | ~80px |
| 「改名 / 模式 / 重启 / 重置 / ⚙」文字按钮 | ~170px |
| gap（6 × 8px） | 48px |
| **合计** | **~411px（超出 354px）** |

**结论：布局必然溢出**，`.nm` 的 `flex:1 1 auto` 是唯一可压缩项 → 名字被截断，
而归属与机器标识（`white-space:nowrap`）纹丝不动。**这是优先级倒置，不是配色问题。**

#### 9.8.2 新结构：主身份行 + 元信息行

```
┌────────────────────────────────────────────┐
│ ☐ ●  dutyangsinye-客厅-副-机      [改名][模式][⋯] │  ← 主身份行
│    Ⓐ alice   rig-dutyang-long                  │  ← 元信息行
└────────────────────────────────────────────┘
```

| 行 | 承载 | 伸缩规则 |
|---|---|---|
| `.rig-hd` 主身份行 | 复选框、状态点、**矿机名**、操作组 | 名字 `flex:1 1 auto; min-width:0`；操作组 `.rig-acts` 固定宽 `flex:none` |
| `.rig-meta` 元信息行 | 归属徽标、机器标识、冲突告警 | `flex-wrap:wrap`；无内容时整行 `:empty` 折叠 |

#### 9.8.3 视觉契约

| 元素 | 形态 | 理由 |
|---|---|---|
| 矿机名 | 15px / 700 粗体 / `--text` | 卡片主身份，最高对比 |
| 归属（已绑定） | **圆形首字母头像**（`--brand` 底）+ 常规字重用户名 | 圆形是「人/账号」的形态信号，与粗体文字天然区分；比 👤 emoji 更紧凑且无跨平台字形漂移 |
| 归属（未绑定） | **虚线描边 + 中性灰**，无头像 | 「空槽待填」而非「故障」—— 7 台未绑定不该是 7 个橙色告警块。语义靠「虚线/实线 + 文案 + 位置」三重线索，不靠颜色单独承载 |
| 机器标识 | 灰色等宽字体 | 技术性字段而非状态，与状态徽标区分 |
| 冲突告警 | 红色 tag | 真正的异常才用告警色 |

#### 9.8.4 操作入口：恒定三控件 + 「更多」菜单

次要操作（重启 / 重置客户端密钥 / 配置矿池内核）收进 **`⋯` 下拉菜单**：

- 头部控件数量在**精简 / 全展开两模式下恒为 3**，卡片宽度不随模式漂移；
- 菜单是次要操作的**唯一入口**（明细区不再重复承载 `rig-ops`），避免双入口歧义；
- 菜单项 = 图标 + 主文案 + 说明两行结构，宽度自适应并受 `max-width:calc(100% - 28px)` 约束，不溢出卡片；
- 「重启矿机」用红色标记为高后果操作；
- 交互：`Esc` 关闭、点外部关闭、焦点自动落到首个菜单项、关闭后归还焦点至触发按钮；
  菜单项被点击时以 `closeRigMenu(false)` 跳过归还焦点，避免与随后的确认框抢焦点。

#### 9.8.5 层叠上下文陷阱

`.rig:hover` 的 `transform: translateY(-2px)` 会**创建层叠上下文**，
导致展开菜单的卡片被后续兄弟卡片压住。解法：

```css
.rig:hover{z-index:20}          /* hover 抬升 */
.rig.menu-open{z-index:60}      /* 展开菜单再抬升 */
```

#### 9.8.6 验证

| 层级 | 手段 | 结果 |
|---|---|---|
| 静态断言 | `tests/test_dashboard_compact_cards.py` | 44 断言全绿（新增 §G 段 13 条设计契约断言） |
| 真实浏览器 | `tests/verify_card_header.js`（CDP 驱动 Edge） | 22 断言全绿，含长名场景复现 |
| 响应式 | 1440 / 1024 / 768 / 375px 四视口 | 全部无截断、无溢出；375px 头部自动换行 |

---

## 十、数据迁移

### 10.1 迁移原则：**零停机、零历史断裂、不动 samples/commands/alerts**

```
Step 1  建新表 users / sessions / rig_owner_log / login_fail
Step 2  ALTER TABLE rigs ADD COLUMN owner_id / display_name / bound_at
        （幂等：已存在则跳过，走现有 try/except 模式）
Step 3  回填 display_name = name（存量矿机显示名与现在一致）
Step 4  存量矿机 owner_id 保持 0（无主）
        → 各自机主在矿机端用目标账号登录一次 → 自动绑定到该账号
Step 5  首个 admin 账号（D1：**首次启动强制交互输入**）：
        users 表为空时，服务端启动进入阻塞式交互：
        提示输入管理员用户名 / 密码 / 确认密码（不回显）
        → 校验通过才建 users 行
        → 无 TTY 环境改用 ADMIN_USER / ADMIN_PASSWORD 环境变量预置
        → 绝不生成随机密码并打印到控制台
Step 6  老客户端平滑升级：
        1.12.0.5 → 1.13.0.0，首次运行自动补 rig_uid/display_name，
        rig_name 保持原值不变 → 现有 7 台矿机在服务端无需任何改动
```

### 10.2 存量矿机为什么保持「无主」而不是自动归 admin

- 自动归 admin 会让「谁在用这台机器」的语义混乱 —— 归属应当反映真实使用关系
- 保持无主 → 归属由机主在矿机端登录这一动作自然产生，无需 admin 介入
- 同时给客户一个过渡期：客户先在矿机端登录 → 自动绑定到自己名下
- 看板 admin 侧用 `🔗 未绑定` 标签明示这些矿机待认领，tooltip 里指引去矿机端登录

---

## 十一、安全要点

| 面 | 措施 |
|---|---|
| 密码 | PBKDF2-HMAC-SHA256 / 200k 轮 / 16 字节随机盐 / `compare_digest` 恒时比较 |
| 传输 | 生产已有 HTTPS 前提；令牌走 header 不进 URL |
| 暴力破解 | `login_fail` 表计数；5 次失败锁 10 分钟；按用户名 + IP 双维度 |
| 用户名 | 3~24 字符，字母数字下划线起步；保留词（admin/root/system）拒绝注册 |
| 越权 | **每个读写 rig 的接口都要做归属校验**，不能只在前端过滤 |
| 令牌泄露 | 三域令牌互不复用；客户端令牌可单独吊销（`/api/rig_logout`） |
| 越权改他人名 | `rig_rename` 校验 owner 或 admin；矿机端上报改名的 SQL 带 `token` 匹配条件 |
| 审计 | `rig_owner_log` 记录所有归属变更（谁、何时、从谁到谁、为什么） |
| 注册滥用 | `allow_register` 开关（D2：**不要邀请码**，无白名单） |
| 敏感字段 | 任何接口不回传 `pwd_hash` / `pwd_salt` |
| 客户端配置篡改 | D7：`server` / `token` / `rig_name` 在客户端**只读**，`set_field` 层面拒绝写入 |

---

## 十二、实施阶段

| 阶段 | 内容 | 交付 | 验收 | 状态 |
|---|---|---|---|---|
| **P0** | 修「改 rig_name 即掉线」既有缺陷（拆 display_name，rig_name 只读） | 客户端 1.13.0.0 | 矿机端改显示名后，中控仍在线 | ✅ 完成 |
| **P1** | 服务端：users/sessions 表 + 注册/登录/会话 + 鉴权统一入口 | 中控 1.13.0.0 | 注册登录通；老 admin_token 仍可用 | ✅ 完成 |
| **P2** | 服务端：归属过滤改造（fleet/history/daily/alerts/command/wallet）+ 钱包隔离 | 中控 1.13.0.0 | A 用户看不到 B 的矿机与钱包余额 | ✅ 完成 |
| **P3** | 服务端：`/api/rig_login` + 自动挂载 + 审计 | 中控 1.13.0.0 | 首次登录自动挂载；重复登录幂等；他人已绑定则 409 | ✅ 完成 |
| **P4** | 服务端：`/api/rig_rename` + `set_display_name` 命令 | 中控 1.13.0.0 | 中控改名 → 矿机端同步 | ✅ 完成 |
| **P5** | 客户端：GUI 账号区 + 改名 + 本地态持久化 + D7 只读 | 客户端 1.13.0.0 | 矿机端注册/登录/改名全通 | ✅ 完成 |
| **P6** | 看板：登录/注册页 + 改名交互 + admin 用户管理页 + 卡片信息分层 | 中控前端 | 多浏览器不同账号数据隔离正确 | ✅ 完成 |
| **P7a** | 迁移 + 生产部署（存量矿机认领 + 批量升级） | 生产 | 存量 7 台矿机平滑升级不断线 | ⏳ 待执行（脚本就绪：`deploy/deploy_multiuser.py`，待 SSH 凭据） |
| **P7b** | 生产首启 admin 账号预置（`config.json` 或 `ADMIN_USER`/`ADMIN_PASSWORD`） | 生产 | 首个 admin 可登录 | ⏳ 待执行（部署脚本第②步自动预置） |

> 部署操作手册见 [`deploy-multiuser-runbook.md`](deploy-multiuser-runbook.md)（含回滚步骤与验收清单）。

### 12.1 已落地的自动化测试

| 测试文件 | 断言数 | 覆盖 |
|---|---|---|
| `tests/test_multiuser_isolation.py` | 82 | 注册/登录/会话、归属隔离、admin 视图、改名只改 display_name、未登录鉴权矩阵、表结构幂等、D7 约束 |
| `tests/test_multiuser_e2e.py` | 26 | 矿机端登录绑定 → 中控改名 → 客户端静默保存 → report 同步 → 改名后仍在线 |
| `tests/test_keepalive_body_drain.py` | 12 | HTTP/1.1 keep-alive 复用同一 TCP 连接时「未读请求体」导致的 501 串包 |
| `tests/test_dashboard_compact_cards.py` | 44 | 卡片信息分层：精简/全展开默认值、偏好按角色隔离、明细折叠、退出清状态；**卡片头设计契约**（主身份行/元信息行分层、归属头像形态、未绑定虚线态、操作组固定宽、菜单层级、空态折叠） |
| `tests/test_rig_binding.py` | 31 | 归属语义：无主矿机可接入、4 个手工归属接口全 404、矿机端首登自动绑定、重复登录幂等、他人矿机拒绝（admin 也不行）、转移唯一路径、admin 应急解绑、审计只记归属变更 |
| `tests/test_first_login_password.py` | 24 | 首次登录强制改密：标记写入、命中不发 token、改密前拿不到受保护资源、旧密码错被拒、弱密码/新旧相同被拒、改密清标记、新密码可登录、旧密码失效、token 态自助改密、矿机端同样受约束 |
| `tests/test_first_login_pwd_e2e.py` | 22 | 改密端到端（真实 HTTP + 真实 server.py）：首登强制改密 → 改密 → 重登 → 自动绑定 → 旧密码失效 → 重复登录幂等 → 他人矿机 409 |
| `tests/verify_pwd_change_ui.js` | 浏览器 | CDP 驱动 Edge：中控看板改密卡真实渲染与交互（弹出/两次不一致/弱密码/成功自动登录/刷新免登录/无 JS 异常） |
| `tests/verify_client_pwd_ui.js` | 浏览器 | CDP 驱动 Edge：矿机端改密表单真实渲染与交互（`acct_pwd_required` 推送后切表单、三项校验、下发 `user_change_password`、改密后回到账号态） |
| `tests/check_undefined_names.py` | — | `symtable` 作用域感知未定义名审计（client + server） |
| `tests/preflight_client.py` | 5 关卡 | 客户端源码级 + exe 包级门禁（版本号从 spec 读取，不再硬编码） |
| `tests/verify_dashboard_multiuser.js` | 浏览器 | CDP 驱动 Edge：登录/注册/改名/隔离/精简卡片真实渲染验证 |
| `tests/verify_card_header.js` | 浏览器 | CDP 驱动 Edge：卡片头信息分层（长名不截断、归属独立成行、头像形态、未绑定虚线态、头部控件数恒定、更多菜单开合/Esc/点外部关闭/焦点管理、无 JS 异常） |
| `tests/prod_verify_multiuser.py` | 生产 | 生产验收：鉴权矩阵、已删接口 404、首登强制改密、改密后新旧密码行为、隔离、审计 |

### 12.2 已知缺陷与修复记录

| 现象 | 根因 | 修复 |
|---|---|---|
| 登出后换账号登录返回 **501 + HTML**，前端 `SyntaxError: Unexpected token '<'` | POST 路由未消费请求体 → keep-alive 连接上残留 `{}` 与下一个请求行拼成 `{}POST`，BaseHTTPRequestHandler 回默认 501 HTML | `Handler._read_raw_body()` 缓存请求体 + `parse_request()` 按请求重置缓存 + `do_POST/do_GET/do_OPTIONS` 入口统一 drain |
| 共享浏览器下换账号短暂看到上一账号的矿机 | 15 秒轮询的旧请求在新账号登录后才返回 | `_authGen` 世代号丢弃过期响应 + `clearRenderedData()` 清空 DOM 与筛选条件 |
| preflight 版本号误报 | 内联 `python -c` 的正则含 NUL 字节 → Windows 命令行在 NUL 处截断，检测静默失效 | 抽出 `tests/check_exe_version.py` 独立文件（UTF-16LE 版本串正则修正为 `(?:[0-9]\x00|\x2e\x00)+`） |
| `/api/rig_claim` 批量认领接口挂死无响应 | 路由持有 `_db_lock` 时调用 `log_owner_change()`，后者内部又获取同一把锁 → **自死锁** | 审计写入移到锁外；`log_owner_change` 改用独立审计锁，与主库锁解耦 |
| 认领显式指定矿机时「已被他人占用」无提示 | 查询 SQL 只筛 `owner_id=0`，已归属的矿机根本不进结果集 → `skipped` 恒为空 | 查询放宽为「`owner_id=0` 或在指定列表内」，逐条判定归属并回填 `skipped` |
| 测试往生产库写入了用户与会话 | 测试在 `import server` **之后**才改 `S.DATA_DIR`，但 `DB_FILE` 在 import 时已固定 | 改为在 `import server` **之前**设 `MINRIGS_DATA_DIR` / `MINRIGS_CONFIG` 环境变量；隔离测试已确认生产库无污染 |
| 部署脚本改了 `config.json` 但配置不生效 | 只写了 `config.json.tmp` 却从未 rename | 补 `os.replace(tmp, dst)` + `fsync`；同时去掉 `sudo -S python3 -`（sudo 抢 stdin 会吞 heredoc） |
| Git Bash 把 `/opt/minrigs` 转成 `D:/WorkBuddy/.../opt/minrigs` | MSYS 对命令行参数的路径自动转换 | `normalize_remote_dir()` 检测盘符并还原为候选 POSIX 路径 |
| 用户名 `admin` 被保留词列表拒绝 | 保留词检查对所有注册路径生效 | `validate_username(name, allow_reserved=True)` —— 管理员路径放行（权限来自 `users.is_admin` 字段而非用户名），普通用户注册仍拦截 |
| 改了 `server.py` 重启后初始密码仍然长期有效 | `ensure_admin_user()` 第一行就「已有 admin → 直接返回」，预置分支（含打 `must_change_pwd` 标记）根本不执行 | 部署脚本新增 **SEED_ADMIN** 步骤：容器内直接重置 `pwd_hash`/`pwd_salt` + 置 `must_change_pwd=1` + 吊销该用户全部旧会话 |
| `config.json` 里长期残留明文 `admin_password` | 生产 config.json 是 `:ro` bind-mount，服务端自己删不掉 | PRESET 步骤探测 `users` 表：**已有管理员时删除**明文字段；并输出 `admin_password` 改由 SEED_ADMIN 直接写库 |

---

## 十三、风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| 全局聚合改造遗漏某处 → 跨用户泄露 | 高 | P2 阶段强制跑隔离断言测试；代码评审逐处核对 6.1 表格 |
| 客户端 `rig_name` 语义变更引发老配置异常 | 中 | 首次运行只读化 + 保持原值；升级前后对比 `client_config.json` |
| 客户端用户态过期导致「以为掉线」 | 中 | 明确降级文案：登录态过期只影响远程管理，不影响挖矿与上报 |
| 姓名冲突（两台矿机 display_name 相同） | 低 | display_name 不唯一约束，仅提示；name 才是唯一键 |
| admin_token 与 session 双通道并存被误用 | 中 | 统一 `auth_context()` 入口，禁止在业务代码里直接读 header |
| 现有 118 个 API 路由部分遗漏鉴权改造 | 高 | P2 阶段逐路由过一遍；门禁测试覆盖全部公开路由 |

---

## 十四、已确认决策（附录 A，需求方拍板）

| # | 议题 | 决策 | 对设计的影响 |
|---|---|---|---|
| D1 | admin 初始密码发放方式 | **首次启动强制交互输入**（生产部署改为**首次登录强制改密**） | 服务端启动时若 `users` 表无 admin，**阻塞式**在控制台提示输入管理员用户名/密码（不回显），不生成随机密码、不写明文到 config。生产部署场景下 `ensure_admin_user()` 会因「已有 admin」直接返回，故由部署脚本的 SEED_ADMIN 步骤直接写库并打 `must_change_pwd` 标记 —— **首次登录必须改密**。详见 §9.7 |
| D2 | 是否需要邀请码 | **注册不要邀请码** | `POST /api/auth/register` 只需用户名+密码；删除 `invite_codes` 白名单机制，保留 `allow_register` 总开关（默认 true） |
| D3 | 普通用户能否看自己矿机钱包明文 | **能** | 用户对自己名下矿机的钱包地址**明文展示**，不做掩码；跨用户仍完全不可见 |
| D4 | 改名是否留历史 | **不留历史** | 不建 `rig_name_log` / 改名审计表；改名**就地覆盖** `display_name`，只保留一条 `rig_owner_log`（归属变更才审计） |
| D5 | 是否支持多用户只读共享 | **不能** | 严格单一归属：`rigs.owner_id` 一个值，无 `rig_share` 关联表；一台矿机同时只能属于一个账号 |
| D6 | 端上改名是否弹窗提示 | **不弹窗，但可保存** | 矿机端收到中控 `set_display_name` 命令 → **静默写入本地配置并落盘**，只在日志区留一行，不打断用户 |
| D7 | 客户端「中控地址」「介入口令」是否可改 | **不允许改** | 客户端 GUI 这两项**只读展示、禁止编辑**（`set_field` 层面拒绝写入），避免矿机被指向假中控或被夺取接入码 |
| D8 | 卡片信息展示密度 | **管理员精简 / 个人全展开** | 管理员侧卡片默认只留主信息（名称、在线状态、算力、收益、钱包），内核版本/指标/曲线/GPU 温度/次要按钮收进「▼详情」默认收起；个人侧卡片全部展开。详见 §9.5 |
| D9 | 卡片头信息架构 | **主身份行 + 元信息行两层**，次要操作收进「更多」菜单 | 矿机名独占可伸缩空间不被截断；归属/机器标识下沉独立成行；头部控件数在两模式下恒定；次要操作单一入口（更多菜单）。详见 §9.8 |

### D1 落地细节：首次启动强制交互

```
服务端启动 → 检查 users 表
  ├─ 已有 admin 用户 → 正常启动，不打扰
  └─ 无 admin 用户   → 控制台强制交互（阻塞直到输入成功）
        ┌──────────────────────────────────────────┐
        │ 首次启动：创建管理员账号                  │
        │ 用户名 [admin      ]                    │
        │ 密码   [            ] （不回显，需确认）  │
        │ 确认密码[            ]                    │
        └──────────────────────────────────────────┘
        → 校验通过才建 users 行并放行启动
        → 中途 Ctrl+C / EOF → 拒绝启动（不建半成品账号）
        → 非交互环境（无 TTY，如 Docker/systemd）
           → 明确报错退出，提示用 ADMIN_USER/ADMIN_PASSWORD
             环境变量预置（避免生产环境被随机密码卡死）
```

**不允许**「生成随机密码打印到控制台」——那等于把 admin 权限日志化。

### D7 落地细节：客户端只读字段

| 客户端字段 | 原状态 | 新状态 |
|---|---|---|
| 中控地址 `server` | 可编辑 | **只读**，仅展示 |
| 接入口令 `token` | 可编辑 | **只读**，仅展示（掩码） |
| 机器标识 `rig_name` | 可编辑 | **只读**（见 §2.2 掉线缺陷） |
| 中控显示名 `display_name` | — | **可编辑**（新增） |

客户端 `set_field` 白名单：`display_name` / `wallet` / `worker` / `games` / `kernel_dir` / `pool_url`。
`server` / `token` / `rig_name` 命中即丢弃并回一条 warn 日志。

> 理由：中控地址可改 = 矿机可被指向任意服务端（数据外泄 / 冒充上报）；
> 接入码可改 = 矿机可被冒充顶替原机身份（抢占他人矿机槽位）。

### D4 落地细节：不留改名的具体取舍

- 不建改名历史表 → **无法回溯**「这台矿机以前叫什么」。
- 补偿手段：改名前的旧名仍会短暂出现在中控下发的命令/日志里；
  看板改名交互要求二次确认（输入新名确认）降低误操作。
- 归属变更（bind/rebind/unbind）**仍然审计**（`rig_owner_log`），
  因为那是安全事件，与改名不是一回事。

### D5 落地细节：严格单一归属

- `rigs.owner_id` 单列，不建关联表。
- 「已属别人」的矿机，普通用户登录时一律 **409**，没有「申请观察」入口。
- admin 可强制转移，写审计。
