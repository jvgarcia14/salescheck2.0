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
