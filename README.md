# Keep Sync — Keep 运动数据同步

从 Keep APP 自动同步所有运动数据到 GitHub 仓库，供 Agent 读取和调用。

## 数据格式

### Agent 摘要 `data/agent_summary.json`

**Agent 默认优先读取这个文件。** 它是由 `generate_summary.py` 从完整运动数据自动生成的轻量摘要，避免每次为了回答趋势问题读取体积较大的 `data/data.json`。

目前以 `VirtualRide` 作为主要运动类型，并标记为“动感单车 / 室内骑行”。摘要包含：

- 全部运动次数、日期范围和运动类型分布；
- 动感单车历史总次数、总分钟数、单次平均时长和平均心率；
- 按年、按月的动感单车次数、总时长和平均心率；
- 最近 20 次动感单车；
- 最近 20 条全部运动记录。

对于“最近运动得怎么样”“今年骑了多少”“哪几个月最规律”“最近一次动感单车是什么时候”这类问题，先读 `data/agent_summary.json`；只有需要更细的单次记录、轨迹或摘要中没有的字段时，再读完整数据。

### SQLite 数据库 `data/data.db`

```sql
CREATE TABLE activities (
  run_id INTEGER PRIMARY KEY,
  name VARCHAR,
  distance FLOAT,
  moving_time DATETIME,
  elapsed_time DATETIME,
  type VARCHAR,
  subtype VARCHAR,
  start_date VARCHAR,
  start_date_local VARCHAR,
  location_country VARCHAR,
  summary_polyline VARCHAR,
  average_heartrate FLOAT,
  average_speed FLOAT,
  elevation_gain FLOAT
);
```

### 完整 JSON `data/data.json`

包含所有活动的完整导出数据。适合需要 session-level 明细的分析，不再作为 Agent 日常趋势查询的首选入口。

### GPX 文件

GPS 轨迹文件保存在 `data/` 中，可用于地图和室外运动轨迹分析。

## Agent 调用方式

### 默认：读取轻量摘要

```bash
cat data/agent_summary.json
```

### 查看动感单车月度趋势

```bash
jq '.primary_activity.by_month' data/agent_summary.json
```

### 查看最近动感单车

```bash
jq '.primary_activity.recent_sessions' data/agent_summary.json
```

### 需要完整明细时

```bash
jq '.[] | select(.type == "VirtualRide")' data/data.json
```

## 生成摘要

```bash
python3 generate_summary.py
```

脚本兼容当前数据中两种运动时长表示：数值秒数，以及历史数据里的 `1970-01-01 HH:MM:SS` 格式。

## 配置

在 GitHub Secrets 中设置：

- `KEEP_MOBILE` — Keep 登录手机号
- `KEEP_PASSWORD` — Keep 登录密码

## 自动同步

GitHub Actions 每天自动从 Keep 拉取数据，然后运行 `generate_summary.py` 刷新 `data/agent_summary.json`，最后把数据变化提交回仓库。

手动触发：Settings → Actions → Sync Keep Data → Run workflow

> GitHub Actions 的 cron 使用 UTC；当前 `0 22 * * *` 对应北京时间次日 06:00。

## 当前历史数据

当前仓库已有约 220 条运动记录（2018–2026），其中绝大多数为 `VirtualRide`（动感单车 / 室内骑行）。精确统计以后以自动生成的 `data/agent_summary.json` 为准，README 不再手工维护具体计数，避免数据更新后文档过期。
