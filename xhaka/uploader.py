#!/usr/bin/env python

import os
import sys
import urllib.parse as urlparse
from urllib.parse import parse_qs

import requests

BASE_URL = "https://www.googleapis.com/upload/drive/v3/files"


def main():
    access_token = os.getenv("GOOGLE_API_ACCESS_TOKEN")
    if not access_token:
        sys.exit("access_token not found, set GOOGLE_API_ACCESS_TOKEN env variable.")
    if not len(sys.argv) > 1:
        sys.exit("file_name and destination_folder_id are required.")
    if not len(sys.argv) > 2:
        sys.exit("destination_folder_id is required.")
    file_name = sys.argv[1]
    destination_folder_id = sys.argv[2]
    CHUNK_SIZE = 256 * 1024
    uploader = requests.Session()
    uploader.params = {
        "access_token": access_token,
        "uploadType": "resumable",
    }
    print(file_name, destination_folder_id)
    create_file_resp = uploader.post(
        BASE_URL,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        json={
            "name": file_name,
            "parents": [destination_folder_id] if destination_folder_id else [],
        },
    )
    if create_file_resp.status_code != 200:
        sys.exit(f"Create file upload error! Error: {create_file_resp.json()}")
    parsed_create_file_url = urlparse.urlparse(create_file_resp.headers["Location"])
    upload_id = parse_qs(parsed_create_file_url.query)["upload_id"][0]
    print(upload_id)
    uploader.params["upload_id"] = upload_id
    i = 0
    while True:
        buf = sys.stdin.buffer.read(CHUNK_SIZE)
        if not buf:
            break
        content_length = len(buf)
        if content_length == CHUNK_SIZE:
            content_range = [i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE - 1]
            file_length = "*"  # fake lenght cause we dont know it
        else:
            file_length = i * CHUNK_SIZE + content_length
            content_range = [i * CHUNK_SIZE, file_length - 1]
        resp = uploader.put(
            BASE_URL,
            headers={
                "Content-Length": str(content_length),
                "Content-Range": f"bytes {content_range[0]}-{content_range[1]}/{file_length}",
            },
            data=buf,
        )
        print(
            f"i={i},content_length={file_length}",
            f"bytes {content_range[0]}-{content_range[1]}/{file_length}",
            resp.content,
            resp.status_code,
        )
        if resp.status_code in [200, 201]:
            print("Upload completed!")
        elif resp.status_code == 308:
            i += 1
        else:
            sys.exit("Upload failed!")


if __name__ == "__main__":
    main()
