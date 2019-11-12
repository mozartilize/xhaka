import subprocess
import redis
from functools import wraps
from datetime import datetime, timezone, timedelta
from email import encoders
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio
from flask import Flask, current_app, render_template, request, redirect, \
    url_for, session, json
from werkzeug.exceptions import Unauthorized
from authlib.flask.client import OAuth
from authlib.common.errors import AuthlibBaseError
from loginpass import create_flask_blueprint, Google
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler import events
import json as pyjson
from .helpers import folder_list_filted, get_folder_hierarchy


class JobBaseException(Exception):
    def __init__(self, msg, user_id):
        super().__init__(msg)
        self.user_id = user_id


class GDriveUploadError(JobBaseException):
    pass


class YoutubedlError(JobBaseException):
    pass


def download_vid_n_upload_to_ggdrive(yt_url, destination_folder_id, user_id):
    """Download youtube video, convert it to mp3 format
    and then upload to gg drive."""
    try:
        result = subprocess.run(
            ["youtube-dl", "-x", "--audio-format", "mp3", yt_url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
    except subprocess.CalledProcessError as e:
        current_app.logger.error(e.stderr.decode('utf-8'))
        raise YoutubedlError(e.stderr.decode('utf-8'), user_id)

    result_info = result.stdout.decode("utf-8")
    current_app.logger.info(result_info)
    file_path = result_info.split("[ffmpeg] Destination: ")[1]
    file_path = file_path.split("\nDeleting original file")[0]

    related = MIMEMultipart('related')
    related.set_boundary("--foo_bar_baz--")

    fileinfo = MIMEApplication(pyjson.dumps(
        {
            "name": file_path,
            "parents": [destination_folder_id]
        }), "json", _encoder=encoders.encode_noop, charset='utf-8')
    related.attach(fileinfo)

    with open(file_path, 'rb') as f:
        upload_file = MIMEAudio(f.read(), 'mpeg')
    related.attach(upload_file)

    body = related.as_string().split('\n\n', 1)[1]
    headers = dict(related.items())
    gdrive_upload_resp = oauth.google.post(
        "/upload/drive/v3/files?uploadType=multipart",
        data=body,
        headers=headers
    )
    if gdrive_upload_resp.status_code == 200:
        current_app.logger.info('upload successfully')
    else:
        current_app.logger.error(gdrive_upload_resp.status_code)
        current_app.logger.error(gdrive_upload_resp.text)
        raise GDriveUploadError(gdrive_upload_resp.text, user_id)


def schedule_job(yt_url, destination_folder_id, token, user_id):
    with app.app_context():
        oauth.google.token = token
        try:
            download_vid_n_upload_to_ggdrive(
                yt_url, destination_folder_id, user_id)
        except BaseException as e:
            e.user_id = user_id
            raise e
    return user_id


def job_event_handler(event):
    user_id = (event.exception and event.exception.user_id) or event.retval
    jobinfo = json.loads(redis_jobstore.redis.hget(user_id, event.job_id))
    if event.exception:
        jobinfo['status'] = 'error'
        jobinfo['msg'] = str(event.exception)
    else:
        jobinfo['status'] = 'success'

    with redis_jobstore.redis.pipeline() as pipe:
        redis_jobstore.redis.hset(
                user_id,
                event.job_id,
                json.dumps(jobinfo)
            )
        pipe.execute()


app = Flask(__name__)
app.config.from_object('xhaka.settings')
csrf = CSRFProtect(app)  # noqa


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(s):
    return datetime.fromtimestamp(s, timezone(timedelta(0)))


scheduler = BackgroundScheduler()
redis_jobstore = RedisJobStore(
    host=app.config['REDIS_HOST'],
    port=app.config['REDIS_PORT'],
    password=app.config['REDIS_PASSWORD']
)
try:
    redis_jobstore.redis.ping()
except redis.ConnectionError:
    raise

scheduler.add_jobstore(redis_jobstore)
scheduler.add_listener(job_event_handler,
                       events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED)

scheduler.start()


def fetch_token(name):
    if name == 'google':
        token = {
            "access_token": request.cookies.get("access_token"),
            "expires_at": request.cookies.get("expires_at"),
            "refresh_token": request.cookies.get("refresh_token"),
            "token_type": "Bearer",
        }
        if not token.get('refresh_token'):
            return {}
        if not token.get('access_token'):
            current_app.logger.info("Refesh access token")
            try:
                new_token = oauth.google.fetch_access_token(
                    refresh_token=token.get("refresh_token"),
                    grant_type="refresh_token"
                )
            except AuthlibBaseError as e:
                current_app.logger.error(e)
                return {}
            else:
                token["access_token"] = new_token["access_token"]
                token["expires_at"] = new_token["expires_at"]
        return token


@app.after_request
def send_credentials_cookie(resp):
    token = oauth.google.token
    if not request.cookies.get("access_token") and token.get("access_token"):
        resp.set_cookie(
            'access_token', token['access_token'],
            expires=token['expires_at'], httponly=True)
        resp.set_cookie('expires_at', str(token['expires_at']), httponly=True)
        if not session.get('user_id'):
            session['user_id'] = oauth.google.profile()['sub']
    return resp


oauth = OAuth(app, fetch_token=fetch_token)


def handle_authorize(remote, token, user_info):
    if token:
        resp = redirect(url_for("upload"))
        resp.set_cookie(
            'access_token', token['access_token'],
            max_age=token['expires_in'], httponly=True)
        resp.set_cookie('expires_at', str(token['expires_at']), httponly=True)
        resp.set_cookie('refresh_token', token['refresh_token'], httponly=True)
        session['user_id'] = user_info['sub']
        return resp
    raise Unauthorized()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if oauth.google.token and not oauth.google.token.get("refresh_token") \
                or not oauth.google.token:
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return wrapper


google_bp = create_flask_blueprint(Google, oauth, handle_authorize)
app.register_blueprint(google_bp, url_prefix="/google")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    stt_code = 200
    msg = session.pop('msg', None)
    if msg and msg['msg'] == 'Task created':
        stt_code = 202

    does_client_store_folders = request.cookies.get('folders_stored')
    folder_hierarchy = []
    folders_map = None
    if not does_client_store_folders:
        folders_data_resp = oauth.google.get("/drive/v2/files", params={
            "corpora": "default",
            "q": "mimeType='application/vnd.google-apps.folder'",
            "orderBy": "folder",
        })
        folders_data = folder_list_filted(folders_data_resp.json())
        folder_hierarchy, folders_map = get_folder_hierarchy(folders_data)

    if request.method == "POST":
        folder_id = request.form.get("folder_id")
        yt_url = request.form.get("url")
        job = scheduler.add_job(
            schedule_job,
            args=(yt_url, folder_id, oauth.google.token, session['user_id']))
        with redis_jobstore.redis.pipeline() as pipe:
            redis_jobstore.redis.hset(
                session['user_id'],
                job.id,
                json.dumps({
                    'started_at': int(job.trigger.run_date.timestamp()),
                    'yt_url': yt_url,
                    'folder_id': folder_id,
                    'folder_name': folders_map.get(folder_id, ''),
                    'status': None,
                    'msg': None
                })
            )
            pipe.execute()
        session['msg'] = {'type': 'success', 'msg': 'Task created'}
        return redirect(url_for("upload"))

    return render_template(
        "upload.html",
        folder_hierarchy=folder_hierarchy,
        folders_map=folders_map,
        msg=msg
    ), stt_code


@app.route("/tasks")
@login_required
def tasks():
    jobdata = redis_jobstore.redis.hgetall(session['user_id'])
    jobs = {
        kv[0].decode('ascii'): json.loads(kv[1])
        # value of jobinfo still in bytes but we can order base on it because
        # started_at is first item
        for kv in sorted(jobdata.items(), key=lambda x: x[1], reverse=True)
    }
    return render_template('tasks.html', jobs=jobs)
