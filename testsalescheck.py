# ==========================================
#   ULTIMATE SALES + GOAL BOT (RAILWAY) - DB VERSION + AUTO GOALBOARD REPORTS (TOPIC SUPPORT)
#   - Saves sales/goals/admins/teams/overrides to Postgres
#   - Loads everything from DB on startup
#
#   ✅ /registergoal 1
#     - per-team destination (run inside a topic to save message_thread_id)
#
#   ✅ /registergoalall
#     - GLOBAL destination (run inside a topic)
#     - bot auto-sends GOALBOARD for ALL TEAMS into that topic
#     - One GOALBOARD message per team
#     - If a team table is huge: that team becomes Part 1/2, Part 2/2 (still per team)
#
#   ✅ NEW: AUTO TEAM PAGES (IMPORTANT FIX)
#     - Scheduled GOALBOARD will ONLY show pages that:
#         • had sales in the current shift, OR
#         • were used by that team before (saved in team_pages)
#     - This stops the “HUGE TABLE” problem.
#
#   ✅ FIX: NO MORE DUPLICATE PAGES (brittanyamain vs Brittanya Main, cocopaid vs Coco Paid, etc.)
#     - ALL reads/writes are normalized to a single canonical display name (ALLOWED_PAGES value)
#     - Old DB rows are merged in output automatically (no need to wipe data)
#
#   ✅ NO MORE SILENT FAILURES
#     - logs RetryAfter (flood control), message-too-long, etc. in Railway logs
#     - safe_send retries once after RetryAfter
#
#   ✅ /listteams /deleteteam
#     - FIXED delete behavior:
#         /deleteteam 2            deletes "Team 2" (if it exists)
#         /deleteteam Team Black   deletes Team Black
#         /deleteteam 99           deletes Team 99 (if it exists)
#       (It will NOT delete based on list position anymore.)
# ==========================================

import os
import asyncio
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import psycopg2
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut, NetworkError, BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------- CONFIG -----------------
OWNER_ID = 5513230302
PH_TZ = ZoneInfo("Asia/Manila")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

db = psycopg2.connect(DATABASE_URL, sslmode="require")
db.autocommit = True

TG_MAX = 4096
TG_SAFE = 3900

# ----------------- PAGES -----------------
ALLOWED_PAGES = {
    "#alannafreeoftv": "Alanna Free / OFTV",
    "#alannapaid": "Alanna Paid",
    "#alannawelcome": "Alanna Welcome",
    "#alexalana": "Alexa lana",

    "#alexis": "Alexis",

    "#allyfree": "Ally Free",
    "#allypaid": "Ally Paid",

    "#aprilb": "April B",
    "#ashley": "Ashley",

    "#asiadollpaidfree": "Asia Doll Paid / Free",

    "#autumnfree": "Autumn Free",
    "#autumnpaid": "Autumn Paid",
    "#autumnwelcome": "Autumn Welcome",

    "#brifreeoftv": "Bri Free / OFTV",
    "#bripaid": "Bri Paid",
    "#briwelcome": "Bri Welcome",

    "#brittanyamain": "Brittanya Main",
    "#brittanyapaidfree": "Brittanya Paid / Free",

    "#bronwinfree": "Bronwin Free",
    "#bronwinoftvmcarteroftv": "Bronwin OFTV & MCarter OFTV",
    "#bronwinpaid": "Bronwin Paid",
    "#bronwinwelcome": "Bronwin Welcome",

    "#carterpaidfree": "Carter Paid / Free",

    "#christipaidfree": "Christi Paid and Free",

    "#claire": "Claire",

    "#cocofree": "Coco Free",
    "#cocopaid": "Coco Paid",

    "#cyndiecynthiacolby": "Cyndie, Cynthia & Colby",
    "#cynthiafree": "Cyndie, Cynthia & Colby",

    "#dandfreeoftv": "Dan D Free / OFTV",
    "#dandpaid": "Dan D Paid",
    "#dandwelcome": "Dan D Welcome",

    "#emilyraypaidfree": "Emily Ray Paid / Free",
    "#essiepaidfree": "Essie Paid / Free",

    "#fanslyteam1": "Fansly Team1",
    "#fanslyteam2": "Fansly Team2",
    "#fanslyteam3": "Fansly Team3",

    "#gracefree": "Grace Free",

    "#haileywfree": "Hailey W Free",
    "#haileywpaid": "Hailey W Paid",

    "#hazeyfree": "Hazey Free",
    "#hazeypaid": "Hazey Paid",
    "#hazeywelcome": "Hazey Welcome",

    "#honeynoppv": "Honey NO PPV",
    "#honeyvip": "Honey VIP",

    "#isabellaxizziekay": "Isabella x Izzie Kay",

    "#islafree": "Isla Free",
    "#islaoftv": "Isla OFTV",
    "#islapaid": "Isla Paid",
    "#islawelcome": "Isla Welcome",

    "#kayleexjasmyn": "Kaylee X Jasmyn",

    "#kissingcousinsxvalerievip": "Kissing Cousins X Valerie VIP",

    "#lexipaid": "Lexi Paid",

    "#lilahfree": "Lilah Free",
    "#lilahpaid": "Lilah Paid",

    "#livv": "Livv",

    "#mathildefree": "Mathilde Free",
    "#mathildepaid": "Mathilde Paid",
    "#mathildewelcome": "Mathilde Welcome",
    "#mathildepaidxisaxalexalana": "Mathilde Paid x Isa A x Alexa Lana",

    "#michellefree": "Michelle Free",
    "#michellevip": "Michelle VIP",

    "#mommycarter": "Mommy Carter",

    "#natalialfree": "Natalia L Free",
    "#natalialpaid": "Natalia L Paid",
    "#natalialnicolefansly": "Natalia L, Nicole Fansly",

    "#natalierfree": "Natalie R Free",
    "#natalierpaid": "Natalie R Paid",

    "#niapaid": "nia Paid",

    "#paris": "Paris",

    "#popstfree": "Pops T Free",
    "#popstpaid": "Pops T Paid",

    "#rubirosefree": "Rubi Rose Free",
    "#rubirosepaid": "Rubi Rose Paid",

    "#salah": "Salah",
    "#sarahc": "Sarah C",

    "#skypaidfree": "Sky Paid / Free",
}

