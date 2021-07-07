import logging
from collections import namedtuple

import dramatiq
import orjson as json
from dramatiq.brokers.redis import RedisBroker

from . import settings

redis_broker = RedisBroker(url=settings.REDIS_URL)

logger = logging.getLogger(__name__)


Job = namedtuple("Job", "started_at id yt_url folder_id folder_name status msg")


class JobDTO:
    def __init__(self, redis_client):
        self.redis_client = redis_client

    def get_job(self, user_id, job_id):
        key = "apscheduler.jobinfo:%s:%s" % (user_id, job_id)
        job_data = self.redis_client.get(key)
        job = json.loads(job_data)
        return job

    def update_job(self, user_id, job):
        key = "apscheduler.jobinfo:%s:%s" % (user_id, job.id)
        with self.redis_client.pipeline() as pipe:
            self.redis_client.set(
                key,
                json.dumps(dict(job._asdict())),
                px=self.redis_client.pttl(key),
                xx=True,
            )
            pipe.execute()

    def save_job(self, user_id, job):
        with self.redis_client.pipeline() as pipe:
            self.redis_client.set(
                "apscheduler.jobinfo:%s:%s" % (user_id, job.id),
                json.dumps(dict(job._asdict())),
                ex=3600,  # expires in 1 hour
            )
            pipe.execute()

    def get_jobs_for_user_id(self, user_id, as_dict=False):
        jobdata = self.redis_client.hgetall(user_id)
        cur = 0
        while 1:
            cur, keys = self.redis_client.scan(
                cur,
                "apscheduler.jobinfo:%s:*" % (user_id),
            )
            for key in keys:
                jobdata[key.rsplit(b":", 1)[1]] = self.redis_client.get(key)
            if cur == 0:
                break

        jobs = {
            kv[0].decode(): json.loads(kv[1]) if as_dict else Job(**json.loads(kv[1]))
            # value of jobinfo still in bytes but
            # we can order base on it because
            # started_at is first item
            for kv in sorted(jobdata.items(), key=lambda x: x[1])
        }
        return jobs


class SetResultMiddleware(dramatiq.Middleware):
    def after_process_message(self, broker, message, *, result=None, exception=None):
        _, _, _, user_id = message.args
        job_id = message.message_id
        job_dto = JobDTO(broker.client)
        job_data = job_dto.get_job(user_id, job_id)
        if exception is not None:
            job_data["status"] = "Failed"
            job_data["msg"] = str(exception)
        else:
            job_data["status"] = "Success"
        job_dto.update_job(user_id, Job(**job_data))


redis_broker.add_middleware(SetResultMiddleware())
dramatiq.set_broker(redis_broker)


@dramatiq.actor(max_retries=0)
def main_task(url, folder_id, access_token, user_id):
    import sys
    from pathlib import Path
    from subprocess import PIPE, Popen

    from youtube_dl import YoutubeDL

    with YoutubeDL({"quiet": True}) as ytdl:
        info = ytdl.extract_info(url, download=False)

    info_echo = Popen(["echo", "-n", json.dumps(info).decode()], stdout=PIPE)

    file_name = f"{info['title']}.mp3"

    ytdl_task_args = [
        "youtube-dl",
        "-q",
        "--load-info-json",
        "-",
        "-f",
        "bestaudio[ext=webm,ext=m4a]",
        url,
        "-o",
        "-",
    ]
    ytdl_task = Popen(ytdl_task_args, stdin=info_echo.stdout, stdout=PIPE)
    ffmpeg_task_args = [
        "ffmpeg",
        "-i",
        "pipe:0",
        "-vn",
        "-ab",
        "128k",
        "-ar",
        "44100",
        "-f",
        "mp3",
        "-v",
        "error",
        "pipe:1",
    ]
    ffmpeg_task = Popen(ffmpeg_task_args, stdin=ytdl_task.stdout, stdout=PIPE)
    base_dir = Path(__file__).resolve().parent
    uploader_path = base_dir / "uploader.py"
    upload_task_args = [sys.executable, str(uploader_path), file_name, folder_id]
    upload_task = Popen(
        upload_task_args,
        stdin=ffmpeg_task.stdout,
        stdout=PIPE,
        stderr=PIPE,
        env={
            "GOOGLE_API_ACCESS_TOKEN": access_token,
        },
    )
    info_echo.stdout.close()
    ytdl_task.stdout.close()
    ffmpeg_task.stdout.close()
    _, err = upload_task.communicate()
    if err:
        ytdl_task.kill()
        ffmpeg_task.kill()
        raise Exception(err.decode("utf-8"))
    logger.info(f"{user_id} saved {file_name} done.")
