# Local Protect touchscreen viewer

ESP-IDF firmware for a Guition JC3248W535C and an on-demand FFmpeg MJPEG gateway for a Raspberry Pi 5 (4 GB). Viewing needs Wi-Fi, the LAN gateway and the local camera source. Home Assistant, cloud services, audio, recording, talkback and automation are outside this release.

**Validation status:** firmware builds on ESP-IDF 6.1; host parser/switching tests and gateway lifecycle tests pass. Real FFmpeg synthetic tests verify all eight fit/fill and rotation combinations. User-supplied Pi 5 host logs confirm a 30-frame HEVC hardware decode and resize/JPEG test; the opt-in Docker hardware path still needs validation on the Pi. No board or Pi shell was supplied: LCD/touch operation, latency, power and a 24-hour soak remain unmeasured. The included binary uses placeholder provisioning. Do not flash it expecting a configured viewer.

## Pi deployment

Use a 64-bit Linux installation with Python 3.9+. Commands below run from the project root **on the Pi**. `up` detects and installs missing host prerequisites through the existing apt repositories, using sudo when necessary. No existing Protect/Frigate/HA configuration is modified by this project.

1. Inspect the Pi with `sh tools/pi-inventory.sh > pi-inventory.txt`. Record OS, cooling, existing workload, FFmpeg build, devices and available decoders.
2. In Protect's per-camera settings, enable its local RTSP/RTSPS output (look for Share Livestream / Secure RTSPS Output; older versions group profiles under Advanced). Copy each offered low/medium/high URL exactly. Treat the entire URL as a password. Do not infer codec from the URL or blindly rewrite RTSPS ports. Start with the lowest profile, then probe and compare quality. Doorbell main and package cameras may be separate streams.
3. If Frigate/go2rtc already exposes a local stream, probe that URL before choosing it. A restream can reduce source sessions but still carries H.264/H.265. Verify an actual JPEG/transcoding output before reusing one. No go2rtc instance was available for inspection here.
4. Initialize the single private configuration and deploy:

```sh
python3 tools/deploy.py init --lan-ip 192.168.1.50
nano gateway/config.json
python3 tools/deploy.py check
python3 tools/deploy.py up
python3 tools/deploy.py status
```

`init` generates a random viewer token and creates `gateway/config.json` with mode 0600; it never overwrites an existing file. Edit every camera's `url` in that file. It also holds `lan_ip`, the shared viewer `token`, and output/resource settings. Copy its token into ESP32 provisioning. The file and its migration backups are excluded from Git and Docker build context. No per-camera secret files are required. The deployment script itself uses Python's standard library; no gateway Python dependencies are needed on the host.

Remove the doorbell entry for a one-camera installation, or add entries in swipe order. Every ID must be unique and stable (`[a-z0-9_-]`, 1–32 chars). Names use 1–31 printable ASCII characters, rendered uppercase by the small firmware font. Required camera fields are `id`, `name`, `url`; source credentials never enter the catalog. Other settings:

| Field | Default / range |
|---|---|
| `fps` | 10; integer 5–15 |
| `quality` | FFmpeg JPEG quantizer 5; 2–15, lower is better/larger |
| `mode` | `fit` letterboxes without upscaling; `fill` crops centered to 3:2 |
| `orientation` | `landscape` (default) or `portrait`; portrait (e.g. a doorbell) uses the panel's native 320×480 layout, with overlays and touch controls following it |
| `rotation` | 0, 90, 180, 270 degrees clockwise, before fit/fill |
| `decoder` | `software`; override only after a successful full pipeline probe |
| `hwaccel` | `none` (default) or `drm`; `drm` enables the Raspberry Pi patched FFmpeg HEVC hardware path |
| `hw_download` | false; true adds `hwdownload,format=nv12` for device frames |
| `source_ca_file` | optional in-container PEM path for RTSPS verification |
| `idle_seconds` | global 5; 0–60 |
| `max_workers` | global 2; 1–8, limit counts starting and grace-period workers |
| `max_clients` | global 8; 1–32 |