# ----------------- CANONICALIZATION (FIX DUPLICATES) -----------------
def _slugify(s: str) -> str:
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())

_KEY_NOHASH_TO_DISPLAY = {k.lstrip("#").lower(): v for k, v in ALLOWED_PAGES.items()}
_SLUGKEY_TO_DISPLAY = {_slugify(k.lstrip("#")): v for k, v in ALLOWED_PAGES.items()}
_DISPLAY_LOWER_TO_DISPLAY = {v.lower(): v for v in ALLOWED_PAGES.values()}
_SLUGDISPLAY_TO_DISPLAY = {_slugify(v): v for v in ALLOWED_PAGES.values()}

def canonical_page(value: str | None) -> str | None:
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    token = raw.split()[0].strip()
    low = token.lower()

    if low.startswith("#"):
        return ALLOWED_PAGES.get(low)

    if low in _KEY_NOHASH_TO_DISPLAY:
        return _KEY_NOHASH_TO_DISPLAY[low]

    if raw.lower() in _DISPLAY_LOWER_TO_DISPLAY:
        return _DISPLAY_LOWER_TO_DISPLAY[raw.lower()]

    slug = _slugify(raw)
    if slug in _SLUGKEY_TO_DISPLAY:
        return _SLUGKEY_TO_DISPLAY[slug]
    if slug in _SLUGDISPLAY_TO_DISPLAY:
        return _SLUGDISPLAY_TO_DISPLAY[slug]

    return raw

# ----------------- IN-MEM CACHE (loaded from DB) -----------------
GROUP_TEAMS = {}                 # chat_id -> team name
CHAT_ADMINS = defaultdict(dict)  # chat_id -> {user_id: level}

shift_goals = defaultdict(float)  # page -> goal (global per page)
page_goals = defaultdict(float)   # page -> goal (global per page)

manual_shift_totals = defaultdict(float)  # page -> override amount (REPLACE total if > 0)
manual_page_totals = defaultdict(float)   # page -> override amount (REPLACE total if > 0)

# ---------------- UTIL ----------------
def clean(text: str):
    if not isinstance(text, str):
        return ""
    return (
        text.replace("*", "")
        .replace("_", "")
        .replace("`", "")
        .replace("[", "(")
        .replace("]", ")")
        .strip()
    )

def now_ph() -> datetime:
    return datetime.now(PH_TZ)

def day_start_ph(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=PH_TZ)

def normalize_page(raw_page: str):
    if not raw_page:
        return None
    token = raw_page.strip().split()[0].lower()
    if not token.startswith("#"):
        return None
    return ALLOWED_PAGES.get(token)

def canonicalize_page_name(page_str: str):
    page_str = clean(page_str)
    if not page_str:
        return None
    return canonical_page(page_str)

def current_shift_label(dt: datetime) -> str:
    dt = dt.astimezone(PH_TZ)
    h = dt.hour
    if 8 <= h < 16:
        return "Prime (8AM–4PM)"
    if 16 <= h < 24:
        return "Midshift (4PM–12AM)"
    return "Closing (12AM–8AM)"

def shift_start(dt: datetime) -> datetime:
    dt = dt.astimezone(PH_TZ)
    d = dt.date()
    h = dt.hour
    if 8 <= h < 16:
        return datetime(d.year, d.month, d.day, 8, 0, 0, tzinfo=PH_TZ)
    if 16 <= h < 24:
        return datetime(d.year, d.month, d.day, 16, 0, 0, tzinfo=PH_TZ)
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=PH_TZ)

def shift_end(dt: datetime) -> datetime:
    s = shift_start(dt)
    return s + timedelta(hours=8)

def get_team(chat_id: int):
    return GROUP_TEAMS.get(chat_id)

