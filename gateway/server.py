import asyncio
import contextlib
import hmac
import json
import logging
import os
import random
import re
import time
from pathlib import Path

from aiohttp import web
from media import JPEGFrames, command

LOG = logging.getLogger('gateway')
CFG = web.AppKey('config', dict)
WORKERS = web.AppKey('workers', dict)
ADMISSION = web.AppKey('admission', asyncio.Lock)


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    cfg['token'] = Path(cfg['token_file']).read_text().strip()
    if not 32 <= len(cfg['token']) <= 128 or not cfg['token'].isascii() or not cfg.get('cameras') or len(cfg['cameras']) > 16:
        raise ValueError('token must have >=32 characters; configure 1..16 cameras')
    ids = set()
    for c in cfg['cameras']:
        c.setdefault('fps', 10); c.setdefault('quality', 5)
        c.setdefault('mode', 'fit'); c.setdefault('rotation', 0); c.setdefault('decoder', 'software')
        c.setdefault('hw_download', False)
        if not isinstance(c['hw_download'], bool): raise ValueError('hw_download must be boolean')
        if not re.fullmatch(r'[a-z0-9_-]{1,32}', c['id']) or c['id'] in ids:
            raise ValueError('invalid or duplicate camera ID')
        ids.add(c['id'])
        if type(c['fps']) is not int or type(c['quality']) is not int or not 5 <= c['fps'] <= 15 or not 2 <= c['quality'] <= 15 or c['mode'] not in ('fit', 'fill') or c['rotation'] not in (0,90,180,270):
            raise ValueError('invalid output profile')
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,64}', c['decoder']):
            raise ValueError('invalid decoder')
        if not c['name'].isascii() or not 1 <= len(c['name']) <= 31:
            raise ValueError('use 1..31 ASCII characters for display names')
        c['url'] = Path(c['url_file']).read_text().strip()
        if not c['url'].startswith(('rtsp://','rtsps://')) or '\n' in c['url']:
            raise ValueError('expected RTSP(S) URL')
    cfg.setdefault('idle_seconds', 5); cfg.setdefault('max_workers', 2); cfg.setdefault('max_clients', 8)
    if not 0 <= cfg['idle_seconds'] <= 60 or not 1 <= cfg['max_workers'] <= 8 or not 1 <= cfg['max_clients'] <= 32:
        raise ValueError('invalid resource limits')
    return cfg


class Worker:
    def __init__(self, camera, idle, spawn=asyncio.create_subprocess_exec):
        self.camera, self.idle, self.spawn = camera, idle, spawn
        self.clients = set()
        self.task = None
        self.proc = None
        self.stop_timer = None
        self.latest = None
        self.sequence = 0
        self.failures = 0
        self.last_frame = 0

    def subscribe(self):
        if self.stop_timer:
            self.stop_timer.cancel(); self.stop_timer = None
        q = asyncio.Queue(maxsize=1)
        self.clients.add(q)
        if self.task is None: self.start()
        # Do not send cached frames to new subscribers.
        return q

    def start(self):
        self.task = asyncio.create_task(self.run())
        self.task.add_done_callback(self.finished)

    def finished(self, task):
        if self.task is not task: return
        self.task = None
        if not task.cancelled() and task.exception() is not None:
            LOG.error('camera=%s worker failed (%s)', self.camera['id'], type(task.exception()).__name__)
            return  # HTTP source timeout/reconnect can retry without a hot restart loop.
        # Covers cancellation before coroutine entry as well as late subscribers.
        if self.clients: self.start()

    def unsubscribe(self, q):
        self.clients.discard(q)
        if not self.clients and self.stop_timer is None:
            self.stop_timer = asyncio.get_running_loop().call_later(
                self.idle, self.request_stop)

    def request_stop(self):
        self.stop_timer = None
        if self.task and not self.task.cancelling():
            self.task.cancel()

    async def terminate(self):
        if self.proc:
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError): self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 2)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError): self.proc.kill()
            await self.proc.wait()
            self.proc = None

    async def run(self):
        delay = 1
        try:
            while True:
                try:
                    self.proc = await self.spawn(*command(self.camera, self.camera['url']),
                                                 stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL,
                                                 limit=65536)
                    parser = JPEGFrames()
                    while True:
                        data = await asyncio.wait_for(self.proc.stdout.read(16384), 8)
                        if not data: raise RuntimeError('source ended')
                        for frame in parser.feed(data):
                            self.sequence += 1
                            self.last_frame = time.monotonic()
                            self.latest = frame
                            delay = 1
                            for q in tuple(self.clients):
                                if q.full(): q.get_nowait()
                                q.put_nowait((self.sequence, self.last_frame, frame))
                except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
                    self.failures += 1
                    LOG.warning('camera=%s source unavailable; retry in %.1fs', self.camera['id'], delay)
                finally:
                    await self.terminate()
                await asyncio.sleep(delay + random.uniform(0, delay / 4))
                delay = min(30, delay * 2)
        finally:
            await self.terminate()
            self.latest = None

    async def close(self):
        if self.stop_timer: self.stop_timer.cancel(); self.stop_timer = None
        self.clients.clear()
        if self.task:
            if not self.task.cancelling(): self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.task


