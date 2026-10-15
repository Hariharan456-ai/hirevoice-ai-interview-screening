import secrets
from datetime import timedelta
from flask import Flask
from . import config, database

def create_app():
    app = Flask(__name__, template_folder=str(config.BASE_DIR / "templates"),
                static_folder=str(config.BASE_DIR / "static"))
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = config.DATA_DIR / "secret.key"
    if not key_file.exists(): key_file.write_text(secrets.token_hex(32))
    app.secret_key = key_file.read_text().strip()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    database.init_db()
    from .routes import bp
    app.register_blueprint(bp)
    from .services import pipeline
    pipeline.start_worker()
    return app
