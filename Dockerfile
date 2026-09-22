FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DISPLAY=:99
RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver xvfb x11vnc fluxbox novnc websockify git ca-certificates curl x11-xserver-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/jev_ultrafast /app/jev_ultrafast
COPY app /app/app
COPY static /app/static
COPY docker/rove-vnc.html /usr/share/novnc/rove.html
COPY docker/chromium-supervisor.sh /usr/local/bin/chromium-supervisor.sh
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/chromium-supervisor.sh
EXPOSE 8080 5900 6080 9222
ENTRYPOINT ["/entrypoint.sh"]
