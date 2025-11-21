# routes.py
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, and_, desc

from models import db, User, Friend, FriendRequest, Message

bp = Blueprint("routes", __name__)

# ---------- helpers ----------

def _json_error(message, code=400):
    return jsonify({"error": message}), code

def _strip(s):
    return (s or "").strip()

def _get_user_by_username(username: str):
    if not username:
        return None
    return User.query.filter_by(username=username).first()


# ---------- auth ----------

@bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = _strip(data.get("username"))
    email    = _strip(data.get("email"))
    password = data.get("password") or ""

    if not username or not email or not password:
        return _json_error("username,email,password required", 400)

    # Basic length checks (tweak as you like)
    if len(username) < 3 or len(password) < 4:
        return _json_error("weak username or password", 400)

    # Uniqueness checks (fast path before commit)
    if User.query.filter_by(username=username).first():
        return _json_error("username exists", 400)
    if User.query.filter_by(email=email).first():
        return _json_error("email exists", 400)

    u = User(username=username, email=email)
    try:
        u.set_password(password)
    except Exception as exc:
        current_app.logger.exception("set_password failed: %s", exc)
        return _json_error("server error", 500)

    db.session.add(u)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Race condition fallback if another request created same username/email
        return _json_error("username or email already exists", 400)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("register commit failed: %s", exc)
        return _json_error("server error", 500)

    return jsonify({"msg": "registered", "user": u.to_dict()}), 201


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = _strip(data.get("username"))
    password = data.get("password") or ""

    if not username or not password:
        return _json_error("username and password required", 400)

    u = _get_user_by_username(username)
    if not u or not u.check_password(password):
        return _json_error("invalid credentials", 401)

    token = create_access_token(identity=u.username)
    return jsonify({"access_token": token, "user": u.to_dict()}), 200


# ---------- users / search ----------

@bp.route("/search", methods=["GET"])
@jwt_required()
def search():
    """
    Behavior:
      - If ?q=... (non-empty) -> search users by username (as before).
      - If q is empty/missing -> return only users that are part of
        incoming OR outgoing pending friend requests for the current user.
        (This makes the small user-list under the search box show requests only.)
    """
    q = _strip(request.args.get("q") or "")
    me_username = get_jwt_identity()
    me = _get_user_by_username(me_username)
    if not me:
        return _json_error("user not found", 404)

    if q:
        # typed search: search across all users (exclude self)
        query = User.query.filter(User.username != me_username)
        query = query.filter(User.username.ilike(f"%{q}%"))
        users = query.order_by(User.username.asc()).limit(200).all()
        return jsonify([u.to_dict() for u in users]), 200

    # q is empty => show only pending request users (incoming + outgoing)
    incoming = (
        db.session.query(FriendRequest, User)
        .join(User, User.id == FriendRequest.from_user)
        .filter(FriendRequest.to_user == me.id, FriendRequest.status == 'pending')
        .all()
    )
    outgoing = (
        db.session.query(FriendRequest, User)
        .join(User, User.id == FriendRequest.to_user)
        .filter(FriendRequest.from_user == me.id, FriendRequest.status == 'pending')
        .all()
    )

    # Combine unique users preserving a consistent order (incoming first then outgoing)
    seen = set()
    result = []
    for fr, u in incoming:
        if u.username not in seen:
            result.append(u.to_dict())
            seen.add(u.username)
    for fr, u in outgoing:
        if u.username not in seen:
            result.append(u.to_dict())
            seen.add(u.username)

    return jsonify(result), 200


# ---------- friendship ----------

@bp.route("/friend/request", methods=["POST"])
@jwt_required()
def send_friend_request():
    data = request.get_json(silent=True) or {}
    to_username = _strip(data.get("to"))
    me_username = get_jwt_identity()

    if not to_username:
        return _json_error("to required", 400)
    if to_username == me_username:
        return _json_error("cannot friend self", 400)

    me_u = _get_user_by_username(me_username)
    to_u = _get_user_by_username(to_username)
    if not to_u:
        return _json_error("user not found", 404)

    if Friend.are_friends(me_u.id, to_u.id):
        return _json_error("already friends", 400)

    exists = FriendRequest.query.filter_by(
        from_user=me_u.id, to_user=to_u.id, status='pending'
    ).first()
    if exists:
        return _json_error("request already sent", 400)

    fr = FriendRequest(from_user=me_u.id, to_user=to_u.id, status='pending')
    db.session.add(fr)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("send_friend_request failed: %s", exc)
        return _json_error("server error", 500)

    return jsonify({"msg": "request_sent"}), 200


@bp.route("/friend/accept", methods=["POST"])
@jwt_required()
def accept_request():
    data = request.get_json(silent=True) or {}
    from_username = _strip(data.get("from"))
    me_username = get_jwt_identity()
    if not from_username:
        return _json_error("from required", 400)

    me_u = _get_user_by_username(me_username)
    other = _get_user_by_username(from_username)
    if not other:
        return _json_error("user not found", 404)

    fr = FriendRequest.query.filter_by(
        from_user=other.id, to_user=me_u.id, status='pending'
    ).first()
    if not fr:
        return _json_error("request not found", 404)

    fr.status = 'accepted'
    a, b = sorted([me_u.id, other.id])
    if not Friend.are_friends(a, b):
        db.session.add(Friend(user_a=a, user_b=b))

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("accept_request failed: %s", exc)
        return _json_error("server error", 500)

    return jsonify({"msg": "accepted"}), 200