Manage deployment using the same script (the script can be invoked by absolute path from any working directory):

| Command (`python3 tools/deploy.py …`) | Behavior |
|---|---|
| `init --lan-ip ADDRESS` | create private config and random token, then edit URLs |
| `check` | validate config/permissions/placeholders without Docker or printing secrets |
| `setup` | detect/install missing Docker, Compose, Buildx, FFmpeg and ffprobe; start/check Docker, without requiring config |
| `up` | validate, prepare prerequisites, build, recreate, and wait up to 90 seconds for service health |
| `update` | rebuild/redeploy the current checkout; does not pull Git changes |
| `restart` | validate and recreate with the current image to reload config |
| `status` | show Compose service status |
| `logs` | follow the last 100 log lines; Ctrl+C exits the viewer |
| `down` | stop/remove this Compose deployment, preserving config and images |
| `migrate --lan-ip ADDRESS` | import old token/URL files into config, preserving token and camera order |

The script supplies Docker's `LAN_IP` from `lan_ip` in the config. On Linux it runs the container with the invoking user's UID/GID so the mode-0600 config remains readable. Use the same account for initialization and deployment. When deliberately run as root, initialization assigns the config and container to UID/GID 10001. If your login cannot access the local Docker socket, the script invokes Docker through sudo while preserving your runtime UID/GID. No logout or permanent Docker-group change is needed. A failed build does not stop the old container; a failed health wait returns a nonzero exit code. Health checks validate service liveness, not camera playback.

Prerequisite preparation runs automatically for `up`, `update`, and `restart`, or separately with `python3 tools/deploy.py setup`. Working executables and plugins are reused, including your existing Raspberry Pi FFmpeg build. Apt runs only for missing components; it is instructed not to upgrade installed packages or remove conflicting packages. Missing host FFmpeg/ffprobe are installed for inventory/probing; the container still supplies its own FFmpeg. The script starts the local Docker service if stopped and installs an engine if only a local CLI exists. Remote/rootless contexts are preserved; connection failures there report an error rather than installing a local replacement. `status`, `logs`, and `down` do not install packages.

Automatic package installation supports Debian/Raspberry Pi OS/Ubuntu with apt and compatible configured repositories. On your Debian 13 Pi, distro packages `docker-cli`, `docker.io` (engine), `docker-compose` (v2), and `docker-buildx` are available; where Docker CE repositories are already configured, their packages are preferred. The CLI is installed explicitly because Debian 13's engine package only recommends it, and this script disables optional recommendations. Existing engine-only installations are completed by adding the missing CLI. The script does not replace apt sources, run a downloaded installer, remove an existing container stack, or replace a broken existing executable. Package/service errors stop deployment with a retryable explanation. Package operations may request your sudo password, retry downloads, and wait up to 120 seconds for dpkg locks.

For deployment-specific device mappings or TLS mounts, pass `--override PATH` (repeatable) to each Docker action, for example `python3 tools/deploy.py up --override compose.hwa.yaml`. The script does not change firewall rules or automatically change hardware access.

`restart` recreates so editor/atomic file replacements are remounted correctly. `down`, `status`, and `logs` work even if config is invalid or missing. To migrate an older installation, run `migrate`, then `up`; `config.json.legacy` and old secret files are retained, so remove those copies yourself only after verifying deployment. Transcode settings remain fixed per camera ID, and subscribers share identical output. Grace-period workers can be reclaimed for a switch when the worker limit is reached. No adjacent prewarming occurs.

The image pins Python 3.12.8 and all Python dependencies; apt supplies the distribution security-maintained FFmpeg. Record the image ID and `ffmpeg -version` with each deployment, then preserve the built image for exact rollback. For byte-identical rebuilds, pin a tested base-image digest and Debian snapshot in your deployment; the public Dockerfile intentionally does not freeze OS security updates. Docker/ARM image build has not been run in this Windows environment.

## Pi 5 acceleration and source selection

