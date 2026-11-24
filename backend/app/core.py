import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return secrets.token_hex(16)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


@dataclass(frozen=True)
class Settings:
    database: Path = ROOT / os.getenv("AI_COLLAB_DB", "var/app.sqlite3")
    demo: bool = os.getenv("AI_COLLAB_MODE", "demo") == "demo"
    pseudonym_key: str = os.getenv("AI_COLLAB_PSEUDONYM_KEY", "")

    def key(self):
        if self.pseudonym_key and (self.demo or len(self.pseudonym_key) >= 32):
            return self.pseudonym_key.encode()
        if self.demo:
            path = self.database.with_suffix(".pseudonym-key")
            try:
                with path.open("x", encoding="ascii") as output:
                    output.write(secrets.token_hex(32))
            except FileExistsError:
                pass
            key = path.read_text(encoding="ascii")
            if len(key) == 64:
                return key.encode()
            raise RuntimeError("本地假名密钥文件不完整，请检查权限或恢复密钥。")
        raise RuntimeError("生产模式需要持久的 AI_COLLAB_PSEUDONYM_KEY。")


def pseudonym(key, scope, identifier):
    identifier = unicodedata.normalize("NFC", identifier.strip())
    return hmac.new(key, dump([scope, identifier]).encode(), hashlib.sha256).hexdigest()


class Problem(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message = status, code, message
        self.details = details or {}


def row_or_404(row):
    if row is None:
        raise Problem(404, "NOT_FOUND", "找不到所请求的记录。")
    return row


def connect(settings):
    db = sqlite3.connect(settings.database, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def database(settings, *, write=False):
    db = connect(settings)
    try:
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def migrate(settings):
    settings.database.parent.mkdir(parents=True, exist_ok=True)
    settings.key()
    db = connect(settings)
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
        for path in sorted((ROOT / "backend" / "migrations").glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8")
                    + f"\nINSERT INTO schema_migrations VALUES ({version});\nCOMMIT;"
                )
        from .studychat_quality import quarantine_existing

        db.execute("BEGIN IMMEDIATE")
        quarantine_existing(db)
        db.commit()
    finally:
        db.close()


def require_role(user, *roles):
    if user["role"] not in roles:
        raise Problem(403, "FORBIDDEN", "当前角色无权执行此操作。")


def course_access(db, user, course_id, *, teacher=False):
    if not course_id:
        return False
    course = db.execute("SELECT owner_user_id FROM courses WHERE id=?", (course_id,)).fetchone()
    if course and course["owner_user_id"] == user["id"]:
        return True
    membership = db.execute(
        "SELECT role FROM course_memberships WHERE course_id=? AND user_id=? AND status='active'",
        (course_id, user["id"]),
    ).fetchone()
    return bool(membership and (not teacher or membership["role"] == "teacher"))


def source_access(db, user, source, permission="view"):
    if user["role"] == "admin":
        return True
    if user["role"] != "teacher":
        return False
    if source["owner_user_id"] == user["id"]:
        return True
    permissions = (permission, "manage") if permission != "view" else (
        "view", "evaluate", "review", "export", "manage"
    )
    for grant in db.execute("SELECT * FROM data_source_grants WHERE data_source_id=?", (source["id"],)):
        if grant["permission"] in permissions and (
            grant["user_id"] == user["id"] or course_access(db, user, grant["course_id"], teacher=True)
        ):
            return True
    return False


def get_source(db, user, source_id, permission="view"):
    source = row_or_404(db.execute("SELECT * FROM data_sources WHERE id=?", (source_id,)).fetchone())
    if not source_access(db, user, source, permission):
        raise Problem(403, "SOURCE_FORBIDDEN", "您没有该数据源的操作权限。")
    return source


def get_session(db, user, session_id, permission="view"):
    session = row_or_404(db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
    if user["role"] == "admin":
        return session
    if session["source_id"]:
        get_source(db, user, session["source_id"], permission)
    elif session["owner_user_id"] != user["id"] and not (
        user["role"] == "teacher" and permission != "manage"
        and course_access(db, user, session["course_id"], teacher=True)
    ):
        raise Problem(403, "SESSION_FORBIDDEN", "您没有该会话的操作权限。")
    return session