def is_owner(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id == OWNER_ID

def get_color(p):
    if p >= 100: return "💚"
    if p >= 90:  return "🟢"
    if p >= 61:  return "🔵"
    if p >= 31:  return "🟡"
    if p >= 11:  return "🟠"
    return "🔴"

def money(x: float) -> str:
    try:
        return f"${x:,.2f}"
    except Exception:
        return f"${x}"

# ----------------- DB SCHEMA + HELPERS -----------------
def init_db():
    with db.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            chat_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS admins (
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            level INT NOT NULL DEFAULT 1,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS sales (
            id BIGSERIAL PRIMARY KEY,
            team TEXT NOT NULL,
            page TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            ts TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS page_goals (
            page TEXT PRIMARY KEY,
            goal NUMERIC NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shift_goals (
            page TEXT PRIMARY KEY,
            goal NUMERIC NOT NULL
        );

        CREATE TABLE IF NOT EXISTS manual_overrides (
            page TEXT PRIMARY KEY,
            shift_total NUMERIC NOT NULL DEFAULT 0,
            page_total  NUMERIC NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS report_groups (
            team TEXT PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            thread_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS global_report_dest (
            id INT PRIMARY KEY DEFAULT 1,
            chat_id BIGINT NOT NULL,
            thread_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS team_pages (
            team TEXT NOT NULL,
            page TEXT NOT NULL,
            PRIMARY KEY (team, page)
        );
        """)

        cur.execute("""ALTER TABLE report_groups ADD COLUMN IF NOT EXISTS thread_id BIGINT;""")
        cur.execute("""ALTER TABLE global_report_dest ADD COLUMN IF NOT EXISTS thread_id BIGINT;""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_sales_team_ts ON sales (team, ts DESC);""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_sales_team_page_ts ON sales (team, page, ts DESC);""")

def db_register_team(chat_id: int, team_name: str):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO teams (chat_id, name)
            VALUES (%s, %s)
            ON CONFLICT (chat_id)
            DO UPDATE SET name = EXCLUDED.name;
            """,
            (chat_id, team_name)
        )

def db_delete_team(chat_id: int):
    with db.cursor() as cur:
        cur.execute("DELETE FROM teams WHERE chat_id=%s", (chat_id,))
        cur.execute("DELETE FROM admins WHERE chat_id=%s", (chat_id,))

def db_delete_team_by_name(team: str) -> int:
    """
    Deletes team mapping(s) + team-specific data.
    Returns number of team group rows removed.
    """
    removed = 0
    with db.cursor() as cur:
        cur.execute("SELECT chat_id FROM teams WHERE name=%s", (team,))
        ids = [int(r[0]) for r in cur.fetchall()]
        for cid in ids:
            cur.execute("DELETE FROM admins WHERE chat_id=%s", (cid,))
            cur.execute("DELETE FROM teams WHERE chat_id=%s", (cid,))
            removed += 1

        # team-specific tables
        cur.execute("DELETE FROM sales WHERE team=%s", (team,))
        cur.execute("DELETE FROM team_pages WHERE team=%s", (team,))
        cur.execute("DELETE FROM report_groups WHERE team=%s", (team,))
    return removed

def db_upsert_admin(chat_id: int, user_id: int, level: int):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO admins (chat_id, user_id, level)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, user_id)
            DO UPDATE SET level = EXCLUDED.level;
            """,
            (chat_id, user_id, level)
        )

def db_delete_admin(chat_id: int, user_id: int):
    with db.cursor() as cur:
        cur.execute("DELETE FROM admins WHERE chat_id=%s AND user_id=%s", (chat_id, user_id))

def db_add_sale(team: str, page: str, amount: float, ts_iso: str):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sales (team, page, amount, ts) VALUES (%s, %s, %s, %s)",
            (team, page, amount, ts_iso)
        )

def db_add_team_page(team: str, page: str):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_pages (team, page)
            VALUES (%s, %s)
            ON CONFLICT (team, page) DO NOTHING;
            """,
            (team, page)
        )

def db_get_team_pages(team: str):
    with db.cursor() as cur:
        cur.execute("SELECT page FROM team_pages WHERE team=%s", (team,))
        rows = [str(r[0]) for r in cur.fetchall()]

    out = []
    seen = set()
    for p in rows:
        cp = canonical_page(p) or p
        if cp not in seen:
            seen.add(cp)
            out.append(cp)
    return out

def db_upsert_page_goal(page: str, goal: float):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_goals (page, goal)
            VALUES (%s, %s)
            ON CONFLICT (page)
            DO UPDATE SET goal = EXCLUDED.goal;
            """,
            (page, goal)
        )

def db_upsert_shift_goal(page: str, goal: float):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shift_goals (page, goal)
            VALUES (%s, %s)
            ON CONFLICT (page)
            DO UPDATE SET goal = EXCLUDED.goal;
            """,
            (page, goal)
        )

def db_clear_page_goals():
    with db.cursor() as cur:
        cur.execute("DELETE FROM page_goals")

def db_clear_shift_goals():
    with db.cursor() as cur:
        cur.execute("DELETE FROM shift_goals")

def db_upsert_override(page: str, shift_total=None, page_total=None):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO manual_overrides (page, shift_total, page_total) VALUES (%s, 0, 0) "
            "ON CONFLICT (page) DO NOTHING",
            (page,)
        )
        if shift_total is not None:
            cur.execute("UPDATE manual_overrides SET shift_total=%s WHERE page=%s", (shift_total, page))
        if page_total is not None:
            cur.execute("UPDATE manual_overrides SET page_total=%s WHERE page=%s", (page_total, page))

def db_clear_override_shift(page: str):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET shift_total=0 WHERE page=%s", (page,))
        cur.execute("DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0", (page,))

def db_clear_override_page(page: str):
    page = canonical_page(page) or page
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET page_total=0 WHERE page=%s", (page,))
        cur.execute("DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0", (page,))

def db_set_report_group(team: str, chat_id: int, thread_id):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_groups (team, chat_id, thread_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (team)
            DO UPDATE SET chat_id = EXCLUDED.chat_id,
                          thread_id = EXCLUDED.thread_id;
            """,
            (team, chat_id, thread_id)
        )

def db_get_report_groups():
    with db.cursor() as cur:
        cur.execute("SELECT team, chat_id, thread_id FROM report_groups")
        out = []
        for (t, cid, th) in cur.fetchall():
            out.append((str(t), int(cid), int(th) if th is not None else None))
        return out

def db_set_global_report_dest(chat_id: int, thread_id):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO global_report_dest (id, chat_id, thread_id)
            VALUES (1, %s, %s)
            ON CONFLICT (id)
            DO UPDATE SET chat_id=EXCLUDED.chat_id,
                          thread_id=EXCLUDED.thread_id;
            """,
            (chat_id, thread_id)
        )

def db_get_global_report_dest():
    with db.cursor() as cur:
        cur.execute("SELECT chat_id, thread_id FROM global_report_dest WHERE id=1")
        row = cur.fetchone()
        if not row:
            return None
        chat_id, thread_id = row
        return int(chat_id), (int(thread_id) if thread_id is not None else None)

def db_list_all_teams() -> list[str]:
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT name FROM teams ORDER BY name ASC")
        return [str(r[0]) for r in cur.fetchall()]

def db_reset_daily_sales(team: str):
    start = day_start_ph(now_ph())
    with db.cursor() as cur:
        cur.execute("DELETE FROM sales WHERE team=%s AND ts >= %s", (team, start))

def db_sum_sales(team: str, since_ts: datetime, until_ts: datetime | None = None) -> dict[str, float]:
    """
    Returns {page: total} for team between since_ts and until_ts (optional).
    """
    out = defaultdict(float)
    with db.cursor() as cur:
        if until_ts is None:
            cur.execute(
                "SELECT page, SUM(amount) FROM sales WHERE team=%s AND ts >= %s GROUP BY page",
                (team, since_ts)
            )
        else:
            cur.execute(
                "SELECT page, SUM(amount) FROM sales WHERE team=%s AND ts >= %s AND ts < %s GROUP BY page",
                (team, since_ts, until_ts)
            )
        for page, total in cur.fetchall():
            p = canonical_page(str(page)) or str(page)
            out[p] += float(total or 0)
    return dict(out)

def db_pages_with_sales_in_window(team: str, since_ts: datetime, until_ts: datetime | None = None) -> set[str]:
    """
    Returns a set of pages that had ANY sales in that window.
    """
    pages = set()
    with db.cursor() as cur:
        if until_ts is None:
            cur.execute("SELECT DISTINCT page FROM sales WHERE team=%s AND ts >= %s", (team, since_ts))
        else:
            cur.execute("SELECT DISTINCT page FROM sales WHERE team=%s AND ts >= %s AND ts < %s", (team, since_ts, until_ts))
        for (page,) in cur.fetchall():
            pages.add(canonical_page(str(page)) or str(page))
    return pages

def load_from_db():
    GROUP_TEAMS.clear()
    CHAT_ADMINS.clear()
    shift_goals.clear()
    page_goals.clear()
    manual_shift_totals.clear()
    manual_page_totals.clear()

    with db.cursor() as cur:
        cur.execute("SELECT chat_id, name FROM teams")
        for chat_id, name in cur.fetchall():
            GROUP_TEAMS[int(chat_id)] = str(name)

        cur.execute("SELECT chat_id, user_id, level FROM admins")
        for chat_id, user_id, level in cur.fetchall():
            CHAT_ADMINS[int(chat_id)][int(user_id)] = int(level)

        cur.execute("SELECT page, goal FROM shift_goals")
        for page, goal in cur.fetchall():
            p = canonical_page(str(page)) or str(page)
            shift_goals[p] = float(goal)

        cur.execute("SELECT page, goal FROM page_goals")
        for page, goal in cur.fetchall():
            p = canonical_page(str(page)) or str(page)
            page_goals[p] = float(goal)

        cur.execute("SELECT page, shift_total, page_total FROM manual_overrides")
        for page, s, p2 in cur.fetchall():
            p = canonical_page(str(page)) or str(page)
            manual_shift_totals[p] = float(s or 0)
            manual_page_totals[p] = float(p2 or 0)

# ----------------- ACCESS CONTROL -----------------
async def require_owner(update: Update) -> bool:
    if not is_owner(update):
        await update.message.reply_text("⛔ Only the bot owner can use this command.")
        return False
    return True

def is_registered_admin(chat_id: int, user_id: int, min_level: int = 1) -> bool:
    return int(CHAT_ADMINS.get(chat_id, {}).get(user_id, 0)) >= min_level

async def require_registered_admin(update: Update, min_level: int = 1) -> bool:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_registered_admin(chat_id, user_id, min_level=min_level):
        await update.message.reply_text("⛔ You don’t have permission to use this command.")
        return False
    return True

async def require_team(update: Update):
    team = get_team(update.effective_chat.id)
    if team is None:
        await update.message.reply_text(
            "Not a team group yet.\n\nOwner can register this group using:\n/registerteam Team 1\n\nTo see the group ID:\n/chatid"
        )
        return None
    return team

# ----------------- LOGGING / ERROR HANDLER -----------------
def log_exc(prefix: str, e: Exception):
    print(f"{prefix}: {type(e).__name__}: {e}")
    traceback.print_exc()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    e = context.error
    print("❌ HANDLER ERROR:", repr(e))
    traceback.print_exc()

async def safe_send(bot, *, chat_id: int, thread_id: int | None, text: str, parse_mode: str | None = None):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            message_thread_id=thread_id if thread_id else None,
            disable_web_page_preview=True,
        )
    except RetryAfter as e:
        log_exc("⏳ RetryAfter (flood control)", e)
        try:
            await asyncio.sleep(int(getattr(e, "retry_after", 2)) + 1)
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                message_thread_id=thread_id if thread_id else None,
                disable_web_page_preview=True,
            )
        except Exception as e2:
            log_exc("❌ Retry send failed", e2)
    except BadRequest as e:
        log_exc("⚠️ BadRequest", e)
    except (TimedOut, NetworkError) as e:
        log_exc("🌐 Network/TimedOut", e)
    except Exception as e:
        log_exc("❌ Send failed", e)

