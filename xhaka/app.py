from datetime import datetime, timedelta, timezone
from logging.config import dictConfig

import redis
from flask import (
    Flask,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect

from .ggdrive_folders import folder_list_filted, get_folder_hierarchy
from .tasks import Job, JobDTO, main_task

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[PID %(process)d - %(asctime)s] %(levelname)s in %(module)s: %(message)s",
            }
        },
        "handlers": {
            "wsgi": {"class": "logging.StreamHandler", "formatter": "default"}
        },
        "root": {"level": "DEBUG", "handlers": ["wsgi"]},
    }
)

csrf = CSRFProtect()


def get_folders_and_store_to_session(client):
    current_app.logger.info("Refresh folders for user_id: %s" % (session["user_id"]))
    folders_data_resp = client.get(
        "/drive/v2/files",
        params={
            "corpora": "default",
            "q": "mimeType='application/vnd.google-apps.folder'",
            "orderBy": "folder",
        },
    )
    folders_data = folder_list_filted(folders_data_resp.json())
    folder_hierarchy, folders_map = get_folder_hierarchy(folders_data)
    session["folder_hierarchy"] = folder_hierarchy
    session["folders_map"] = folders_map
    return folder_hierarchy, folders_map


def create_app():
    app = Flask("xhaka")
    app.config.from_object("xhaka.settings")

    csrf.init_app(app)

    from .oauth import google_bp, login_required, oauth

    oauth.init_app(app)
    app.register_blueprint(google_bp, url_prefix="")

    @app.template_filter("timestamp_to_datetime")
    def timestamp_to_datetime(s):
        return datetime.fromtimestamp(s, timezone(timedelta(0)))

    @app.route("/")
    def home():
        return render_template("index.html")

    @app.route("/upload", methods=["GET", "POST"])
    @login_required
    def upload():
        stt_code = 200
        msg = session.pop("msg", None)
        if msg and msg["msg"] == "Task created":
            stt_code = 202

        if not session.get("folder_hierarchy") or not session.get("folders_map"):
            folder_hierarchy, folders_map = get_folders_and_store_to_session(
                oauth.google
            )
        else:
            folder_hierarchy = session["folder_hierarchy"]
            folders_map = session["folders_map"]

        if request.method == "POST":
            folder_id = request.form.get("folder_id")
            yt_url = request.form.get("url")
            app.logger.info("adding job")
            job = main_task.send(
                yt_url,
                folder_id,
                oauth.google.token["access_token"],
                session["user_id"],
            )
            app.logger.info("add job done")

            app.logger.info("save initial job info to redis")
            job_dto = JobDTO(redis.Redis.from_url(app.config["REDIS_URL"]))
            job_dto.save_job(
                session["user_id"],
                Job(
                    **{
                        "id": job.message_id,
                        "started_at": int(job.message_timestamp / 1000),
                        "yt_url": yt_url,
                        "folder_id": folder_id,
                        "folder_name": folders_map.get(folder_id, ""),
                        "status": None,
                        "msg": None,
                    }
                ),
            )
            app.logger.info("save to redis done")

            session["msg"] = {"type": "success", "msg": "Task created"}
            return redirect(url_for("upload"))

        return (
            render_template(
                "upload.html",
                folder_hierarchy=folder_hierarchy,
                folders_map=folders_map,
                msg=msg,
            ),
            stt_code,
        )

    @app.route("/refresh-folders")
    @login_required
    def refresh_folders():
        get_folders_and_store_to_session(oauth.google)
        return redirect(url_for("upload"))

    @app.route("/tasks")
    @login_required
    def tasks():
        job_dto = JobDTO(redis.Redis.from_url(app.config["REDIS_URL"]))
        jobs = job_dto.get_jobs_for_user_id(session["user_id"], as_dict=True)
        return render_template("tasks.html", jobs=jobs)

    @app.after_request
    def send_credentials_cookie(resp):
        token = oauth.google.token
        if not request.cookies.get("access_token") and token.get("access_token"):
            resp.set_cookie(
                "access_token",
                token["access_token"],
                expires=token["expires_at"],
                httponly=True,
            )
            resp.set_cookie(
                "expires_at",
                str(token["expires_at"]),
                httponly=True,
                secure=current_app.config["ENV"] == "production",
            )
            if not session.get("user_id"):
                session["user_id"] = oauth.google.profile()["sub"]
        return resp

    return app
