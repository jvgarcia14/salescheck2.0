import os
import re
import bcrypt
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
import httpx

# =========================
# CONFIG
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")

db = psycopg2.connect(DATABASE_URL, sslmode="require")
db.autocommit = True

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "onboarding@resend.dev")  # Resend default testing sender
DEBUG_RETURN_RESET_CODE = os.getenv("DEBUG_RETURN_RESET_CODE", "").strip() == "1"

FIXED_INVITE_CODE = os.getenv("FIXED_INVITE_CODE", "TASTY-ACCESS").strip()
OTP_TTL_MINUTES = int(os.getenv("OTP_TTL_MINUTES", "15"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# HELPERS
# =========================
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_utc():
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return e

    local, domain = e.split("@", 1)

    # Gmail normalization: remove +tag so test+1@gmail.com == test@gmail.com
    if domain in ("gmail.com", "googlemail.com"):
        if "+" in local:
            local = local.split("+", 1)[0]
        e = f"{local}@{domain}"

    return e


def validate_email(email: str):
    if not email or not EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email format")


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_pw(pw: str, h: str) -> bool:
    return bcrypt.checkpw(pw.encode("utf-8"), h.encode("utf-8"))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_reset_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def get_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, value = parts[0].strip().lower(), parts[1].strip()
    if scheme != "bearer" or not value:
        return None
    return value


def table_columns(table_name: str) -> set[str]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (table_name,),
        )
        return {r[0] for r in cur.fetchall()}


def column_type(table_name: str, column_name: str) -> str | None:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s
            """,
            (table_name, column_name),
        )
        row = cur.fetchone()
        return row[0] if row else None


def safe_add_column(table: str, coldef: str):
    with db.cursor() as cur:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {coldef};")


def is_used(v) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "t", "true", "yes", "y")


def used_value_for_db(value_bool: bool):
    """
    Returns the right value type for invites.used (bool or int)
    depending on column type in DB.
    """
    t = column_type("invites", "used")
    if t in ("integer", "bigint", "smallint"):
        return 1 if value_bool else 0
    # default boolean
    return bool(value_bool)


def require_token(authorization: str | None, token_query: str | None) -> str:
    t = get_bearer_token(authorization) or token_query
    if not t:
        raise HTTPException(401, "Missing token")
    return t


def get_user_id_from_token(token: str) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM sessions WHERE token=%s AND expires > now()",
            (token,),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(401, "Invalid or expired session")
    return int(row[0])


def delete_user_account(user_id: int):
    """
    Permanently deletes the user and related auth data.
    """
    with db.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        email = row[0]

        # log out everywhere
        cur.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))

        # remove reset codes
        cur.execute("DELETE FROM password_resets WHERE email=%s", (email,))

        # delete user
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))


# =========================
# EMAIL (RESEND)
# =========================
async def send_reset_code_email(to_email: str, code: str, minutes_valid: int):
    if not RESEND_API_KEY:
        raise HTTPException(500, "Email service not configured (RESEND_API_KEY missing)")

    subject = "Your password reset code"
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.4;">
      <h2>Password reset</h2>
      <p>Your 6-digit code is:</p>
      <div style="font-size: 28px; font-weight: 800; letter-spacing: 6px; margin: 12px 0;">
        {code}
      </div>
      <p>This code expires in <b>{minutes_valid} minutes</b>.</p>
      <p>If you didn’t request this, you can ignore this email.</p>
    </div>
    """

    payload = {
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if r.status_code >= 400:
        raise HTTPException(500, f"Failed to send email: {r.text}")


# =========================
# DB INIT + MIGRATIONS
# =========================
def init_db():
    with db.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                username TEXT,
                password_hash TEXT NOT NULL,
                pin_hash TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS invites (
                code TEXT PRIMARY KEY,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT now(),
                used_by BIGINT,
                used_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id BIGINT,
                expires TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                id BIGSERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

    # Safe migrations
    safe_add_column("users", "username TEXT")
    safe_add_column("users", "pin_hash TEXT")
    safe_add_column("users", "created_at TIMESTAMPTZ DEFAULT now()")

    safe_add_column("invites", "used_by BIGINT")
    safe_add_column("invites", "used_at TIMESTAMPTZ")
    safe_add_column("invites", "created_at TIMESTAMPTZ DEFAULT now()")

    safe_add_column("sessions", "user_id BIGINT")
    safe_add_column("sessions", "expires TIMESTAMPTZ")
    safe_add_column("sessions", "created_at TIMESTAMPTZ DEFAULT now()")

    # password_resets compatibility (older columns)
    safe_add_column("password_resets", "token_hash TEXT")
    safe_add_column("password_resets", "code_hash TEXT")
    safe_add_column("password_resets", "expires TIMESTAMPTZ")
    safe_add_column("password_resets", "expires_at TIMESTAMPTZ")
    safe_add_column("password_resets", "used BOOLEAN DEFAULT FALSE")
    safe_add_column("password_resets", "created_at TIMESTAMPTZ DEFAULT now()")

    with db.cursor() as cur:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_resets_email_created ON password_resets (email, created_at);")

    # ✅ Ensure the FIXED_INVITE_CODE exists (unlimited)
    with db.cursor() as cur:
        cols = table_columns("invites")
        if "used" in cols:
            cur.execute(
                """
                INSERT INTO invites (code, used)
                VALUES (%s, %s)
                ON CONFLICT (code) DO NOTHING
                """,
                (FIXED_INVITE_CODE, used_value_for_db(False)),
            )
        else:
            cur.execute(
                """
                INSERT INTO invites (code)
                VALUES (%s)
                ON CONFLICT (code) DO NOTHING
                """,
                (FIXED_INVITE_CODE,),
            )


init_db()

# =========================
# MODELS
# =========================
class InviteReq(BaseModel):
    code: str


class RegisterReq(BaseModel):
    invite_code: str
    username: str
    email: str
    password: str


class LoginReq(BaseModel):
    email: str
    password: str


class PinReq(BaseModel):
    pin: str


class UnlockReq(BaseModel):
    pin: str


class ForgotReq(BaseModel):
    email: str


class ResetReq(BaseModel):
    email: str
    reset_code: str
    new_password: str


class DeleteAccountReq(BaseModel):
    password: str
    pin: str | None = None


# =========================
# INVITE
# =========================
@app.post("/invite/verify")
def verify_invite(body: InviteReq):
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, "Invalid invite code")

    # ✅ unlimited invite always valid
    if code == FIXED_INVITE_CODE:
        return {"ok": True, "unlimited": True}

    with db.cursor() as cur:
        cur.execute("SELECT used FROM invites WHERE code=%s", (code,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Invalid invite code")

        if is_used(row[0]):
            raise HTTPException(400, "Invite already used")

    return {"ok": True, "unlimited": False}


# =========================
# REGISTER
# =========================
@app.post("/auth/register")
def register(body: RegisterReq):
    email = normalize_email(body.email)
    username = (body.username or "").strip()
    invite_code = (body.invite_code or "").strip()

    validate_email(email)

    if len(body.password or "") < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    # ✅ Check invite (unlimited passes)
    if invite_code != FIXED_INVITE_CODE:
        with db.cursor() as cur:
            cur.execute("SELECT used FROM invites WHERE code=%s", (invite_code,))
            inv = cur.fetchone()
        if not inv:
            raise HTTPException(400, "Invalid invite code")
        if is_used(inv[0]):
            raise HTTPException(400, "Invite already used")

    pw_hash = hash_pw(body.password)

    try:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, username, password_hash)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (email, username, pw_hash),
            )
            user_id = cur.fetchone()[0]

            # ✅ Mark single-use invites as used (NOT the unlimited one)
            if invite_code != FIXED_INVITE_CODE:
                cur.execute(
                    "UPDATE invites SET used=%s, used_by=%s, used_at=now() WHERE code=%s",
                    (used_value_for_db(True), user_id, invite_code),
                )
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(400, "Email already exists")
        raise HTTPException(500, "Registration failed")

    token = new_token()
    expires = now_utc() + timedelta(days=7)

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, expires) VALUES (%s, %s, %s)",
            (token, user_id, expires),
        )

    return {"ok": True, "token": token, "needs_pin": True}


