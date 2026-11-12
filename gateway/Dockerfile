FROM debian:trixie-slim

# Default Pi 5 image: use Raspberry Pi's patched FFmpeg, not stock Debian FFmpeg.
RUN test "$(dpkg --print-architecture)" = arm64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://raw.githubusercontent.com/raspberrypi/rpi-image-gen/fb44a4cde4ac54847ef1966957149e2c2b716fdf/keydir/raspberrypi-archive-keyring.gpg -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
    && echo '3a29901549ae65a910de13a32551e6eea7f45ba1fd70f0cb67e5c70d98488071  /usr/share/keyrings/raspberrypi-archive-keyring.gpg' | sha256sum -c - \
    && printf '%s\n' 'deb [arch=arm64 signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian trixie main' > /etc/apt/sources.list.d/raspberrypi.list \
    && printf '%s\n' 'Package: ffmpeg libav* libsw* libpostproc*' 'Pin: origin "archive.raspberrypi.com"' 'Pin-Priority: 700' > /etc/apt/preferences.d/raspberrypi-ffmpeg \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg python3 python3-venv raspberrypi-archive-keyring \
    && ffmpeg -version 2>&1 | grep -q -- --enable-v4l2-request \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY gateway/requirements.txt .
RUN python3 -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt
COPY gateway/config.py gateway/media.py gateway/server.py ./
COPY tools/probe.py ./
USER 10001:10001
CMD ["python", "server.py"]
