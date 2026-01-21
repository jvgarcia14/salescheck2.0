# ==========================================
#   ULTIMATE SALES + GOAL BOT (RAILWAY) - DB VERSION + AUTO GOALBOARD REPORTS
#   - Saves sales/goals/admins/teams/overrides to Postgres
#   - Loads everything from DB on startup
#   - Auto-sends GOALBOARD TABLE to a registered GC every 2 hours (8AM, 10AM, 12PM... PH)
#
#   OWNER ONLY (OWNER_ID):
#     /registerteam Team 1
#     /unregisterteam
#     /registeradmin 1   (reply to user to register them)
#     /unregisteradmin   (reply or /unregisteradmin <user_id>)
#     /listadmins
#     /registergoal 1    (run inside the GC that should receive scheduled goalboard reports)
#
#   EVERYONE (in registered team groups):
#     Sales input: +amount #tag
#     /pages, /leaderboard, /goalboard, /redpages, /setgoal (SHIFT GOALS)
#
#   BOT-ADMINS (level >= 1):
#     /pagegoal (PERIOD GOALS for 15/30)
#     /viewshiftgoals, /viewpagegoals
#     /clearshiftgoals, /clearpagegoals
#     /quotahalf, /quotamonth
#     /editgoalboard, /editpagegoals
#     /cleargoalboardoverride, /clearpageoverride
# ==========================================

11from telegram import Update
11from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from collections import defaultdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import os
import psycopg2

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

# ----------------- PAGES -----------------
ALLOWED_PAGES = {
    "#alannafreeoftv": "Alanna Free / OFTV",
    "#alannapaid": "Alanna Paid",
    "#alannawelcome": "Alanna Welcome",

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
    "#cocopaid": "Coco Paid",  # FIXED: was "#cocopaID" which would never match .lower()

    "#cyndiecynthiacolby": "Cyndie, Cynthia & Colby",

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

# goals
shift_goals = defaultdict(float)  # page -> goal
page_goals = defaultdict(float)   # page -> goal

# manual overrides
manual_shift_totals = defaultdict(float)  # page -> override amount
manual_page_totals = defaultdict(float)   # page -> override amount

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

def parse_ts(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str)

def split_internal(internal: str):
    parts = internal.split("|", 1)
    if len(parts) != 2:
        return internal, ""
    return parts[0], parts[1]

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
    t = dt.timetz()
    if time(8, 0, tzinfo=PH_TZ) <= t < time(16, 0, tzinfo=PH_TZ):
        return "Prime (8AM–4PM)"
    if time(16, 0, tzinfo=PH_TZ) <= t < time(23, 59, 59, tzinfo=PH_TZ):
        return "Midshift (4PM–12AM)"
    return "Closing (12AM–8AM)"

def shift_start(dt: datetime) -> datetime:
    d = dt.date()
    t = dt.timetz()
    if time(8, 0, tzinfo=PH_TZ) <= t < time(16, 0, tzinfo=PH_TZ):
        return datetime.combine(d, time(8, 0), PH_TZ)
    if time(16, 0, tzinfo=PH_TZ) <= t < time(23, 59, 59, tzinfo=PH_TZ):
        return datetime.combine(d, time(16, 0), PH_TZ)
    return datetime.combine(d, time(0, 0), PH_TZ)

def get_team(chat_id: int):
    return GROUP_TEAMS.get(chat_id)

def is_owner(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id == OWNER_ID

def get_color(p):
    if p >= 100: return "💚"
    if p >= 90: return "🟢"
    if p >= 61: return "🔵"
    if p >= 31: return "🟡"
    if p >= 11: return "🟠"
    return "🔴"

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

        -- NEW: which GC receives scheduled goalboard reports (per team)
        CREATE TABLE IF NOT EXISTS report_groups (
            team TEXT PRIMARY KEY,
            chat_id BIGINT NOT NULL
        );
        """)

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
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sales (team, page, amount, ts) VALUES (%s, %s, %s, %s)",
            (team, page, amount, ts_iso)
        )

def db_upsert_page_goal(page: str, goal: float):
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

def db_upsert_override(page: str, shift_total: float | None = None, page_total: float | None = None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO manual_overrides (page, shift_total, page_total) VALUES (%s, 0, 0) ON CONFLICT (page) DO NOTHING",
            (page,)
        )
        if shift_total is not None:
            cur.execute("UPDATE manual_overrides SET shift_total=%s WHERE page=%s", (shift_total, page))
        if page_total is not None:
            cur.execute("UPDATE manual_overrides SET page_total=%s WHERE page=%s", (page_total, page))

def db_clear_override_shift(page: str):
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET shift_total=0 WHERE page=%s", (page,))
        cur.execute("DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0", (page,))

def db_clear_override_page(page: str):
    with db.cursor() as cur:
        cur.execute("UPDATE manual_overrides SET page_total=0 WHERE page=%s", (page,))
        cur.execute("DELETE FROM manual_overrides WHERE page=%s AND shift_total=0 AND page_total=0", (page,))

def db_set_report_group(team: str, chat_id: int):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO report_groups (team, chat_id)
            VALUES (%s, %s)
            ON CONFLICT (team)
            DO UPDATE SET chat_id = EXCLUDED.chat_id;
            """,
            (team, chat_id)
        )

def db_get_report_groups():
    with db.cursor() as cur:
        cur.execute("SELECT team, chat_id FROM report_groups")
        return [(str(t), int(cid)) for (t, cid) in cur.fetchall()]

def load_from_db():
    GROUP_TEAMS.clear()
    CHAT_ADMINS.clear()
    shift_goals.clear()
    page_goals.clear()
    manual_shift_totals.clear()
    manual_page_totals.clear()

    with db.cursor() as cur:
        # teams
        cur.execute("SELECT chat_id, name FROM teams")
        for chat_id, name in cur.fetchall():
            GROUP_TEAMS[int(chat_id)] = str(name)

        # admins
        cur.execute("SELECT chat_id, user_id, level FROM admins")
        for chat_id, user_id, level in cur.fetchall():
            CHAT_ADMINS[int(chat_id)][int(user_id)] = int(level)

        # goals
        cur.execute("SELECT page, goal FROM shift_goals")
        for page, goal in cur.fetchall():
            shift_goals[str(page)] = float(goal)

        cur.execute("SELECT page, goal FROM page_goals")
        for page, goal in cur.fetchall():
            page_goals[str(page)] = float(goal)

        # overrides
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
            "Not a team group yet.\n\nOwner can register this group using:\n/registerteam Team 1\n\nTo see the group ID:\n/chatid"
        )
        return None
    return team

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

    chat_id = update.effective_chat.id

    GROUP_TEAMS[chat_id] = team_name
    db_register_team(chat_id, team_name)

    return await update.message.reply_text(
        f"✅ Registered this group!\nTeam: {team_name}\nChat ID: {chat_id}\nNext: /registeradmin 1"
    )