Pi 5 has a hardware **HEVC decoder**, rather than the H.264 encode/decode blocks from older Pis. Hardware H.264/H.265 encoding and JPEG encoding are not assumed. GPU presence is not proof of codec support. Raspberry Pi's [processor documentation](https://www.raspberrypi.com/documentation/computers/processors.html) describes the HEVC block; its [Pi 5 encoding report](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-010033-WP-1-H.264%20encoding%20performance%20on%20Raspberry%20Pi%205_series%20computers.pdf) describes software H.264 encoding.

On the Pi, install Python 3 and FFmpeg/ffprobe (or run the probe in the same image used for deployment). Probe every offered source profile:

```sh
python3 tools/probe.py --camera front --output front-low.json
# To compare another source profile, edit that camera's URL in the same config.
python3 tools/probe.py --camera front --output front-medium.json
```

The probe reads `gateway/config.json` by default (override with `--config PATH`) and uses that camera's output settings. Reports contain codec, profile, dimensions, pixel format, frame rates, FFmpeg inventory and a real 30-frame decode → resize → JPEG test. Failed stages include `source_diagnostic` / `pipeline_diagnostic` categories, return codes, and fixed safe explanations for unsupported options, TLS verification, authorization, network and decoder/filter failures. Raw FFmpeg stderr and source URLs are never copied into the report or console. Run as an account allowed to read the config. FFmpeg necessarily receives the source URL in its process arguments: restrict host process visibility and admin access accordingly.

The Pi 5 host test supplied for this project successfully decoded a 352×480 HEVC doorbell stream through `/dev/media1` and `/dev/video19`, then produced 30 JPEGs with the existing CPU filters. The host used Raspberry Pi FFmpeg 7.1.5 with `--enable-v4l2-request`. This build exposes acceleration through the regular `hevc` decoder with `-hwaccel drm`; it does **not** need a decoder named `hevc_v4l2request`. A missing standalone decoder name does not rule out hardware support.

Test that path on the host without changing the camera configuration:

```sh
python3 tools/probe.py --camera doorbell \
  --decoder hevc --hwaccel drm --no-hw-download --output doorbell-hwa.json
```

The DRM probe captures verbose FFmpeg logs internally and checks for the fixed `Hwaccel V4L2 HEVC stateless` driver marker. Reports expose only `hardware_decode_confirmed`, never raw logs (verbose SDP contains session crypto keys even when the URL is redacted). A successful DRM probe requires that marker and all 30 JPEG frames; `hardware_unconfirmed` means output succeeded without confirming this specific Pi driver. The tested build automatically transfers frames to CPU memory, so leave `hw_download` false. Other decoder builds may need different frame handling.

### Opt-in Pi 5 Docker image

The default Bookworm image supplies stock Debian FFmpeg and has no hardware device mappings. Use `compose.rpi-hwa.yaml` for a separate ARM64 Trixie image that installs Raspberry Pi's patched FFmpeg from its signed apt repository. The build checks for `--enable-v4l2-request`; repository updates can change the packaged version, so always run the container probe. It does not mount host executables or host libraries. Only `/dev/video19` and `/dev/media1` are mapped, with supplementary group `44` (`video` on the tested Pi). Verify those node numbers and the numeric group on other hosts. No privileged container or DRM display/GPU mappings are needed for the tested decode path.

First build the image; this does not recreate the running gateway. The deployment script supplies environment variables only during its own commands, so set them for these direct Compose commands:

```sh
export LAN_IP="$(python3 -c 'import json; print(json.load(open("gateway/config.json"))["lan_ip"])')"
export VIEWER_UID="$(id -u)" VIEWER_GID="$(id -g)"
docker compose -f compose.yaml -f compose.rpi-hwa.yaml build gateway
```

Probe in a disposable container with the production mounts, permissions and device mappings. It writes only to that container's temporary directory and leaves the running gateway alone:

```sh
docker compose -f compose.yaml -f compose.rpi-hwa.yaml run --rm --no-deps gateway \
  sh -c 'python /app/probe.py --config /config/config.json --camera doorbell --decoder hevc --hwaccel drm --no-hw-download --output /tmp/doorbell-hwa.json; result=$?; cat /tmp/doorbell-hwa.json; exit "$result"'
```

