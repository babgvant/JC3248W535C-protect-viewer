import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gateway'))
from media import JPEGFrames, MAX_FRAME, command, rtsp_input_options
from server import Worker, create_app, WORKERS
from aiohttp.test_utils import TestClient, TestServer

JPEG = b'\xff\xd8test\xff\xd9'
CAM = dict(id='test', name='Test', url='rtsp://example.invalid/live', fps=10,
           quality=5, mode='fit', rotation=0, decoder='software')


class FramingTests(unittest.TestCase):
    def test_all_fragment_sizes(self):
        for size in range(1, 30):
            p = JPEGFrames(); result = []
            data = b'noise' + JPEG * 3
            for offset in range(0, len(data), size): result += p.feed(data[offset:offset+size])
            self.assertEqual(result, [JPEG] * 3)

    def test_bounded_and_recovery(self):
        p = JPEGFrames()
        self.assertEqual(p.feed(b'x' * 100000), [])
        self.assertLessEqual(len(p.buf), 1)
        self.assertEqual(p.feed(b'\xff\xd8truncated' + JPEG), [JPEG])
        with self.assertRaises(ValueError): JPEGFrames().feed(b'\xff\xd8' + b'x' * MAX_FRAME)

    def test_args_no_shell_and_explicit_profile(self):
        args = command(CAM, 'rtsp://user:$(danger)@host/stream')
        self.assertIn('rtsp://user:$(danger)@host/stream', args)
        self.assertIn('mjpeg', args); self.assertIn('yuvj420p', args)
        self.assertIn('transpose=2', args[args.index('-vf')+1])

    def test_portrait_orientation_skips_canvas_rotation(self):
        vf = command(dict(CAM, orientation='portrait', mode='fill'), 'rtsp://x/y')
        vf = vf[vf.index('-vf')+1]
        self.assertIn('crop=320:480', vf); self.assertNotIn('transpose=2', vf)
        vf = command(dict(CAM, orientation='portrait'), 'rtsp://x/y')
        self.assertIn('pad=320:480', vf[vf.index('-vf')+1])

    def test_rtsp_socket_timeout_is_a_demuxer_option_before_input(self):
        for url in ('rtsp://camera.invalid/live','rtsps://camera.invalid/live'):
            args=command(CAM,url)
            self.assertNotIn('-rw_timeout',args)
            self.assertLess(args.index('-timeout'),args.index('-i'))
            self.assertEqual(args[args.index('-timeout')+1],'5000000')
            if url.startswith('rtsps:'):
                self.assertEqual(args[args.index('-tls_verify')+1],'1')
        self.assertEqual(rtsp_input_options(CAM,'rtsp://camera.invalid/live'),
                         ['-rtsp_transport','tcp','-timeout','5000000'])


