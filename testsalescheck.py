# ==========================================
#   ULTIMATE SALES + GOAL BOT (RAILWAY)
#   + MOBILE API (FastAPI) ADDED
# ==========================================

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from collections import defaultdict
from datetime import datetime, timedelta, time, date
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
    # simple safe write
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
                sales_log.extend(raw)

    if os.path.exists(MANUAL_OVERRIDES_FILE):
        with open(MANUAL_OVERRIDES_FILE, "r") as f:
            raw = json.load(f)
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
# VIEW/CLEAR GOALS (bot-admin)
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
# /GOALBOARD (shift)
# -------------------------------------------------
async def goalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    per_user = defaultdict(lambda: defaultdict(float))

    for ev in sales_log:
        try:
            ev_team = split_internal(ev["user"])[1]
            if ev_team != team:
                continue
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < start:
                continue
            per_user[ev["user"]][ev["page"]] += float(ev["amt"])
        except Exception:
            continue

    if manual_shift_totals:
        internal_manual = f"__MANUAL_OVERRIDE__|{team}"
        for page, val in manual_shift_totals.items():
            per_user[internal_manual][page] = float(val)

    if not per_user:
        return await update.message.reply_text(
            f"🎯 GOAL PROGRESS — {team}\n"
            f"🕒 Shift: {label}\n"
            f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
            "No sales yet for this shift."
        )

    data = []
    for internal, pages_map in per_user.items():
        uname, _ = split_internal(internal)
        total = sum(pages_map.values())
        data.append((uname, pages_map, total))

    data.sort(key=lambda x: x[2], reverse=True)

    msg = f"🎯 GOAL PROGRESS — {team}\n"
    msg += f"🕒 Shift: {label}\n"
    msg += f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

    for i, (uname, pages_map, _) in enumerate(data, 1):
        msg += f"{i}. {uname}\n"
        for page, amt in pages_map.items():
            goal = shift_goals.get(page, 0)
            if goal:
                pct = (amt / goal) * 100
                msg += f"   {get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"
            else:
                msg += f"   ⚪ {page}: ${amt:.2f} (no shift goal)\n"
        msg += "\n"

    await update.message.reply_text(msg)

# -------------------------------------------------
# /REDPAGES
# -------------------------------------------------
async def redpages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    totals = defaultdict(float)
    for ev in sales_log:
        try:
            ev_team = split_internal(ev["user"])[1]
            if ev_team != team:
                continue
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < start:
                continue
            totals[ev["page"]] += float(ev["amt"])
        except Exception:
            continue

    for page, val in manual_shift_totals.items():
        totals[page] = float(val)

    msg = f"🚨 RED PAGES — {team}\n"
    msg += f"🕒 Shift: {label}\n"
    msg += f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

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

# -------------------------------------------------
# QUOTAS (15/30) - bot-admin
# -------------------------------------------------
async def quota_period(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int, title: str):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    cutoff = now_ph() - timedelta(days=days)
    totals = defaultdict(float)

    for ev in sales_log:
        try:
            ev_team = split_internal(ev["user"])[1]
            if ev_team != team:
                continue
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < cutoff:
                continue
            totals[ev["page"]] += float(ev["amt"])
        except Exception:
            continue

    for page, val in manual_page_totals.items():
        totals[page] = float(val)

    if not totals:
        return await update.message.reply_text(f"No sales found for the last {days} days.")

    sorted_rows = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    msg = f"📊 {title} — {team}\n"
    msg += f"🗓️ From: {cutoff.strftime('%b %d, %Y %I:%M %p')} (PH)\n"
    msg += f"🗓️ To:   {now_ph().strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

    for page, amt in sorted_rows:
        goal = page_goals.get(page, 0)
        if goal:
            pct = (amt / goal) * 100
            msg += f"{get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"
        else:
            msg += f"⚪ {page}: ${amt:.2f} (no page goal)\n"

    await update.message.reply_text(msg)

async def quotahalf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, days=15, title="QUOTA HALF (15 DAYS)")

async def quotamonth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, days=30, title="QUOTA MONTH (30 DAYS)")

