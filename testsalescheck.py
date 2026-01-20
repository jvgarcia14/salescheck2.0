# ==========================================
#   ULTIMATE SALES + GOAL BOT (RAILWAY)
#   + MOBILE API (FastAPI) ADDED (FIXED)
# ==========================================

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from collections import defaultdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import json, os, threading
from typing import Optional, Dict, Any, List

# ---------------------------
# FASTAPI (for mobile app)
# ---------------------------
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


# -------------------------------------------------
# OWNER
# -------------------------------------------------
OWNER_ID = 5513230302

# -------------------------------------------------
# TIMEZONE (PH)
# -------------------------------------------------
PH_TZ = ZoneInfo("Asia/Manila")

# -------------------------------------------------
# FILES
# -------------------------------------------------
TEAMS_FILE = "teams.json"
ADMINS_FILE = "admins.json"
GOALS_FILE = "goals.json"
SALES_FILE = "sales.json"
SALES_LOG_FILE = "sales_log.json"
MANUAL_OVERRIDES_FILE = "manual_overrides.json"

# -------------------------------------------------
# DEFAULT TEAM MAP (optional)
# -------------------------------------------------
DEFAULT_GROUP_TEAMS = {
    # -1001234567890: "Team 1",
}

# -------------------------------------------------
# PAGE TAGS (enforced)
# -------------------------------------------------
ALLOWED_PAGES = {
    "#autumnpaid": "AUTUMN PAID",
    "#autumnfree": "AUTUMN FREE",
}

# -------------------------------------------------
# DATA STORAGE (in-memory; persisted to JSON)
# -------------------------------------------------
GROUP_TEAMS = dict(DEFAULT_GROUP_TEAMS)
CHAT_ADMINS = defaultdict(dict)

sales_data = defaultdict(lambda: defaultdict(float))
shift_goals = defaultdict(float)
page_goals = defaultdict(float)
sales_log = []

manual_shift_totals = defaultdict(float)
manual_page_totals = defaultdict(float)


# -------------------------------------------------
# UTIL
# -------------------------------------------------
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

def canonicalize_page_name(page_str: str) -> str | None:
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


# -------------------------------------------------
# GOAL COLOR
# -------------------------------------------------
def get_color(p):
    if p >= 100: return "💚"
    if p >= 90: return "🟢"
    if p >= 61: return "🔵"
    if p >= 31: return "🟡"
    if p >= 11: return "🟠"
    return "🔴"


# -------------------------------------------------
# PERSISTENCE
# -------------------------------------------------
def save_json(path: str, obj: Any):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def save_all():
    save_json(SALES_FILE, {u: dict(p) for u, p in sales_data.items()})
    save_json(GOALS_FILE, {"shift_goals": dict(shift_goals), "page_goals": dict(page_goals)})
    save_json(SALES_LOG_FILE, sales_log)
    save_json(MANUAL_OVERRIDES_FILE, {"shift": dict(manual_shift_totals), "page": dict(manual_page_totals)})

def load_all():
    if os.path.exists(SALES_FILE):
        with open(SALES_FILE, "r") as f:
            raw = json.load(f)
            for u, pages in raw.items():
                for page, val in pages.items():
                    sales_data[u][page] = float(val)

    if os.path.exists(GOALS_FILE):
        with open(GOALS_FILE, "r") as f:
            raw = json.load(f)
            if isinstance(raw, dict) and "shift_goals" in raw and "page_goals" in raw:
                for page, goal in raw.get("shift_goals", {}).items():
                    shift_goals[page] = float(goal)
                for page, goal in raw.get("page_goals", {}).items():
                    page_goals[page] = float(goal)
            else:
                for page, goal in raw.items():
                    page_goals[page] = float(goal)

    if os.path.exists(SALES_LOG_FILE):
        with open(SALES_LOG_FILE, "r") as f:
            raw = json.load(f)
            if isinstance(raw, list):
                sales_log[:] = raw

    if os.path.exists(MANUAL_OVERRIDES_FILE):
        with open(MANUAL_OVERRIDES_FILE, "r") as f:
            raw = json.load(f)
            manual_shift_totals.clear()
            manual_page_totals.clear()
            for p, v in raw.get("shift", {}).items():
                manual_shift_totals[p] = float(v)
            for p, v in raw.get("page", {}).items():
                manual_page_totals[p] = float(v)

def save_teams():
    save_json(TEAMS_FILE, {str(k): v for k, v in GROUP_TEAMS.items()})

def load_teams():
    global GROUP_TEAMS
    GROUP_TEAMS = dict(DEFAULT_GROUP_TEAMS)
    if os.path.exists(TEAMS_FILE):
        with open(TEAMS_FILE, "r") as f:
            raw = json.load(f)
            for k, v in raw.items():
                try:
                    GROUP_TEAMS[int(k)] = str(v)
                except Exception:
                    continue