async def unregisterteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id = update.effective_chat.id
    if chat_id not in GROUP_TEAMS:
        return await update.message.reply_text("This group is not registered.")

    team = GROUP_TEAMS.pop(chat_id, None)
    if chat_id in CHAT_ADMINS:
        del CHAT_ADMINS[chat_id]

    db_delete_team(chat_id)
    await update.message.reply_text(f"🗑️ Team unregistered.\nRemoved team: {team}\nChat ID: {chat_id}")

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

    chat_id = update.effective_chat.id
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
    else:
        target_user = update.effective_user

    CHAT_ADMINS[chat_id][target_user.id] = level
    db_upsert_admin(chat_id, target_user.id, level)

    name = clean(target_user.username or target_user.first_name or str(target_user.id))
    await update.message.reply_text(f"✅ Registered bot-admin: {name} (level {level})")

async def unregisteradmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id = update.effective_chat.id
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

    if target_id not in CHAT_ADMINS.get(chat_id, {}):
        return await update.message.reply_text("That user is not a bot-admin in this group.")

    del CHAT_ADMINS[chat_id][target_id]
    db_delete_admin(chat_id, target_id)
    await update.message.reply_text(f"🗑️ Removed bot-admin access for: {target_label}")

async def listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id = update.effective_chat.id
    admins = CHAT_ADMINS.get(chat_id, {})
    if not admins:
        return await update.message.reply_text("No bot-admins registered in this group.")

    lines = []
    for uid, lvl in sorted(admins.items(), key=lambda x: (-int(x[1]), int(x[0]))):
        lines.append(f"• User ID: {uid} — level {int(lvl)}")
    await update.message.reply_text("👑 Bot Admins (this group):\n\n" + "\n".join(lines))

