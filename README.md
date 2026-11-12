# Local Protect touchscreen viewer

ESP-IDF firmware for a Guition JC3248W535C and an on-demand FFmpeg MJPEG gateway for a Raspberry Pi 5 (4 GB). Viewing needs Wi-Fi, the LAN gateway and the local camera source. Home Assistant, cloud services, audio, recording, talkback and automation are outside this release.

**Validation status:** firmware builds on ESP-IDF 5.5.2; host parser/switching tests and gateway lifecycle tests pass. Real FFmpeg synthetic tests verify all eight fit/fill and rotation combinations. No board, camera credentials or Pi shell was supplied: LCD/touch operation, actual Protect streams, Pi acceleration, latency, power and a 24-hour soak remain unmeasured. The included binary uses placeholder provisioning. Do not flash it expecting a configured viewer.

## Pi deployment

Use a 64-bit Linux installation and Docker Compose v2. Commands below run from the project root **on the Pi**. No existing Protect/Frigate/HA configuration is modified by this project.

1. Inspect the Pi with `sh tools/pi-inventory.sh > pi-inventory.txt`. Record OS, cooling, existing workload, FFmpeg build, devices and available decoders.
2. In Protect's per-camera settings, enable its local RTSP/RTSPS output (look for Share Livestream / Secure RTSPS Output; older versions group profiles under Advanced). Copy each offered low/medium/high URL exactly. Treat the entire URL as a password. Do not infer codec from the URL or blindly rewrite RTSPS ports. Start with the lowest profile, then probe and compare quality. Doorbell main and package cameras may be separate streams.
3. If Frigate/go2rtc already exposes a local stream, probe that URL before choosing it. A restream can reduce source sessions but still carries H.264/H.265. Verify an actual JPEG/transcoding output before reusing one. No go2rtc instance was available for inspection here.
4. Provision files:

```sh
mkdir -p gateway/secrets
cp gateway/config.example.json gateway/config.json
openssl rand -hex 32 > gateway/secrets/viewer_token
# Use an editor to place ONE exact source URL in each file; avoid shell history.
nano gateway/secrets/front_url
nano gateway/secrets/doorbell_url
chmod 750 gateway/secrets
chmod 640 gateway/secrets/*
# Container runs as 10001. Use a dedicated group/ACL or this ownership:
sudo chown -R 10001:10001 gateway/secrets
export LAN_IP=192.168.1.50
docker compose build
docker compose up -d
docker compose ps
```

Remove the doorbell entry for a one-camera installation, or add entries in swipe order. Every ID must be unique and stable (`[a-z0-9_-]`, 1–32 chars). Names use 1–31 ASCII characters, rendered uppercase by the small firmware font. Required fields are `id`, `name`, `url_file`; source credentials never enter the catalog. Other settings:

| Field | Default / range |
|---|---|
| `fps` | 10; integer 5–15 |
| `quality` | FFmpeg JPEG quantizer 5; 2–15, lower is better/larger |
| `mode` | `fit` letterboxes without upscaling; `fill` crops centered to 3:2 |
| `rotation` | 0, 90, 180, 270 degrees clockwise, before fit/fill |
| `decoder` | `software`; override only after a successful full pipeline probe |
| `hw_download` | false; true adds `hwdownload,format=nv12` for device frames |
| `source_ca_file` | optional in-container PEM path for RTSPS verification |
| `idle_seconds` | global 5; 0–60 |
| `max_workers` | global 2; 1–8, limit counts starting and grace-period workers |
| `max_clients` | global 8; 1–32 |

Invalid configuration fails at startup. Edit files and `docker compose restart gateway` to reload. Transcode settings are fixed per stable camera ID, so all subscribers to that ID share identical output. Grace-period workers can be reclaimed early for a switch when the worker limit is reached. No adjacent prewarming occurs.

