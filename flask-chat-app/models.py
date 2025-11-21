# models.py
from __future__ import annotations

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, Index, event
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ---------- small helper: UTC ISO string with trailing 'Z' ----------
def _utc_iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# -----------------------------
# Users
# -----------------------------
class User(db.Model):
    __tablename__ = "users"
    __table_args__ = {
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
        "mysql_engine": "InnoDB",
    }

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, plain: str):
        self.password_hash = generate_password_hash(plain)

    def check_password(self, plain: str) -> bool:
        return check_password_hash(self.password_hash, plain)

    def to_dict(self):
        # Keep payload same as before (no created_at to avoid breaking UI)
        return {"id": self.id, "username": self.username, "email": self.email}

    def __repr__(self) -> str:
        return f"<User {self.username}>"


# -----------------------------
# Friends (undirected edge)
# Always store (min(id), max(id)) as (user_a, user_b)
# -----------------------------
class Friend(db.Model):
    __tablename__ = "friends"
    __table_args__ = (
        UniqueConstraint("user_a", "user_b", name="uq_friend_pair"),
        Index("ix_friends_user_a", "user_a"),
        Index("ix_friends_user_b", "user_b"),
        {
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
            "mysql_engine": "InnoDB",
        },
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_a = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_b = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @staticmethod
    def normalized_pair(a_id: int, b_id: int) -> tuple[int, int]:
        a, b = int(a_id), int(b_id)
        return (a, b) if a <= b else (b, a)

    @staticmethod
    def are_friends(a_id: int, b_id: int) -> bool:
        a, b = Friend.normalized_pair(a_id, b_id)
        return db.session.query(Friend.id).filter_by(user_a=a, user_b=b).first() is not None

    def __repr__(self) -> str:
        return f"<Friend {self.user_a} <-> {self.user_b}>"


# Normalize (user_a, user_b) before insert/update
@event.listens_for(Friend, "before_insert")
def _friend_before_insert(mapper, connection, target: Friend):
    target.user_a, target.user_b = Friend.normalized_pair(target.user_a, target.user_b)


@event.listens_for(Friend, "before_update")
def _friend_before_update(mapper, connection, target: Friend):
    target.user_a, target.user_b = Friend.normalized_pair(target.user_a, target.user_b)


# -----------------------------
# Friend Requests (directed edge)
# -----------------------------
class FriendRequest(db.Model):
    __tablename__ = "friend_requests"
    __table_args__ = (
        Index("ix_fr_to_status", "to_user", "status"),
        Index("ix_fr_from_status", "from_user", "status"),
        {
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
            "mysql_engine": "InnoDB",
        },
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    from_user = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_user = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(
        db.Enum("pending", "accepted", "rejected", name="fr_status"),
        default="pending",
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<FriendRequest {self.from_user} -> {self.to_user} [{self.status}]>"


# -----------------------------
# Messages
# -----------------------------
class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_msg_pair_time", "from_user", "to_user", "created_at"),
        {
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
            "mysql_engine": "InnoDB",
        },
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    from_user = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_user = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read_flag = db.Column(db.Boolean, default=False, nullable=False)
    deleted = db.Column(db.Boolean, default=False, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "from_user": self.from_user,
            "to_user": self.to_user,
            "text": self.text,
            "created_at": _utc_iso(self.created_at),  # <<— UTC with 'Z'
            "read_flag": bool(self.read_flag),
            "deleted": bool(self.deleted),
        }

    def __repr__(self) -> str:
        return f"<Message {self.from_user}->{self.to_user} at {self.created_at:%Y-%m-%d %H:%M:%S}>"
