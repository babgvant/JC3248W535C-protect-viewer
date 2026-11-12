# Acceptance procedure and report

Everything below is a target or test procedure until the report table is filled with observed results. Local checks completed on Windows on 2026-11-12: ESP-IDF 6.1 compile/link; host C parser/selection/JPEG-profile tests; 12 Python gateway tests (including real child reaping, slow/fast viewers, simultaneous admission, idle shutdown, source stall, cancellation before task entry and rapid subscription changes); real FFmpeg synthetic output tests across fit/fill × four rotations. Synthetic test frames were about 8–13 KB; this does not predict camera bandwidth. Docker ARM build, physical flash, live Protect ingest and hardware acceleration remain untested.

## First hardware session

1. Compare PCB markings, manufacturer schematic pin distribution and vendor initialization against `board.h` / the pinned driver. Save the source and revision used as evidence. Confirm 16 MB flash and 8 MB octal PSRAM in boot logs. Boot `VIEWER_BRINGUP_ONLY=1` and inspect color order, orientation, all edges, touch alignment and brightness. Tap and swipe every edge; ensure taps/jitter do not switch.
2. Run Pi inventory and separate redacted probes for each Protect profile. Record codec/profile/dimensions/FPS and actual FFmpeg capabilities. Compare CPU versus a supported HEVC hardware candidate using the same output parameters and scene. Verify device access inside Docker. Never use a listed decoder as proof of acceleration.
3. Disable bring-up mode. Start one camera, then test multiple cameras including the portrait doorbell. Check fit and fill, single-camera behavior, fast repeated swipes, immediate name changes, and no late previous-camera image. Measure worst-case gesture-to-name and DMA cancellation latency with filmed screen/touch evidence.
4. Verify trusted TLS, wrong token rejection, invalid certificates/hostnames, local clock unavailable, missing secrets, invalid IDs, empty catalog and unsupported JPEG errors. HTTP continues without NTP; HTTPS waits for the configured local time source.

## Quality and pacing comparison

Use a consistent 60-second scene with movement, then a dark/noisy scene. Test these starting profiles:

| Profile | Output | FPS | JPEG quantizer | Status |
|---|---|---:|---:|---|
| Quiet | logical 480×320, fit | 5 | 7 | candidate, unmeasured |
| Balanced | logical 480×320, fit | 10 | 5 | initial default, unmeasured |
| Motion | logical 480×320, fit | 15 | 4 | candidate, unmeasured |

Output storage is native 320×480; fit can letterbox a smaller non-upscaled source. Repeat with doorbell fill. Prefer a profile with consistent pacing/headroom over one with a higher average FPS and stalls. Lower source resolution first if Pi decode dominates. Hardware acceleration is useful only if measured total workload/latency improves.

## Automated soak and fault injection

Install gateway requirements in a local venv; commands run from project root. The single gateway config must be readable by the invoking account. For HTTPS, set `--gateway https://YOUR_HOST` and configure the client's OS/Python CA trust; do not use a certificate bypass.

```sh
python tools/soak.py --config gateway/config.json --seconds 86400 --dwell 30 > soak.jsonl
python tools/soak.py --config gateway/config.json --seconds 300 --rapid > rapid.jsonl
# Run concurrently with a normal reader:
python tools/soak.py --config gateway/config.json --seconds 300 --slow > slow.jsonl
```

The rapid tool switches after short dwell intervals between complete frames; it does not replace a real rapid-touch test or cancellation during a blocked connect. Slow-client testing should also include a client that stops reading altogether (covered by the local automated gateway test).

During the 24-hour test, keep the real ESP32 viewing and switch it at least every 30 seconds for one hour; leave it steady overnight; repeat rapid swipes periodically. Log serial counters every 10 seconds. At regular intervals capture authenticated `/v1/diagnostics`, `docker stats --no-stream`, Pi temperature/throttling and process/socket counts. Use `docker compose top gateway`, `ss -tnp` and `/proc/<pid>/fd` counts as needed; avoid commands that dump FFmpeg argument URLs into shared logs. Run a second viewer to confirm sharing. With all viewers closed, wait idle grace + 3 seconds and confirm zero FFmpeg children. Repeat this 100 times. With a 5-second idle grace and a healthy disconnect, children should be reaped within ~7.25 seconds including worst-case terminate escalation; source/kernel conditions require actual verification.

Fault injection, within your authorized maintenance window:

