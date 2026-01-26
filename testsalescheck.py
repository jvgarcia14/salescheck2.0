# ==========================================
#   ULTIMATE SALES + GOAL BOT (RAILWAY) - DB VERSION + AUTO GOALBOARD REPORTS (TOPIC SUPPORT)
#   - Saves sales/goals/admins/teams/overrides to Postgres
#   - Loads everything from DB on startup
#   - Auto-sends GOALBOARD TABLE every 2 hours (8AM, 10AM, 12PM... PH)
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
#   ✅ /resetdaily
#     - deletes TODAY’s sales for the current team (00:00 PH -> now)
#     - shift "reset" still works automatically (because goalboard filters by shift start)
#
#   ✅ NEW (AUTO TEAM PAGES)
#     - Scheduled GOALBOARD will ONLY show pages that exist for that team.
#     - A page becomes "available" for a team automatically when:
#       • a sale is recorded for that page, OR
#       • you set a goal for that page in that team
#
#   ✅ NO MORE SILENT FAILURES
#     - logs RetryAfter (flood control), message-too-long, etc. in Railway logs
#
#   ✅ FIXED TELEGRAM FORMAT
#     - Uses HTML <pre> blocks (more stable than Markdown triple backticks)
#     - Chunks /pages and “unknown tags” so it never exceeds 4096 chars
# ==========================================

import os
import html
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
TG_SAFE = 3800  # safer when using HTML tags

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
    "#juliavip": "Julia Vip",
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

# ----------------- IN-MEM CACHE (loaded from DB) -----------------
GROUP_TEAMS = {}  # chat_id -> team name
CHAT_ADMINS = defaultdict(dict)  # chat_id -> {user_id: level}

# These are global goals in this DB schema (not per team)
shift_goals = defaultdict(float)  # page -> goal
page_goals = defaultdict(float)  # page -> goal

# Manual overrides (global per page in schema)
manual_shift_totals = defaultdict(float)  # page -> override amount
manual_page_totals = defaultdict(float)  # page -> override amount


# ---------------- UTIL ----------------
def clean(text: str) -> str:
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
    if page_str.lower().startswith("#"):
        return ALLOWED_PAGES.get(page_str.lower())
    return page_str


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


def get_team(chat_id: int):
    return GROUP_TEAMS.get(chat_id)


