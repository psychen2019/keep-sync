#!/usr/bin/env python3
"""Generate a compact, agent-friendly summary from Keep's exported activity JSON."""

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "data.json"
OUTPUT = ROOT / "data" / "agent_summary.json"
PRIMARY_TYPE = "VirtualRide"
RECENT_LIMIT = 20


def duration_seconds(value):
    """Support both numeric seconds and legacy `1970-01-01 HH:MM:SS` values."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text)
        return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1_000_000
    except ValueError:
        return 0.0


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def rounded(value, digits=1):
    return round(value, digits) if value is not None else None


def bucket_stats(items):
    seconds = [duration_seconds(a.get("moving_time")) for a in items]
    heart_rates = [float(a["average_heartrate"]) for a in items if a.get("average_heartrate") not in (None, "")]
    return {
        "sessions": len(items),
        "total_minutes": rounded(sum(seconds) / 60),
        "average_minutes_per_session": rounded(sum(seconds) / 60 / len(items)) if items else 0,
        "average_heart_rate": rounded(sum(heart_rates) / len(heart_rates)) if heart_rates else None,
        "heart_rate_samples": len(heart_rates),
    }


def compact_activity(a):
    return {
        "date": a.get("start_date_local") or a.get("start_date"),
        "type": a.get("type"),
        "duration_minutes": rounded(duration_seconds(a.get("moving_time")) / 60),
        "average_heart_rate": a.get("average_heartrate"),
        "distance_km": rounded(float(a.get("distance") or 0) / 1000, 2),
    }


def main():
    with SOURCE.open(encoding="utf-8") as f:
        activities = json.load(f)

    dated = [(parse_date(a.get("start_date_local") or a.get("start_date")), a) for a in activities]
    dated = [(dt, a) for dt, a in dated if dt is not None]
    dated.sort(key=lambda pair: pair[0], reverse=True)

    types = Counter(a.get("type") or "Unknown" for _, a in dated)
    primary = [(dt, a) for dt, a in dated if a.get("type") == PRIMARY_TYPE]

    monthly = defaultdict(list)
    yearly = defaultdict(list)
    for dt, a in primary:
        monthly[dt.strftime("%Y-%m")].append(a)
        yearly[dt.strftime("%Y")].append(a)

    latest_dt = dated[0][0] if dated else None
    latest_primary_dt = primary[0][0] if primary else None

    summary = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "data/data.json",
        "agent_note": "Prefer this compact file for trend questions. Read data/data.json only when session-level detail beyond recent_activity is required.",
        "overall": {
            "sessions": len(dated),
            "first_activity_date": dated[-1][0].date().isoformat() if dated else None,
            "latest_activity_date": latest_dt.date().isoformat() if latest_dt else None,
            "types": dict(types.most_common()),
        },
        "primary_activity": {
            "type": PRIMARY_TYPE,
            "label": "动感单车 / 室内骑行",
            "sessions": len(primary),
            "share_of_all_sessions_percent": rounded(len(primary) * 100 / len(dated)) if dated else 0,
            "first_date": primary[-1][0].date().isoformat() if primary else None,
            "latest_date": latest_primary_dt.date().isoformat() if latest_primary_dt else None,
            "all_time": bucket_stats([a for _, a in primary]),
            "by_year": {key: bucket_stats(yearly[key]) for key in sorted(yearly)},
            "by_month": {key: bucket_stats(monthly[key]) for key in sorted(monthly)},
            "recent_sessions": [compact_activity(a) for _, a in primary[:RECENT_LIMIT]],
        },
        "recent_activity": [compact_activity(a) for _, a in dated[:RECENT_LIMIT]],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {OUTPUT}: {len(dated)} activities, {len(primary)} {PRIMARY_TYPE} sessions")


if __name__ == "__main__":
    main()