Require `pipeline_ok: true`, `hardware_decode_confirmed: true`, and `frames: 30`. Then add these fields to the doorbell entry in `gateway/config.json`:

```json
"decoder": "hevc",
"hwaccel": "drm",
"hw_download": false
```

Validate and recreate with the tested image (no rebuild):

```sh
python3 tools/deploy.py check
python3 tools/deploy.py restart --override compose.rpi-hwa.yaml
python3 tools/deploy.py status --override compose.rpi-hwa.yaml
```

Keep passing `--override compose.rpi-hwa.yaml` to subsequent deployment actions. To return to CPU decoding, set `decoder` to `software`, `hwaccel` to `none`, and `hw_download` to false, then use `python3 tools/deploy.py up` without the override to rebuild/recreate the default image. Container hardware decoding, sustained stability and CPU/power savings still need validation on the Pi; only the short host test has been supplied so far.

For an H.264 source, use the CPU baseline or an existing capable local transcode service. Compare a low H.264 profile on CPU against a larger HEVC profile using hardware decode: a larger source plus frame transfer can cost more overall. JPEG encoding and scaling here still use CPU. Choose the lowest-cost source that resolves useful detail at the panel resolution. Measure CPU/memory/temperature; those are proxies, not watts.

## API and network security

All endpoints except liveness require `Authorization: Bearer <token>`. The token persists in the mounted configuration across restarts; use 32–128 printable ASCII characters without whitespace (`init` generates 64 hex characters). It never belongs in query strings.

| Endpoint | Behavior |
|---|---|
| `GET /healthz` | unauthenticated service liveness only, not camera health |
| `GET /v1/cameras` | `{ "cameras": [{ "id": "front", "name": "Front" }] }` in configured order |
| `GET /v1/stream/{id}` | multipart JPEG, boundary `frame`, mandatory Content-Length per part |
| `GET /v1/diagnostics` | per-camera clients, running, frames, failures, latest frame age |

Unknown IDs return 404; missing/wrong tokens 401; worker/client capacity returns 503. Stream headers are sent immediately so firmware can cancel cold workers promptly. If no first frame arrives in 8 seconds, the connection closes; after startup a 4-second source stall closes it. The firmware distinguishes successful stream negotiation with no frames (source unavailable) from failure to reach the gateway. Failure after the first frame shows reconnecting and visibly ages the last frame. A lagging write gets at most 1 second before disconnect. The newest pending frame replaces older frames per client. One blocked viewer cannot block the worker or other viewers.

Compose binds port 8080 to `LAN_IP`, **using unencrypted HTTP**. Video and the bearer token are visible to anyone able to capture that LAN traffic. Allow only the viewer VLAN/trusted LAN through your host firewall; do not publish the port to the Internet. Docker-published ports may bypass uncomplicated host firewall rules, so enforce network/DOCKER-USER rules as appropriate to your setup.