def is_owner(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id == OWNER_ID


def get_color(p: float) -> str:
    if p >= 100:
        return "💚"
    if p >= 90:
        return "🟢"
    if p >= 61:
        return "🔵"
    if p >= 31:
        return "🟡"
    if p >= 11:
        return "🟠"
    return "🔴"


# ----------------- DB SCHEMA + HELPERS -----------------
def init_db():
    with db.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                chat_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                level INT NOT NULL DEFAULT 1,
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id BIGSERIAL PRIMARY KEY,
                team TEXT NOT NULL,
                page TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS page_goals (
                page TEXT PRIMARY KEY,
                goal NUMERIC NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_goals (
                page TEXT PRIMARY KEY,
                goal NUMERIC NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_overrides (
                page TEXT PRIMARY KEY,
                shift_total NUMERIC NOT NULL DEFAULT 0,
                page_total NUMERIC NOT NULL DEFAULT 0
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS report_groups (
                team TEXT PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                thread_id BIGINT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS global_report_dest (
                id INT PRIMARY KEY DEFAULT 1,
                chat_id BIGINT NOT NULL,
                thread_id BIGINT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS team_pages (
                team TEXT NOT NULL,
                page TEXT NOT NULL,
                PRIMARY KEY (team, page)
            );
            """
        )

        # safety migrations / indexes
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
            ON CONFLICT (chat_id) DO UPDATE
            SET name = EXCLUDED.name;
            """,
            (chat_id, team_name),
        )


def db_delete_team(chat_id: int):
    with db.cursor() as cur:
        cur.execute("DELETE FROM teams WHERE chat_id=%s", (chat_id,))
        cur.execute("DELETE FROM admins WHERE chat_id=%s", (chat_id,))


def db_upsert_admin(chat_id: int, user_id: int, level: int):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO admins (chat_id, user_id, level)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET level = EXCLUDED.level;
            """,
            (chat_id, user_id, level),
        )


def db_delete_admin(chat_id: int, user_id: int):
    with db.cursor() as cur:
        cur.execute("DELETE FROM admins WHERE chat_id=%s AND user_id=%s", (chat_id, user_id))


def db_add_sale(team: str, page: str, amount: float, ts_iso: str):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sales (team, page, amount, ts) VALUES (%s, %s, %s, %s)",
            (team, page, amount, ts_iso),
        )


def db_add_team_page(team: str, page: str):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_pages (team, page)
            VALUES (%s, %s)
            ON CONFLICT (team, page) DO NOTHING;
            """,
            (team, page),
        )


def db_get_team_pages(team: str):
    with db.cursor() as cur:
        cur.execute("SELECT page FROM team_pages WHERE team=%s ORDER BY page ASC", (team,))
        return [str(r[0]) for r in cur.fetchall()]


def db_upsert_page_goal(page: str, goal: float):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_goals (page, goal)
            VALUES (%s, %s)
            ON CONFLICT (page) DO UPDATE
            SET goal = EXCLUDED.goal;
            """,
            (page, goal),
        )


def db_upsert_shift_goal(page: str, goal: float):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shift_goals (page, goal)
            VALUES (%s, %s)
            ON CONFLICT (page) DO UPDATE
            SET goal = EXCLUDED.goal;
            """,
            (page, goal),
        )


def db_clear_page_goals():
    with db.cursor() as cur:
        cur.execute("DELETE FROM page_goals")


def db_clear_shift_goals():
    with db.cursor() as cur:
        cur.execute("DELETE FROM shift_goals")


def db_upsert_override(page: str, shift_total=None, page_total=None):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_overrides (page, shift_total, page_total)
            VALUES (%s, 0, 0)
            ON CONFLICT (page) DO NOTHING
            """,
            (page,),
        )
        if shift_total is not None:
            cur.execute("UPDATE manual_overrides SET shift_total=%s WHERE page=%s", (shift_total, page))
        if page_total is not None:
            cur.execute("UPDATE manual_overrides SET page_total=%s WHERE page=%s", (page_total, page))


def db_clear_override_shift(page: str):
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET shift_total=0 WHERE page=%s", (page,))
        cur.execute(
            "DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0",
            (page,),
        )


def db_clear_override_page(page: str):
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET page_total=0 WHERE page=%s", (page,))
        cur.execute(
            "DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0",
            (page,),
        )


def db_set_report_group(team: str, chat_id: int, thread_id):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_groups (team, chat_id, thread_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (team) DO UPDATE
            SET chat_id = EXCLUDED.chat_id,
                thread_id = EXCLUDED.thread_id;
            """,
            (team, chat_id, thread_id),
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
            ON CONFLICT (id) DO UPDATE
            SET chat_id=EXCLUDED.chat_id,
                thread_id=EXCLUDED.thread_id;
            """,
            (chat_id, thread_id),
        )


def db_get_global_report_dest():
    with db.cursor() as cur:
        cur.execute("SELECT chat_id, thread_id FROM global_report_dest WHERE id=1")
        row = cur.fetchone()
        if not row:
            return None
        chat_id, thread_id = row
        return int(chat_id), (int(thread_id) if thread_id is not None else None)


def db_reset_daily_sales(team: str):
    start = day_start_ph(now_ph())
    with db.cursor() as cur:
        cur.execute("DELETE FROM sales WHERE team=%s AND ts >= %s", (team, start))


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
            shift_goals[str(page)] = float(goal)

        cur.execute("SELECT page, goal FROM page_goals")
        for page, goal in cur.fetchall():
            page_goals[str(page)] = float(goal)

        cur.execute("SELECT page, shift_total, page_total FROM manual_overrides")
        for page, s, p in cur.fetchall():
            page = str(page)
            manual_shift_totals[page] = float(s)
            manual_page_totals[page] = float(p)


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
            "Not a team group yet.\n\n"
            "Owner can register this group using:\n"
            "/registerteam Team 1\n\n"
            "To see the group ID:\n"
            "/chatid"
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


async def safe_send(bot, *, chat_id: int, thread_id, text: str, parse_mode=None):
    """Sends message and logs Flood control / too-long / bad requests."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text[:TG_MAX],
            parse_mode=parse_mode,
            message_thread_id=thread_id if thread_id else None,
            disable_web_page_preview=True,
        )
    except RetryAfter as e:
        log_exc("⏳ RetryAfter (flood control)", e)
    except BadRequest as e:
        log_exc("⚠️ BadRequest", e)
    except (TimedOut, NetworkError) as e:
        log_exc("🌐 Network/TimedOut", e)
    except Exception as e:
        log_exc("❌ Send failed", e)


async def send_long_lines(update: Update, title: str, lines: list[str]):
    """Chunk long outputs so they never exceed Telegram limits."""
    if not lines:
        return await update.message.reply_text(f"{title}\n\n(none)")

    chunks = []
    current = []
    cur_len = len(title) + 2

    for ln in lines:
        add_len = len(ln) + 1
        if current and (cur_len + add_len) > TG_SAFE:
            chunks.append(current)
            current = [ln]
            cur_len = len(title) + 2 + add_len
        else:
            current.append(ln)
            cur_len += add_len

    if current:
        chunks.append(current)

    for i, ch in enumerate(chunks, 1):
        prefix = title if len(chunks) == 1 else f"{title} (Part {i}/{len(chunks)})"
        await update.message.reply_text(prefix + "\n\n" + "\n".join(ch))


def html_pre(block: str) -> str:
    """Wrap text into HTML <pre> safely."""
    return "<pre>" + html.escape(block) + "</pre>"


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
        "✅ Registered this destination for scheduled GOALBOARD reports.\n"
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


# ----------------- SALES HANDLER -----------------
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

        canonical_page = normalize_page(parts[1])
        if not canonical_page:
            bad_token = parts[1].strip().split()[0]
            unknown_tags.add(bad_token)
            continue

        db_add_sale(team, canonical_page, float(amount), ts_iso)
        db_add_team_page(team, canonical_page)  # ✅ auto-available
        saved = True

    if saved:
        await update.message.reply_text("✅ Sale recorded")

    if unknown_tags:
        bad = sorted(unknown_tags)
        allowed = sorted(ALLOWED_PAGES.keys())
        lines = (
            ["⚠️ Unknown/invalid page tag(s):"]
            + [f"• {t}" for t in bad]
            + ["", "Use ONLY these approved tags:"]
            + [f"• {t}" for t in allowed]
        )
        await send_long_lines(update, "⚠️ Invalid Tags", lines)


# ----------------- DISPLAY COMMANDS -----------------
async def pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    lines = [f"{tag} → {ALLOWED_PAGES[tag]}" for tag in sorted(ALLOWED_PAGES.keys())]
    await send_long_lines(update, f"📘 Approved Pages (use tags) — {team}", lines)


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, SUM(amount) as total
            FROM sales
            WHERE team=%s
            GROUP BY page
            ORDER BY total DESC
            """,
            (team,),
        )
        rows = cur.fetchall()

    if not rows:
        return await update.message.reply_text("No sales yet.")

    msg = f"🏆 SALES LEADERBOARD (LIFETIME by Page) — {team}\n\n"
    for i, (page, total) in enumerate(rows, 1):
        msg += f"{i}. {page} — ${float(total):.2f}\n"
    await update.message.reply_text(msg)


async def setgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    raw = update.message.text.replace("/setgoal", "", 1).strip()
    entries = [e.strip() for e in raw.replace("\n", ",").split(",") if e.strip()]
    results, errors = [], []

    for entry in entries:
        parts = entry.split()
        if len(parts) < 2:
            errors.append(entry)
            continue

        try:
            goal = float(parts[-1])
        except ValueError:
            errors.append(entry)
            continue

        page_raw = " ".join(parts[:-1])
        page = canonicalize_page_name(page_raw)
        if page is None:
            errors.append(entry)
            continue

        shift_goals[page] = goal
        db_upsert_shift_goal(page, goal)
        db_add_team_page(team, page)  # ✅ ensure visible for this team

        results.append(f"✓ {page} = ${goal:.2f}")

    msg = "🎯 Shift Goals Updated:\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)
    await update.message.reply_text(msg)


def build_goalboard_text(team: str) -> str:
    """Build GOALBOARD (current shift) text for scheduled reports."""
    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, COALESCE(SUM(amount), 0) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            """,
            (team, start),
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] += float(total)

    # manual overrides (global per page)
    for page, val in manual_shift_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    # ✅ show ONLY pages available for this team (team_pages table)
    team_pages = db_get_team_pages(team)
    pages_to_show = team_pages[:] if team_pages else sorted(totals.keys())

    # If team has pages but no totals yet, we still want to show them as $0.00
    # So we don’t early-return.
    header = (
        f"🎯 GOAL PROGRESS — {team}\n"
        f"🕒 Shift: {label}\n"
        f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
    )

    lines = []
    for page in pages_to_show:
        amt = float(totals.get(page, 0.0))
        goal = float(shift_goals.get(page, 0.0))
        if goal > 0:
            pct = (amt / goal) * 100.0 if goal else 0.0
            lines.append(f"{get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)")
        else:
            lines.append(f"⚪ {page}: ${amt:.2f} (no shift goal)")

    if not lines:
        lines = ["No pages yet for this team. Add a sale or set a goal to create team pages."]

    block = header + "\n".join(lines)
    # Use HTML <pre> for stability
    return html_pre(block)


async def goalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    # In-chat version can still be plain text (Telegram handles it fine),
    # but HTML pre looks cleaner + consistent.
    await update.message.reply_text(build_goalboard_text(team), parse_mode=ParseMode.HTML)


async def redpages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, COALESCE(SUM(amount), 0) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            """,
            (team, start),
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] += float(total)

    for page, val in manual_shift_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    team_pages = db_get_team_pages(team)
    pages_to_check = team_pages[:] if team_pages else sorted(totals.keys())

    lines = []
    for page in pages_to_check:
        goal = float(shift_goals.get(page, 0))
        if goal <= 0:
            continue
        amt = float(totals.get(page, 0.0))
        pct = (amt / goal) * 100.0
        if pct < 31:
            lines.append(f"🔴 {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)")

    if not lines:
        return await update.message.reply_text("✅ No red pages right now (this shift).")

    header = (
        f"🚨 RED PAGES — {team}\n"
        f"🕒 Shift: {label}\n"
        f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
    )
    await update.message.reply_text(html_pre(header + "\n".join(lines)), parse_mode=ParseMode.HTML)


# ----------------- BOT-ADMIN COMMANDS -----------------
async def pagegoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/pagegoal", "", 1).strip()
    entries = [e.strip() for e in raw.replace("\n", ",").split(",") if e.strip()]
    results, errors = [], []

    for entry in entries:
        parts = entry.split()
        if len(parts) < 2:
            errors.append(entry)
            continue

        try:
            goal = float(parts[-1])
        except ValueError:
            errors.append(entry)
            continue

        page_raw = " ".join(parts[:-1])
        page = canonicalize_page_name(page_raw)
        if page is None:
            errors.append(entry)
            continue

        page_goals[page] = goal
        db_upsert_page_goal(page, goal)
        db_add_team_page(team, page)  # ✅ ensure it shows for this team

        results.append(f"✓ {page} = ${goal:.2f}")

    msg = "📊 Page Goals Updated (15/30 days):\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)

    await update.message.reply_text(msg)


async def viewshiftgoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    if not shift_goals:
        return await update.message.reply_text("No shift goals set yet.\nUse: /setgoal PAGE AMOUNT")

    msg = f"🎯 SHIFT GOALS — {team}\n\n"
    for page in sorted(shift_goals.keys()):
        msg += f"• {page}: ${shift_goals[page]:.2f}\n"
    await update.message.reply_text(msg)


async def viewpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    if not page_goals:
        return await update.message.reply_text("No page goals set yet.\nUse: /pagegoal PAGE AMOUNT")

    msg = f"📊 PAGE GOALS (15/30 DAYS) — {team}\n\n"
    for page in sorted(page_goals.keys()):
        msg += f"• {page}: ${page_goals[page]:.2f}\n"
    await update.message.reply_text(msg)


async def clearshiftgoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    shift_goals.clear()
    db_clear_shift_goals()
    await update.message.reply_text("🧹 Cleared all SHIFT goals.")


async def clearpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    page_goals.clear()
    db_clear_page_goals()
    await update.message.reply_text("🧹 Cleared all PAGE goals (15/30 days).")


async def quota_period(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int, title: str):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    cutoff = now_ph() - timedelta(days=days)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, COALESCE(SUM(amount), 0) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            ORDER BY total DESC
            """,
            (team, cutoff),
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] = float(total)

    for page, val in manual_page_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    team_pages = db_get_team_pages(team)
    pages_to_show = team_pages[:] if team_pages else list(totals.keys())

    msg_lines = []
    for page in pages_to_show:
        amt = float(totals.get(page, 0.0))
        goal = float(page_goals.get(page, 0.0))
        if goal > 0:
            pct = (amt / goal) * 100.0
            msg_lines.append(f"{get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)")
        else:
            msg_lines.append(f"⚪ {page}: ${amt:.2f} (no page goal)")

    header = (
        f"📊 {title} — {team}\n"
        f"🗓️ From: {cutoff.strftime('%b %d, %Y %I:%M %p')} (PH)\n"
        f"🗓️ To: {now_ph().strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
    )

    if not msg_lines:
        return await update.message.reply_text(f"No pages yet for {team}. Add a sale or set a goal first.")

    await update.message.reply_text(html_pre(header + "\n".join(msg_lines)), parse_mode=ParseMode.HTML)