# ----------------- TEXT SPLITTER -----------------
def split_telegram(text: str, limit: int = TG_SAFE) -> list[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]

    parts = []
    buf = ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            if len(line) > limit:
                # hard split long line
                chunk = line
                while len(chunk) > limit:
                    parts.append(chunk[:limit])
                    chunk = chunk[limit:]
                buf += chunk
            else:
                buf += line
        else:
            buf += line

    if buf:
        parts.append(buf)
    return parts

# ----------------- BASIC -----------------
async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat type: {chat.type}\nChat ID: {chat.id}")

# ----------------- OWNER COMMANDS -----------------
async def registerteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    team_name = clean(" ".join(context.args)).strip()
    if not team_name:
        return await update.message.reply_text("Format: /registerteam Team 1")

    chat_id_ = update.effective_chat.id
    GROUP_TEAMS[chat_id_] = team_name
    db_register_team(chat_id_, team_name)

    return await update.message.reply_text(
        f"✅ Registered this group!\nTeam: {team_name}\nChat ID: {chat_id_}\nNext: /registeradmin 1"
    )

async def unregisterteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id_ = update.effective_chat.id
    if chat_id_ not in GROUP_TEAMS:
        return await update.message.reply_text("This group is not registered.")

    team = GROUP_TEAMS.pop(chat_id_, None)
    if chat_id_ in CHAT_ADMINS:
        del CHAT_ADMINS[chat_id_]

    db_delete_team(chat_id_)
    await update.message.reply_text(f"🗑️ Team unregistered.\nRemoved team: {team}\nChat ID: {chat_id_}")

