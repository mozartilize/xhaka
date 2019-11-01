from functools import wraps
import subprocess
import json
from email import encoders
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio
from flask import Flask, render_template, make_response, request, redirect, url_for, Response, session
from werkzeug.exceptions import HTTPException
from authlib.flask.client import OAuth
from loginpass import create_flask_blueprint, Google

from .helpers import folder_list_filted, get_folder_hierarchy


app = Flask(__name__)
app.config.from_object('xhaka.settings')


def fetch_token(name):
    return {
        "access_token": request.cookies.get("access_token"),
        "expires_at": request.cookies.get("expires_at"),
        "refresh_token": request.cookies.get("refresh_token"),
        "token_type": "Bearer",
    } 


oauth = OAuth(app, fetch_token=fetch_token)


def handle_authorize(remote, token, user_info):
    if token:
        resp = redirect(url_for("upload"))
        resp.set_cookie('access_token', token['access_token'], max_age=token['expires_in'], httponly=True)
        resp.set_cookie('expires_at', str(token['expires_at']), httponly=True)
        resp.set_cookie('refresh_token', token.get('refresh_token', ''), httponly=True)
        return resp
    raise HTTPException


def refresh_access_token(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if oauth.google.token.get("access_token") is None:
            if oauth.google.token.get("refresh_token"):
                token = oauth.google.fetch_access_token(
                    refresh_token=oauth.google.token.get("refresh_token"),
                    grant_type="refresh_token"
                )
                oauth.google.token["access_token"] = token["access_token"]
                oauth.google.token["expires_at"] = token["expires_at"]
                resp = f(*args, **kwargs)
                if not isinstance(resp, Response):
                    resp = make_response(resp)
                resp.set_cookie('access_token', token["access_token"], max_age=token['expires_in'], httponly=True)
                resp.set_cookie('expires_at', str(token['expires_at']), httponly=True)
                return resp
            else:
                return redirect(url_for("home"))
        else:
            return f(*args, **kwargs)
    return wrapper


google_bp = create_flask_blueprint(Google, oauth, handle_authorize)
app.register_blueprint(google_bp, url_prefix="/google")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/upload", methods=["GET", "POST"])
@refresh_access_token
def upload():
    stt_code = 200
    msg = session.pop('msg', None)
    if request.method == "POST":
        parents = request.form.get("parents")
        yt_url = request.form.get("url")
        if yt_url:
            try:
                result = subprocess.run(
                    ["youtube-dl", "-x", "--audio-format", "mp3", yt_url],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True
                )
            except subprocess.CalledProcessError as e:
                stt_code = 400
                app.logger.info(e)
                msg = {'type': 'error', 'msg': str(e.stderr.decode("utf-8"))}
            else:
                result_info = result.stdout.decode("utf-8")
                app.logger.info(result_info)
                file_path = result_info.split("[ffmpeg] Destination: ")[1]
                file_path = file_path.split("\nDeleting original file")[0]

                related = MIMEMultipart('related')
                related.set_boundary("--foo_bar_baz--")

                fileinfo = MIMEApplication(json.dumps({
                    "name": file_path,
                    "parents": [parents]
                }).encode('utf-8'), "json; charset=UTF-8", _encoder=encoders.encode_noop)
                related.attach(fileinfo)

                upload_file = MIMEAudio(open(file_path, 'rb').read(), 'mpeg')
                related.attach(upload_file)

                body = related.as_string().split('\n\n', 1)[1]
                headers = dict(related.items())
                gdrive_upload_resp = oauth.google.post(
                    "/upload/drive/v3/files?uploadType=multipart",
                    data=body,
                    headers=headers
                )
                app.logger.info(gdrive_upload_resp.status_code)
                app.logger.info(gdrive_upload_resp.text)
                if gdrive_upload_resp.status_code == 200:
                    session['msg'] = {'type': 'success', 'msg': 'Success'}
                    return redirect(url_for("upload"))
                elif 400 <= gdrive_upload_resp.status_code < 500:
                    msg = {'type': 'error', 'msg': gdrive_upload_resp.text}
                    stt_code = gdrive_upload_resp.status_code
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
    return render_template(
        "upload.html",
        folder_hierarchy=folder_hierarchy,
        folders_map=folders_map,
        msg=msg
    ), stt_code
