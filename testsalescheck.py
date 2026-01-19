# ================================
#   ULTIMATE SALES + GOAL BOT
#   SHIFT RESET + QUOTA (15/30D)
#   + /registerteam (admin only)
#   FULL STABLE VERSION (RAILWAY)
# ================================

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from collections import defaultdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import json, os

# -------------------------------------------------
# TIMEZONE (PH)
# -------------------------------------------------
PH_TZ = ZoneInfo("Asia/Manila")

# -------------------------------------------------
# DEFAULT TEAM MAP (optional seed)
# NOTE: You can keep this or leave it empty.
# /registerteam will add new group IDs automatically.
# -------------------------------------------------
DEFAULT_GROUP_TEAMS = {
    -1003316845910: "Team 1",
    -1003375611734: "Team 2",
    -1003418783640: "Team 3",
    -1003515063005: "Team 4",
    -1003552893317: "Team 5",
}

TEAMS_FILE = "teams.json"   # stores registered groups -> teams

# -------------------------------------------------
# PAGE ENFORCEMENT (TAGS ONLY)
# Users must type: +200 #autumnpaid
# -------------------------------------------------
ALLOWED_PAGES = {
    "#autumnpaid": "AUTUMN PAID",
    "#autumnfree": "AUTUMN FREE",
    # add more tags here...
}

# -------------------------------------------------
# DATA STORAGE
# -------------------------------------------------
# Lifetime totals (useful for /leaderboard, admin edits, etc.)
sales_data = defaultdict(lambda: defaultdict(float))

# Page goals (you can hardcode manually or use /setgoal)
page_goals = defaultdict(float)

# Event log for quotas + shift-based goalboard
# each event: {"ts": "ISO+08:00", "user": "username|Team 1", "page": "AUTUMN PAID", "amt": 200.0}
sales_log = []

# Undo buffer (stores BOTH sales_data snapshot + removed events)
last_deleted = None

# Runtime team map (loaded from teams.json + defaults)
GROUP_TEAMS = dict(DEFAULT_GROUP_TEAMS)


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

def save_all():
    with open("sales.json", "w") as f:
        json.dump({u: dict(p) for u, p in sales_data.items()}, f)
    with open("goals.json", "w") as f:
        json.dump(dict(page_goals), f)
    with open("sales_log.json", "w") as f:
        json.dump(sales_log, f)

def load_all():
    if os.path.exists("sales.json"):
        with open("sales.json", "r") as f:
            raw = json.load(f)
            for u, pages in raw.items():
                for page, val in pages.items():
                    sales_data[u][page] = float(val)

    if os.path.exists("goals.json"):
        with open("goals.json", "r") as f:
            raw = json.load(f)
            for page, goal in raw.items():
                page_goals[page] = float(goal)

    if os.path.exists("sales_log.json"):
        with open("sales_log.json", "r") as f:
            raw = json.load(f)
            if isinstance(raw, list):
                sales_log.extend(raw)

def save_teams():
    with open(TEAMS_FILE, "w") as f:
        # JSON keys must be strings, convert back on load
        json.dump({str(k): v for k, v in GROUP_TEAMS.items()}, f)

def load_teams():
    global GROUP_TEAMS
    GROUP_TEAMS = dict(DEFAULT_GROUP_TEAMS)
    if os.path.exists(TEAMS_FILE):
        with open(TEAMS_FILE, "r") as f:
            raw = json.load(f)
            # convert string keys -> int
            for k, v in raw.items():
                try:
                    GROUP_TEAMS[int(k)] = str(v)
                except Exception:
                    continue

def get_team(chat_id: int):
    return GROUP_TEAMS.get(chat_id)

def split_internal(internal: str):
    # "username|Team 1"
    parts = internal.split("|", 1)
    if len(parts) != 2:
        return internal, ""
    return parts[0], parts[1]

def normalize_page(raw_page: str):
    """
    Enforce pages via tags like #autumnpaid.
    Takes the FIRST token after the amount.
    Returns canonical page name or None if not allowed.
    """
    if not raw_page:
        return None
    token = raw_page.strip().split()[0].lower()
    if not token.startswith("#"):
        return None
    return ALLOWED_PAGES.get(token)

