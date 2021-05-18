from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import current_app, redirect, request, session, url_for
from loginpass import Google, create_flask_blueprint
from werkzeug.exceptions import Unauthorized


def fetch_token(name):
    token = {
        "access_token": request.cookies.get("access_token"),
        "expires_at": request.cookies.get("expires_at", 0),
        "refresh_token": request.cookies.get("refresh_token"),
        "token_type": "Bearer",
    }
    if not token.get("refresh_token"):
        return {}
    if not token.get("access_token"):
        current_app.logger.info("Refresh access token")
        try:
            new_token = oauth.google.fetch_access_token(
                refresh_token=token.get("refresh_token"), grant_type="refresh_token"
            )
        except Exception as e:
            current_app.logger.error(e)
            return {}
        else:
            token["access_token"] = new_token["access_token"]
            token["expires_at"] = new_token["expires_at"]
    return token


def handle_authorize(remote, token, user_info):
    if token:
        is_secure = current_app.config["ENV"] == "production"
        resp = redirect(url_for("upload"))
        resp.set_cookie(
            "access_token",
            token["access_token"],
            max_age=token["expires_in"],
            httponly=True,
            secure=is_secure,
        )
        resp.set_cookie(
            "expires_at",
            str(token["expires_at"]),
            httponly=True,
            secure=is_secure,
        )
        resp.set_cookie(
            "refresh_token",
            token["refresh_token"],
            httponly=True,
            secure=is_secure,
        )
        session["user_id"] = user_info["sub"]
        return resp
    raise Unauthorized()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if (
            oauth.google.token
            and not oauth.google.token.get("refresh_token")
            or not oauth.google.token
        ):
            return redirect(url_for("home"))
        return f(*args, **kwargs)

    return wrapper


oauth = OAuth(fetch_token=fetch_token)
google_bp = create_flask_blueprint([Google], oauth, handle_authorize)