The image pins Python 3.12.8 and all Python dependencies; apt supplies the distribution security-maintained FFmpeg. Record the image ID and `ffmpeg -version` with each deployment, then preserve the built image for exact rollback. For byte-identical rebuilds, pin a tested base-image digest and Debian snapshot in your deployment; the public Dockerfile intentionally does not freeze OS security updates. Docker/ARM image build has not been run in this Windows environment.

## Pi 5 acceleration and source selection

Pi 5 has a hardware **HEVC decoder**, rather than the H.264 encode/decode blocks from older Pis. Hardware H.264/H.265 encoding and JPEG encoding are not assumed. GPU presence is not proof of codec support. Raspberry Pi's [processor documentation](https://www.raspberrypi.com/documentation/computers/processors.html) describes the HEVC block; its [Pi 5 encoding report](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-010033-WP-1-H.264%20encoding%20performance%20on%20Raspberry%20Pi%205_series%20computers.pdf) describes software H.264 encoding.

On the Pi, install Python 3 and FFmpeg/ffprobe (or run the probe in the same image used for deployment). Probe every offered source profile:

```sh
python3 tools/probe.py --url-file /secure/front_low_url --output front-low.json
python3 tools/probe.py --url-file /secure/front_medium_url --output front-medium.json
```

Reports contain codec, profile, dimensions, pixel format, frame rates, FFmpeg inventory and a real 30-frame decode → resize → JPEG test. URLs and FFmpeg error output are suppressed. Run as an account allowed to read those secret files. FFmpeg necessarily receives the source URL in its process arguments: restrict host process visibility and admin access accordingly.

If the source is HEVC and the installed Pi-specific FFmpeg exposes a request-API decoder, test its **exact advertised name**, for example:

```sh
python3 tools/probe.py --url-file /secure/front_low_url \
  --decoder hevc_v4l2request --hw-download --output front-low-hwa.json
```

That name is an example, **not a promise that stock Debian FFmpeg includes it**. Some builds output ordinary CPU frames and need `--hw-download` omitted. A decoder listed by `ffmpeg -decoders` still needs device access, compatible kernel/media drivers and a successful pipeline test. Test inside the production container as well as on the host. Add a reviewable Compose override mapping only the `/dev/video*`, `/dev/media*` and/or `/dev/dri/*` nodes actually required by that driver, with the corresponding numeric `group_add`; use the same tested FFmpeg build in the image. Do not grant `privileged: true`. Set the camera `decoder` and `hw_download` only after success. No unsupported auto-detection or silent fallback occurs; an invalid configured hardware path reports source unavailable and backs off.

For an H.264 source, use the CPU baseline or an existing capable local transcode service. Compare a low H.264 profile on CPU against a larger HEVC profile using hardware decode: a larger source plus GPU download can cost more overall. JPEG encoding and scaling here still use CPU. Choose the lowest-cost source that resolves useful detail at 480×320. Measure CPU/memory/temperature; those are proxies, not watts. No hardware acceleration was measured or enabled on your Pi remotely.

## API and network security

All endpoints except liveness require `Authorization: Bearer <viewer_token>`. Tokens persist in the mounted file across restarts; use at least 32 ASCII characters (example generates 64 hex characters). They never belong in query strings.

| Endpoint | Behavior |
|---|---|
| `GET /healthz` | unauthenticated service liveness only, not camera health |
| `GET /v1/cameras` | `{ "cameras": [{ "id": "front", "name": "Front" }] }` in configured order |
| `GET /v1/stream/{id}` | multipart JPEG, boundary `frame`, mandatory Content-Length per part |
| `GET /v1/diagnostics` | per-camera clients, running, frames, failures, latest frame age |

Unknown IDs return 404; missing/wrong tokens 401; worker/client capacity returns 503. Stream headers are sent immediately so firmware can cancel cold workers promptly. If no first frame arrives in 8 seconds, the connection closes; after startup a 4-second source stall closes it. The firmware distinguishes successful stream negotiation with no frames (source unavailable) from failure to reach the gateway. Failure after the first frame shows reconnecting and visibly ages the last frame. A lagging write gets at most 1 second before disconnect. The newest pending frame replaces older frames per client. One blocked viewer cannot block the worker or other viewers.

