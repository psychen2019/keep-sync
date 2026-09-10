# Keep Sync — Keep 运动数据同步

从 Keep APP 自动同步所有运动数据到 GitHub 仓库，供 Agent 读取和调用。

## 数据格式

### SQLite 数据库 `data/data.db`
```sql
CREATE TABLE activities (
  run_id INTEGER PRIMARY KEY,
  name VARCHAR,           -- 标题（如"傍晚跑步"）
  distance FLOAT,         -- 距离（米）
  moving_time DATETIME,   -- 运动时长
  elapsed_time DATETIME,  --  elapsed time
  type VARCHAR,           -- Run/Walk/VirtualRide/Hiking
  subtype VARCHAR,
  start_date VARCHAR,     -- UTC 时间
  start_date_local VARCHAR, -- 本地时间
  location_country VARCHAR, -- 位置信息
  summary_polyline VARCHAR, -- GPX 轨迹编码
  average_heartrate FLOAT, -- 平均心率
  average_speed FLOAT,    -- 平均配速
  elevation_gain FLOAT    -- 爬升高度
);
```

### JSON 文件 `data/data.json`
Agent 直接读取此文件即可，包含所有活动数据。

### GPX 文件 `data/GPX_OUT/`
GPS 轨迹文件，可用于地图展示。

## Agent 调用方式

### 读取全部数据
```bash
cat data/data.json | jq '.[] | {date: .start_date_local, type: .type, distance: .distance, hr: .average_heartrate}'
```

### 查询某年数据
```bash
cat data/data.json | jq '[.[] | select(.start_date_local | startswith("2025"))]'
```

### 统计摘要
```bash
python3 -c "
import json
with open('data/data.json') as f:
    d = json.load(f)
print(f'总记录: {len(d)}')
types = set(a['type'] for a in d)
print(f'类型: {types}')
"
```

## 配置

在 GitHub Secrets 中设置：
- `KEEP_MOBILE` — Keep 登录手机号
- `KEEP_PASSWORD` — Keep 登录密码

## 自动同步

仓库已配置 GitHub Actions，每天 22:00 自动从 Keep 拉取新数据并推送到仓库。

手动触发：Settings → Actions → Sync Keep Data → Run workflow

## 历史数据

当前已同步 220 条记录（2018-2026），包含：
- Run: 34 条（室外跑步）
- VirtualRide: 179 条（室内骑行/训练）
- Walk: 6 条
- Hiking: 1 条