async def registeradmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    if not context.args:
        return await update.message.reply_text("Format: /registeradmin 1\nTip: reply to a user then run /registeradmin 1")

    try:
        level = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("Level must be a number. Example: /registeradmin 1")

    chat_id_ = update.effective_chat.id
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
    else:
        target_user = update.effective_user

    CHAT_ADMINS[chat_id_][target_user.id] = level
    db_upsert_admin(chat_id_, target_user.id, level)

    name = clean(target_user.username or target_user.first_name or str(target_user.id))
    await update.message.reply_text(f"✅ Registered bot-admin: {name} (level {level})")

async def unregisteradmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id_ = update.effective_chat.id
    target_id = None
    target_label = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        target_id = u.id
        target_label = clean(u.username or u.first_name or str(u.id))
    elif context.args:
        try:
            target_id = int(context.args[0])
            target_label = str(target_id)
        except ValueError:
            return await update.message.reply_text("Use: reply then /unregisteradmin\nor: /unregisteradmin <user_id>")
    else:
        return await update.message.reply_text("Use: reply then /unregisteradmin\nor: /unregisteradmin <user_id>")

    if target_id not in CHAT_ADMINS.get(chat_id_, {}):
        return await update.message.reply_text("That user is not a bot-admin in this group.")

    del CHAT_ADMINS[chat_id_][target_id]
    db_delete_admin(chat_id_, target_id)
    await update.message.reply_text(f"🗑️ Removed bot-admin access for: {target_label}")

