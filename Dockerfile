FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY yt_clipper.py /app/yt_clipper.py

RUN useradd --create-home --uid 1000 clipper \
    && mkdir -p /work \
    && chown clipper:clipper /work

USER clipper
WORKDIR /work

ENTRYPOINT ["python", "/app/yt_clipper.py"]
