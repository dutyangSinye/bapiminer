# 扒皮矿工（BaPi Miner）品牌与视觉规范

> 面向：MinRigs 群控系统（矿机端 exe / NAS 中控看板 / 安卓 APK）的实现与评审。
> 范围：品牌标识、命名契约、颜色/字体令牌、应用位置与生产检查。
> 证据基础：本仓库现有 UI（深色矿机端 GUI、浅色中控看板）与 2026-10-23 实测截图（`shots/dash-brand2.png`、`dist/gui-new.png`）。
> 关键假设：产品主线为 PRL/pearlhash 挖矿群控；用户端运行环境为 Windows 10/11 与 Android 6+。

## 1. 视觉主题

- **概念**：「扒皮 → 见矿」。六边形矿晶徽章，右上角一片皮被掀开，露出内部的绿色矿脉。喻义：剥开表层、直取价值，对应群控系统「直接、狠、有效」的定位。
- **主视觉谱系**：深色硬核科技 × 算力绿。沿用产品既有色（GUI `#12151d/#191e29` + `#2fd56b`），不另起炉灶。
- **辅助谱系**：中控看板的浅色系（Element Plus 基准，`#f5f7fb` 面）。徽章以描边+绿色块保证在浅底上的对比。
- **不应复制**：拟物矿工头像、镐头交叉、渐变字效等游戏化套路；不得给徽章加外发光滤镜或改描边色。

## 2. 色板

| 语义 | CSS 变量 | HEX | 用途 |
|---|---|---|---|
| 徽章描边起 | `--bp-cyan` | `#3cc9e8` | 六边形描边上部、剖切高光 |
| 品牌主绿 | `--bp-green` | `#2fd56b` | 矿脉主色、动作按钮、成功态 |
| 矿脉深绿 | `--bp-green-deep` | `#17a551` | 矿脉渐变收尾、安卓图标底纹 |
| 矿脉亮绿 | `--bp-green-hi` | `#66f59b` | 渐变高光、裂口近光侧 |
| 岩层 | `--bp-rock` | `#262f3d` | 徽章左上岩层、深色面板辅助 |
| 墨底 | `--bp-ink` | `#12151d` | GUI 主背景、徽章底盘 |
| 侧栏 | `--bp-side` | `#191e29` | GUI 侧栏、托盘图标底 |
| 皮（翻起） | `--bp-flap` | `#4d8db4→#82c2e4` | 掀起片的渐变 |
| 文字（深底） | `--bp-text` | `#e9eef7` | GUI 正文 |
| 文字（浅底） | `--bp-text-2` | `#1f2733` | 看板正文 |

对比度状态：徽章用色均为装饰性图形，正文对比未重新测量；看板正文沿用既有令牌（此前已按 WCAG AA 校准，未改动）。

## 3. 字体

- 中文：`Microsoft YaHei`（Windows 端零依赖）；备选 `PingFang SC` / `Noto Sans SC`。**系统字体，无授权风险。**
- 拉丁：`Segoe UI`（BAPI MINER 字标）。
- 字标字重：中文 800，副标 700 + letter-spacing 6px；正文 letter-spacing 一律 0。
- 长标题：矿机名/钱包地址保持等宽省略（既有实现），不改。

## 4. 组件样式

**徽章（logo-mark）**
- 结构：六边形（描边 9/256、圆角连接）→ 岩层 + 矿脉 40° 剖切 → 裂口 + 翻起皮（压描边之上）。
- 源文件：`brand/logo-mark.svg`（完整版）、`brand/logo-mark-simple.svg`（≤48px 简标：描边 13、去纹理刻面）。
- 最小尺寸：完整版 ≥64px；<64px 必须用简标（ICO 已内置此规则：16–48px 简标，64px+ 主标）。