# =========================
# LOGIN
# =========================
@app.post("/auth/login")
def login(body: LoginReq):
    email = normalize_email(body.email)
    validate_email(email)

    with db.cursor() as cur:
        cur.execute("SELECT id, password_hash, pin_hash FROM users WHERE email=%s", (email,))
        row = cur.fetchone()

    if not row or not check_pw(body.password, row[1]):
        raise HTTPException(401, "Invalid credentials")

    token = new_token()
    expires = now_utc() + timedelta(days=7)

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, expires) VALUES (%s, %s, %s)",
            (token, row[0], expires),
        )

    return {"ok": True, "token": token, "needs_pin": row[2] is None}


# =========================
# CREATE PIN
# =========================
@app.post("/auth/create-pin")
def create_pin(body: PinReq, token: str | None = None, authorization: str | None = Header(default=None)):
    t = require_token(authorization, token)

    pin = (body.pin or "").strip()
    if len(pin) < 4:
        raise HTTPException(400, "PIN too short (min 4 digits)")

    user_id = get_user_id_from_token(t)

    with db.cursor() as cur:
        cur.execute("UPDATE users SET pin_hash=%s WHERE id=%s", (hash_pw(pin), user_id))

    return {"ok": True}


# =========================
# UNLOCK (PIN)
# =========================
@app.post("/unlock")
def unlock(body: UnlockReq, token: str | None = None, authorization: str | None = Header(default=None)):
    t = require_token(authorization, token)
    user_id = get_user_id_from_token(t)

    with db.cursor() as cur:
        cur.execute("SELECT pin_hash FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()

    if not row or not row[0]:
        raise HTTPException(400, "PIN not set")

    if not check_pw((body.pin or "").strip(), row[0]):
        raise HTTPException(401, "Invalid PIN")

    return {"ok": True}


# =========================
# FORGOT PASSWORD
# =========================
@app.post("/password/forgot")
async def password_forgot(body: ForgotReq):
    email = normalize_email(body.email)
    validate_email(email)

    with db.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

    if not user:
        raise HTTPException(404, "Email not found")

    code = new_reset_code()
    expires_dt = now_utc() + timedelta(minutes=OTP_TTL_MINUTES)
    code_h = hash_pw(code)

    cols = table_columns("password_resets")

    insert_cols = ["email"]
    insert_vals = [email]

    if "token_hash" in cols:
        insert_cols.append("token_hash")
        insert_vals.append(code_h)
    if "code_hash" in cols:
        insert_cols.append("code_hash")
        insert_vals.append(code_h)

    if "expires_at" in cols:
        insert_cols.append("expires_at")
        insert_vals.append(expires_dt)
    if "expires" in cols:
        insert_cols.append("expires")
        insert_vals.append(expires_dt)

    if "used" in cols:
        insert_cols.append("used")
        insert_vals.append(False)

    if "created_at" in cols:
        insert_cols.append("created_at")
        insert_vals.append(now_utc())

    if len(insert_cols) < 3:
        raise HTTPException(500, f"password_resets schema unexpected: {sorted(list(cols))}")

    placeholders = ", ".join(["%s"] * len(insert_cols))
    col_sql = ", ".join(insert_cols)

    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO password_resets ({col_sql}) VALUES ({placeholders})",
            tuple(insert_vals),
        )

    # ✅ Send email (Resend)
    await send_reset_code_email(email, code, minutes_valid=OTP_TTL_MINUTES)

    if DEBUG_RETURN_RESET_CODE:
        return {"ok": True, "expires_in_minutes": OTP_TTL_MINUTES, "debug_code": code}

    return {"ok": True, "expires_in_minutes": OTP_TTL_MINUTES}


