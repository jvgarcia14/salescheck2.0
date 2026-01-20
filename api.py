import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from fastapi import FastAPI, HTTPException

PH_TZ = ZoneInfo("Asia/Manila")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Connection pool (better than one global conn in FastAPI)
pool = SimpleConnectionPool(
    minconn=1,
    maxconn=5,
    dsn=DATABASE_URL,
    sslmode="require",
)

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

def now_ph() -> datetime:
    return datetime.now(PH_TZ)

def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # DANGER: wipes old tables
            cur.execute("DROP TABLE IF EXISTS sales;")
            cur.execute("DROP TABLE IF EXISTS page_goals;")
            cur.execute("DROP TABLE IF EXISTS teams;")

            cur.execute("""
            CREATE TABLE teams (
                chat_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE sales (
                chat_id BIGINT,
                team TEXT NOT NULL,
                page TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                ts TIMESTAMPTZ DEFAULT now()
            );
            """)

            cur.execute("""
            CREATE TABLE page_goals (
                team TEXT NOT NULL,
                page TEXT NOT NULL,
                goal NUMERIC NOT NULL,
                PRIMARY KEY (team, page)
            );
            """)

            cur.execute("""CREATE INDEX idx_sales_team_ts ON sales(team, ts);""")
            cur.execute("""CREATE INDEX idx_sales_team_page ON sales(team, page);""")

        conn.commit()
    finally:
        put_conn(conn)


app = FastAPI(title="Sales Bot API")
init_db()

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
        return {"db_ok": (row and row[0] == 1)}
    finally:
        put_conn(conn)

@app.get("/teams")
def teams():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM teams ORDER BY name;")
            rows = cur.fetchall()
        return {"teams": [r[0] for r in rows]}
    finally:
        put_conn(conn)

@app.get("/summary")
def summary(days: int = 15, team: str = "Team 1"):
    try:
        if days not in (15, 30):
            raise HTTPException(status_code=400, detail="days must be 15 or 30")
        team = (team or "").strip()
        if not team:
            raise HTTPException(status_code=400, detail="team is required")

        # Query window: use UTC for DB compare (timestamptz is UTC-safe)
        now_utc = datetime.now(timezone.utc)
        cutoff_utc = now_utc - timedelta(days=days)

        conn = get_conn()
        try:
            # 1) Sales totals per page
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

            # 2) Page goals for this team
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

            page_goals = {}
            for page, goal in goal_rows:
                try:
                    page_goals[str(page)] = float(goal)
                except (TypeError, ValueError, InvalidOperation):
                    page_goals[str(page)] = 0.0

        finally:
            put_conn(conn)

        # Merge keys
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

        # Return window in PH time for display
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/summary crashed: {type(e).__name__}: {e}")
