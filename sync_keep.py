#!/usr/bin/env python3
"""Keep ingestion layer: fetch, preserve raw responses, normalize core fields, export SQLite/JSON."""

import argparse
import base64
import json
import os
import sqlite3
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import eviltransform
import gpxpy
import polyline
import requests
from Crypto.Cipher import AES
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "data.db"
JSON_PATH = DATA_DIR / "data.json"
RAW_DIR = DATA_DIR / "raw"
GPX_DIR = DATA_DIR / "GPX_OUT"

KEEP_SPORT_TYPES = ("running", "hiking", "cycling", "training")
KEEP2ACTIVITY = {
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
    for path in (DATA_DIR, RAW_DIR, GPX_DIR):
        path.mkdir(parents=True, exist_ok=True)


def make_session():
    session = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            run_id INTEGER PRIMARY KEY,
            keep_log_id TEXT,
            keep_sport_type TEXT,
            keep_data_type TEXT,
            name TEXT,
            distance REAL,
            moving_time REAL,
            elapsed_time REAL,
            type TEXT,
            subtype TEXT,
            start_date TEXT,
            start_date_local TEXT,
            timezone TEXT,
            location_country TEXT,
            summary_polyline TEXT,
            average_heartrate REAL,
            max_heartrate REAL,
            calories REAL,
            average_speed REAL,
            elevation_gain REAL,
            map TEXT,
            raw_path TEXT,
            synced_at TEXT
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    migrations = {
        "keep_log_id": "TEXT", "keep_sport_type": "TEXT", "keep_data_type": "TEXT",
        "timezone": "TEXT", "max_heartrate": "REAL", "calories": "REAL",
        "raw_path": "TEXT", "synced_at": "TEXT",
    }
    for column, sql_type in migrations.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {column} {sql_type}")
    conn.commit()
    return conn


def login(session, mobile, password):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    response = session.post(LOGIN_API, headers=headers, data={"mobile": mobile, "password": password}, timeout=30)
    response.raise_for_status()
    token = response.json()["data"]["token"]
    headers["Authorization"] = f"Bearer {token}"
    return headers


def get_all_run_ids(session, headers, sport_type):
    last_date, result, seen_pages = 0, [], set()
    while True:
        response = session.get(RUN_DATA_API.format(sport_type=sport_type, last_date=last_date), headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()["data"]
        for record in payload.get("records", []):
            for log in record.get("logs", []):
                stats = log.get("stats", {})
                if stats.get("id") and not stats.get("isDoubtful"):
                    result.append(stats["id"])
        next_date = payload.get("lastTimestamp")
        if not next_date or next_date == last_date or next_date in seen_pages:
            break
        seen_pages.add(next_date)
        last_date = next_date
        time.sleep(0.5)
    return list(dict.fromkeys(result))


def decode_data(text, is_geo=False):
    raw = base64.b64decode(text)
    if is_geo:
        key = base64.b64decode("NTZmZTU5OzgyZjpkODczYw==")
        iv = base64.b64decode("MjM0Njg5MjQzMjkyMDMwMA==")
        raw = AES.new(key, AES.MODE_CBC, iv).decrypt(raw)
    return json.loads(zlib.decompress(raw, 16 + zlib.MAX_WBITS))


def local_datetime(start_ms, tz_value):
    utc_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    try:
        offset = int(tz_value) if str(tz_value).lstrip("-").isdigit() else 0
    except (TypeError, ValueError):
        offset = 0
    return utc_dt, utc_dt + timedelta(hours=offset)


def first_number(obj, paths):
    for path in paths:
        value = obj
        try:
            for key in path:
                value = value[key]
        except (KeyError, TypeError):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return value
    return None


def save_raw(sport_type, keep_id, payload):
    target_dir = RAW_DIR / sport_type
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{keep_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(target.relative_to(ROOT))


def create_gpx(points, start_time, sport_type):
    gpx = gpxpy.gpx.GPX(); track = gpxpy.gpx.GPXTrack(); segment = gpxpy.gpx.GPXTrackSegment()
    track.name, track.type = "keep", sport_type
    gpx.tracks.append(track); track.segments.append(segment)
    threshold = 3_600_000
    start_ts = 0 if points and points[0].get("timestamp", 0) > threshold else start_time
    for p in points:
        ts = p.get("timestamp", 0)
        point = gpxpy.gpx.GPXTrackPoint(
            latitude=p["latitude"], longitude=p["longitude"],
            time=datetime.fromtimestamp(start_ts / 1000 + ts / 10, tz=timezone.utc), elevation=p.get("altitude"))
        if p.get("hr"):
            point.extensions.append(ET.fromstring(
                f'<gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><gpxtpx:hr>{p["hr"]}</gpxtpx:hr></gpxtpx:TrackPointExtension>'))
        segment.points.append(point)
    return gpx


def parse_run(session, headers, run_id, sport_type, refresh_raw=False):
    response = session.get(RUN_LOG_API.format(sport_type=sport_type, run_id=run_id), headers=headers, timeout=30)
    response.raise_for_status()
    envelope = response.json()
    run_data = envelope["data"]
    keep_id = str(run_data["id"]).split("_")[-1]
    raw_path = save_raw(sport_type, keep_id, envelope)

    start_time = run_data.get("startTime") or 0
    data_type = run_data.get("dataType", "")
    activity_type = KEEP2ACTIVITY.get(data_type, "Workout")
    points, polyline_str = [], ""
    if run_data.get("geoPoints"):
        points = decode_data(run_data["geoPoints"], True)
        gpx_points = [dict(p) for p in points]
        if TRANS_GCJ02_TO_WGS84:
            coords = [eviltransform.gcj2wgs(p["latitude"], p["longitude"]) for p in points]
            for p, (lat, lon) in zip(points, coords):
                p["latitude"], p["longitude"] = lat, lon
            for p, (lat, lon) in zip(gpx_points, coords):
                p["latitude"], p["longitude"] = lat, lon
        polyline_str = polyline.encode([[p["latitude"], p["longitude"]] for p in points])
        if data_type.startswith("outdoor") or data_type == "mountaineering":
            (GPX_DIR / f"{keep_id}.gpx").write_text(create_gpx(gpx_points, start_time, activity_type).to_xml(), encoding="utf-8")

    distance = float(run_data.get("distance") or 0)
    duration = float(run_data.get("duration") or 0)
    heart = run_data.get("heartRate") or {}
    avg_hr = first_number(run_data, [("heartRate", "averageHeartRate"), ("averageHeartRate",)])
    max_hr = first_number(run_data, [("heartRate", "maxHeartRate"), ("maxHeartRate",)])
    calories = first_number(run_data, [("calories",), ("calorie",), ("kilocalorie",), ("stats", "calories")])
    utc_dt, local_dt = local_datetime(start_time, run_data.get("timezone", ""))

    return {
        "run_id": int(keep_id), "keep_log_id": str(run_data.get("id", run_id)),
        "keep_sport_type": sport_type, "keep_data_type": data_type,
        "name": f"{activity_type} from keep", "distance": distance,
        "moving_time": duration, "elapsed_time": duration, "type": activity_type, "subtype": activity_type,
        "start_date": utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "start_date_local": local_dt.strftime("%Y-%m-%d %H:%M:%S"), "timezone": str(run_data.get("timezone", "")),
        "location_country": str(run_data.get("region", "")), "summary_polyline": polyline_str,
        "average_heartrate": avg_hr, "max_heartrate": max_hr, "calories": calories,
        "average_speed": distance / duration if duration else 0, "elevation_gain": None,
        "map": polyline_str[:200], "raw_path": raw_path,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def upsert(conn, data):
    columns = list(data)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != "run_id")
    conn.execute(f"INSERT INTO activities ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(run_id) DO UPDATE SET {updates}", [data[c] for c in columns])


def export_json(conn):
    cur = conn.execute("SELECT * FROM activities ORDER BY start_date DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run_sync(mobile, password, refresh_raw=False):
    ensure_dirs(); session = make_session(); headers = login(session, mobile, password); conn = init_db()
    existing = {str(r[0]) for r in conn.execute("SELECT run_id FROM activities")}
    changed = 0
    for sport_type in KEEP_SPORT_TYPES:
        ids = get_all_run_ids(session, headers, sport_type)
        targets = ids if refresh_raw else [rid for rid in ids if str(rid).split("_")[-1] not in existing]
        print(f"{sport_type}: {len(ids)} total, {len(targets)} to fetch")
        for rid in targets:
            try:
                data = parse_run(session, headers, rid, sport_type, refresh_raw)
                upsert(conn, data); existing.add(str(data["run_id"])); changed += 1
            except Exception as exc:
                print(f"WARN {sport_type}/{rid}: {exc}")
            time.sleep(0.25)
        conn.commit()
    export_json(conn); total = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]; conn.close()
    print(f"Keep sync complete: {total} activities, {changed} fetched/updated")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Keep activity ingestion")
    parser.add_argument("mobile", nargs="?", default=os.getenv("KEEP_MOBILE"))
    parser.add_argument("password", nargs="?", default=os.getenv("KEEP_PASSWORD"))
    parser.add_argument("--refresh-raw", action="store_true", help="Re-fetch all historical detail responses and preserve raw JSON")
    args = parser.parse_args()
    if not args.mobile or not args.password:
        parser.error("Keep credentials are required via arguments or KEEP_MOBILE/KEEP_PASSWORD")
    run_sync(args.mobile, args.password, args.refresh_raw)


if __name__ == "__main__":
    main()