async def quotahalf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, 15, "QUOTA HALF (15 DAYS)")


async def quotamonth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, 30, "QUOTA MONTH (30 DAYS)")


async def editgoalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/editgoalboard", "", 1).strip()
    parts = raw.split()
    if len(parts) < 2:
        return await update.message.reply_text("Format: /editgoalboard PAGE AMOUNT")

    amount_str = parts[-1]
    page_str = " ".join(parts[:-1])
    page = canonicalize_page_name(page_str)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    try:
        amount = float(amount_str)
    except ValueError:
        return await update.message.reply_text("Amount must be a number.")

    manual_shift_totals[page] = amount
    manual_page_totals[page] = amount
    db_upsert_override(page, shift_total=amount, page_total=amount)
    db_add_team_page(team, page)

    await update.message.reply_text(
        f"✅ Updated totals\n"
        f"Goalboard (shift): {page} = ${amount:.2f}\n"
        f"Quotas (15/30): {page} = ${amount:.2f}"
    )


async def editpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/editpagegoals", "", 1).strip()
    parts = raw.split()
    if len(parts) < 2:
        return await update.message.reply_text("Format: /editpagegoals PAGE AMOUNT")

    amount_str = parts[-1]
    page_str = " ".join(parts[:-1])
    page = canonicalize_page_name(page_str)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    try:
        amount = float(amount_str)
    except ValueError:
        return await update.message.reply_text("Amount must be a number.")

    manual_page_totals[page] = amount
    db_upsert_override(page, page_total=amount)
    db_add_team_page(team, page)

    await update.message.reply_text(f"✅ Updated quotas\n{page} = ${amount:.2f} (15/30 days)")


