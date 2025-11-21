# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Secrets
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")

    # ---- DATABASE: force MySQL as the default ----
    # Change user/pass/port/db to match your Workbench setup
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URI",
        "mysql+pymysql://root:root@localhost:3306/chatdb"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Healthier MySQL connection (avoids stale connections)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,  # seconds
    }

    # Sockets: Windows dev → threading
    SOCKET_ASYNC_MODE = os.getenv("SOCKET_ASYNC_MODE", "threading")
