import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import psycopg2
from fastapi import FastAPI, HTTPException

PH_TZ = ZoneInfo("Asia/Manila")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

conn = psycopg2.connect(DATABASE_URL, sslmode="require")
conn.autocommit = True


def init_db():
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            chat_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            chat_id BIGINT,
            team TEXT NOT NULL,
            page TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            ts TIMESTAMPTZ DEFAULT now()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS page_goals (
            team TEXT NOT NULL,
            page TEXT NOT NULL,
            goal NUMERIC NOT NULL,
            PRIMARY KEY (team, page)
        );
        """)


def now_ph():
    return datetime.now(PH_TZ)


app = FastAPI(title="Sales Bot API")
init_db()

@app.get("/dbtest")
def dbtest():
    with conn.cursor() as cur:
        cur.execute("SELECT 1;")
        row = cur.fetchone()
    return {"db_ok": row[0] == 1}
    
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/teams")
def teams():
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT name FROM teams ORDER BY name;")
        rows = cur.fetchall()

    return {
        "teams": [r[0] for r in rows]
    }

from fastapi import HTTPException

@app.get("/summary")
def summary(days: int = 15, team: str = "Team 1"):
    try:
        if days not in (15, 30):
            raise HTTPException(status_code=400, detail="days must be 15 or 30")

        log = _load_json(SALES_LOG_FILE, [])
        if not isinstance(log, list):
            log = []

        overrides = _load_json(MANUAL_OVERRIDES_FILE, {"shift": {}, "page": {}})
        if not isinstance(overrides, dict):
            overrides = {"shift": {}, "page": {}}
        overrides.setdefault("shift", {})
        overrides.setdefault("page", {})

        goals_raw = _load_json(GOALS_FILE, {"shift_goals": {}, "page_goals": {}})
        if not isinstance(goals_raw, dict):
            goals_raw = {"shift_goals": {}, "page_goals": {}}
        goals_raw.setdefault("shift_goals", {})
        goals_raw.setdefault("page_goals", {})

        page_goals = goals_raw["page_goals"]

        cutoff = _now_ph() - timedelta(days=days)
        totals = defaultdict(float)

        for ev in log:
            try:
                if not isinstance(ev, dict):
                    continue

                _, ev_team = _split_internal(str(ev.get("user", "")))
                if ev_team != team:
                    continue

                ts_raw = ev.get("ts")
                if not ts_raw:
                    continue

                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts < cutoff:
                    continue

                page = str(ev.get("page", "")).strip()
                amt = float(ev.get("amt", 0))
                if page:
                    totals[page] += amt
            except Exception:
                continue

        # Apply quota overrides (page)
        for page, val in overrides["page"].items():
            try:
                totals[str(page)] = float(val)
            except Exception:
                pass

        all_pages = set(totals.keys()) | set(page_goals.keys())

        rows = []
        total_sales = 0.0
        total_goal = 0.0

        for page in all_pages:
            sales = float(totals.get(page, 0.0))
            goal = float(page_goals.get(page, 0.0) or 0.0)
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

    except HTTPException:
        raise
    except Exception as e:
        # IMPORTANT: this makes the real error show up in the browser
        raise HTTPException(
            status_code=500,
            detail=f"/summary crashed: {type(e).__name__}: {e}"
        )

