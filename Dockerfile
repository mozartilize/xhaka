FROM python:3.7

RUN apt update && apt install --yes ffmpeg

ADD ./requirements.txt /app/requirements.txt

RUN pip install -r /app/requirements.txt

ADD ./xhaka /app/xhaka
ADD ./uwsgi.ini /app

RUN useradd -m xhaka
RUN chown xhaka:xhaka /app
WORKDIR /app

USER xhaka

ENV PORT=5000
CMD ["uwsgi", "uwsgi.ini" ]

EXPOSE 5000