class FakeProcess:
    def __init__(self, frames=True, frame=JPEG):
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.dead = asyncio.Event()
        self.frame = frame
        self.producer = asyncio.create_task(self.produce(frames))

    async def produce(self, frames):
        try:
            while True:
                if frames: self.stdout.feed_data(self.frame)
                await asyncio.sleep(.005)
        except asyncio.CancelledError: pass

    def terminate(self):
        self.returncode = 0; self.producer.cancel(); self.stdout.feed_eof(); self.dead.set()

    kill = terminate

    async def wait(self):
        await self.dead.wait()
        with contextlib.suppress(asyncio.CancelledError): await self.producer
        return self.returncode


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processes = []
        async def spawn(*args, **kwargs):
            p = FakeProcess(); self.processes.append(p); return p
        self.spawn = spawn

    async def test_shared_latest_bounded_and_idle_shutdown(self):
        w = Worker(CAM, .02, self.spawn)
        a = w.subscribe(); b = w.subscribe()
        await asyncio.wait_for(a.get(), 1); await asyncio.wait_for(b.get(), 1)
        await asyncio.sleep(.06)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(a.qsize(), 1); self.assertEqual(b.qsize(), 1)
        w.unsubscribe(a); w.unsubscribe(b)
        await asyncio.sleep(.08)
        self.assertIsNone(w.task)
        self.assertTrue(all(p.returncode is not None for p in self.processes))
        await w.close()

    async def test_rapid_resubscribe_and_clean_shutdown(self):
        w = Worker(CAM, .01, self.spawn)
        for _ in range(30):
            q = w.subscribe(); await asyncio.wait_for(q.get(), 1); w.unsubscribe(q)
        await w.close()
        self.assertIsNone(w.task); self.assertIsNone(w.proc)
        self.assertTrue(all(p.returncode is not None for p in self.processes))

    async def test_crash_reaped_and_backoff_cancellable(self):
        async def dead(*args, **kwargs):
            p = FakeProcess(False); p.terminate(); self.processes.append(p); return p
        w = Worker(CAM, .01, dead); q = w.subscribe()
        await asyncio.sleep(.03)
        self.assertEqual(w.failures, 1); self.assertEqual(len(self.processes), 1)
        w.unsubscribe(q); await asyncio.sleep(.05)
        self.assertIsNone(w.task)

    async def test_http_auth_catalog_stream_and_disconnect(self):
        cfg = dict(token='x'*64, cameras=[CAM], idle_seconds=.01, max_workers=1, max_clients=2)
        app = create_app(cfg, self.spawn)
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get('/healthz')).status, 200)
            self.assertEqual((await client.get('/v1/cameras')).status, 401)
            headers = {'Authorization': 'Bearer ' + cfg['token']}
            cat = await client.get('/v1/cameras', headers=headers)
            self.assertEqual((await cat.json())['cameras'][0]['id'], 'test')
            r = await client.get('/v1/stream/test', headers=headers)
            self.assertEqual(r.status, 200)
            self.assertIn(b'Content-Length: 8', await r.content.readuntil(b'\r\n\r\n'))
            self.assertEqual(await r.content.readexactly(8), JPEG)
            r.close()
            await asyncio.sleep(.15)
        self.assertTrue(all(p.returncode is not None for p in self.processes))

    async def test_real_child_is_reaped(self):
        async def spawn(*args, **kwargs):
            return await asyncio.create_subprocess_exec(sys.executable, '-u', '-c',
                'import sys,time\nwhile True:\n sys.stdout.buffer.write(bytes.fromhex("ffd874657374ffd9"));sys.stdout.flush();time.sleep(.01)',
                **kwargs)
        w = Worker(CAM, .01, spawn); q = w.subscribe()
        await asyncio.wait_for(q.get(), 2); proc = w.proc
        await w.close()
        self.assertIsNotNone(proc.returncode)
        self.assertIsNone(w.proc); self.assertIsNone(w.task)

    async def test_cancel_before_worker_entry_can_restart(self):
        w=Worker(CAM,.01,self.spawn)
        w.subscribe(); await w.close()
        self.assertIsNone(w.task)
        q=w.subscribe(); await asyncio.wait_for(q.get(),1)
        await w.close();self.assertIsNone(w.task)

    async def test_slow_client_does_not_stall_fast_client(self):
        async def spawn(*args, **kwargs):
            p=FakeProcess(frame=b'\xff\xd8'+b'x'*190000+b'\xff\xd9');self.processes.append(p);return p
        cfg=dict(token='x'*64,cameras=[CAM],idle_seconds=.01,max_workers=1,max_clients=2)
        app=create_app(cfg,spawn)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':'Bearer '+cfg['token']}
            slow=await client.get('/v1/stream/test',headers=headers,read_bufsize=1024)
            fast=await client.get('/v1/stream/test',headers=headers)
            async def consume():
                total=0
                while True:
                    data=await fast.content.read(65536)
                    if not data: break
                    total+=len(data)
                return total
            consumer=asyncio.create_task(consume())
            await asyncio.sleep(2.2)
            self.assertEqual(len(app[WORKERS]['test'].clients),1)
            self.assertEqual(len(self.processes),1)
            slow.close();fast.close();consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError,ConnectionError): await consumer

    async def test_source_stall_closes_connection_and_idles(self):
        async def spawn(*args,**kwargs):
            p=FakeProcess(False);self.processes.append(p);return p
        cfg=dict(token='x'*64,cameras=[CAM],idle_seconds=.01,max_workers=1,max_clients=2,source_timeout=.02)
        app=create_app(cfg,spawn)
        async with TestClient(TestServer(app)) as client:
            r=await client.get('/v1/stream/test',headers={'Authorization':'Bearer '+cfg['token']})
            with self.assertRaises(Exception): await r.content.read()
            await asyncio.sleep(.05)
            self.assertIsNone(app[WORKERS]['test'].task)

    async def test_simultaneous_request_limit(self):
        cfg=dict(token='x'*64,cameras=[CAM],idle_seconds=.01,max_workers=1,max_clients=1)
        app=create_app(cfg,self.spawn)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':'Bearer '+cfg['token']}
            responses=await asyncio.gather(*(client.get('/v1/stream/test',headers=headers) for _ in range(5)))
            self.assertEqual(sorted(r.status for r in responses),[200,503,503,503,503])
            for r in responses:r.close()


if __name__ == '__main__': unittest.main()
