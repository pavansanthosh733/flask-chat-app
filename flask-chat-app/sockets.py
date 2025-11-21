# sockets.py
from __future__ import annotations

from datetime import timezone
from flask import request, current_app
from flask_socketio import emit, join_room
from flask_jwt_extended import decode_token

from models import db, User, Message, Friend


# -------- helpers --------

def _utc_iso(dt):
    """Return ISO 8601 string in UTC with trailing 'Z'."""
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is None:
        # treat stored datetimes as UTC-naive; tag them UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_bearer_token() -> str | None:
    """
    Try to find a JWT the client sent. We check, in order:
    1) Socket.IO 'auth' payload (dict) -> handled only in on_connect(auth)
    2) Authorization: Bearer <token> header
    3) Query string ?token=...
    4) Query string ?auth=... (string)
    """
    auth_obj = request.args.get("auth")

    # 2) Authorization header
    auth_hdr = request.headers.get("Authorization") or request.environ.get("HTTP_AUTHORIZATION")
    if auth_hdr and isinstance(auth_hdr, str) and auth_hdr.lower().startswith("bearer "):
        return auth_hdr.split(" ", 1)[1].strip()

    # 3) token in query params
    q = request.args or {}
    if q.get("token"):
        return q.get("token")

    # 4) socket.io 'auth' passed as a plain string (?auth=TOKEN)
    if isinstance(auth_obj, str) and auth_obj.strip():
        return auth_obj.strip()

    return None


def _decode_username_from_token(token: str | None) -> str | None:
    """
    Decode JWT and return the username stored as the identity.
    Works with flask-jwt-extended defaults (identity in 'sub').
    """
    if not token:
        return None
    try:
        decoded = decode_token(token)  # verifies signature & expiry
        return decoded.get("sub") or decoded.get("identity")
    except Exception as exc:
        current_app.logger.debug("JWT decode failed: %s", exc)
        return None


def _require_users(from_username: str, to_username: str) -> tuple[User | None, User | None]:
    """Fetch User rows for the two usernames."""
    from_user = User.query.filter_by(username=from_username).first()
    to_user = User.query.filter_by(username=to_username).first()
    return from_user, to_user


# -------- wire handlers --------