def save_admins():
    data = {}
    for chat_id, users in CHAT_ADMINS.items():
        data[str(chat_id)] = {str(uid): int(level) for uid, level in users.items()}
    save_json(ADMINS_FILE, data)

def load_admins():
    CHAT_ADMINS.clear()
    if os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "r") as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                for chat_id_str, users in raw.items():
                    try:
                        chat_id = int(chat_id_str)
                    except Exception:
                        continue
                    if isinstance(users, dict):
                        for uid_str, lvl in users.items():
                            try:
                                CHAT_ADMINS[chat_id][int(uid_str)] = int(lvl)
                            except Exception:
                                continue


# -------------------------------------------------
# ACCESS CONTROL
# -------------------------------------------------
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

async def require_team(update: Update) -> str | None:
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


# -------------------------------------------------
# BASIC: /chatid
# -------------------------------------------------
async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat type: {chat.type}\nChat ID: {chat.id}")


# -------------------------------------------------
# OWNER ONLY: /registerteam Team 1
# -------------------------------------------------
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
    save_teams()

    await update.message.reply_text(
        f"✅ Registered this group!\n\nTeam: {team_name}\nChat ID: {chat_id}\n\nNext: /registeradmin 1"
    )

# -------------------------------------------------
# OWNER ONLY: /unregisterteam
# -------------------------------------------------
async def unregisterteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    chat_id = update.effective_chat.id
    if chat_id not in GROUP_TEAMS:
        return await update.message.reply_text("This group is not registered.")

    team = GROUP_TEAMS.pop(chat_id, None)
    save_teams()

    if chat_id in CHAT_ADMINS:
        del CHAT_ADMINS[chat_id]
        save_admins()

    await update.message.reply_text(f"🗑️ Team unregistered.\nRemoved team: {team}\nChat ID: {chat_id}")

# -------------------------------------------------
# OWNER ONLY: /registeradmin 1 (reply to user)
# -------------------------------------------------
async def registeradmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")
    if not await require_owner(update):
        return

    if not context.args:
        return await update.message.reply_text(
            "Format: /registeradmin 1\nTip: reply to a user then run /registeradmin 1"
        )

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
    save_admins()

    name = clean(target_user.username or target_user.first_name or str(target_user.id))
    await update.message.reply_text(f"✅ Registered bot-admin: {name} (level {level})")

# -------------------------------------------------
# OWNER ONLY: /unregisteradmin
# -------------------------------------------------
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
    save_admins()
    await update.message.reply_text(f"🗑️ Removed bot-admin access for: {target_label}")

# -------------------------------------------------
# OWNER ONLY: /listadmins
# -------------------------------------------------
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


# -------------------------------------------------
# SALES INPUT: +amount #tag
# -------------------------------------------------
async def handle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    team = get_team(update.effective_chat.id)
    if team is None:
        return

    user = update.message.from_user
    username = clean(user.username or user.first_name)
    internal = f"{username}|{team}"

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

        sales_log.append({
            "ts": ts_iso,
            "user": internal,
            "page": canonical_page,
            "amt": float(amount),
        })

        sales_data[internal][canonical_page] = sales_data[internal].get(canonical_page, 0.0) + float(amount)
        saved = True

    if saved:
        save_all()
        await update.message.reply_text("✅ Sale recorded")

    if unknown_tags:
        allowed = "\n".join(sorted(ALLOWED_PAGES.keys()))
        bad = "\n".join(sorted(unknown_tags))
        await update.message.reply_text(
            "⚠️ Unknown/invalid page tag(s):\n"
            f"{bad}\n\n"
            "Use ONLY these approved tags:\n"
            f"{allowed}"
        )


# -------------------------------------------------
# /PAGES
# -------------------------------------------------
async def pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    if not ALLOWED_PAGES:
        return await update.message.reply_text("No allowed pages configured.")

    lines = [f"{tag} → {ALLOWED_PAGES[tag]}" for tag in sorted(ALLOWED_PAGES.keys())]
    await update.message.reply_text(f"📘 Approved Pages (use tags) — {team}\n\n" + "\n".join(lines))


# -------------------------------------------------
# /LEADERBOARD (lifetime)
# -------------------------------------------------
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    rows = []
    for internal, pages_map in sales_data.items():
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        for page, amt in pages_map.items():
            rows.append((uname, page, amt))

    if not rows:
        return await update.message.reply_text("No sales yet.")

    rows.sort(key=lambda x: x[2], reverse=True)

    msg = f"🏆 SALES LEADERBOARD (LIFETIME) — {team}\n\n"
    for i, (u, p, a) in enumerate(rows, 1):
        msg += f"{i}. {u} ({p}) — ${a:.2f}\n"
    await update.message.reply_text(msg)


