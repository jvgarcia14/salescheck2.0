from fastapi import FastAPI, HTTPException
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import json, os

DATA_DIR = os.getenv("DATA_DIR", "/app/data")

def path(name: str) -> str:
    return os.path.join(DATA_DIR, name)
    
PH_TZ = ZoneInfo("Asia/Manila")

SALES_LOG_FILE = path("sales_log.json")
TEAMS_FILE = path("teams.json")
MANUAL_OVERRIDES_FILE = path("manual_overrides.json")
GOALS_FILE = path("goals.json")

app = FastAPI(title="Sales Bot API")

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        if os.path.getsize(path) == 0:
            return default
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def _split_internal(internal: str):
    parts = internal.split("|", 1)
    if len(parts) != 2:
        return internal, ""
    return parts[0], parts[1]

def _now_ph():
    return datetime.now(PH_TZ)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/teams")
def teams():
    teams_map = _load_json(TEAMS_FILE, {})
    unique = sorted(set(str(v) for v in teams_map.values()))
    return {"teams": unique}

@app.get("/summary")
def summary(days: int = 15, team: str = "Team 1"):
    if days not in (15, 30):
        raise HTTPException(status_code=400, detail="days must be 15 or 30")

    log = _load_json(SALES_LOG_FILE, [])
    overrides = _load_json(MANUAL_OVERRIDES_FILE, {"shift": {}, "page": {}})
    goals_raw = _load_json(GOALS_FILE, {"shift_goals": {}, "page_goals": {}})

    page_goals = goals_raw.get("page_goals", {})

    cutoff = _now_ph() - timedelta(days=days)
    totals = defaultdict(float)

    for ev in log:
        try:
            _, ev_team = _split_internal(ev["user"])
            if ev_team != team:
                continue
            ts = datetime.fromisoformat(ev["ts"])
            if ts < cutoff:
                continue
            totals[ev["page"]] += float(ev["amt"])
        except Exception:
            continue

    # Apply quota overrides
    for page, val in overrides.get("page", {}).items():
        totals[page] = float(val)

    all_pages = set(totals.keys()) | set(page_goals.keys())
    rows = []
    total_sales = 0.0
    total_goal = 0.0

    for page in all_pages:
        sales = float(totals.get(page, 0.0))
        goal = float(page_goals.get(page, 0.0))
        pct = (sales / goal * 100.0) if goal > 0 else None

        total_sales += sales
        total_goal += goal

        rows.append({
            "page": page,
            "sales": round(sales, 2),
            "goal": round(goal, 2),
            "pct": round(pct, 1) if pct is not None else None
        })

    rows.sort(key=lambda r: r["sales"], reverse=True)
    overall_pct = (total_sales / total_goal * 100.0) if total_goal > 0 else None

    return {
        "team": team,
        "days": days,
        "from": cutoff.isoformat(),
        "to": _now_ph().isoformat(),
        "total_sales": round(total_sales, 2),
        "total_goal": round(total_goal, 2),
        "overall_pct": round(overall_pct, 1) if overall_pct is not None else None,
        "rows": rows
    }