* Disconnect viewer Wi-Fi for 10 and 60 seconds, then restore. Confirm Wi-Fi label, aged/cleared video, bounded reconnects, recovered touch and no task growth.
* `docker compose restart gateway` during playback, rapid switches and source startup. Confirm automatic recovery and no orphan FFmpeg processes.
* Point one secret URL at an unreachable local source, restart gateway, select it, then restore it. Confirm source-unavailable versus gateway-unavailable labels.
* Stop or kill only a gateway-owned FFmpeg child. Confirm it is reaped and restart rate stays bounded. Do not target unrelated Frigate/Protect processes.
* Test low bandwidth, packet loss and stalled readers in an isolated test network. Verify slow viewers disconnect while a normal viewer continues; detect latency accumulation, rather than judging only FPS.
* Enable screen-off after 15 seconds. Confirm Wi-Fi can remain connected while receive/decode stops, worker idles, and touch wakes with a fresh connection. Set both thresholds to 0 afterward for always-on.
* Repeat the host parser suite and add captured malformed fixtures where useful. It covers fragmented boundaries/headers/payloads, malformed lengths, oversize headers/frames, JPEG validation, and generation rejection. Truncated streams must hit the progress timeout and reconnect on hardware too.

## Measurement method

Film the camera's view of a running millisecond clock and the physical viewer in the same shot, ideally with a high-frame-rate reference camera. Subtract the reference times visible at source and display across multiple samples to estimate glass-to-glass latency. Report reference-camera frame interval as measurement uncertainty. FFmpeg start time, HTTP first-byte time and JPEG decode timing are not substitutes for this measurement.

Firmware serial `timing` logs report sampled read wait, parser, decode, receipt-to-decode age, frame bytes, display transfer and selection-to-first-complete-display duration. Read wait includes network/frame pacing; it is not a capture timestamp. `stats` reports cumulative received/displayed/dropped/reconnects, RSSI, frame/byte deltas and internal/PSRAM free memory; derive delivered FPS and bandwidth from deltas. `memory` reports minimum/largest free blocks and task stack high-water marks (IDF reports bytes). The first stats interval includes startup and should be excluded from steady-state averages. RSSI is also in the diagnostics control overlay. Verify switching with filmed evidence and report warm grace-period worker and cold new worker separately. Python soak `first_frame_s` is network startup on that client, not gesture-to-glass time.

Power requires an inline meter at the Pi and viewer supply, measured with both active/idle and identical backgrounds. Report supply scope, meter model and uncertainty. CPU, temperature, clock frequency and Wi-Fi airtime are proxies, never watts. Include the incremental cost of the gateway versus its preexisting workload.

## Report template

| Item | Target / method | Observed |
|---|---|---|
| Firmware source/build ID, IDF/dependency hashes | preserve build export | pending |
| Board PCB revision and pin/init evidence | manufacturer comparison + physical bring-up | pending |
| Pi OS/kernel/FFmpeg/image ID, cooling and baseline workload | inventory | pending |
| Source codec/profile/resolution/FPS | ffprobe each offered profile | pending |
| Hardware decode path and proof | successful in-container pipeline + comparative measurements | pending |
| Output FPS/quantizer/mode, avg/p95/max JPEG size | logs + stream measurements | pending |
| Receive/decode/display duration p50/p95/max | separate timing samples | pending |
| Touch feedback p95/max | target <100 ms, filmed | pending |
| Glass-to-glass p50/p95/max | target <750 ms healthy LAN, filmed clock | pending |
| Switch first fresh display, warm/cold separately | target <2 s, filmed | pending |
| Delivered FPS/frame pacing/bandwidth/RSSI | deltas + controls | pending |
| Drops/reconnects/fault recovery | counters + injected-fault log | pending |
| Internal and PSRAM min/free/largest block; task stack margins | 24 h trace/instrumentation | pending |
| Gateway/FFmpeg CPU/RSS/temp/throttle/socket/FD counts | initial/peak/24 h/after idle | pending |
| Idle shutdown / child processes after 100 cycles | zero workers without subscribers after grace | pending |
| Source/TLS/auth/error tests | all observed states and recovery | pending |
| Viewer/Pi/combined electrical power | meter only, idle vs viewing | not measured |
| Chosen profile and whole-system justification | consistent pacing/latency/resource margin | pending |

Release acceptance requires stable memory/task/process counts and no unmarked stale frame across the full run. A passing local test suite is not that acceptance result.
