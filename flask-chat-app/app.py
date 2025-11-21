# app.py
import os
import logging
from urllib.parse import urlparse

from flask import Flask, render_template, jsonify, request
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from sqlalchemy import create_engine, text

from config import Config
from models import db
from routes import bp as api_bp
from sockets import init_socketio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("flask-chat-app")

def _pick_async_mode(default="threading"):
    wanted = (os.getenv("SOCKET_ASYNC_MODE", default) or default).lower().strip()
    if wanted == "eventlet":
        try:
            import eventlet  # noqa
            log.info("Using async_mode=eventlet")
            return "eventlet"
        except Exception as exc:
            log.warning("eventlet not available (%s). Falling back to threading.", exc)
    elif wanted in {"gevent", "gevent_uwsgi"}:
        try:
            import gevent  # noqa
            log.info("Using async_mode=%s", wanted)
            return wanted
        except Exception as exc:
            log.warning("%s not available (%s). Falling back to threading.", wanted, exc)
    log.info("Using async_mode=threading")
    return "threading"

def _sanitize_uri(uri: str) -> str:
    try:
        p = urlparse(uri)
        if p.password:
            return uri.replace(p.password, "****")
    except Exception:
        pass
    return uri

def _ensure_mysql_database_exists(uri: str):
    if not (uri or "").startswith("mysql+pymysql://"):
        return
    p = urlparse(uri)
    dbname = (p.path or "").lstrip("/")
    if not dbname:
        log.warning("MySQL URI has no database name: %s", _sanitize_uri(uri))
        return
    server_uri = f"{p.scheme}://{p.username}:{p.password}@{p.hostname}:{p.port or 3306}/"
    eng = create_engine(server_uri, pool_pre_ping=True)
    with eng.connect() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        ))
        log.info("Verified/created MySQL database `%s`.", dbname)

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)

    log.info("SQLALCHEMY_DATABASE_URI = %s", _sanitize_uri(app.config["SQLALCHEMY_DATABASE_URI"]))
    try:
        _ensure_mysql_database_exists(app.config["SQLALCHEMY_DATABASE_URI"])
    except Exception as exc:
        log.exception("Failed to ensure MySQL database exists: %s", exc)

    db.init_app(app)
    JWTManager(app)

    app.register_blueprint(api_bp, url_prefix="/api")

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/")
    def index():
        return render_template("login.html")

    @app.get("/register")
    def register_page():
        return render_template("register.html")

    @app.get("/dashboard")
    def dashboard_page():
        return render_template("dashboard.html")

    def _json_or_html(resp_json, status):
        if request.path.startswith("/api"):
            return jsonify(resp_json), status
        if status == 404:
            return render_template("login.html"), status
        return jsonify(resp_json), status

    app.register_error_handler(404, lambda e: _json_or_html({"error": "not found", "path": request.path}, 404))
    app.register_error_handler(405, lambda e: _json_or_html({"error": "method not allowed", "path": request.path}, 405))
    def _api_500(e):
        log.exception("Unhandled server error: %s", e)
        return _json_or_html({"error": "server error"}, 500)
    app.register_error_handler(500, _api_500)

    return app

app = create_app()

async_mode = _pick_async_mode(default=app.config.get("SOCKET_ASYNC_MODE", "threading"))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=async_mode, logger=True, engineio_logger=True)

init_socketio(socketio)

with app.app_context():
    try:
        db.create_all()
        log.info("Database tables are ready.")
    except Exception as exc:
        log.exception("db.create_all failed: %s", exc)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    host = os.getenv("HOST", "127.0.0.1")
    log.info("Starting server on http://%s:%d  (async_mode=%s)", host, port, async_mode)
    socketio.run(app, host=host, port=port, debug=True, allow_unsafe_werkzeug=True)
