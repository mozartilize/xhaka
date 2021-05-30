import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    ENV = os.path.abspath(os.getenv("ENV") or os.path.join(BASE_DIR, "../.env"))
    load_dotenv(ENV)

SECRET_KEY = os.getenv("SECRET_KEY", "something secret")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_CLIENT_KWARGS = {
    "scope": "openid email profile https://www.googleapis.com/auth/drive.file"
    " https://www.googleapis.com/auth/drive.metadata.readonly"
}
GOOGLE_AUTHORIZE_PARAMS = {"prompt": "consent", "access_type": "offline"}

REDIS_URL = os.getenv("REDIS_URL")

TEMPLATES_AUTO_RELOAD = True

KAFKA_BROKERS = "localhost"