@web.middleware
async def auth(request, handler):
    if request.path != '/healthz' and not hmac.compare_digest(
            request.headers.get('Authorization', '').encode(), ('Bearer ' + request.app[CFG]['token']).encode()):
        raise web.HTTPUnauthorized()
    return await handler(request)


async def next_frame(q, request, timeout):
    end = asyncio.get_running_loop().time() + timeout
    while True:
        if request.transport is None or request.transport.is_closing(): raise ConnectionError()
        remaining = end - asyncio.get_running_loop().time()
        if remaining <= 0: raise asyncio.TimeoutError()
        try: return await asyncio.wait_for(q.get(), min(.25, remaining))
        except asyncio.TimeoutError: continue


async def bounded_write(response, request, data):
    task = asyncio.create_task(response.write(data))
    try:
        done, _ = await asyncio.wait({task}, timeout=1)
        if not done:
            if request.transport: request.transport.abort()
            with contextlib.suppress(ConnectionError): await task
            raise asyncio.TimeoutError()
        await task
    finally:
        if not task.done():
            if request.transport: request.transport.abort()
            with contextlib.suppress(ConnectionError): await task


async def stream(request):
    app = request.app
    worker = app[WORKERS].get(request.match_info['id'])
    if worker is None: raise web.HTTPNotFound()
    async with app[ADMISSION]:
        if sum(len(w.clients) for w in app[WORKERS].values()) >= app[CFG]['max_clients']:
            raise web.HTTPServiceUnavailable(text='viewer limit')
        if worker.task is None and sum(w.task is not None for w in app[WORKERS].values()) >= app[CFG]['max_workers']:
            # Reclaim unused grace-period workers before admitting a switch.
            for w in app[WORKERS].values():
                if w.task and not w.clients: await w.close()
            if sum(w.task is not None for w in app[WORKERS].values()) >= app[CFG]['max_workers']:
                raise web.HTTPServiceUnavailable(text='worker limit')
        q = worker.subscribe()
    response = web.StreamResponse(headers={'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                                          'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
    try:
        await response.prepare(request)
        request.transport.set_write_buffer_limits(high=32768, low=8192)
        item = await next_frame(q, request, app[CFG].get('source_timeout', 8))
        while True:
            seq, stamp, frame = item
            if time.monotonic() - stamp > 1: raise asyncio.TimeoutError()
            header = (f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n'
                      f'X-Sequence: {seq}\r\n\r\n').encode()
            await bounded_write(response, request, header + frame + b'\r\n')
            item = await next_frame(q, request, 4)
    except (ConnectionError, asyncio.TimeoutError):
        if not response.prepared: raise web.HTTPBadGateway(text='source unavailable')
        if request.transport: request.transport.close()
    finally:
        worker.unsubscribe(q)
    return response


def create_app(cfg, spawn=asyncio.create_subprocess_exec):
    app = web.Application(middlewares=[auth], client_max_size=1024)
    app[CFG] = cfg
    app[WORKERS] = {c['id']: Worker(c, cfg['idle_seconds'], spawn) for c in cfg['cameras']}
    app[ADMISSION] = asyncio.Lock()
    async def health(_): return web.json_response({'status': 'ok'})
    async def cameras(_):
        return web.json_response({'cameras': [{'id': c['id'], 'name': c['name']} for c in cfg['cameras']]})
    async def metrics(_):
        return web.json_response({k: {'clients': len(w.clients), 'running': w.task is not None,
                                     'frames': w.sequence, 'failures': w.failures,
                                     'frame_age_s': time.monotonic()-w.last_frame if w.last_frame else None}
                                  for k,w in app[WORKERS].items()})
    async def shutdown(_): await asyncio.gather(*(w.close() for w in app[WORKERS].values()))
    app.router.add_get('/healthz', health)
    app.router.add_get('/v1/cameras', cameras)
    app.router.add_get('/v1/diagnostics', metrics)
    app.router.add_get('/v1/stream/{id}', stream)
    app.on_shutdown.append(shutdown)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    # Disable access logging: never log credentials or source URLs.
    web.run_app(create_app(load_config(os.getenv('VIEWER_CONFIG', '/config/config.json'))),
                host='0.0.0.0', port=8080, access_log=None, shutdown_timeout=5)
