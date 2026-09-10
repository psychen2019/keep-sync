# Keep Sync · Personal Activity Intelligence

把 Keep 从“运动记录 App”变成一个 **Git-native、AI-readable、可长期演化的个人运动时间序列系统**。

核心原则：**源数据尽量无损保存；标准化与分析可重复生成；Agent 分层读取；事实与解释分离。** Keep 只是当前 Adapter，不是最终的数据模型。

## Live Dashboard

仓库现在包含一个面向人的实时仪表盘：`docs/index.html`。

它只读取公开展示所需的三个安全摘要层：

- `data/agent_context.json`
- `data/metrics/monthly.json`
- `data/metrics/yearly.json`

页面不会读取 `raw/`、GPX、polyline 或精确位置。Dashboard 每次打开都会从仓库读取最新摘要，因此 Keep 日常同步完成后无需重新生成 HTML。

正式 GitHub Pages 地址设计为：

```text
https://psychen2019.github.io/keep-sync/
```

`.github/workflows/pages.yml` 负责把 `docs/` 发布到 GitHub Pages。首次使用时如果仓库尚未启用 Pages，请在仓库 `Settings → Pages → Build and deployment → Source` 选择 **GitHub Actions**，然后手动运行一次 `Deploy Activity Dashboard`，之后数据更新会自动触发页面重新部署。

## 数据流水线

```text
Keep private API
  ↓
data/raw/                 原始 API 响应（source evidence）
  ↓
data.db + data.json       稳定活动模型 / 兼容层
  ↓
build_intelligence.py     确定性统计与个人基线
  ↓
agent_context.json        Agent 默认入口
  ├─ metrics/monthly.json
  ├─ metrics/yearly.json
  └─ normalized/activities.jsonl
  ↓
Joi / Codex / other agents
```

## Agent 读取协议

**默认只读 `data/agent_context.json`。** 它回答最近 7/30/90/365 天运动量、最近一次运动、动感单车状态、历史分布、个人描述性基线等常见问题。

需要长期趋势时读取 `data/metrics/monthly.json` 或 `yearly.json`；需要逐次分析时读取 `data/normalized/activities.jsonl`；需要核查 Keep 原始字段时最后下钻 `data/raw/<sport_type>/<id>.json`。不要为了一个简单趋势问题把整个原始历史塞进 LLM context。

`VirtualRide` 在本仓库语义中对应 Keep `indoorCycling`，即主要运动“动感单车 / 室内骑行”。

## Keep API Adapter

当前逆向接口：

```text
POST https://api.gotokeep.com/v1.1/users/login
GET  https://api.gotokeep.com/pd/v3/stats/detail?dateUnit=all&type=<type>&lastDate=<cursor>
GET  https://api.gotokeep.com/pd/v3/<type>log/<run_id>
```

同步域：`running`、`hiking`、`cycling`、`training`。这些是 Keep 的非公开内部接口，可能随 App 更新变化，因此所有 Keep-specific 代码应限制在 ingestion 层。

## 无损 Raw Layer

新同步的每条详情会保存完整 API envelope：

```text
data/raw/running/<id>.json
data/raw/cycling/<id>.json
data/raw/training/<id>.json
data/raw/hiking/<id>.json
```

这样未来发现新的 Keep 字段时可以重新解析，而不必依赖 API 永远可用。历史记录首次升级时，可在 Actions 手动运行并勾选 `refresh_raw`，重新抓取全部历史详情并建立 raw archive。

> Raw 文件可能包含位置、轨迹及 Keep 返回的其他个人运动信息。仓库若公开，请把它视为个人数据发布面并自行决定可接受的暴露范围。

## 标准化字段

SQLite / `data.json` 在旧字段基础上增加：`keep_log_id`、`keep_sport_type`、`keep_data_type`、`timezone`、`max_heartrate`、`calories`、`raw_path`、`synced_at`。未知或 Keep 未提供的数据保持 `null`，不猜测。

GPX 继续写入 `data/GPX_OUT/`，室外轨迹保持 GCJ-02 → WGS84 转换逻辑。

## Intelligence Layer

运行：

```bash
python3 build_intelligence.py
```

输出包括：

- `agent_context.json`：小型、面向 AI 的当前状态与 drill-down 路由；
- `metrics/monthly.json`：月度全部运动与动感单车统计；
- `metrics/yearly.json`：年度统计；
- `normalized/activities.jsonl`：紧凑逐次时间序列。

个人 baseline 使用“有运动的月份”的历史中位数，仅作为**描述性个人基线**，不是医学目标。系统只持久化事实与确定性统计；“为什么中断”“是不是懒”“工作导致运动减少”等因果解释属于后续分析假设。

旧的 `agent_summary.json` 暂时继续生成，作为兼容层；新 Agent 应优先使用 `agent_context.json`。

## 自动同步

GitHub Actions 每天北京时间 **22:17** 自动运行：同步 Keep → 保存 raw → 更新标准化数据 → 构建 intelligence → 校验 JSON → 有变化才 commit/push。避开整点也能减少 scheduled workflow 高峰期延迟风险。

手动运行 Actions 时可选择 `refresh_raw=true` 做一次完整历史 raw backfill。日常定时任务只抓新增记录。

GitHub Actions 现在支持为 schedule 指定 IANA timezone，本仓库显式使用 `Asia/Shanghai`，避免再靠 UTC 心算。

## Secrets

只在 GitHub Actions Secrets 保存：

- `KEEP_MOBILE`
- `KEEP_PASSWORD`

凭据不写入仓库、不写入 raw、不写入日志。同步脚本日志只输出数据类型和数量。

## 本地运行

```bash
export KEEP_MOBILE='...'
export KEEP_PASSWORD='...'
python3 sync_keep.py
python3 build_intelligence.py
```

首次建立完整历史 raw archive：

```bash
python3 sync_keep.py --refresh-raw
python3 build_intelligence.py
```

## 设计边界

这个项目不负责自动做医疗诊断，也不把一次算法统计包装成健康结论。它负责提供可靠的个人运动事实层与可审计时间序列。未来 Apple Health、体重、睡眠、静息心率等数据源应通过新的 Adapter 接入统一模型，而不是把 Keep-specific 结构继续扩散到整个系统。