@bp.route("/friend/remove", methods=["POST"])
@jwt_required()
def remove_friend():
    data = request.get_json(silent=True) or {}
    other_username = _strip(data.get("with"))
    me_username = get_jwt_identity()
    if not other_username:
        return _json_error("with required", 400)

    me_u = _get_user_by_username(me_username)
    other = _get_user_by_username(other_username)
    if not other:
        return _json_error("user not found", 404)

    a, b = sorted([me_u.id, other.id])
    try:
        Friend.query.filter_by(user_a=a, user_b=b).delete()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("remove_friend failed: %s", exc)
        return _json_error("server error", 500)

    return jsonify({"msg": "removed"}), 200


# ---------- messages ----------

@bp.route("/messages/<friend_username>", methods=["GET"])
@jwt_required()
def get_messages(friend_username):
    """
    Return messages between current user and friend.
    Query params:
      - all=true -> return the full conversation (no server-side limit)
      - limit=N  -> when all!=true, use this limit (default 200)
      - before=<ISO timestamp> -> return messages created BEFORE this timestamp (useful for pagination)
    """
    me_username = get_jwt_identity()
    me_u = _get_user_by_username(me_username)
    friend = _get_user_by_username(_strip(friend_username))

    if not friend:
        return _json_error("user not found", 404)
    if not Friend.are_friends(me_u.id, friend.id):
        return _json_error("not friends", 403)

    # Build base query (both directions)
    base_q = Message.query.filter(
        or_(
            and_(Message.from_user == me_u.id, Message.to_user == friend.id),
            and_(Message.from_user == friend.id, Message.to_user == me_u.id),
        )
    )

    # Exclude soft-deleted messages (deleted flag False or NULL)
    # If your schema uses boolean with default False, this is fine.
    base_q = base_q.filter(or_(Message.deleted == False, Message.deleted.is_(None)))

    # Optional 'before' param for pagination
    before = _strip(request.args.get("before") or "")
    if before:
        try:
            # assume ISO-8601 timestamp string — compare against created_at
            # SQLAlchemy will attempt to compare; if your created_at is timezone-aware adjust accordingly.
            base_q = base_q.filter(Message.created_at < before)
        except Exception:
            # ignore parse issues and continue without before filter
            pass

    # all flag
    all_flag = str(request.args.get("all", "false")).lower() in ("1", "true", "yes")

    if all_flag:
        msgs = base_q.order_by(Message.created_at.asc()).all()
    else:
        # safe default limit (change if desired)
        try:
            limit = int(request.args.get("limit", 200))
            if limit <= 0:
                limit = 200
        except Exception:
            limit = 200
        msgs = base_q.order_by(Message.created_at.asc()).limit(limit).all()

    # mark read for messages addressed to me (same behavior as before)
    changed = False
    for m in msgs:
        if m.to_user == me_u.id and not getattr(m, "read_flag", False):
            m.read_flag = True
            changed = True
    if changed:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.debug("mark-read commit failed: %s", exc)

    return jsonify([m.to_dict() for m in msgs]), 200


# ---------- optional convenience (no UI dependence) ----------

@bp.route("/friends", methods=["GET"])
@jwt_required()
def list_friends():
    """Return the list of usernames I am friends with."""
    me_username = get_jwt_identity()
    me_u = _get_user_by_username(me_username)

    pairs = Friend.query.filter(
        or_(Friend.user_a == me_u.id, Friend.user_b == me_u.id)
    ).all()

    ids = []
    for f in pairs:
        ids.append(f.user_a if f.user_a != me_u.id else f.user_b)

    if not ids:
        return jsonify([]), 200

    users = User.query.filter(User.id.in_(ids)).order_by(User.username.asc()).all()
    return jsonify([u.to_dict() for u in users]), 200


@bp.route("/friend/requests", methods=["GET"])
@jwt_required()
def list_requests():
    """List pending friend requests (incoming and outgoing)."""
    me_username = get_jwt_identity()
    me_u = _get_user_by_username(me_username)

    incoming = (
        db.session.query(FriendRequest, User)
        .join(User, User.id == FriendRequest.from_user)
        .filter(FriendRequest.to_user == me_u.id, FriendRequest.status == 'pending')
        .all()
    )
    outgoing = (
        db.session.query(FriendRequest, User)
        .join(User, User.id == FriendRequest.to_user)
        .filter(FriendRequest.from_user == me_u.id, FriendRequest.status == 'pending')
        .all()
    )

    return jsonify({
        "incoming": [{"from": u.username, "at": fr.created_at.isoformat()} for fr, u in incoming],
        "outgoing": [{"to": u.username, "at": fr.created_at.isoformat()} for fr, u in outgoing],
    }), 200