def current_shift_label(dt: datetime) -> str:
    """
    Prime = 8am–4pm
    Midshift = 4pm–12am
    Closing = 12am–8am
    """
    t = dt.timetz()
    if time(8, 0, tzinfo=PH_TZ) <= t < time(16, 0, tzinfo=PH_TZ):
        return "Prime (8AM–4PM)"
    if time(16, 0, tzinfo=PH_TZ) <= t < time(23, 59, 59, tzinfo=PH_TZ):
        return "Midshift (4PM–12AM)"
    return "Closing (12AM–8AM)"

def shift_start(dt: datetime) -> datetime:
    """
    Shift reset points:
      08:00 PH
      16:00 PH
      00:00 PH
    """
    d = dt.date()
    t = dt.timetz()

    if time(8, 0, tzinfo=PH_TZ) <= t < time(16, 0, tzinfo=PH_TZ):
        return datetime.combine(d, time(8, 0), PH_TZ)

    if time(16, 0, tzinfo=PH_TZ) <= t < time(23, 59, 59, tzinfo=PH_TZ):
        return datetime.combine(d, time(16, 0), PH_TZ)

    if t < time(8, 0, tzinfo=PH_TZ):
        return datetime.combine(d, time(0, 0), PH_TZ)

    return datetime.combine(d, time(16, 0), PH_TZ)

async def require_team(update: Update) -> str | None:
    """
    Returns team string if registered, else replies with a helpful message and returns None.
    """
    team = get_team(update.effective_chat.id)
    if team is None:
        await update.message.reply_text(
            "Not a team group yet.\n\n"
            "Admin can register this group using:\n"
            "/registerteam Team 1\n\n"
            "To see the group ID:\n"
            "/chatid"
        )
        return None
    return team

async def is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if the message sender is an admin/creator of the chat.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(chat_id)
    return any(a.user.id == user_id for a in admins)

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
# /CHATID (works anywhere)
# -------------------------------------------------
async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat type: {chat.type}\nChat ID: {chat.id}")


# -------------------------------------------------
# /REGISTERTEAM Team 1  (admin only)
# This saves the group chat_id -> team name to teams.json
# -------------------------------------------------
async def registerteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("Run this command inside the team group (not in private).")

    if not await is_chat_admin(update, context):
        return await update.message.reply_text("Only group admins can register a team.")

    team_name = clean(" ".join(context.args)).strip()
    if not team_name:
        return await update.message.reply_text("Format: /registerteam Team 1")

    chat_id = update.effective_chat.id
    GROUP_TEAMS[chat_id] = team_name
    save_teams()

    await update.message.reply_text(
        f"✅ Registered this group!\n\n"
        f"Team: {team_name}\n"
        f"Chat ID: {chat_id}\n\n"
        f"Try: /pages or /goalboard"
    )


# -------------------------------------------------
# HANDLE SALES (+amount #tag)
# Example: +200 #autumnpaid
# -------------------------------------------------
async def handle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    team = get_team(update.effective_chat.id)
    if team is None:
        return  # ignore sales in unregistered groups

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

        # log event (enables quotas + shift reset)
        sales_log.append({
            "ts": ts_iso,
            "user": internal,
            "page": canonical_page,
            "amt": float(amount),
        })

        # lifetime totals
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
# /LEADERBOARD (Lifetime totals, per team)
# -------------------------------------------------
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    rows = []
    for internal, pages in sales_data.items():
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        for page, amt in pages.items():
            rows.append((uname, page, amt))

    if not rows:
        return await update.message.reply_text("No sales yet.")

    rows.sort(key=lambda x: x[2], reverse=True)

    msg = f"🏆 SALES LEADERBOARD (LIFETIME) — {team}\n\n"
    for i, (u, p, a) in enumerate(rows, 1):
        msg += f"{i}. {u} ({p}) — ${a:.2f}\n"

    await update.message.reply_text(msg)


# -------------------------------------------------
# /GOALBOARD (SHIFT-BASED; auto “resets” at 00:00/08:00/16:00 PH)
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

    if not per_user:
        return await update.message.reply_text(
            f"🎯 GOAL PROGRESS — {team}\n"
            f"🕒 Shift: {label}\n"
            f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"
            "No sales yet for this shift."
        )

    data = []
    for internal, pages in per_user.items():
        uname, _ = split_internal(internal)
        total = sum(pages.values())
        data.append((uname, pages, total))

    data.sort(key=lambda x: x[2], reverse=True)

    msg = f"🎯 GOAL PROGRESS — {team}\n"
    msg += f"🕒 Shift: {label}\n"
    msg += f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

    for i, (uname, pages, _) in enumerate(data, 1):
        msg += f"{i}. {uname}\n"
        for page, amt in pages.items():
            goal = page_goals.get(page, 0)
            if goal:
                pct = (amt / goal) * 100
                msg += f"   {get_color(pct)} {page}: ${amt:.2f} / ${goal:.2f} ({pct:.1f}%)\n"
            else:
                msg += f"   ⚪ {page}: ${amt:.2f} (no goal)\n"
        msg += "\n"

    await update.message.reply_text(msg)