# =========================
# RESET PASSWORD
# =========================
@app.post("/password/reset")
def password_reset(body: ResetReq):
    email = normalize_email(body.email)
    validate_email(email)

    if len(body.new_password or "") < 6:
        raise HTTPException(400, "New password must be at least 6 characters")

    code = (body.reset_code or "").strip()
    if len(code) != 6:
        raise HTTPException(400, "Invalid reset code")

    cols = table_columns("password_resets")

    expiry_col = "expires_at" if "expires_at" in cols else ("expires" if "expires" in cols else None)
    if not expiry_col:
        raise HTTPException(500, f"password_resets missing expires column: {sorted(list(cols))}")

    hash_cols = []
    if "token_hash" in cols:
        hash_cols.append("token_hash")
    if "code_hash" in cols:
        hash_cols.append("code_hash")
    if not hash_cols:
        raise HTTPException(500, f"password_resets missing hash column: {sorted(list(cols))}")

    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, {", ".join(hash_cols)}
            FROM password_resets
            WHERE email=%s
              AND (used=FALSE OR used IS NULL)
              AND {expiry_col} > now()
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (email,),
        )
        rr = cur.fetchone()

    if not rr:
        raise HTTPException(400, "Reset code expired or not found")

    reset_id = rr[0]

    stored_hash = None
    for h in rr[1:]:
        if h:
            stored_hash = h
            break

    if not stored_hash or not check_pw(code, stored_hash):
        raise HTTPException(400, "Wrong reset code")

    with db.cursor() as cur:
        cur.execute("UPDATE users SET password_hash=%s WHERE email=%s", (hash_pw(body.new_password), email))
        if "used" in cols:
            cur.execute("UPDATE password_resets SET used=TRUE WHERE id=%s", (reset_id,))

    return {"ok": True}


# =========================
# DELETE ACCOUNT (APPLE 5.1.1(v))
# =========================
@app.post("/auth/delete-account")
def delete_account(
    body: DeleteAccountReq,
    token: str | None = None,
    authorization: str | None = Header(default=None),
):
    t = require_token(authorization, token)
    user_id = get_user_id_from_token(t)

    pw = (body.password or "").strip()
    if not pw:
        raise HTTPException(400, "Password required")

    with db.cursor() as cur:
        cur.execute("SELECT password_hash, pin_hash FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(404, "User not found")

    password_hash, pin_hash = row[0], row[1]

    if not check_pw(pw, password_hash):
        raise HTTPException(401, "Wrong password")

    # If PIN exists, require it as confirmation
    if pin_hash:
        provided_pin = (body.pin or "").strip()
        if len(provided_pin) < 4 or not check_pw(provided_pin, pin_hash):
            raise HTTPException(401, "Wrong PIN")

    delete_user_account(user_id)
    return {"ok": True}
