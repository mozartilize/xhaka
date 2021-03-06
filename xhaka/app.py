from functools import wraps
from datetime import datetime, timezone, timedelta
from logging.config import dictConfig
from flask import Flask, current_app, render_template, request, redirect, \
    url_for, session, json
from werkzeug.exceptions import Unauthorized
from authlib.flask.client import OAuth
from authlib.common.errors import AuthlibBaseError
from loginpass import create_flask_blueprint, Google
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler import events
from xhaka.ggdrive_folders import folder_list_filted, get_folder_hierarchy
# from .scheduler_setup import is_predefined_crontask_lck

import atexit

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[PID %(process)d - %(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {'wsgi': {
        'class': 'logging.StreamHandler',
        'formatter': 'default'
    }},
    'root': {
        'level': 'DEBUG',
        'handlers': ['wsgi']
    }
})

jobinfo_keys = set([
    'started_at',
    'yt_url',
    'folder_id',
    'folder_name',
    'status',
    'msg'
])


class GDriveUploadError(Exception):
    pass


class YoutubedlError(Exception):
    pass


def download_vid_n_upload_to_ggdrive(yt_url, destination_folder_id):
    """Download youtube video, convert it to mp3 format
    and then upload to gg drive.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["youtube-dl", "-x", "--audio-format", "mp3", yt_url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
    except subprocess.CalledProcessError as e:
        current_app.logger.error(e.stderr.decode('utf-8'))
        raise YoutubedlError(e.stderr.decode('utf-8'))

    result_info = result.stdout.decode("utf-8")
    current_app.logger.info(result_info)
    file_path = result_info.split("[ffmpeg] Destination: ")[1]
    file_path = file_path.split("\nDeleting original file")[0]

    from xhaka.packaging import prepare_package
    headers, body = prepare_package(file_path, destination_folder_id)
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
        raise GDriveUploadError(gdrive_upload_resp.text)


def schedule_job(yt_url, destination_folder_id, token, user_id):
    with app.app_context():
        oauth.google.token = token
        try:
            download_vid_n_upload_to_ggdrive(yt_url, destination_folder_id)
        except BaseException as e:
            e.user_id = user_id
            raise e
    return user_id


def job_event_handler(event):
    if event.jobstore == 'memory':
        return

    user_id = (event.exception and event.exception.user_id) or event.retval
    key = "apscheduler.jobinfo:%s:%s" % (user_id, event.job_id)
    jobinfo = json.loads(redis_jobstore.redis.get(key))
    if event.exception:
        jobinfo['status'] = event.exception.__class__.__name__
        jobinfo['msg'] = str(event.exception)
    else:
        jobinfo['status'] = 'success'

    with redis_jobstore.redis.pipeline() as pipe:
        redis_jobstore.redis.set(
                key,
                json.dumps(jobinfo),
                px=redis_jobstore.redis.pttl(key),
                xx=True
            )
        pipe.execute()


app = Flask(__name__)
app.config.from_object('xhaka.settings')
csrf = CSRFProtect(app)  # noqa


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(s):
    return datetime.fromtimestamp(s, timezone(timedelta(0)))


scheduler = BackgroundScheduler()
memory_jobstore = MemoryJobStore()
scheduler.add_jobstore(memory_jobstore, 'memory')
if app.config.get("REDIS_URL"):
    from redis import Redis
    redis_jobstore = RedisJobStore()
    redis_jobstore.redis = Redis.from_url(app.config.get("REDIS_URL"))
else:
    redis_jobstore = RedisJobStore(
        host=app.config['REDIS_HOST'],
        port=app.config['REDIS_PORT'],
        password=app.config['REDIS_PASSWORD']
    )
# check if redis server is ready
redis_jobstore.redis.ping()

scheduler.add_jobstore(redis_jobstore)
scheduler.add_listener(job_event_handler,
                       events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED)

scheduler.start()


def shutdown_scheduler_on_exit():
    scheduler.shutdown()


atexit.register(shutdown_scheduler_on_exit)


def clean_up_job_info():
    from json import JSONDecodeError
    print('cleaning up task running')
    cur = 0
    while 1:
        cur, keys = redis_jobstore.redis.scan(cur, "*")
        for key in keys:
            if redis_jobstore.redis.type(key) == b'hash':
                jobs = redis_jobstore.redis.hgetall(key)
                oldjobids = []
                for jobid, jobinfodata in jobs.items():
                    try:
                        jobinfo = json.loads(jobinfodata)
                    except JSONDecodeError:
                        pass
                    else:
                        if isinstance(jobinfo, dict) \
                                and set(jobinfo.keys()) == jobinfo_keys \
                                and datetime.now(tz=timezone(timedelta(0))) \
                                - datetime.fromtimestamp(
                                    jobinfo['started_at'],
                                    timezone(timedelta(0))) \
                                > timedelta(hours=1):
                            oldjobids.append(jobid)
                if oldjobids:
                    with redis_jobstore.redis.pipeline() as pipe:
                        redis_jobstore.redis.hdel(key, *oldjobids)
                        if len(oldjobids) == len(jobs):
                            redis_jobstore.redis.delete(key)
                        pipe.execute()
        if cur == 0:
            break
    print('cleaning up task done')


# if is_predefined_crontask_lck:
#     scheduler.add_job(
#         clean_up_job_info,
#         jobstore='memory',
#         trigger='interval',
#         days=1,
#         next_run_time=datetime.now()
#     )


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
            current_app.logger.info("Refresh access token")
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

    if not session.get('folder_hierarchy') or not session.get('folders_map'):
        folder_hierarchy, folders_map = get_folders_and_store_to_session()
    else:
        folder_hierarchy = session['folder_hierarchy']
        folders_map = session['folders_map']

    if request.method == "POST":
        folder_id = request.form.get("folder_id")
        yt_url = request.form.get("url")
        app.logger.info("adding job")
        job = scheduler.add_job(
            schedule_job,
            args=(yt_url, folder_id, oauth.google.token, session['user_id']))
        app.logger.info("add job done")

        app.logger.info("save initial job info to redis")
        with redis_jobstore.redis.pipeline() as pipe:
            redis_jobstore.redis.set(
                "apscheduler.jobinfo:%s:%s" % (session['user_id'], job.id),
                json.dumps({
                    'started_at': int(job.trigger.run_date.timestamp()),
                    'yt_url': yt_url,
                    'folder_id': folder_id,
                    'folder_name': folders_map.get(folder_id, ''),
                    'status': None,
                    'msg': None
                }),
                ex=3600  # expires in 1 hour
            )
            pipe.execute()
        app.logger.info("save to redis done")

        session['msg'] = {'type': 'success', 'msg': 'Task created'}
        return redirect(url_for("upload"))

    return render_template(
        "upload.html",
        folder_hierarchy=folder_hierarchy,
        folders_map=folders_map,
        msg=msg
    ), stt_code


@app.route("/refresh-folders")
@login_required
def refresh_folders():
    get_folders_and_store_to_session()
    return redirect(url_for('upload'))


def get_folders_and_store_to_session():
    current_app.logger.info('Refresh folders for user_id: %s'
                            % (session['user_id']))
    folders_data_resp = oauth.google.get("/drive/v2/files", params={
            "corpora": "default",
            "q": "mimeType='application/vnd.google-apps.folder'",
            "orderBy": "folder",
        })
    folders_data = folder_list_filted(folders_data_resp.json())
    folder_hierarchy, folders_map = get_folder_hierarchy(folders_data)
    session['folder_hierarchy'] = folder_hierarchy
    session['folders_map'] = folders_map
    return folder_hierarchy, folders_map


@app.route("/tasks")
@login_required
def tasks():
    # for backward compatible
    jobdata = redis_jobstore.redis.hgetall(session['user_id'])
    cur = 0
    while 1:
        cur, keys = redis_jobstore.redis.scan(
            cur, "apscheduler.jobinfo:%s:*" % (session['user_id']))
        for key in keys:
            jobdata[key.rsplit(b':', 1)[1]] = redis_jobstore.redis.get(key)
        if cur == 0:
            break

    jobs = {
        kv[0].decode('ascii'): json.loads(kv[1])
        # value of jobinfo still in bytes but we can order base on it because
        # started_at is first item
        for kv in sorted(jobdata.items(), key=lambda x: x[1])
    }
    return render_template('tasks.html', jobs=jobs)
