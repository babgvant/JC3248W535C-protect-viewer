# Decisions and bounded resources

The chosen first release transports baseline JPEG frames over HTTP multipart. The Pi ingests an explicitly probed local RTSP(S) profile, decodes it, limits to 5–15 FPS (default 10), rotates and fits/fills a 480×320 logical canvas, rotates that canvas counterclockwise into native 320×480 storage, and encodes baseline 8-bit YUV420 JPEG. Firmware decodes straight to RGB565 with byte swapping for the LCD bus. Transport and codec are distinct: RTSP can carry H.264, HEVC or other media; an RTSP URL is not a JPEG source.

| Alternative | Practical tradeoff |
|---|---|
| Direct camera H.264 on S3 | S3 has no hardware H.264 decode. Software decode, references and transport add substantial memory/CPU and compatibility risk. Not implemented. |
| Gateway low-resolution H.264 | Saves Wi-Fi bandwidth, still requires H.264 decoding on S3 and increases firmware complexity. A hardware-capable receiver would change this decision. |
| Multipart MJPEG | Independent frames, straightforward bounded decoder, good drop/switch behavior; greater bandwidth and continuous Pi JPEG work. Default. |
| Periodic JPEG snapshots | Lowest activity at sparse intervals; simpler error recovery but discontinuous motion and potentially costly per-request camera snapshots. Useful future low-activity mode, not this live release. |

No 30 FPS claim is made. No codec benchmark is treated as full-viewer performance. At 10 FPS and 15–40 KiB/frame, indicative bandwidth is 1.2–3.3 Mbit/s per viewer, excluding overhead; actual camera scene/quality may differ substantially. These are arithmetic examples, not measurements. Compare source codec/profile and whole-system workload before choosing acceleration. HEVC hardware decode can help on Pi 5 if its installed kernel and FFmpeg support it; JPEG encode here remains CPU work. A smaller H.264 input decoded on CPU may still cost less than a larger HEVC input with device-to-CPU transfer.

## Board and decoder selection

Use ESP-IDF 5.5.2 and exact Espressif AXS15231B 2.1.1 and esp_jpeg 1.3.2 components (transitives locked with content hashes). esp_jpeg/TJpgDec was chosen for its small explicit scratch buffer, RGB565 support and compatible baseline profile, rather than introducing LVGL or a second software decode pipeline. esp_new_jpeg remains a benchmark candidate if hardware measurements show decode is the bottleneck. S3 has no hardware JPEG engine like newer P4-class chips. The included esp_jpeg measurements upstream concern different resolutions/clocks/memory placement and are not reproduced as project results.

The manufacturer product family is identified, but the physical PCB revision/pin map is not conclusively verified. Follow README bring-up before deployment. The stock driver's QSPI `draw_bitmap` deliberately omits RASET and uses RAMWR for the first strip, RAMWRC thereafter. Therefore render sends **full native frames in sequential full-width strips**, always starting at y=0. Arbitrary dirty rectangles or parallel UI transfers would corrupt this protocol. It does not use a hardware XY swap for landscape. Gateway rotation avoids frame rotation on S3.

## Firmware ownership

* Network task (12 KiB stack) exclusively owns its HTTP handle, parser buffer and camera-generation tag. Connection/header operations time out at 500 ms; streaming reads at 100 ms. Only that task closes/frees its client, avoiding cross-task cancellation races. Read timeouts do not reset a partially received JPEG. Frame-progress timeout is 8 seconds cold, 3 seconds once frames have flowed. Malformed/incomplete input eventually reconnects. Camera changes interrupt backoff in 25 ms steps.
* Two frame slots are explicitly passed through a free queue (capacity 2) and ready queue (capacity 1). Network owns a slot while filling it; render owns it while decoding; slots return to the free queue. If ready already contains a frame, it is recycled for the newer frame. The parser's separate buffer prevents decode/input aliasing.
* Render task (6 KiB stack) owns both RGB565 buffers, scratch memory and every LCD command after initialization. It validates generation before and after decode, and before every display strip. It rejects frames older than one second in the local receive queue. An already queued 16-row DMA strip completes before cancellation takes effect; firmware cannot recall pixels already sent. The next render clears the previous image and labels the selected camera. Physical feedback timing must be measured.
* Touch task (4 KiB stack) polls at 15 ms, updates selection/control state under a short mutex and never waits on network or decode. It controls PWM, while render exclusively controls LCD IO. Wi-Fi events update state and schedule jittered reconnects; no HTTP operations run on the touch/event tasks.

Core affinity, CPU clock and Wi-Fi power-save defaults are left unchanged. DMA is asynchronous but the conservative baseline waits for each strip completion before reusing its single staging buffer. It overlaps touch/network activity with transfers; it does not claim decode/transfer overlap within one frame. Adding a second DMA buffer should be measurement-driven.

## Memory budget (allocations, not measured headroom)