async def listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id_ = update.effective_chat.id
    admins = CHAT_ADMINS.get(chat_id_, {})
    if not admins:
        return await update.message.reply_text("No bot-admins registered in this group.")

    lines = []
    for uid, lvl in sorted(admins.items(), key=lambda x: (-int(x[1]), int(x[0]))):
        lines.append(f"• User ID: {uid} — level {int(lvl)}")
    await update.message.reply_text("👑 Bot Admins (this group):\n\n" + "\n".join(lines))

async def listteams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    teams = db_list_all_teams()
    if not teams:
        return await update.message.reply_text("No teams registered yet.")
    msg = "📌 Registered Teams:\n\n" + "\n".join([f"• {t}" for t in teams])
    await update.message.reply_text(msg)

async def deleteteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    if not context.args:
        return await update.message.reply_text("Format:\n/deleteteam 2\n/deleteteam Team 2\n/deleteteam Team Black")

    raw = clean(" ".join(context.args)).strip()
    team = None

    # if they type "2" or "99" => Team 2 / Team 99
    if raw.isdigit():
        team = f"Team {raw}"
    else:
        team = raw if raw.lower().startswith("team ") else raw

    existing = set(db_list_all_teams())
    if team not in existing:
        return await update.message.reply_text(f"❌ Team not found: {team}")

    removed_rows = db_delete_team_by_name(team)

    # refresh cache (important)
    load_from_db()

    await update.message.reply_text(
        f"🗑️ Deleted team: {team}\n"
        f"Removed {removed_rows} team-group mapping(s) (teams table).\n"
        f"Also cleared: sales, team_pages, report destination for that team."
    )

async def registergoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the target GC (not in private).")
    if not await require_owner(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Format: /registergoal 1\n"
            "Run it inside the TOPIC you want (e.g., PAGE STATS) to send scheduled stats there."
        )

    arg = clean(" ".join(context.args)).strip()
    team = f"Team {arg}" if arg.isdigit() else arg

    chat_id_ = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id if update.effective_message else None

    try:
        db_set_report_group(team, chat_id_, thread_id)
    except Exception as e:
        log_exc("❌ DB error while saving report destination", e)
        return await update.message.reply_text(f"❌ DB error while saving report destination:\n{e}")

    where = "General" if not thread_id else f"Topic (thread_id={thread_id})"
    await update.message.reply_text(
        f"✅ Registered this destination for scheduled GOALBOARD reports.\n"
        f"Team: {team}\n"
        f"Posts to: {where}\n\n"
        "Schedule: 8AM, 10AM, 12PM, 2PM, 4PM, 6PM, 8PM, 10PM (PH)"
    )

async def registergoalall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the target GC (not in private).")
    if not await require_owner(update):
        return

    chat_id_ = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id if update.effective_message else None

    try:
        db_set_global_report_dest(chat_id_, thread_id)
    except Exception as e:
        log_exc("❌ DB error while saving GLOBAL destination", e)
        return await update.message.reply_text(f"❌ DB error while saving GLOBAL destination:\n{e}")

    where = "General" if not thread_id else f"Topic (thread_id={thread_id})"
    await update.message.reply_text(
        "✅ Registered GLOBAL destination for scheduled GOALBOARD reports (ALL TEAMS).\n"
        f"Posts to: {where}\n\n"
        "Schedule: 8AM, 10AM, 12PM, 2PM, 4PM, 6PM, 8PM, 10PM (PH)"
    )

async def resetdaily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_owner(update):
        return

    db_reset_daily_sales(team)
    await update.message.reply_text(
        f"🧹 Daily reset complete for {team}.\nDeleted TODAY’s sales only (00:00 PH → now)."
    )

# ----------------- GOALS / OVERRIDES COMMANDS -----------------
async def setshiftgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Format:\n/setshiftgoal #autumnpaid 1000\n(set shift goal for a page)")
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    try:
        goal = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Goal must be a number.")
    shift_goals[page] = goal
    db_upsert_shift_goal(page, goal)
    await update.message.reply_text(f"✅ Shift goal set:\n{page} → {money(goal)}")

async def setpagegoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Format:\n/setpagegoal #autumnpaid 30000\n(set period goal for a page)")
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    try:
        goal = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Goal must be a number.")
    page_goals[page] = goal
    db_upsert_page_goal(page, goal)
    await update.message.reply_text(f"✅ Page goal set:\n{page} → {money(goal)}")

async def clearshiftgoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    shift_goals.clear()
    db_clear_shift_goals()
    await update.message.reply_text("🧹 Cleared ALL shift goals (global).")

async def clearpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    page_goals.clear()
    db_clear_page_goals()
    await update.message.reply_text("🧹 Cleared ALL page goals (global).")