**横版字标（logo-wordmark-dark.svg）**
- 比例 720×200；徽章 72% + 「扒皮矿工」80px/800 + `BAPI MINER` + 分隔竖线 + 「挖矿群控系统」。
- 仅深底版。浅底需求未出现；出现时先反白校验对比再扩。

**内嵌资源**
- `client/bapiminer-64.png`（侧栏，预合成 `#191e29` 底，规避 tk alpha）、`client/bapiminer-256.png`（托盘，透明底）。
- `server/static/bapiminer.svg`（看板顶栏 + favicon，路由 `/bapiminer.svg`）。
- `brand/android/icon-{48,72,96,144,192}.png`（安卓启动器图标，深底 + 12% 留边）。

## 5. 布局（品牌应用位）

| 位置 | 资产 | 说明 |
|---|---|---|
| exe 文件图标 | `brand/export/bapiminer.ico` | PyInstaller `icon=`，7 帧 |
| GUI 侧栏头部 | 64px 合成 PNG + 「扒皮矿工 / BAPI MINER·PRL」 | 源码 fallback：⛏ 文字版 |
| GUI 托盘 | 256px 透明 PNG → 64 缩放 | 缺资源时回退程序化矿镐 |
| 看板顶栏 + favicon | `/bapiminer.svg` 34px | `drop-shadow(0 4px 10px rgba(16,185,129,.35))` |
| 安卓启动器 | manifest icons 四档 | 包名 `com.sinye.minrigs` 保持不变（覆盖升级） |

## 6. 深度与层次

- 徽章内部即三层：底盘（L0）→ 岩层/矿脉（L1）→ 裂口+翻皮（L2，唯一允许压过描边的元素）。
- 看板顶栏 logo 允许一个绿色投影；GUI 侧栏不加影。

## 7. 注意事项

- **禁止**：拉伸/旋转徽章、替换描边渐变、把翻皮改成其他颜色（它是品牌记忆点）、给 ICO 追加非白名单尺寸。
- 安卓图标必须带不透明底（透明底在部分启动器会变黑块）；生成脚本见 `brand/export_assets.py` 的安卓段。
- `deploy_gamemode.py` 中的 `minrigs` 是 NAS 运维路径/容器名，**与品牌无关，不得改名**。
- 旧文件名 `MinRigs.exe` / `MinRigs-android.apk` 已废弃；NAS `downloads/` 白名单与下载卡均已切到 `BaPiMiner*`。旧包如仍被直链引用会 404，属预期。
- APK 云打包已完成（2026-10-23 12:01，包名 `com.sinye.minrigs` 不变可覆盖升级，应用名/图标已换新）；启动器圆角适配待装机确认。

## 8. 尺寸变体与验收

| 尺寸 | 用法 | 验收（已执行） |
|---|---|---|
| 16/24/32 | favicon、托盘、任务栏 | ICO 7 帧结构校验通过 ✅ |
| 48–64 | GUI 侧栏、小图标 | GUI 截图清晰可辨 ✅ |
| 128–256 | 安装页、README | `logo-256.png` 满幅居中 ✅ |
| 720×200 | 横版字标 | `logo-wordmark-dark.png` ✅ |

**未执行**：真实启动器上的安卓图标圆角适配（等 APK 装机验证）；浅色 wordmark 变体（暂无需求）。

## 9. 实现提示（可复用）

> 为「扒皮矿工（BaPi Miner）」生产界面时：深色底用 `#12151d`，侧栏 `#191e29`，品牌绿 `#2fd56b`，辅助青 `#3cc9e8`；徽章直接引用 `brand/logo-mark.svg`（大）或 `logo-mark-simple.svg`（≤48px），不得重绘或改色；中文 800 字重的 Microsoft YaHei，letter-spacing 0；产品名一律「扒皮矿工」，英文标识一律 `BaPiMiner`（文件名/标识符用小写 `bapiminer`）。验证状态：logo/ICO/看板/GUI 已实测（2026-10-23）；安卓云打包与装机图标待核验。