def init_socketio(socketio):
    """
    Call this once after creating the SocketIO(app) instance in app.py.
    Example:
        socketio = SocketIO(app, cors_allowed_origins="*")
        init_socketio(socketio)
    """

    @socketio.on("connect")
    def on_connect(auth=None):
        """
        Socket.IO v4 passes the client's `auth` payload here.
        Expected client usage:
            io({ auth: { token: "<JWT>" } })
        """
        token = None
        if isinstance(auth, dict) and auth.get("token"):
            token = auth.get("token")

        if not token:
            token = _extract_bearer_token()

        username = _decode_username_from_token(token)
        if not username:
            current_app.logger.info("Socket connect rejected: missing/invalid/expired JWT.")
            raise ConnectionRefusedError("unauthorized")

        join_room(f"user:{username}")
        current_app.logger.info("Socket connected: %s", username)
        emit("connected", {"msg": f"connected as {username}"})

    @socketio.on("disconnect")
    def on_disconnect():
        current_app.logger.info("Socket disconnected from %s", request.remote_addr)

    @socketio.on("send_message")
    def on_send_message(data):
        """
        Expected payload:
        {
          "token": "...",           # optional if sent via header or connect auth
          "to": "friend_username",
          "text": "hello",
          "client_id": "uuid-or-random"   # <-- used to dedupe on sender
        }
        Emits to both participants' rooms:
          event: "new_message"
          payload: { id, from, to, text, time, client_id }
        """
        try:
            token = (data or {}).get("token") or _extract_bearer_token()
            from_username = _decode_username_from_token(token)
            if not from_username:
                emit("error", {"error": "auth required"})
                return

            to_username = (data.get("to") or "").strip()
            text = (data.get("text") or "").strip()
            client_id = (data.get("client_id") or "").strip()
            if not to_username or not text:
                emit("error", {"error": "to and text required"})
                return

            from_user, to_user = _require_users(from_username, to_username)
            if not from_user or not to_user:
                emit("error", {"error": "recipient not found"})
                return

            # Require friendship (optional policy)
            try:
                are_friends = Friend.are_friends(from_user.id, to_user.id)
            except Exception:
                are_friends = True
            if not are_friends:
                emit("error", {"error": "not friends"})
                return

            # Save message
            m = Message(from_user=from_user.id, to_user=to_user.id, text=text)
            db.session.add(m)
            db.session.commit()

            payload = {
                "id": m.id,
                "from": from_username,
                "to": to_username,
                "text": text,
                "time": _utc_iso(getattr(m, "created_at", None)),  # UTC with Z
                "client_id": client_id or None,                    # echo back to sender for dedupe
            }

            # Notify both sender and recipient rooms (same event)
            socketio.emit("new_message", payload, room=f"user:{to_username}")
            socketio.emit("new_message", payload, room=f"user:{from_username}")

        except Exception as exc:
            current_app.logger.exception("send_message failed: %s", exc)
            emit("error", {"error": "server error"})

    @socketio.on("delete_message")
    def on_delete_message(data):
        """
        Sender-only delete that hides a message for both UIs (no DB mutation).

        Expected payload:
        {
          "token": "...",         # optional if sent via header or connect auth
          "id": 123,              # message id (required)
          "with": "friend_user"   # optional; helps build the notify payload
        }

        Emits to both rooms:
          event: "message_deleted"
          payload: { id, by, with }
        """
        try:
            token = (data or {}).get("token") or _extract_bearer_token()
            sender_username = _decode_username_from_token(token)
            if not sender_username:
                emit("error", {"error": "auth required"})
                return

            msg_id = (data or {}).get("id")
            friend_username = ((data or {}).get("with") or "").strip()

            if not msg_id:
                emit("error", {"error": "id required"})
                return

            # Look up sender & message
            sender = User.query.filter_by(username=sender_username).first()
            if not sender:
                emit("error", {"error": "user not found"})
                return

            msg = Message.query.filter_by(id=msg_id).first()
            if not msg:
                emit("error", {"error": "message not found"})
                return

            # Only the original sender may delete
            if msg.from_user != sender.id:
                emit("error", {"error": "only sender can delete"})
                return

            # Figure out the other participant for payload (if not provided)
            if not friend_username:
                # Since sender == from_user, friend is the recipient
                other = User.query.filter_by(id=msg.to_user).first()
                friend_username = other.username if other else ""

            # No DB mutation: keep the row intact.
            notify = {"id": msg.id, "by": sender_username, "with": friend_username}

            # Notify both sides so they remove the message from UI
            socketio.emit("message_deleted", notify, room=f"user:{sender_username}")
            if friend_username:
                socketio.emit("message_deleted", notify, room=f"user:{friend_username}")

        except Exception as exc:
            current_app.logger.exception("delete_message failed: %s", exc)
            emit("error", {"error": "server error"})

    @socketio.on("clear_chat")
    def on_clear_chat(data):
        """
        Expected payload:
        {
          "token": "...",    # optional if sent via header or connect auth
          "with": "friend_username"
        }
        Emits:
          event: "chat_cleared" to both rooms
          payload: { by, with }

        NOTE: This keeps your existing behavior of marking rows as deleted in DB.
        """
        try:
            token = (data or {}).get("token") or _extract_bearer_token()
            username = _decode_username_from_token(token)
            if not username:
                emit("error", {"error": "invalid token"})
                return

            friend_username = (data.get("with") or "").strip()
            if not friend_username:
                emit("error", {"error": "with required"})
                return

            user = User.query.filter_by(username=username).first()
            friend = User.query.filter_by(username=friend_username).first()
            if not user or not friend:
                emit("error", {"error": "friend not found"})
                return

            try:
                if not Friend.are_friends(user.id, friend.id):
                    emit("error", {"error": "not friends"})
                    return
            except Exception:
                pass

            # Soft delete (mark) all messages between the pair
            q = Message.query.filter(
                ((Message.from_user == user.id) & (Message.to_user == friend.id)) |
                ((Message.from_user == friend.id) & (Message.to_user == user.id))
            )
            q.update({"deleted": True}, synchronize_session=False)
            db.session.commit()

            notify = {"by": username, "with": friend_username}
            socketio.emit("chat_cleared", notify, room=f"user:{friend_username}")
            socketio.emit("chat_cleared", notify, room=f"user:{username}")

        except Exception as exc:
            current_app.logger.exception("clear_chat failed: %s", exc)
            emit("error", {"error": "server error"})