async def setoverride_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Format:\n/overrideshift #autumnpaid 500\n(sets shift total override; replaces computed total)")
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    try:
        val = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Value must be a number.")
    manual_shift_totals[page] = val
    db_upsert_override(page, shift_total=val)
    await update.message.reply_text(f"✅ Shift override set:\n{page} → {money(val)}")

async def clearoverride_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
        if not context.args:
        return await update.message.reply_text("Format:\n/clearoverrideshift #autumnpaid")
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    manual_shift_totals[page] = 0
    db_clear_override_shift(page)
    await update.message.reply_text(f"🧹 Cleared shift override for: {page}")

async def setoverride_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if len(context.args) < 2:
        return await update.message.reply_text(
            "Format:\n/overridepage #autumnpaid 5000\n(sets period total override; replaces computed total)"
        )
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    try:
        val = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Value must be a number.")
    manual_page_totals[page] = val
    db_upsert_override(page, page_total=val)
    await update.message.reply_text(f"✅ Page override set:\n{page} → {money(val)}")

async def clearoverride_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if not context.args:
        return await update.message.reply_text("Format:\n/clearoverridepage #autumnpaid")
    page = canonicalize_page_name(context.args[0])
    if not page:
        return await update.message.reply_text("❌ Invalid page tag.")
    manual_page_totals[page] = 0
    db_clear_override_page(page)
    await update.message.reply_text(f"🧹 Cleared page override for: {page}")

async def viewshiftgoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if not shift_goals:
        return await update.message.reply_text("No shift goals set.")
    lines = ["📌 Shift Goals (global):"]
    for p in sorted(shift_goals.keys()):
        lines.append(f"• {p}: {money(float(shift_goals[p]))}")
    await update.message.reply_text("\n".join(lines))

async def viewpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registered_admin(update, min_level=1):
        return
    if not page_goals:
        return await update.message.reply_text("No page goals set.")
    lines = ["📌 Page Goals (global):"]
    for p in sorted(page_goals.keys()):
        lines.append(f"• {p}: {money(float(page_goals[p]))}")
    await update.message.reply_text("\n".join(lines))

# ----------------- SALES HANDLER (CONTINUATION) -----------------
async def handle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    team = get_team(update.effective_chat.id)
    if team is None:
        return

    saved = False
    unknown_tags = set()
    ts_iso = now_ph().isoformat()

    for raw in update.message.text.splitlines():
        line = raw.strip()
        line = line.lstrip("*•- ").strip()
        if not line.startswith("+"):
            continue

        parts = line[1:].split(maxsplit=1)
        if len(parts) < 2:
            continue

        try:
            amount = float(parts[0])
        except ValueError:
            continue

        canonical_page_name = normalize_page(parts[1])
        if not canonical_page_name:
            bad_token = parts[1].strip().split()[0]
            unknown_tags.add(bad_token)
            continue

        try:
            db_add_sale(team, canonical_page_name, amount, ts_iso)
            db_add_team_page(team, canonical_page_name)  # IMPORTANT: save team-page usage
            saved = True
        except Exception as e:
            log_exc("❌ DB error adding sale", e)

    if unknown_tags:
        await update.message.reply_text(
            "❌ Unknown/invalid tag(s):\n" + "\n".join([f"• {t}" for t in sorted(unknown_tags)])
        )
        return

    if saved:
        # keep it quiet (less spam). If you want confirmation, uncomment:
        # await update.message.reply_text("✅ Saved.")
        return

# ----------------- GOALBOARD BUILDER -----------------
def build_goalboard_table(
    *,
    team: str,
    pages: list[str],
    totals: dict[str, float],
    goals: dict[str, float],
    title: str,
) -> str:
    lines = [f"🏁 **{title}**", f"Team: **{clean(team)}**", ""]
    lines.append("Page | Total | Goal | %")
    lines.append("---|---:|---:|---:")

    for page in pages:
        total = float(totals.get(page, 0) or 0)
        goal = float(goals.get(page, 0) or 0)
        pct = (total / goal * 100) if goal > 0 else 0
        color = get_color(pct) if goal > 0 else "⚪"
        lines.append(f"{color} {page} | {money(total)} | {money(goal)} | {pct:.0f}%")

    return "\n".join(lines)

def pages_for_team_for_shift(team: str, shift_s: datetime, shift_e: datetime) -> list[str]:
    # show pages that had sales in the shift OR pages previously used by team (team_pages)
    used_before = set(db_get_team_pages(team))
    used_in_shift = db_pages_with_sales_in_window(team, shift_s, shift_e)
    pages = sorted(set(used_before) | set(used_in_shift))
    return pages

