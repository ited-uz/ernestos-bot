FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# FFmpeg validates duration and normalizes Telegram OGG / browser WebM / M4A.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints-tested.txt ./
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.1" \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home ernest && chown -R ernest:ernest /app
USER ernest
# One polling worker. Do not start a second instance with the same bot token.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