async def registergoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    OWNER runs this in the GC that should RECEIVE the scheduled goalboard reports.
    Example: /registergoal 1  -> registers this GC for Team 1
             /registergoal Team 1 -> also works
    """
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the target GC (not in private).")
    if not await require_owner(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Format: /registergoal 1\n"
            "This registers THIS GC as the scheduled goalboard report group for Team 1."
        )

    arg = clean(" ".join(context.args)).strip()
    team = f"Team {arg}" if arg.isdigit() else arg

    db_set_report_group(team, update.effective_chat.id)
    await update.message.reply_text(
        f"✅ Registered this GC for scheduled GOALBOARD reports.\nTeam: {team}\n"
        "Schedule: 8AM, 10AM, 12PM, 2PM, 4PM, 6PM, 8PM, 10PM (PH)"
    )

# ----------------- SALES HANDLER -----------------
async def handle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    team = get_team(update.effective_chat.id)
    if team is None:
        return

    user = update.message.from_user
    username = clean(user.username or user.first_name)
    _internal = f"{username}|{team}"

    saved = False
    unknown_tags = set()
    ts_iso = now_ph().isoformat()

    for line in update.message.text.splitlines():
        line = line.strip()
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
        saved = True

    if saved:
        await update.message.reply_text("✅ Sale recorded")

    if unknown_tags:
        allowed = "\n".join(sorted(ALLOWED_PAGES.keys()))
        bad = "\n".join(sorted(unknown_tags))
        await update.message.reply_text(
            "⚠️ Unknown/invalid page tag(s):\n"
            f"{bad}\n\nUse ONLY these approved tags:\n{allowed}"
        )

# ----------------- DISPLAY COMMANDS -----------------
async def pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    lines = [f"{tag} → {ALLOWED_PAGES[tag]}" for tag in sorted(ALLOWED_PAGES.keys())]
    await update.message.reply_text(f"📘 Approved Pages (use tags) — {team}\n\n" + "\n".join(lines))

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
            (team,)
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

        page = clean(" ".join(parts[:-1]))
        shift_goals[page] = goal
        db_upsert_shift_goal(page, goal)
        results.append(f"✓ {page} = ${goal:.2f}")

    msg = "🎯 Shift Goals Updated:\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)
    await update.message.reply_text(msg)

async def goalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, SUM(amount) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            """,
            (team, start)
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] += float(total)

    for page, val in manual_shift_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    if not totals:
        return await update.message.reply_text(
            f"🎯 GOAL PROGRESS — {team}\n🕒 Shift: {label}\n✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\nNo sales yet for this shift."
        )

    msg = f"🎯 GOAL PROGRESS — {team}\n🕒 Shift: {label}\n✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
    for page, amt in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        goal = shift_goals.get(page, 0)
        if goal:
            pct = (amt / goal) * 100
            msg += f"{get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"
        else:
            msg += f"⚪ {page}: ${amt:.2f} (no shift goal)\n"

    await update.message.reply_text(msg)

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
            SELECT page, SUM(amount) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            """,
            (team, start)
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] += float(total)

    for page, val in manual_shift_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    msg = f"🚨 RED PAGES — {team}\n🕒 Shift: {label}\n✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
    any_found = False
    for page, amt in sorted(totals.items()):
        goal = shift_goals.get(page, 0)
        if goal <= 0:
            continue
        pct = (amt / goal) * 100
        if pct < 31:
            any_found = True
            msg += f"🔴 {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"

    if not any_found:
        return await update.message.reply_text("✅ No red pages right now (this shift).")
    await update.message.reply_text(msg)

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

        page = clean(" ".join(parts[:-1]))
        page_goals[page] = goal
        db_upsert_page_goal(page, goal)
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
            SELECT page, SUM(amount) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            ORDER BY total DESC
            """,
            (team, cutoff)
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] = float(total)

    for page, val in manual_page_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    if not totals:
        return await update.message.reply_text(f"No sales found for the last {days} days.")

    msg = f"📊 {title} — {team}\n"
    msg += f"🗓️ From: {cutoff.strftime('%b %d, %Y %I:%M %p')} (PH)\n"
    msg += f"🗓️ To:   {now_ph().strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

    for page, amt in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        goal = page_goals.get(page, 0)
        if goal:
            pct = (amt / goal) * 100
            msg += f"{get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"
        else:
            msg += f"⚪ {page}: ${amt:.2f} (no page goal)\n"

    await update.message.reply_text(msg)

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

    await update.message.reply_text(
        f"✅ Updated totals\nGoalboard (shift): {page} = ${amount:.2f}\nQuotas (15/30): {page} = ${amount:.2f}"
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

    await update.message.reply_text(f"✅ Updated quotas\n{page} = ${amount:.2f} (15/30 days)")

async def cleargoalboardoverride(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/cleargoalboardoverride", "", 1).strip()
    if not raw:
        return await update.message.reply_text("Format: /cleargoalboardoverride PAGE\nExample: /cleargoalboardoverride AUTUMN PAID")

    page = canonicalize_page_name(raw)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    manual_shift_totals[page] = 0
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
        return await update.message.reply_text("Format: /clearpageoverride PAGE\nExample: /clearpageoverride AUTUMN PAID")

    page = canonicalize_page_name(raw)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    manual_page_totals[page] = 0
    db_clear_override_page(page)
    await update.message.reply_text(f"✅ Cleared quota override for {page}.")

# ----------------- SCHEDULED GOALBOARD (TABLE) -----------------
def _build_goalboard_table_lines(team: str, start: datetime):
    """
    Returns:
      header_text: str
      rows: list[str]  (each row already formatted monospaced)
    """
    now = now_ph()
    label = current_shift_label(now)

    # shift totals from DB
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT page, COALESCE(SUM(amount), 0) AS total
            FROM sales
            WHERE team=%s AND ts >= %s
            GROUP BY page
            """,
            (team, start)
        )
        rows = cur.fetchall()

    totals = defaultdict(float)
    for page, total in rows:
        totals[str(page)] = float(total)

    # apply overrides (non-zero)
    for page, val in manual_shift_totals.items():
        if float(val) != 0:
            totals[page] = float(val)

    # include ALL pages (even if 0 sales)
    all_pages = sorted(set(ALLOWED_PAGES.values()) | set(shift_goals.keys()) | set(totals.keys()))

    # column widths (tweak-safe)
    # page names can be long, so we cap them to keep the table readable in Telegram
    PAGE_W = 26
    SALES_W = 10
    GOAL_W = 10
    PCT_W = 7
    STAT_W = 2

    def trunc(s: str, w: int):
        s = str(s)
        if len(s) <= w:
            return s.ljust(w)
        return (s[: w - 1] + "…")  # keep width-ish; Telegram monospace is fine

    table_rows = []
    grand_sales = 0.0
    grand_goal = 0.0

    for page in all_pages:
        amt = float(totals.get(page, 0.0))
        goal = float(shift_goals.get(page, 0.0))

        pct = (amt / goal * 100.0) if goal > 0 else 0.0
        color = get_color(pct) if goal > 0 else "⚪"

        grand_sales += amt
        if goal > 0:
            grand_goal += goal

        row = (
            f"{color} "
            f"{trunc(page, PAGE_W)} "
            f"{('$' + format(amt, '.2f')).rjust(SALES_W)} "
            f"{('$' + format(goal, '.2f')).rjust(GOAL_W) if goal > 0 else ' ' * GOAL_W} "
            f"{(format(pct, '.1f') + '%').rjust(PCT_W) if goal > 0 else ' ' * PCT_W}"
        )
        table_rows.append(row)

    header_text = (
        f"🎯 GOALBOARD — {team}\n"
        f"🕒 Shift: {label}\n"
        f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n"
        f"📌 Updated: {now.strftime('%b %d, %Y %I:%M %p')} (PH)\n"
        f"💰 Shift Total: ${grand_sales:.2f}\n"
    )

    # table header line
    col_header = (
        f"   {'PAGE'.ljust(PAGE_W)} "
        f"{'SALES'.rjust(SALES_W)} "
        f"{'GOAL'.rjust(GOAL_W)} "
        f"{'%'.rjust(PCT_W)}"
    )
    sep = "-" * (3 + PAGE_W + 1 + SALES_W + 1 + GOAL_W + 1 + PCT_W)

    return header_text, [col_header, sep] + table_rows

async def send_scheduled_goalboard(context: ContextTypes.DEFAULT_TYPE):
    """
    Sends the GOALBOARD TABLE (all pages) to each registered report GC.
    Splits into chunks of 50 rows per message.
    """
    now = now_ph()
    start = shift_start(now)

    report_groups = db_get_report_groups()
    if not report_groups:
        return

    for team, chat_id in report_groups:
        header_text, lines = _build_goalboard_table_lines(team, start)

        # chunk rows: Telegram message length limit exists; also user asked 50 rows per message
        chunk_size = 50
        # lines includes table header + sep + data rows
        table_head = lines[:2]  # header + sep
        data_rows = lines[2:]

        # if there are no pages for some reason
        if not data_rows:
            msg = header_text + "\nNo pages found."
            try:
                await context.application.bot.send_message(chat_id=chat_id, text=msg)
            except Exception:
                pass
            continue

        # send in chunks
        for i in range(0, len(data_rows), chunk_size):
            chunk = data_rows[i:i + chunk_size]
            part = (i // chunk_size) + 1
            total_parts = ((len(data_rows) - 1) // chunk_size) + 1

            # Put the big header only on the first message to avoid spam
            prefix = header_text if i == 0 else f"🎯 GOALBOARD — {team} (Part {part}/{total_parts})\n"

            msg = prefix + "\n" + "```\n" + "\n".join(table_head + chunk) + "\n```"

            try:
                await context.application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            except Exception:
                # fallback without Markdown if Telegram complains
                try:
                    msg2 = prefix + "\n" + "\n".join(table_head + chunk)
                    await context.application.bot.send_message(chat_id=chat_id, text=msg2)
                except Exception:
                    pass

# ----------------- START -----------------
def main():
    init_db()
    load_from_db()

    # IMPORTANT: timezone set so run_daily uses PH time correctly
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # sales input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))

    # basic
    app.add_handler(CommandHandler("chatid", chatid))

    # owner-only
    app.add_handler(CommandHandler("registerteam", registerteam))
    app.add_handler(CommandHandler("unregisterteam", unregisterteam))
    app.add_handler(CommandHandler("registeradmin", registeradmin))
    app.add_handler(CommandHandler("unregisteradmin", unregisteradmin))
    app.add_handler(CommandHandler("listadmins", listadmins))
    app.add_handler(CommandHandler("registergoal", registergoal))  # NEW

    # everyone
    app.add_handler(CommandHandler("pages", pages))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("goalboard", goalboard))
    app.add_handler(CommandHandler("redpages", redpages))
    app.add_handler(CommandHandler("setgoal", setgoal))

    # bot-admin
    app.add_handler(CommandHandler("pagegoal", pagegoal))
    app.add_handler(CommandHandler("viewshiftgoals", viewshiftgoals))
    app.add_handler(CommandHandler("viewpagegoals", viewpagegoals))
    app.add_handler(CommandHandler("clearshiftgoals", clearshiftgoals))
    app.add_handler(CommandHandler("clearpagegoals", clearpagegoals))
    app.add_handler(CommandHandler("quotahalf", quotahalf))
    app.add_handler(CommandHandler("quotamonth", quotamonth))
    app.add_handler(CommandHandler("editgoalboard", editgoalboard))
    app.add_handler(CommandHandler("editpagegoals", editpagegoals))

    # single-page override clearing
    app.add_handler(CommandHandler("cleargoalboardoverride", cleargoalboardoverride))
    app.add_handler(CommandHandler("clearpageoverride", clearpageoverride))

    # SCHEDULE: every 2 hours starting 8AM (PH)
    report_hours = [8, 10, 12, 14, 16, 18, 20, 22]
    for h in report_hours:
        app.job_queue.run_daily(
            send_scheduled_goalboard,
            time=time(h, 0, tzinfo=PH_TZ),
            name=f"scheduled_goalboard_{h:02d}00_ph"
        )

    print("BOT RUNNING…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()