# -------------------------------------------------
# /QUOTAMONTH (Last 30 days)
# /QUOTAHALF  (Last 15 days)
# -------------------------------------------------
async def quota_period(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int, title: str):
    team = await require_team(update)
    if team is None:
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
            msg += f"⚪ {page}: ${amt:.2f}\n"

    await update.message.reply_text(msg)

async def quotamonth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, days=30, title="QUOTA MONTH (30 DAYS)")

async def quotahalf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await quota_period(update, context, days=15, title="QUOTA HALF (15 DAYS)")


# -------------------------------------------------
# /SETGOAL
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
        page_goals[page] = goal
        results.append(f"✓ {page} = ${goal:.2f}")

    save_all()

    msg = "🎯 Goals Updated:\n" + ("\n".join(results) if results else "(no valid entries)")
    if errors:
        msg += "\n\n⚠️ Invalid:\n" + "\n".join(errors)

    await update.message.reply_text(msg)


# -------------------------------------------------
# /PAGES (shows approved tags + canonical page names)
# -------------------------------------------------
async def pages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    if not ALLOWED_PAGES:
        return await update.message.reply_text("No allowed pages configured.")

    lines = []
    for tag in sorted(ALLOWED_PAGES.keys()):
        lines.append(f"{tag} → {ALLOWED_PAGES[tag]}")

    await update.message.reply_text(
        f"📘 Approved Pages (use tags) — {team}\n\n" + "\n".join(lines)
    )


# -------------------------------------------------
# /REDPAGES (SHIFT-BASED)
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

    msg = f"🚨 RED PAGES — {team}\n"
    msg += f"🕒 Shift: {label}\n"
    msg += f"✅ Shift started: {start.strftime('%b %d, %Y %I:%M %p')} (PH)\n\n"

    any_found = False
    for page, amt in sorted(totals.items()):
        goal = page_goals.get(page, 0)
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
# DELETE COMMANDS (affect BOTH lifetime totals + event log)
# -------------------------------------------------
async def deleteuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    if not context.args:
        return await update.message.reply_text("Format: /deleteuser username")

    target = " ".join(context.args).lower()

    global last_deleted
    found = None

    for internal in list(sales_data.keys()):
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        if target in uname.lower():
            found = internal
            break

    if not found:
        return await update.message.reply_text("User not found in this team.")

    removed_events = [ev for ev in sales_log if ev.get("user") == found]
    last_deleted = {
        "sales_data": {found: dict(sales_data[found])},
        "sales_log": removed_events
    }

    if found in sales_data:
        del sales_data[found]

    if removed_events:
        keep = [ev for ev in sales_log if ev.get("user") != found]
        sales_log.clear()
        sales_log.extend(keep)

    save_all()
    await update.message.reply_text(f"Deleted all sales for {found} (lifetime + log).")


