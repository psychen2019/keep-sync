#!/usr/bin/env python3
"""
Keep 运动数据同步脚本
从 Keep APP 拉取所有运动数据，存储为 SQLite + JSON
供 Agent 读取和调用

用法:
  python3 sync_keep.py                    # 同步全部类型
  python3 sync_keep.py --types running    # 只同步跑步
  python3 sync_keep.py --dry-run          # 只看新增条数，不写入

输出:
  data.db       - SQLite 数据库（主存储）
  data.json     - JSON 格式（供 Agent 直接读取）
  GPX_OUT/      - GPX 轨迹文件
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# 添加 run_page 到路径
sys.path.insert(0, os.path.dirname(__file__))

from run_page.keep_sync import KEEP_SPORT_TYPES, run_keep_sync
from run_page.config import SQL_FILE, JSON_FILE

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "data.db")
JSON_PATH = os.path.join(DATA_DIR, "data.json")
GPX_DIR = os.path.join(DATA_DIR, "GPX_OUT")


def ensure_dirs():
    """确保输出目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(GPX_DIR, exist_ok=True)
    # 临时覆盖 config 中的路径
    import run_page.config as cfg
    cfg.SQL_FILE = DB_PATH
    cfg.JSON_FILE = JSON_FILE
    cfg.GPX_FOLDER = GPX_DIR


def export_json():
    """将数据库导出为 JSON"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activities ORDER BY start_date DESC")
    cols = [desc[0] for desc in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    conn.close()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
    return len(rows)


def print_summary():
    """打印数据统计摘要"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM activities")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT strftime('%Y', start_date) as yr, COUNT(*) FROM activities GROUP BY yr ORDER BY yr DESC")
    by_year = cursor.fetchall()
    cursor.execute("SELECT type, COUNT(*) FROM activities GROUP BY type ORDER BY COUNT(*) DESC")
    by_type = cursor.fetchall()
    cursor.execute("SELECT SUM(distance), SUM(moving_time), AVG(average_heartrate) FROM activities")
    stats = cursor.fetchone()
    conn.close()

    print(f"\n{'='*50}")
    print(f"  Keep 数据同步完成")
    print(f"{'='*50}")
    print(f"  总记录数: {total}")
    print(f"  总距离:   {stats[0]/1000:.1f} km" if stats[0] else "  总距离:   N/A")
    print(f"  总时长:   {stats[1]/60:.0f} min" if stats[1] else "  总时长:   N/A")
    print(f"  平均心率: {int(stats[2])} bpm" if stats[2] else "  平均心率: N/A")
    print(f"\n  按年份:")
    for yr, cnt in by_year:
        print(f"    {yr}: {cnt} 条")
    print(f"\n  按类型:")
    for t, cnt in by_type:
        print(f"    {t}: {cnt} 条")
    print(f"{'='*50}\n")
    print(f"  数据库: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")
    print(f"  GPX:    {GPX_DIR}/")


def main():
    parser = argparse.ArgumentParser(description="Keep 运动数据同步")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    ensure_dirs()

    # 从环境变量读取 Keep 账号
    mobile = os.environ.get("KEEP_MOBILE")
    password = os.environ.get("KEEP_PASSWORD")

    if not mobile or not password:
        print("错误: 请设置 KEEP_MOBILE 和 KEEP_PASSWORD 环境变量")
        print("  export KEEP_MOBILE=你的手机号")
        print("  export KEEP_PASSWORD=你的密码")
        sys.exit(1)

    print(f"正在同步 Keep 数据... (手机号: {mobile[:3]}***{mobile[-4:]})")
    print(f"数据库: {DB_PATH}")

    # 运行同步
    run_keep_sync(mobile, password, KEEP_SPORT_TYPES, with_gpx=True)

    # 导出 JSON
    count = export_json()
    print_summary()
    print(f"  Agent 读取: cat {JSON_PATH} | jq '.'")


if __name__ == "__main__":
    main()