async def cleargoalboardoverride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/cleargoalboardoverride", "", 1).strip()
    if not raw:
        return await update.message.reply_text(
            "Format: /cleargoalboardoverride PAGE\nExample: /cleargoalboardoverride AUTUMN PAID"
        )

    page = canonicalize_page_name(raw)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    manual_shift_totals[page] = 0.0
    db_clear_override_shift(page)
    await update.message.reply_text(f"✅ Cleared goalboard override for {page}.")


async def clearpageoverride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/clearpageoverride", "", 1).strip()
    if not raw:
        return await update.message.reply_text(
            "Format: /clearpageoverride PAGE\nExample: /clearpageoverride AUTUMN PAID"
        )

    page = canonicalize_page_name(raw)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    manual_page_totals[page] = 0.0
    db_clear_override_page(page)
    await update.message.reply_text(f"✅ Cleared quota override for {page}.")


# ----------------- OWNER: LIST TEAMS / DELETE TEAM -----------------
def db_list_team_details():
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT name, COUNT(*) AS groups
            FROM teams
            GROUP BY name
            ORDER BY name ASC
            """
        )
        return [(str(n), int(c)) for (n, c) in cur.fetchall()]


def db_delete_team_by_name(team_name: str):
    with db.cursor() as cur:
        cur.execute("DELETE FROM teams WHERE name=%s", (team_name,))
        cur.execute("DELETE FROM report_groups WHERE team=%s", (team_name,))
        cur.execute("DELETE FROM team_pages WHERE team=%s", (team_name,))
        # Sales history stays by design. Uncomment to delete sales too:
        # cur.execute("DELETE FROM sales WHERE team=%s", (team_name,))


async def listteams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    items = db_list_team_details()
    if not items:
        return await update.message.reply_text("No teams registered yet.")

    msg = "📋 REGISTERED TEAMS\n\n"
    for i, (name, count) in enumerate(items, 1):
        msg += f"{i}. {name} — {count} group(s)\n"
    msg += "\nTip: /deleteteam <team name>"
    await update.message.reply_text(msg)


async def deleteteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    team_name = clean(" ".join(context.args)).strip()
    if not team_name:
        return await update.message.reply_text("Format: /deleteteam Team 1")

    db_delete_team_by_name(team_name)
    # refresh cache
    load_from_db()
    await update.message.reply_text(
        f"🗑️ Deleted team mappings for: {team_name}\n"
        "Note: Sales history is kept by design."
    )


async def reloadcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    load_from_db()
    await update.message.reply_text("✅ Reloaded cache from DB.")


# ----------------- SCHEDULED REPORTS -----------------
SCHEDULE_HOURS_PH = [8, 10, 12, 14, 16, 18, 20, 22]


async def scheduled_goalboard_run(context: ContextTypes.DEFAULT_TYPE):
    """Runs on schedule and posts goalboards (GLOBAL or per-team destinations)."""
    bot = context.bot

    # Prefer GLOBAL destination if set
    global_dest = None
    try:
        global_dest = db_get_global_report_dest()
    except Exception as e:
        log_exc("❌ DB error reading global_report_dest", e)

    # Load destinations for teams
    try:
        team_dests = db_get_report_groups()
    except Exception as e:
        log_exc("❌ DB error reading report_groups", e)
        team_dests = []

    # Get list of all teams currently in teams table
    # We use the GROUP_TEAMS cache to know registered group -> team name
    # But we also want distinct team names:
    try:
        with db.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM teams ORDER BY name ASC")
            all_team_names = [str(r[0]) for r in cur.fetchall()]
    except Exception as e:
        log_exc("❌ DB error reading distinct team names", e)
        all_team_names = []

    # Destination map for per-team
    per_team_dest_map = {t: (cid, th) for (t, cid, th) in team_dests}

    # If GLOBAL is set, send ALL teams there
    if global_dest:
        gc_chat_id, gc_thread_id = global_dest
        for team in all_team_names:
            try:
                text = build_goalboard_text(team)
                await safe_send(bot, chat_id=gc_chat_id, thread_id=gc_thread_id, text=text, parse_mode=ParseMode.HTML)
            except Exception as e:
                log_exc(f"❌ Scheduled send failed (GLOBAL) team={team}", e)
        return

    # Otherwise: send only to teams that have /registergoal destination
    for team, (chat_id, thread_id) in per_team_dest_map.items():
        try:
            text = build_goalboard_text(team)
            await safe_send(bot, chat_id=chat_id, thread_id=thread_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log_exc(f"❌ Scheduled send failed team={team}", e)


def schedule_jobs(app):
    """Schedules the goalboard at fixed PH hours every day."""
    for h in SCHEDULE_HOURS_PH:
        app.job_queue.run_daily(
            scheduled_goalboard_run,
            time=time(hour=h, minute=0, second=0, tzinfo=PH_TZ),
            name=f"goalboard_{h:02d}00_PH",
        )
    print("✅ Scheduled jobs:", ", ".join([f"{h:02d}:00" for h in SCHEDULE_HOURS_PH]), "PH")


# ----------------- MAIN -----------------
def main():
    init_db()
    load_from_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Error handler
    app.add_error_handler(error_handler)

    # Basic
    app.add_handler(CommandHandler("chatid", chatid))

    # Owner
    app.add_handler(CommandHandler("registerteam", registerteam))
    app.add_handler(CommandHandler("unregisterteam", unregisterteam))
    app.add_handler(CommandHandler("registeradmin", registeradmin))
    app.add_handler(CommandHandler("unregisteradmin", unregisteradmin))
    app.add_handler(CommandHandler("listadmins", listadmins))
    app.add_handler(CommandHandler("registergoal", registergoal))
    app.add_handler(CommandHandler("registergoalall", registergoalall))
    app.add_handler(CommandHandler("resetdaily", resetdaily))
    app.add_handler(CommandHandler("listteams", listteams))
    app.add_handler(CommandHandler("deleteteam", deleteteam))
    app.add_handler(CommandHandler("reloadcache", reloadcache))

    # Everyone
    app.add_handler(CommandHandler("pages", pages))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("setgoal", setgoal))
    app.add_handler(CommandHandler("goalboard", goalboard))
    app.add_handler(CommandHandler("redpages", redpages))

    # Bot-admins
    app.add_handler(CommandHandler("pagegoal", pagegoal))
    app.add_handler(CommandHandler("viewshiftgoals", viewshiftgoals))
    app.add_handler(CommandHandler("viewpagegoals", viewpagegoals))
    app.add_handler(CommandHandler("clearshiftgoals", clearshiftgoals))
    app.add_handler(CommandHandler("clearpagegoals", clearpagegoals))
    app.add_handler(CommandHandler("quotahalf", quotahalf))
    app.add_handler(CommandHandler("quotamonth", quotamonth))
    app.add_handler(CommandHandler("editgoalboard", editgoalboard))
    app.add_handler(CommandHandler("editpagegoals", editpagegoals))
    app.add_handler(CommandHandler("cleargoalboardoverride", cleargoalboardoverride))
    app.add_handler(CommandHandler("clearpageoverride", clearpageoverride))

    # Sales lines like: +100 #autumnpaid
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))

    # Schedule reports
    schedule_jobs(app)

    print("🤖 Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
