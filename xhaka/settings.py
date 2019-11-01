import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(os.path.join(BASE_DIR, "../.env"))

SECRET_KEY = os.getenv("SECRET_KEY", "something secret")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_CLIENT_KWARGS = {
    "scope": "openid email profile https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.metadata.readonly"
}

TEMPLATES_AUTO_RELOAD = True