# -------------------------------------------------
# /SETGOAL (shift goals)
# -------------------------------------------------
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
        results.append(f"✓ {page} = ${goal:.2f}")

    save_all()

    msg = "🎯 Shift Goals Updated:\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)
    await update.message.reply_text(msg)


# -------------------------------------------------
# /PAGEGOAL (period goals)
# -------------------------------------------------
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
        results.append(f"✓ {page} = ${goal:.2f}")

    save_all()

    msg = "📊 Page Goals Updated (15/30 days):\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)
    await update.message.reply_text(msg)


# -------------------------------------------------
# VIEW/CLEAR GOALS (bot-admin)  (kept same)
# -------------------------------------------------
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
    save_all()
    await update.message.reply_text("🧹 Cleared all SHIFT goals.")

async def clearpagegoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    page_goals.clear()
    save_all()
    await update.message.reply_text("🧹 Cleared all PAGE goals (15/30 days).")


# -------------------------------------------------
# /GOALBOARD + /REDPAGES + quotas + edits
# (YOUR EXISTING FUNCTIONS CONTINUE HERE UNCHANGED)
# -------------------------------------------------
# >>> KEEP YOUR goalboard/redpages/quotahalf/quotamonth/editgoalboard/editpagegoals
# >>> (I’m not deleting them; just leave them exactly as you already have them)


# =================================================
#               MOBILE API SECTION
# =================================================
api = FastAPI()
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow phone apps / web during testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_TOKEN = os.getenv("API_TOKEN")  # set in Railway Variables

def require_api_token(auth: Optional[str]):
    if not API_TOKEN:
        return
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth.split(" ", 1)[1].strip()
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

def read_sales_log_fresh() -> List[Dict[str, Any]]:
    if os.path.exists(SALES_LOG_FILE):
        try:
            with open(SALES_LOG_FILE, "r") as f:
                raw = json.load(f)
                return raw if isinstance(raw, list) else []
        except Exception:
            return []
    return []

@api.get("/health")
def health():
    return {"ok": True}


# ---- keep your other API routes here (pages/teams/sales/leaderboard/goalboard/quota) ----
# (leave them as you already wrote them)


# =================================================
# RUN FASTAPI IN BACKGROUND THREAD (THREAD-SAFE)
# =================================================
def run_api_server_threadsafe():
    # Use Railway PORT if provided; default 8080 (common on Railway)
    port = int(os.getenv("PORT", "8080"))

    config = uvicorn.Config(api, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # Prevent uvicorn from installing signal handlers inside a thread
    server.install_signal_handlers = lambda: None

    server.run()


# =================================================
# START BOT
# =================================================
def build_telegram_app(BOT_TOKEN: str):
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # app.add_handler(CommandHandler("goalboard", goalboard))
    app.add_handler(CommandHandler("redpages", redpages))
    app.add_handler(CommandHandler("quotahalf", quotahalf))
    app.add_handler(CommandHandler("quotamonth", quotamonth))
    app.add_handler(CommandHandler("editgoalboard", editgoalboard))
    app.add_handler(CommandHandler("editpagegoals", editpagegoals))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("registerteam", registerteam))
    app.add_handler(CommandHandler("unregisterteam", unregisterteam))
    app.add_handler(CommandHandler("registeradmin", registeradmin))
    app.add_handler(CommandHandler("unregisteradmin", unregisteradmin))
    app.add_handler(CommandHandler("listadmins", listadmins))
    app.add_handler(CommandHandler("pages", pages))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("setgoal", setgoal))
    app.add_handler(CommandHandler("pagegoal", pagegoal))
    app.add_handler(CommandHandler("viewshiftgoals", viewshiftgoals))
    app.add_handler(CommandHandler("viewpagegoals", viewpagegoals))
    app.add_handler(CommandHandler("clearshiftgoals", clearshiftgoals))
    app.add_handler(CommandHandler("clearpagegoals", clearpagegoals))

    # IMPORTANT:
    # Re-add your handlers for goalboard/redpages/quotahalf/quotamonth/editgoalboard/editpagegoals
    # if they are defined above in your file.

    return app


def main():
    load_all()
    load_teams()
    load_admins()

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set")

    # Start API in background thread
    threading.Thread(target=run_api_server_threadsafe, daemon=True).start()
    print("API SERVER STARTED…")

    # Run Telegram in MAIN thread (fixes event loop error)
    tg_app = build_telegram_app(BOT_TOKEN)
    print("TELEGRAM BOT RUNNING…")
    tg_app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

