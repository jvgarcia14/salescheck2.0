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

@app.get("/summary")
def summary(days: int = 15, team: str = "Team 1"):
    if days not in (15, 30):
        raise HTTPException(status_code=400, detail="days must be 15 or 30")

    cutoff_ph = now_ph() - timedelta(days=days)

    # Convert cutoff to UTC-friendly timestamp by using aware PH time,
    # psycopg2 will handle tz conversion correctly with TIMESTAMPTZ.
    cutoff = cutoff_ph

    # 1) Pull totals per page from sales
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT page, COALESCE(SUM(amount), 0) AS total
            FROM sales
            WHERE team = %s AND ts >= %s
            GROUP BY page
            ORDER BY total DESC;
            """,
            (team, cutoff),
        )
        sales_rows = cur.fetchall()

    totals = {page: float(total) for page, total in sales_rows}

    # 2) Pull goals per page from page_goals
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT page, goal
            FROM page_goals
            WHERE team = %s;
            """,
            (team,),
        )
        goal_rows = cur.fetchall()

    page_goals = {page: float(goal) for page, goal in goal_rows}

    # 3) Combine
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
            "pct": round(pct, 1) if pct is not None else None,
        })

    rows.sort(key=lambda r: r["sales"], reverse=True)
    overall_pct = (total_sales / total_goal * 100.0) if total_goal > 0 else None

    return {
        "team": team,
        "days": days,
        "from": cutoff_ph.isoformat(),
        "to": now_ph().isoformat(),
        "total_sales": round(total_sales, 2),
        "total_goal": round(total_goal, 2),
        "overall_pct": round(overall_pct, 1) if overall_pct is not None else None,
        "rows": rows,
    }