| Allocation | Bytes | Placement / lifetime |
|---|---:|---|
| Multipart compressed frame | 196,608 | PSRAM, network lifetime |
| Two compressed ownership slots | 393,216 | PSRAM, application lifetime |
| Raw decoded video | 307,200 | PSRAM, render lifetime |
| Composited display canvas | 307,200 | PSRAM, render lifetime |
| JPEG decoder scratch | 8,192 | internal, render lifetime |
| DMA strip (320×16×2) | 10,240 | 64-byte aligned internal DMA RAM |
| Application task stacks | 22,528 | IDF task allocations, normally internal |
| HTTP receive chunk | 4,096 | within the 12 KiB network stack above |
| HTTP library receive buffer | 4,096 | library-managed; per connection |
| Multipart state | about 400 | network stack |
| Catalog JSON/text | bounded 4 KiB plus cJSON tree | startup only |

Total explicit PSRAM video storage is 1,204,224 bytes (~1.15 MiB). Internal fixed scratch/DMA/task stacks total 40,960 bytes before queue objects, TCBs, application globals, decoder stack frames and networking. Wi-Fi, lwIP, TLS, RTOS, IDF drivers, certificate bundle, HTTP headers, SPI descriptors and other system allocations are **additional**. No claimed minimum free heap is possible until measured. Query minimum/largest block under real TLS load, not just total free heap. Exhausted required allocations abort safely rather than writing out of bounds.

Compressed buffers, RGB565 buffers and decoder scratch are preallocated. HTTP reconnection and initial catalog parsing still allocate library memory. A raw video-to-canvas copy preserves unmodified video when controls disappear or a stale-age overlay changes; at 10 FPS that copy is 3.07 MB/s of payload. It is an intentional baseline cost to benchmark, not a zero-copy claim. No LVGL framebuffer/widget copy is added. Unchanged overlays and PWM levels do not repeatedly redraw/reprogram. Screen-off skips decode/display and stops the source connection.

PSRAM is CPU-accessed and never supplied to SPI DMA. `board_present` copies into an internal `MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL` buffer aligned to 64 bytes. The CPU only overwrites it after the panel-IO completion semaphore. This avoids relying on arbitrary PSRAM DMA capability or cache-maintenance assumptions. The actual board needs transfer/lifetime/color verification. Transfer failure or a 500 ms completion timeout restarts instead of silently reusing an active buffer.

## Parser and failure contracts

The multipart parser consumes arbitrary byte fragments, bounds header lines to 255 characters, requires a valid length <=192 KiB and JPEG content type, and checks SOI/EOI markers. Invalid/oversized headers discard until a boundary; invalid frames are rejected. Truncated payloads recover through rejection and subsequent boundary scanning or a finite progress-timeout reconnect. The parser does not allocate. The decoder contract additionally requires SOF0, 8-bit YUV420 and exactly 320×480; progressive, grayscale, alternate dimensions/subsampling are rejected cleanly. Gateway-produced JPEG includes tables.

The last-frame age means elapsed time since decode, not sensor capture time. The red stale marker activates after one second. Wi-Fi loss invalidates the camera generation; gateway failure, source failure and reconnecting states are separate. HTTP authentication/configuration errors are shown as gateway errors and retried with backoff. No old generation can repopulate the selected camera's video buffer.

## Gateway lifecycle

The service uses one asyncio event loop. An admission lock serializes capacity checks/reclamation/subscription, including simultaneous new requests. Subscribe creates a capacity-one queue and starts one worker if needed. One worker corresponds to one configured camera/output profile. Closing the last subscriber schedules an idle stop; a new subscriber during the grace period cancels it. Switching does not prewarm adjacent cameras. A stopped idle worker reaps FFmpeg before admitting replacement capacity. Restart backoff sleeps are cancellable.

FFmpeg is invoked with argument arrays, no shell. Pipes are bounded, stdin disabled and stderr discarded to prevent token leakage. Each read is at most 16 KiB; JPEG frame storage is bounded to 192 KiB. Per-viewer pending queues contain only the newest immutable frame reference. Socket/application buffers are bounded, writes time out at 1 second, and frames older than 1 second at send time close the connection. OS socket buffers still exist and can briefly hold previously sent frames; the firmware reconnects on receive progress failures, but TCP buffering age is not a capture timestamp. End-to-end tests must check hidden latency accumulation.

Source stalls have finite timeouts and FFmpeg restarts with exponential backoff/jitter. Closing an HTTP client is detected during writes or a 250 ms transport check while awaiting a source frame. Shutdown stops idle timers, cancels workers, terminates each child, waits up to 2 seconds, then kills/reaps it if necessary. No later stop request re-cancels a task already cleaning up. Compose `init: true` provides a process reaper even if the service itself is killed unexpectedly. Restart policy, PID cap, memory/CPU limits and log rotation apply to the deployment. Limits are initial Pi 5 settings and require comparison with existing workload.

No source configuration, hardware capability, measured power, glass-to-glass latency or 24-hour stability is inferred from a successful local compile/test.