For encrypted viewing use a LAN TLS proxy with `gateway/nginx.conf.example`, a certificate whose SAN matches `viewer.home.arpa`, and a trusted CA. Adjust `proxy_pass` to the actual bound LAN address if using the Compose bind above (127.0.0.1 is appropriate only when separately bound there). Set firmware `VIEWER_GATEWAY` to `https://viewer.home.arpa`, and `VIEWER_CA_PEM` to your private CA PEM string, or use a public chain in the IDF certificate bundle. Set `VIEWER_NTP_SERVER` to a reachable **local** NTP server for certificate date validation. For RTSPS, the gateway sets `tls_verify=1`; mount the Protect CA and set `source_ca_file` if needed. The installed FFmpeg must support these RTSP/TLS options; see [FFmpeg protocols](https://ffmpeg.org/ffmpeg-protocols.html). A build that rejects them fails closed: use a compatible build or an explicitly chosen local RTSP restream instead of disabling verification where avoidable.

A per-camera `"source_tls_verify": false` config field exists for a known-local Protect host whose RTSPS certificate cannot be verified (e.g. self-signed, with no CA to mount). Setting it disables certificate checking **for that camera's source connection only**; the stream itself stays unauthenticated RTSP(S) on your LAN either way. Prefer mounting the Protect CA via `source_ca_file` when possible, and only set this on cameras/hosts you control.

## Board checks and firmware

Guition's [model listing](https://www.guition.com/model-selection) identifies JC3248W535C_I_Y as ESP32-S3R8, 16 MB flash, 8 MB PSRAM, QSPI, 320×480. The [manufacturer download page](https://www.guition.com/-download) links its JC3248W535 specification, and its product listings link the `JC3248W535EN.zip` demo package. The manufacturer PDF fetch returned HTTP 403 here, and the exact user's PCB revision has not been inspected. **Pin mapping and panel initialization remain provisional**, rather than being represented as manufacturer-verified for your physical board.

The configured map is CS 45, clock 47, D0/D1/D2/D3 21/48/40/39, backlight 1; touch SDA/SCL 4/8, address 0x3B, polled with no interrupt. No LCD reset pin is assigned. Compare `firmware/main/board.h` with the schematic's IO distribution and the vendor demo's `esp_bsp.h` **before connecting this firmware**. Confirm octal PSRAM and flash identification using boot logs/esptool. The selected Espressif [AXS15231B 2.1.1 driver](https://components.espressif.com/components/espressif/esp_lcd_axs15231b/versions/2.1.1/readme) includes LCD/touch support and a default vendor initialization table; an actual module revision may require a different table. See `docs/architecture.md` for the driver's unusual QSPI sequential-strip behavior.

Install **ESP-IDF v6.1**, including ESP32-S3 toolchains (on Windows, use the [ESP-IDF Tools Installer](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/windows-setup.html) or `install.ps1 esp32s3` from the IDF checkout). Activate the IDF environment in the terminal you build from, then run the build.

**Linux/macOS** (IDF checkout at `~/esp/esp-idf`):

```sh
. ~/esp/esp-idf/export.sh
cp firmware/main/provision.example.h firmware/main/provision.h
# Edit provision.h: SSID/password, gateway URL and token from the Pi.
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p YOUR_USB_PORT flash monitor
```

**Windows (PowerShell)** (IDF checkout at `C:\Users\YOU\esp\v6.1\esp-idf`; use `COMx` for the port):

```powershell
# Allow the unsigned export script for this window only (not a system change):
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# The leading ". " is required so the environment applies to this session:
. C:\Users\YOU\esp\v6.1\esp-idf\export.ps1
Copy-Item firmware\main\provision.example.h firmware\main\provision.h
# Edit provision.h: SSID/password, gateway URL and token from the Pi.
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p COM3 flash monitor
```

On Windows, `export.bat` only works from `cmd.exe`: run from PowerShell it activates a child shell that exits, so `idf.py` is not found afterwards. The Tools Installer also adds an "ESP-IDF PowerShell" shortcut that does all of this for you. Find your port in Device Manager under Ports (COM & LPT). The build downloads exact component versions and uses the checked-in `dependencies.lock` with component hashes. Defaults select 16 MB flash, octal 80 MHz PSRAM and a 6 MB factory application partition. The rest of flash is deliberately unassigned. USB firmware updates are used for this release; OTA is not implemented.

First set `VIEWER_BRINGUP_ONLY=1`: boot should show the embedded native JPEG color bars, readable landscape labels and tap controls. Verify touch coordinates/gesture directions and brightness. This mode does not start Wi-Fi. After validating the display, set it to 0, provision one camera on the Pi, confirm live video, then add cameras in the desired order.

Swipe at least 55 logical pixels, predominantly horizontally, within 800 ms. Small movements and taps do not switch. Single-camera lists are valid. Camera selection changes immediately in shared state; render checks generations before decode, after decode and between LCD strips. An already submitted DMA strip cannot be recalled; subsequent old-camera strips stop. During switches the old stream is closed by the network owner after its current finite read (100 ms once streaming). Connect/header operations have a 500 ms timeout, with DNS/TLS timing still requiring hardware measurement. No concurrent task mutates or frees its HTTP client.

Tap to toggle controls. Bottom thirds dim, brighten, and select the next camera; swipes work throughout. Controls show RSSI and heap. The top overlay names the selected camera and connection state. Stale images show a red `STALE N S` bar after one second without a newly decoded frame. This age measures viewer freshness, **not capture age**. Always-on is default; configurable dim/screen-off thresholds use seconds since touch. Touch wakes the screen. Screen-off disconnects video, stops decoding, and lets the worker idle. Settings are compile-time; brightness and selected camera are not persisted.

Provisioning secrets are excluded by `.gitignore` but present in the resulting firmware image. Protect local binaries and build artifacts. Production flash encryption/secure boot are separate provisioning work, not enabled here. Library HTTP/TLS errors may include the gateway hostname, never the source camera URLs; do not put credentials in the gateway URL.

USB recovery: use a data cable; hold BOOT, press/release RESET, release BOOT, then flash the native USB serial/JTAG port. If the board has no RESET button, hold BOOT while reconnecting power. Use `idf.py -p PORT flash`; use `erase-flash` only when deliberately resetting NVS. Keep a known-good `build` export including bootloader, partition table, application and `flash_args`. Restore all files with their original offsets using esptool's `write_flash @flash_args`, from that export directory. Back up vendor firmware before first replacement if you need to recover its original demo. Do not erase the device just to update the application.

## Tests, troubleshooting, updates

### Docker memory limits on the Pi

If Docker reports `Your kernel does not support memory limit capabilities`, the configured 768 MiB limit is discarded. A healthy gateway can still serve requests, but its container memory ceiling is not enforced. The deployment script reports this separately from service health. Status, builds and deployment use plain Compose progress to avoid repeated terminal redraws over SSH.

Inspect the current host before choosing a boot/kernel fix:

```sh
cat /proc/cmdline
cat /proc/cgroups
cat /sys/fs/cgroup/cgroup.controllers
sudo docker info --format '{{json .}}'
```

On cgroup v2, `memory` should be present in `cgroup.controllers`. Raspberry Pi kernels/images may disable it at boot; the correct fix depends on active boot configuration and kernel support. The Raspberry Pi kernel maintainers discuss this in [issue 6980](https://github.com/raspberrypi/linux/issues/6980). Inspect the active command line for `cgroup_disable=memory` as well as enable flags. Preserve your boot files before any edit, keep the command line on a single line, and plan a reboot if boot parameters must change. The deployment script reports capability status; it does not edit boot files or reboot the Pi automatically. After correction/reboot, verify Docker reports `MemoryLimit: true` and run `python3 tools/deploy.py restart` to recreate the container with its memory limit enforced.

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

For updates, preserve a tagged built image (`docker image tag IMAGE_ID viewer-gateway:known-good`), configuration and local firmware export before rebuilding. After updating your checkout, run tests/probes and `python3 tools/deploy.py update`. Roll back using a Compose override with `image: viewer-gateway:known-good` and `pull_policy: never`, then `python3 tools/deploy.py restart --override rollback.yaml`; restore matching config if its schema changed. Firmware rollback uses the saved USB flash export. See `docs/benchmark.md` for acceptance and soak procedures.

## Runtime settings (device and web)

Values in `provision.h` are only defaults; anything saved at runtime is stored in NVS and wins.

- **Device:** tap the gear in the top-right of the video view. Settings shows Wi-Fi status, the device IP and the web URL. *Scan Wi-Fi* lists networks; tap one and type the password on the on-screen keyboard (lowercase letters show cyan, uppercase white). Open networks join immediately.
- **Web:** browse to `http://<device-ip>/` to set the encoder IP, port and key (the key is write-only; it is never sent back to the page). Saving reloads the camera list. The same page streams the device's log output live, like an ESPHome device page.
- The web page has **no authentication**; use it only on a trusted LAN.
