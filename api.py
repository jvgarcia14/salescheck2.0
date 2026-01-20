import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
from decimal import InvalidOperation

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from fastapi import FastAPI, HTTPException, Header

PH_TZ = ZoneInfo("Asia/Manila")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Optional simple auth for your app
API_TOKEN = os.getenv("API_TOKEN")  # set this in Railway Variables (recommended)

pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL,
    sslmode="require",
)

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

def now_ph() -> datetime:
    return datetime.now(PH_TZ)

def require_token(authorization: str | None):
    # If API_TOKEN not set, endpoint stays public (your choice)
    if not API_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    expected = f"Bearer {API_TOKEN}"
    if authorization.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid token")

def init_db_safe():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # ✅ SAFE: no dropping
            cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                chat_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT,
                team TEXT NOT NULL,
                page TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT now()
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

            cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_team_ts ON sales(team, ts);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_team_page ON sales(team, page);")
        conn.commit()
    finally:
        put_conn(conn)

app = FastAPI(title="Sales Bot API")
init_db_safe()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/dbtest")
def dbtest():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            row = cur.fetchone()
        return {"db_ok": bool(row and row[0] == 1)}
    finally:
        put_conn(conn)

@app.get("/teams")
def teams(authorization: str | None = Header(default=None)):
    require_token(authorization)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM teams ORDER BY name;")
            rows = cur.fetchall()
        return {"teams": [r[0] for r in rows]}
    finally:
        put_conn(conn)

# ✅ Optional: let your app register teams (or your bot can call this too)
@app.post("/teams")
def upsert_team(payload: dict, authorization: str | None = Header(default=None)):
    require_token(authorization)

    chat_id = payload.get("chat_id")
    name = (payload.get("name") or "").strip()
    if not chat_id or not name:
        raise HTTPException(status_code=400, detail="chat_id and name are required")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO teams (chat_id, name)
                VALUES (%s, %s)
                ON CONFLICT (chat_id)
                DO UPDATE SET name = EXCLUDED.name;
                """,
                (int(chat_id), name),
            )
        conn.commit()
        return {"ok": True}
    finally:
        put_conn(conn)

# ✅ Optional: set page goal via API
@app.post("/pagegoal")
def set_page_goal(payload: dict, authorization: str | None = Header(default=None)):
    require_token(authorization)

    team = (payload.get("team") or "").strip()
    page = (payload.get("page") or "").strip()
    goal = payload.get("goal")

    if not team or not page or goal is None:
        raise HTTPException(status_code=400, detail="team, page, goal are required")

    try:
        goal_val = float(goal)
    except Exception:
        raise HTTPException(status_code=400, detail="goal must be a number")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO page_goals (team, page, goal)
                VALUES (%s, %s, %s)
                ON CONFLICT (team, page)
                DO UPDATE SET goal = EXCLUDED.goal;
                """,
                (team, page, goal_val),
            )
        conn.commit()
        return {"ok": True}
    finally:
        put_conn(conn)

@app.get("/summary")
def summary(days: int = 15, team: str = "Team 1", authorization: str | None = Header(default=None)):
    require_token(authorization)

    if days not in (15, 30):
        raise HTTPException(status_code=400, detail="days must be 15 or 30")

    team = (team or "").strip()
    if not team:
        raise HTTPException(status_code=400, detail="team is required")

    # Query window: compare in UTC (ts is timestamptz)
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=days)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT page, COALESCE(SUM(amount), 0) AS sales
                FROM sales
                WHERE team = %s
                  AND ts >= %s
                GROUP BY page
                ORDER BY page;
                """,
                (team, cutoff_utc),
            )
            sales_rows = cur.fetchall()

        totals = defaultdict(float)
        for page, sales in sales_rows:
            try:
                totals[str(page)] = float(sales)
            except (TypeError, ValueError, InvalidOperation):
                totals[str(page)] = 0.0

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

        goals = {}
        for page, goal in goal_rows:
            try:
                goals[str(page)] = float(goal)
            except (TypeError, ValueError, InvalidOperation):
                goals[str(page)] = 0.0

    finally:
        put_conn(conn)

    all_pages = set(totals.keys()) | set(goals.keys())

    rows = []
    total_sales = 0.0
    total_goal = 0.0

    for page in all_pages:
        sales = float(totals.get(page, 0.0))
        goal = float(goals.get(page, 0.0))
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

    now_ph_time = now_ph()
    cutoff_ph_time = now_ph_time - timedelta(days=days)

    return {
        "team": team,
        "days": days,
        "from": cutoff_ph_time.isoformat(),
        "to": now_ph_time.isoformat(),
        "total_sales": round(total_sales, 2),
        "total_goal": round(total_goal, 2),
        "overall_pct": round(overall_pct, 1) if overall_pct is not None else None,
        "rows": rows
    }