# ----------------- GOALBOARD COMMANDS -----------------
async def goalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    dt = now_ph()
    s = shift_start(dt)
    e = shift_end(dt)

    totals = db_sum_sales(team, s, e)

    # apply shift overrides (replace totals if > 0)
    for page, override in manual_shift_totals.items():
        if float(override or 0) > 0:
            totals[page] = float(override)

    pages = pages_for_team_for_shift(team, s, e)

    # ensure we only show pages that are in your allowed/canonical list OR already used
    # (keeps huge tables down)
    msg = build_goalboard_table(
        team=team,
        pages=pages,
        totals=totals,
        goals=shift_goals,
        title=f"GOALBOARD — {current_shift_label(dt)}",
    )

    chunks = split_telegram(msg)
    for i, chunk in enumerate(chunks, start=1):
        suffix = f"\n\nPart {i}/{len(chunks)}" if len(chunks) > 1 else ""
        await safe_send(
            context.bot,
            chat_id=update.effective_chat.id,
            thread_id=update.effective_message.message_thread_id if update.effective_message else None,
            text=chunk + suffix,
            parse_mode=ParseMode.MARKDOWN,
        )

async def pages_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    pages = sorted(db_get_team_pages(team))
    if not pages:
        return await update.message.reply_text("No pages saved yet for this team. (Send sales like `+100 #autumnpaid`)")
    await update.message.reply_text("📌 Team Pages:\n\n" + "\n".join([f"• {p}" for p in pages]))

# ----------------- AUTO SCHEDULED REPORTS -----------------
async def send_team_goalboard(bot, team: str, chat_id: int, thread_id: int | None):
    dt = now_ph()
    s = shift_start(dt)
    e = shift_end(dt)

    totals = db_sum_sales(team, s, e)

    # apply shift overrides
    for page, override in manual_shift_totals.items():
        if float(override or 0) > 0:
            totals[page] = float(override)

    pages = pages_for_team_for_shift(team, s, e)
    msg = build_goalboard_table(
        team=team,
        pages=pages,
        totals=totals,
        goals=shift_goals,
        title=f"GOALBOARD — {current_shift_label(dt)}",
    )

    chunks = split_telegram(msg)
    for i, chunk in enumerate(chunks, start=1):
        suffix = f"\n\nPart {i}/{len(chunks)}" if len(chunks) > 1 else ""
        await safe_send(
            bot,
            chat_id=chat_id,
            thread_id=thread_id,
            text=chunk + suffix,
            parse_mode=ParseMode.MARKDOWN,
        )

async def scheduled_goalboard_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs on schedule and posts to:
    - per-team destination if registered via /registergoal
    - OR global destination (ALL teams) if set via /registergoalall
    """
    bot = context.bot

    try:
        # 1) per-team destinations
        groups = db_get_report_groups()
        for team, chat_id, thread_id in groups:
            await send_team_goalboard(bot, team, chat_id, thread_id)

        # 2) global destination (all teams)
        global_dest = db_get_global_report_dest()
        if global_dest:
            gc_chat_id, gc_thread_id = global_dest
            for team in db_list_all_teams():
                await send_team_goalboard(bot, team, gc_chat_id, gc_thread_id)

    except Exception as e:
        log_exc("❌ Scheduled job error", e)

# ----------------- STARTUP / MAIN -----------------
def add_jobs(app):
    # 8AM, 10AM, 12PM, 2PM, 4PM, 6PM, 8PM, 10PM (PH)
    hours = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]
    for h in hours:
        app.job_queue.run_daily(
            scheduled_goalboard_job,
            time=time(hour=h, minute=0, tzinfo=PH_TZ),
            name=f"goalboard_{h:02d}00",
        )

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("chatid", chatid))

    # owner-only
    app.add_handler(CommandHandler("registerteam", registerteam))
    app.add_handler(CommandHandler("unregisterteam", unregisterteam))
    app.add_handler(CommandHandler("registeradmin", registeradmin))
    app.add_handler(CommandHandler("unregisteradmin", unregisteradmin))
    app.add_handler(CommandHandler("listadmins", listadmins))
    app.add_handler(CommandHandler("listteams", listteams))
    app.add_handler(CommandHandler("deleteteam", deleteteam))
    app.add_handler(CommandHandler("registergoal", registergoal))
    app.add_handler(CommandHandler("registergoalall", registergoalall))
    app.add_handler(CommandHandler("resetdaily", resetdaily))

    # admins
    app.add_handler(CommandHandler("setshiftgoal", setshiftgoal))
    app.add_handler(CommandHandler("setpagegoal", setpagegoal))
    app.add_handler(CommandHandler("viewshiftgoals", viewshiftgoals))
    app.add_handler(CommandHandler("viewpagegoals", viewpagegoals))
    app.add_handler(CommandHandler("clearshiftgoals", clearshiftgoals))
    app.add_handler(CommandHandler("clearpagegoals", clearpagegoals))
    app.add_handler(CommandHandler("overrideshift", setoverride_shift))
    app.add_handler(CommandHandler("clearoverrideshift", clearoverride_shift))
    app.add_handler(CommandHandler("overridepage", setoverride_page))
    app.add_handler(CommandHandler("clearoverridepage", clearoverride_page))

    # user
    app.add_handler(CommandHandler("goalboard", goalboard))
    app.add_handler(CommandHandler("pages", pages_cmd))

    # sales input
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_sales))

    # error handler
    app.add_error_handler(error_handler)

    add_jobs(app)
    return app

def main():
    init_db()
    load_from_db()
    app = build_app()
    print("✅ Bot started (DB version).")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