Compose binds port 8080 to `LAN_IP`, **using unencrypted HTTP**. Video and the bearer token are visible to anyone able to capture that LAN traffic. Allow only the viewer VLAN/trusted LAN through your host firewall; do not publish the port to the Internet. Docker-published ports may bypass uncomplicated host firewall rules, so enforce network/DOCKER-USER rules as appropriate to your setup.

For encrypted viewing use a LAN TLS proxy with `gateway/nginx.conf.example`, a certificate whose SAN matches `viewer.home.arpa`, and a trusted CA. Adjust `proxy_pass` to the actual bound LAN address if using the Compose bind above (127.0.0.1 is appropriate only when separately bound there). Set firmware `VIEWER_GATEWAY` to `https://viewer.home.arpa`, and `VIEWER_CA_PEM` to your private CA PEM string, or use a public chain in the IDF certificate bundle. Set `VIEWER_NTP_SERVER` to a reachable **local** NTP server for certificate date validation. No certificate-verification bypass exists. For RTSPS, the gateway sets `tls_verify=1`; mount the Protect CA and set `source_ca_file` if needed. The installed FFmpeg must support these RTSP/TLS options; see [FFmpeg protocols](https://ffmpeg.org/ffmpeg-protocols.html). A build that rejects them fails closed: use a compatible build or an explicitly chosen local RTSP restream rather than disabling verification.

## Board checks and firmware

Guition's [model listing](https://www.guition.com/model-selection) identifies JC3248W535C_I_Y as ESP32-S3R8, 16 MB flash, 8 MB PSRAM, QSPI, 320×480. The [manufacturer download page](https://www.guition.com/-download) links its JC3248W535 specification, and its product listings link the `JC3248W535EN.zip` demo package. The manufacturer PDF fetch returned HTTP 403 here, and the exact user's PCB revision has not been inspected. **Pin mapping and panel initialization remain provisional**, rather than being represented as manufacturer-verified for your physical board.

The configured map is CS 45, clock 47, D0/D1/D2/D3 21/48/40/39, backlight 1; touch SDA/SCL 4/8, address 0x3B, polled with no interrupt. No LCD reset pin is assigned. Compare `firmware/main/board.h` with the schematic's IO distribution and the vendor demo's `esp_bsp.h` **before connecting this firmware**. Confirm octal PSRAM and flash identification using boot logs/esptool. The selected Espressif [AXS15231B 2.1.1 driver](https://components.espressif.com/components/espressif/esp_lcd_axs15231b/versions/2.1.1/readme) includes LCD/touch support and a default vendor initialization table; an actual module revision may require a different table. See `docs/architecture.md` for the driver's unusual QSPI sequential-strip behavior.

Install **ESP-IDF v5.5.2**, including ESP32-S3 toolchains. Use an IDF terminal:

```sh
cp firmware/main/provision.example.h firmware/main/provision.h
# Edit provision.h: SSID/password, gateway URL and token from the Pi.
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p YOUR_USB_PORT flash monitor
```

On Windows use `Copy-Item` instead of `cp` if needed. The build downloads exact component versions and uses the checked-in `dependencies.lock` with component hashes. Defaults select 16 MB flash, octal 80 MHz PSRAM and a 6 MB factory application partition. The rest of flash is deliberately unassigned. USB firmware updates are used for this release; OTA is not implemented.

First set `VIEWER_BRINGUP_ONLY=1`: boot should show the embedded native JPEG color bars, readable landscape labels and tap controls. Verify touch coordinates/gesture directions and brightness. This mode does not start Wi-Fi. After validating the display, set it to 0, provision one camera on the Pi, confirm live video, then add cameras in the desired order.

Swipe at least 55 logical pixels, predominantly horizontally, within 800 ms. Small movements and taps do not switch. Single-camera lists are valid. Camera selection changes immediately in shared state; render checks generations before decode, after decode and between LCD strips. An already submitted DMA strip cannot be recalled; subsequent old-camera strips stop. During switches the old stream is closed by the network owner after its current finite read (100 ms once streaming). Connect/header operations have a 500 ms timeout, with DNS/TLS timing still requiring hardware measurement. No concurrent task mutates or frees its HTTP client.

Tap to toggle controls. Bottom thirds dim, brighten, and select the next camera; swipes work throughout. Controls show RSSI and heap. The top overlay names the selected camera and connection state. Stale images show a red `STALE N S` bar after one second without a newly decoded frame. This age measures viewer freshness, **not capture age**. Always-on is default; configurable dim/screen-off thresholds use seconds since touch. Touch wakes the screen. Screen-off disconnects video, stops decoding, and lets the worker idle. Settings are compile-time; brightness and selected camera are not persisted.

Provisioning secrets are excluded by `.gitignore` but present in the resulting firmware image. Protect local binaries and build artifacts. Production flash encryption/secure boot are separate provisioning work, not enabled here. Library HTTP/TLS errors may include the gateway hostname, never the source camera URLs; do not put credentials in the gateway URL.

USB recovery: use a data cable; hold BOOT, press/release RESET, release BOOT, then flash the native USB serial/JTAG port. If the board has no RESET button, hold BOOT while reconnecting power. Use `idf.py -p PORT flash`; use `erase-flash` only when deliberately resetting NVS. Keep a known-good `build` export including bootloader, partition table, application and `flash_args`. Restore all files with their original offsets using esptool's `write_flash @flash_args`, from that export directory. Back up vendor firmware before first replacement if you need to recover its original demo. Do not erase the device just to update the application.

## Tests, troubleshooting, updates

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r gateway/requirements.txt
python -m unittest discover -s tests -v
cc -Wall -Wextra -Ifirmware/main tests/test_firmware.c firmware/main/multipart.c firmware/main/viewer_state.c -o /tmp/test_viewer
/tmp/test_viewer
# Optional real FFmpeg synthetic output checks:
pip install imageio-ffmpeg==0.6.0
python tools/test-media.py
```

On Windows the host C suite also builds with MSVC `/Ifirmware\main`; ESP-Clang's default target is embedded and cannot run host tests. `tools/test.ps1` expects a **host** clang in PATH.

If the screen is blank, start with bring-up mode, vendor pin/init comparison, backlight polarity and power. Color swaps suggest RGB/BGR or RGB565 byte order; mirrored/rotated touch requires a revision-specific transform in `board_touch`. PSRAM startup failure means stop and verify memory mode/model. Unsupported JPEG indicates a mismatched gateway or malformed source; firmware only accepts baseline 8-bit 320×480 YUV420. Camera failure: inspect authenticated diagnostics, probe the exact source, verify VLAN reachability and CA trust, and check configured decoder/device permissions. Gateway liveness alone does not imply a healthy source.

Gateway logs intentionally suppress raw FFmpeg stderr because it can reveal stream tokens; the probe reports success/failure without echoing secrets. An operator can run FFmpeg interactively on the Pi when needed, keeping its output private. Worker failures retry at 1–30 seconds plus jitter. Compose restarts the gateway process on failure; health checks report liveness without automatically restarting healthy processes just because a source is down.

For updates, preserve a tagged built image (`docker image tag IMAGE_ID viewer-gateway:known-good`), configuration and local firmware export before rebuilding. Run tests and probes, `docker compose build`, then `docker compose up -d`. Roll back using a Compose override with `image: viewer-gateway:known-good` and `docker compose -f compose.yaml -f rollback.yaml up -d --no-build`; restore matching config if its schema changed. Firmware rollback uses the saved USB flash export. See `docs/benchmark.md` for acceptance and soak procedures.