# -------------------------------------------------
# MANUAL REPLACEMENT EDITS (bot-admin)
# -------------------------------------------------
async def editgoalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return
    if not await require_registered_admin(update, 1):
        return

    raw = update.message.text.replace("/editgoalboard", "", 1).strip()
    parts = raw.split()
    if len(parts) < 2:
        return await update.message.reply_text("Format: /editgoalboard PAGE AMOUNT\nExample: /editgoalboard AUTUMN PAID 2000")

    amount_str = parts[-1]
    page_str = " ".join(parts[:-1])

    page = canonicalize_page_name(page_str)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    try:
        amount = float(amount_str)
    except ValueError:
        return await update.message.reply_text("Amount must be a number. Example: /editgoalboard AUTUMN PAID 2000")

    manual_shift_totals[page] = amount
    manual_page_totals[page] = amount

    save_all()
    await update.message.reply_text(
        f"✅ Updated totals\n\n"
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
        return await update.message.reply_text("Format: /editpagegoals PAGE AMOUNT\nExample: /editpagegoals AUTUMN PAID 3000")

    amount_str = parts[-1]
    page_str = " ".join(parts[:-1])

    page = canonicalize_page_name(page_str)
    if page is None:
        return await update.message.reply_text("Invalid page/tag. Use a valid page name or hashtag tag.")

    try:
        amount = float(amount_str)
    except ValueError:
        return await update.message.reply_text("Amount must be a number. Example: /editpagegoals AUTUMN PAID 3000")

    manual_page_totals[page] = amount
    save_all()
    await update.message.reply_text(f"✅ Updated quotas\n{page} = ${amount:.2f} (15/30 days)")

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
        # If you don't set API_TOKEN, the API is public (NOT recommended)
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

@api.get("/api/pages")
def api_pages(authorization: Optional[str] = Header(default=None)):
    require_api_token(authorization)
    # return both tag and canonical name
    return [{"tag": tag, "name": name} for tag, name in sorted(ALLOWED_PAGES.items())]

@api.get("/api/teams")
def api_teams(authorization: Optional[str] = Header(default=None)):
    require_api_token(authorization)
    teams = sorted(set(GROUP_TEAMS.values()))
    return {"teams": teams}

@api.get("/api/sales")
def api_sales(
    days: int = 15,
    team: str = "",
    page: str = "",
    authorization: Optional[str] = Header(default=None)
):
    """
    Daily totals for charts.
    Optional: team="Team 1", page="AUTUMN PAID"
    """
    require_api_token(authorization)

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be 1..365")

    cutoff = now_ph() - timedelta(days=days - 1)
    start_day = cutoff.astimezone(PH_TZ).date()

    log = read_sales_log_fresh()
    totals_by_day = defaultdict(float)

    for ev in log:
        try:
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < cutoff:
                continue

            ev_user = ev.get("user", "")
            _, ev_team = split_internal(ev_user)

            if team and ev_team != team:
                continue
            if page and ev.get("page") != page:
                continue

            day_key = ev_dt.astimezone(PH_TZ).date().isoformat()
            totals_by_day[day_key] += float(ev.get("amt", 0))
        except Exception:
            continue

    points = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        ds = d.isoformat()
        points.append({"date": ds, "sales": float(totals_by_day.get(ds, 0.0))})

    return {
        "rangeDays": days,
        "team": team,
        "page": page,
        "timezone": "Asia/Manila",
        "points": points,
        "totalSales": sum(p["sales"] for p in points),
    }

@api.get("/api/leaderboard")
def api_leaderboard(team: str, authorization: Optional[str] = Header(default=None)):
    """
    Lifetime leaderboard for one team.
    """
    require_api_token(authorization)

    rows = []
    for internal, pages_map in sales_data.items():
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        for page, amt in pages_map.items():
            rows.append({"user": uname, "page": page, "sales": float(amt)})

    rows.sort(key=lambda x: x["sales"], reverse=True)
    return {"team": team, "rows": rows}

@api.get("/api/goalboard")
def api_goalboard(team: str, authorization: Optional[str] = Header(default=None)):
    """
    Current SHIFT totals vs SHIFT GOALS, team-wide (not per-user).
    Phone-friendly.
    """
    require_api_token(authorization)

    now = now_ph()
    start = shift_start(now)
    label = current_shift_label(now)

    totals = defaultdict(float)
    log = read_sales_log_fresh()
    for ev in log:
        try:
            ev_user = ev.get("user", "")
            _, ev_team = split_internal(ev_user)
            if ev_team != team:
                continue
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < start:
                continue
            totals[ev["page"]] += float(ev.get("amt", 0))
        except Exception:
            continue

    # apply manual shift overrides
    for p, v in manual_shift_totals.items():
        totals[p] = float(v)

    # include pages that have goals even if 0 sales
    pages_set = set(totals.keys()) | set(shift_goals.keys())

    rows = []
    for p in sorted(pages_set):
        goal = float(shift_goals.get(p, 0.0))
        amt = float(totals.get(p, 0.0))
        pct = (amt / goal * 100.0) if goal > 0 else 0.0
        rows.append({"page": p, "sales": amt, "goal": goal, "pct": pct})

    return {
        "team": team,
        "shiftLabel": label,
        "shiftStartPH": start.isoformat(),
        "rows": rows,
    }

@api.get("/api/quota")
def api_quota(team: str, days: int = 15, authorization: Optional[str] = Header(default=None)):
    """
    15/30 day totals vs PAGE GOALS (period goals)
    """
    require_api_token(authorization)

    if days not in (15, 30):
        raise HTTPException(status_code=400, detail="days must be 15 or 30")

    cutoff = now_ph() - timedelta(days=days)
    totals = defaultdict(float)

    log = read_sales_log_fresh()
    for ev in log:
        try:
            ev_user = ev.get("user", "")
            _, ev_team = split_internal(ev_user)
            if ev_team != team:
                continue
            ev_dt = parse_ts(ev["ts"])
            if ev_dt < cutoff:
                continue
            totals[ev["page"]] += float(ev.get("amt", 0))
        except Exception:
            continue

    # apply manual quota overrides
    for p, v in manual_page_totals.items():
        totals[p] = float(v)

    pages_set = set(totals.keys()) | set(page_goals.keys())
    rows = []
    for p in sorted(pages_set):
        goal = float(page_goals.get(p, 0.0))
        amt = float(totals.get(p, 0.0))
        pct = (amt / goal * 100.0) if goal > 0 else 0.0
        rows.append({"page": p, "sales": amt, "goal": goal, "pct": pct})

    rows.sort(key=lambda r: r["sales"], reverse=True)

    return {
        "team": team,
        "days": days,
        "fromPH": cutoff.isoformat(),
        "toPH": now_ph().isoformat(),
        "rows": rows,
    }

def build_telegram_app(BOT_TOKEN: str):
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # sales messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))

    # basic
    app.add_handler(CommandHandler("chatid", chatid))

    # owner-only
    app.add_handler(CommandHandler("registerteam", registerteam))
    app.add_handler(CommandHandler("unregisterteam", unregisterteam))
    app.add_handler(CommandHandler("registeradmin", registeradmin))
    app.add_handler(CommandHandler("unregisteradmin", unregisteradmin))
    app.add_handler(CommandHandler("listadmins", listadmins))

    # everyone (team-only)
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

    return app

def run_telegram_bot(BOT_TOKEN: str):
    tg_app = build_telegram_app(BOT_TOKEN)
    print("TELEGRAM BOT RUNNING…")
    tg_app.run_polling(close_loop=False)
# =================================================
# START BOT
# =================================================
def run_api_server():
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")
def main():
    load_all()
    load_teams()
    load_admins()

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set")

    # Start telegram bot in background
    threading.Thread(target=run_telegram_bot, args=(BOT_TOKEN,), daemon=True).start()

    # Run API as main server (Railway needs this)
    print("API SERVER STARTING…")
    run_api_server()

if __name__ == "__main__":
    main()






