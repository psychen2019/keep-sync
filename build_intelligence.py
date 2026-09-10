#!/usr/bin/env python3
"""Build AI-readable activity context and longitudinal metrics from normalized Keep data."""

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "data.json"
CONTEXT = ROOT / "data" / "agent_context.json"
METRICS = ROOT / "data" / "metrics"
NORMALIZED = ROOT / "data" / "normalized" / "activities.jsonl"
PRIMARY = "VirtualRide"


def dt(value):
    try: return datetime.fromisoformat(str(value))
    except (TypeError, ValueError): return None


def seconds(value):
    if isinstance(value, (int, float)): return max(0.0, float(value))
    try:
        x = datetime.fromisoformat(str(value)); return x.hour * 3600 + x.minute * 60 + x.second
    except (TypeError, ValueError): return 0.0


def num(value):
    try:
        x = float(value); return x if math.isfinite(x) else None
    except (TypeError, ValueError): return None


def compact(a):
    return {"id": a.get("run_id"), "date": a.get("start_date_local") or a.get("start_date"),
            "type": a.get("type"), "keep_data_type": a.get("keep_data_type"),
            "minutes": round(seconds(a.get("moving_time"))/60, 1),
            "avg_hr": num(a.get("average_heartrate")), "max_hr": num(a.get("max_heartrate")),
            "calories": num(a.get("calories")), "distance_km": round((num(a.get("distance")) or 0)/1000, 2)}


def stats(items):
    if not items: return {"sessions": 0, "total_minutes": 0, "median_session_minutes": None, "avg_hr": None}
    mins = [seconds(a.get("moving_time"))/60 for a in items]
    hrs = [num(a.get("average_heartrate")) for a in items]; hrs = [x for x in hrs if x is not None]
    return {"sessions": len(items), "total_minutes": round(sum(mins),1),
            "median_session_minutes": round(median(mins),1),
            "avg_hr": round(sum(hrs)/len(hrs),1) if hrs else None,
            "hr_coverage_percent": round(100*len(hrs)/len(items),1)}


def window(items, now, days):
    cutoff = now - timedelta(days=days)
    return [a for a in items if a["_dt"] >= cutoff]


def longest_gap(items):
    dates = sorted({a["_dt"].date() for a in items})
    if len(dates) < 2: return None
    gaps = [(dates[i]-dates[i-1]).days for i in range(1,len(dates))]
    return max(gaps) if gaps else None


def active_weeks(items, weeks=12):
    if not items: return 0
    now = max(a["_dt"] for a in items)
    cutoff = now - timedelta(weeks=weeks)
    return len({a["_dt"].strftime("%G-W%V") for a in items if a["_dt"] >= cutoff})


def baseline(monthly_primary):
    counts = [v["sessions"] for v in monthly_primary.values() if v["sessions"] > 0]
    mins = [v["total_minutes"] for v in monthly_primary.values() if v["sessions"] > 0]
    return {"active_month_median_sessions": round(median(counts),1) if counts else None,
            "active_month_median_minutes": round(median(mins),1) if mins else None,
            "note": "Personal descriptive baseline from months with at least one primary-activity session; not a medical target."}


def main():
    activities = json.loads(SOURCE.read_text(encoding="utf-8"))
    valid=[]
    for a in activities:
        when=dt(a.get("start_date_local") or a.get("start_date"))
        if when:
            b=dict(a); b["_dt"]=when; valid.append(b)
    valid.sort(key=lambda a:a["_dt"], reverse=True)
    if not valid: raise SystemExit("No dated activities")
    now=datetime.now().astimezone().replace(tzinfo=None)
    primary=[a for a in valid if a.get("type")==PRIMARY]

    monthly_all=defaultdict(list); monthly_primary=defaultdict(list); yearly=defaultdict(list)
    for a in valid:
        monthly_all[a["_dt"].strftime("%Y-%m")].append(a)
        yearly[a["_dt"].strftime("%Y")].append(a)
        if a.get("type")==PRIMARY: monthly_primary[a["_dt"].strftime("%Y-%m")].append(a)
    monthly_primary_stats={k:stats(v) for k,v in sorted(monthly_primary.items())}

    latest=valid[0]["_dt"]; latest_primary=primary[0]["_dt"] if primary else None
    windows={}
    for days in (7,30,90,365):
        all_w=window(valid,now,days); pri_w=window(primary,now,days)
        windows[str(days)]={"all":stats(all_w),"primary":stats(pri_w)}

    context={
        "schema_version":2,
        "generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose":"Compact factual context for AI. Prefer this before raw/session data.",
        "semantics":{"primary_activity":{"type":PRIMARY,"label":"动感单车 / 室内骑行","keep_data_type":"indoorCycling"},
                     "epistemic_rule":"facts and descriptive statistics only; causal explanations are hypotheses, not stored facts"},
        "current_state":{"latest_activity":compact(valid[0]),"days_since_any_activity":max(0,(now.date()-latest.date()).days),
                         "latest_primary_activity":compact(primary[0]) if primary else None,
                         "days_since_primary_activity":max(0,(now.date()-latest_primary.date()).days) if latest_primary else None},
        "windows":windows,
        "all_time":{"all":stats(valid),"primary":stats(primary),"types":dict(Counter(a.get("type") or "Unknown" for a in valid)),
                    "first_activity_date":valid[-1]["_dt"].date().isoformat(),"longest_gap_days":longest_gap(valid),
                    "primary_longest_gap_days":longest_gap(primary),"primary_active_weeks_last_12":active_weeks(primary,12)},
        "personal_baseline":baseline(monthly_primary_stats),
        "recent_primary_sessions":[compact(a) for a in primary[:12]],
        "recent_sessions":[compact(a) for a in valid[:12]],
        "drill_down":{"monthly":"data/metrics/monthly.json","yearly":"data/metrics/yearly.json",
                      "normalized":"data/normalized/activities.jsonl","raw":"data/raw/<sport_type>/<id>.json"}
    }

    METRICS.mkdir(parents=True,exist_ok=True); NORMALIZED.parent.mkdir(parents=True,exist_ok=True)
    CONTEXT.write_text(json.dumps(context,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    monthly={k:{"all":stats(monthly_all[k]),"primary":stats(monthly_primary.get(k,[]))} for k in sorted(monthly_all)}
    yearly_out={k:{"all":stats(v),"primary":stats([a for a in v if a.get("type")==PRIMARY])} for k,v in sorted(yearly.items())}
    (METRICS/"monthly.json").write_text(json.dumps(monthly,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (METRICS/"yearly.json").write_text(json.dumps(yearly_out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with NORMALIZED.open("w",encoding="utf-8") as f:
        for a in sorted(valid,key=lambda x:x["_dt"]): f.write(json.dumps(compact(a),ensure_ascii=False)+"\n")
    print(f"Built intelligence: {len(valid)} activities, {len(primary)} primary sessions")

if __name__=="__main__": main()
