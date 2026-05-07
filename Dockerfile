FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY yt_clipper.py /app/yt_clipper.py

WORKDIR /work

ENTRYPOINT ["python", "/app/yt_clipper.py"]
