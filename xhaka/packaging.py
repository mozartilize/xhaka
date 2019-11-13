import os
import json as pyjson
from email import encoders
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio


def prepare_package(file_path, destination_folder_id):
    related = MIMEMultipart('related')
    related.set_boundary("--foo_bar_baz--")

    fileinfo = MIMEApplication(pyjson.dumps(
        {
            "name": os.path.split(file_path)[1],
            "parents": [destination_folder_id]
        }), "json", _encoder=encoders.encode_noop, charset='utf-8')
    related.attach(fileinfo)

    with open(file_path, 'rb') as f:
        upload_file = MIMEAudio(f.read(), 'mpeg')
    related.attach(upload_file)

    def write_empty_headers(self): pass
    related._write_headers = write_empty_headers  # prevent writing headers

    return dict(related.items()), related.as_bytes()
