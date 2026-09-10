#!/usr/bin/env python3
"""
Keep 运动数据同步脚本
从 Keep APP 拉取所有运动数据，存储为 SQLite + JSON
供 Agent 读取和调用

用法:
  python3 sync_keep.py                    # 同步全部类型
  python3 sync_keep.py --dry-run         # 只看新增条数，不写入

环境变量:
  KEEP_MOBILE     - Keep 登录手机号
  KEEP_PASSWORD   - Keep 登录密码
"""

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import zlib
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from xml.dom import minidom
import xml.etree.ElementTree as ET

import eviltransform
import gpxpy
import polyline
import requests
from Crypto.Cipher import AES

# ============ 配置 ============
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "data.db")
JSON_PATH = os.path.join(DATA_DIR, "data.json")
GPX_DIR = os.path.join(DATA_DIR, "GPX_OUT")

KEEP_SPORT_TYPES = ["running", "hiking", "cycling", "training"]
KEEP2STRAVA = {
    "outdoorWalking": "Walk",
    "outdoorRunning": "Run",
    "outdoorCycling": "Ride",
    "indoorRunning": "VirtualRun",
    "indoorCycling": "VirtualRide",
    "mountaineering": "Hiking",
}
LOGIN_API = "https://api.gotokeep.com/v1.1/users/login"
RUN_DATA_API = "https://api.gotokeep.com/pd/v3/stats/detail?dateUnit=all&type={sport_type}&lastDate={last_date}"
RUN_LOG_API = "https://api.gotokeep.com/pd/v3/{sport_type}log/{run_id}"
TRANS_GCJ02_TO_WGS84 = True


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(GPX_DIR, exist_ok=True)


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            run_id INTEGER PRIMARY KEY,
            name TEXT, distance REAL, moving_time REAL, elapsed_time REAL,
            type TEXT, subtype TEXT, start_date TEXT, start_date_local TEXT,
            location_country TEXT, summary_polyline TEXT, average_heartrate REAL,
            average_speed REAL, elevation_gain REAL, map TEXT
        )
    """)
    conn.commit()
    return conn


def adjust_time(dt, tz_name):
    """调整时区"""
    if not tz_name:
        return dt
    try:
        from datetime import timezone as tz
        offset_hours = int(tz_name) if tz_name.lstrip('-').isdigit() else 0
        return dt + timedelta(hours=offset_hours)
    except:
        return dt


def login(session, mobile, password):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:78.0) Gecko/20100101 Firefox/78.0",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    data = {"mobile": mobile, "password": password}
    r = session.post(LOGIN_API, headers=headers, data=data)
    if r.ok:
        token = r.json()["data"]["token"]
        headers["Authorization"] = f"Bearer {token}"
        return session, headers
    return None, None


def get_all_run_ids(session, headers, sport_type):
    """获取所有运动记录 ID"""
    last_date = 0
    result = []
    while True:
        r = session.get(RUN_DATA_API.format(sport_type=sport_type, last_date=last_date), headers=headers)
        if r.ok:
            run_logs = r.json()["data"]["records"]
            for i in run_logs:
                logs = [j["stats"] for j in i["logs"]]
                result.extend(k["id"] for k in logs if not k.get("isDoubtful"))
            last_date = r.json()["data"]["lastTimestamp"]
            if not last_date:
                break
            time.sleep(1)
    return result


def decode_data(text, is_geo=False):
    """解码 Keep 数据"""
    _bytes = base64.b64decode(text)
    key = "NTZmZTU5OzgyZjpkODczYw=="
    iv = "MjM0Njg5MjQzMjkyMDMwMA=="
    if is_geo:
        cipher = AES.new(base64.b64decode(key), AES.MODE_CBC, base64.b64decode(iv))
        _bytes = cipher.decrypt(_bytes)
    return json.loads(zlib.decompress(_bytes, 16 + zlib.MAX_WBITS))


def parse_run(session, headers, run_id, sport_type, old_gpx_ids):
    """解析单条运动记录"""
    r = session.get(RUN_LOG_API.format(sport_type=sport_type, run_id=run_id), headers=headers)
    if not r.ok:
        return None
    
    run_data = r.json()["data"]
    keep_id = run_data["id"].split("_")[1]
    
    start_time = run_data["startTime"]
    avg_hr = None
    elevation_gain = None
    run_points_data = []
    
    # 心率数据
    if run_data.get("heartRate"):
        avg_hr = run_data["heartRate"].get("averageHeartRate")
        if avg_hr and avg_hr < 0:
            avg_hr = None
    
    # GPS 数据
    if run_data.get("geoPoints"):
        run_points_data = decode_data(run_data["geoPoints"], True)
        run_points_data_gpx = run_points_data
        
        if TRANS_GCJ02_TO_WGS84:
            run_points_data = [
                list(eviltransform.gcj2wgs(p["latitude"], p["longitude"]))
                for p in run_points_data
            ]
            for i, p in enumerate(run_points_data_gpx):
                p["latitude"] = run_points_data[i][0]
                p["longitude"] = run_points_data[i][1]
        
        # 生成 GPX
        data_type = run_data.get("dataType", "")
        sport = KEEP2STRAVA.get(data_type, "Workout")
        if data_type.startswith("outdoor") or data_type == "mountaineering":
            if keep_id not in old_gpx_ids:
                gpx = create_gpx(run_points_data_gpx, start_time, sport)
                save_gpx(gpx, keep_id)
    
    polyline_str = polyline.encode(run_points_data) if run_points_data else ""
    start_date = datetime.fromtimestamp(start_time // 1000, tz=timezone.utc)
    tz_name = run_data.get("timezone", "")
    start_date_local = adjust_time(start_date, tz_name)
    
    distance = run_data.get("distance") or 0
    duration = run_data.get("duration") or 0
    avg_speed = distance / duration if duration > 0 else 0
    
    return {
        "run_id": int(keep_id),
        "name": f"{sport} from keep",
        "distance": distance,
        "moving_time": duration,
        "type": sport,
        "subtype": sport,
        "start_date": start_date.strftime("%Y-%m-%d %H:%M:%S"),
        "start_date_local": start_date_local.strftime("%Y-%m-%d %H:%M:%S"),
        "location_country": str(run_data.get("region", "")),
        "summary_polyline": polyline_str,
        "average_heartrate": int(avg_hr) if avg_hr else None,
        "average_speed": avg_speed,
        "elevation_gain": elevation_gain,
        "map": polyline_str[:200] if polyline_str else "",
    }


def create_gpx(points_data, start_time, sport_type):
    """创建 GPX 文件"""
    gpx = gpxpy.gpx.GPX()
    gpx_track = gpxpy.gpx.GPXTrack()
    gpx_track.name = "keep"
    gpx_track.type = sport_type
    gpx.tracks.append(gpx_track)
    gpx_segment = gpxpy.gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)
    
    ts_threshold = 3_600_000
    start_ts = 0 if (points_data and points_data[0].get("timestamp", 0) > ts_threshold) else start_time
    
    for p in points_data:
        ts = p.get("timestamp", 0)
        time = datetime.fromtimestamp(start_ts // 1000 + ts // 10, tz=timezone.utc)
        point = gpxpy.gpx.GPXTrackPoint(
            latitude=p["latitude"], longitude=p["longitude"], time=time,
            elevation=p.get("altitude")
        )
        if p.get("hr"):
            ext = ET.fromstring(f'<gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><gpxtpx:hr>{p["hr"]}</gpxtpx:hr></gpxtpx:TrackPointExtension>')
            point.extensions.append(ext)
        gpx_segment.points.append(point)
    return gpx


def save_gpx(gpx, run_id):
    """保存 GPX 文件"""
    with open(os.path.join(GPX_DIR, f"{run_id}.gpx"), "w", encoding="utf-8") as f:
        f.write(gpx.to_xml())


def run_sync(mobile, password):
    """执行同步"""
    print(f"登录 Keep: {mobile[:3]}***{mobile[-4:]}")
    s = requests.Session()
    s, headers = login(s, mobile, password)
    if not headers:
        print("登录失败!")
        return 0
    
    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT run_id FROM activities")
    existing_ids = {str(row[0]) for row in cur.fetchall()}
    old_gpx_ids = set(f.split(".")[0] for f in os.listdir(GPX_DIR) if not f.startswith("."))
    
    new_count = 0
    for sport_type in KEEP_SPORT_TYPES:
        print(f"同步 {sport_type}...")
        run_ids = get_all_run_ids(s, headers, sport_type)
        new_ids = [rid for rid in run_ids if rid.split("_")[1] not in existing_ids]
        print(f"  {len(run_ids)} 总记录, {len(new_ids)} 新增")
        
        for rid in new_ids:
            print(f"  处理 {rid}")
            try:
                data = parse_run(s, headers, rid, sport_type, old_gpx_ids)
                if data:
                    cur.execute("""INSERT OR REPLACE INTO activities 
                        (run_id, name, distance, moving_time, type, subtype, start_date, 
                         start_date_local, location_country, summary_polyline, average_heartrate, 
                         average_speed, elevation_gain, map)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (data["run_id"], data["name"], data["distance"], data["moving_time"],
                         data["type"], data["subtype"], data["start_date"], data["start_date_local"],
                         data["location_country"], data["summary_polyline"], data["average_heartrate"],
                         data["average_speed"], data["elevation_gain"], data["map"]))
                    new_count += 1
                    existing_ids.add(str(data["run_id"]))
            except Exception as e:
                print(f"  错误: {e}")
            time.sleep(0.5)
    
    conn.commit()
    
    # 导出 JSON
    cur.execute("SELECT * FROM activities ORDER BY start_date DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
    
    conn.close()
    return new_count


def print_summary():
    """打印统计"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM activities")
    total = cur.fetchone()[0]
    cur.execute("SELECT SUM(distance) FROM activities")
    dist = cur.fetchone()[0] or 0
    cur.execute("SELECT type, COUNT(*) FROM activities GROUP BY type ORDER BY COUNT(*) DESC")
    types = cur.fetchall()
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"  同步完成! 共 {total} 条记录, {dist/1000:.1f} km")
    print(f"  按类型: " + ", ".join(f"{t}({c})" for t, c in types))
    print(f"{'='*50}")
    print(f"  数据库: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")


if __name__ == "__main__":
    ensure_dirs()
    
    mobile = os.environ.get("KEEP_MOBILE")
    password = os.environ.get("KEEP_PASSWORD")
    
    if not mobile or not password:
        print("错误: 请设置环境变量 KEEP_MOBILE 和 KEEP_PASSWORD")
        sys.exit(1)
    
    count = run_sync(mobile, password)
    if count > 0:
        print_summary()
    else:
        print("没有新增数据")