async def deletepage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    raw = " ".join(context.args)
    if "|" not in raw:
        return await update.message.reply_text("Format: /deletepage username | page")

    name_part, page_part = raw.split("|", 1)
    name = name_part.strip().lower()
    page_req = page_part.strip().lower()

    global last_deleted

    target_user = None
    for internal in sales_data:
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        if name in uname.lower():
            target_user = internal
            break

    if not target_user:
        return await update.message.reply_text("User not found in this team.")

    target_page = None
    for p in sales_data[target_user]:
        if page_req in p.lower():
            target_page = p
            break

    if not target_page:
        return await update.message.reply_text("Page not found for that user.")

    removed_events = [ev for ev in sales_log if ev.get("user") == target_user and ev.get("page") == target_page]
    last_deleted = {
        "sales_data": {target_user: {target_page: sales_data[target_user][target_page]}},
        "sales_log": removed_events
    }

    del sales_data[target_user][target_page]
    if not sales_data[target_user]:
        del sales_data[target_user]

    if removed_events:
        keep = [ev for ev in sales_log if not (ev.get("user") == target_user and ev.get("page") == target_page)]
        sales_log.clear()
        sales_log.extend(keep)

    save_all()
    await update.message.reply_text(f"Deleted page {target_page} from {target_user} (lifetime + log).")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /delete Team 1 3
    team = await require_team(update)
    if team is None:
        return

    if len(context.args) < 3:
        return await update.message.reply_text("Format: /delete Team 1 3")

    team_name = f"{context.args[0]} {context.args[1]}"
    try:
        rank = int(context.args[2])
    except ValueError:
        return await update.message.reply_text("Rank must be a number.")

    global last_deleted

    entries = []
    for internal, pages_map in sales_data.items():
        _, uteam = split_internal(internal)
        if uteam == team_name:
            entries.append((internal, sum(pages_map.values())))

    if not entries:
        return await update.message.reply_text("No data for that team.")

    entries.sort(key=lambda x: x[1], reverse=True)

    if rank < 1 or rank > len(entries):
        return await update.message.reply_text("Rank does not exist.")

    target_internal = entries[rank - 1][0]

    removed_events = [ev for ev in sales_log if ev.get("user") == target_internal]
    last_deleted = {
        "sales_data": {target_internal: dict(sales_data[target_internal])},
        "sales_log": removed_events
    }

    del sales_data[target_internal]

    if removed_events:
        keep = [ev for ev in sales_log if ev.get("user") != target_internal]
        sales_log.clear()
        sales_log.extend(keep)

    save_all()
    await update.message.reply_text(f"Deleted rank #{rank} from {team_name} (lifetime + log).")


# -------------------------------------------------
# /EDIT username | page | amount  (overwrite lifetime total only)
# -------------------------------------------------
async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    raw = " ".join(context.args)
    if raw.count("|") != 2:
        return await update.message.reply_text("Format:\n/edit username | page | amount")

    user_part, page_part, amount_part = [x.strip() for x in raw.split("|")]

    try:
        new_amount = float(amount_part)
    except ValueError:
        return await update.message.reply_text("Amount must be a number.")

    target_user = None
    for internal in sales_data:
        uname, uteam = split_internal(internal)
        if uteam != team:
            continue
        if user_part.lower() in uname.lower():
            target_user = internal
            break

    if not target_user:
        return await update.message.reply_text("User not found in this team.")

    target_page = None
    for p in sales_data[target_user]:
        if page_part.lower() in p.lower():
            target_page = p
            break

    if not target_page:
        return await update.message.reply_text("Page not found for that user.")

    sales_data[target_user][target_page] = new_amount
    save_all()

    uname, _ = split_internal(target_user)
    await update.message.reply_text(f"Updated (lifetime): {uname} — {target_page} is now ${new_amount:.2f}")


# -------------------------------------------------
# /UNDO
# -------------------------------------------------
async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    global last_deleted
    if not last_deleted:
        return await update.message.reply_text("Nothing to undo.")

    snap = last_deleted.get("sales_data", {})
    for internal, pages_map in snap.items():
        sales_data[internal] = defaultdict(float, pages_map)

    removed_events = last_deleted.get("sales_log", [])
    if removed_events:
        sales_log.extend(removed_events)

    last_deleted = None
    save_all()
    await update.message.reply_text("Undo complete.")


# -------------------------------------------------
# /RESET (clears BOTH lifetime totals + event log; goals stay)
# -------------------------------------------------
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team = await require_team(update)
    if team is None:
        return

    sales_data.clear()
    sales_log.clear()
    save_all()
    await update.message.reply_text("🗑️ Sales reset (lifetime + log).")


# -------------------------------------------------
# START BOT
# -------------------------------------------------
def main():
    load_all()
    load_teams()

    # OPTIONAL: hardcode goals here
    # page_goals["AUTUMN PAID"] = 1000
    # page_goals["AUTUMN FREE"] = 1000

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))

    # commands (public)
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("registerteam", registerteam))

    # commands (team-only)
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("goalboard", goalboard))
    app.add_handler(CommandHandler("redpages", redpages))
    app.add_handler(CommandHandler("quotamonth", quotamonth))
    app.add_handler(CommandHandler("quotahalf", quotahalf))
    app.add_handler(CommandHandler("setgoal", setgoal))
    app.add_handler(CommandHandler("pages", pages))

    # admin tools
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("deleteuser", deleteuser))
    app.add_handler(CommandHandler("deletepage", deletepage))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("reset", reset))

    print("BOT RUNNING…")
    # Railway-friendly:
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
