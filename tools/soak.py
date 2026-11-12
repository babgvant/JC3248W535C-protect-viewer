"""Bounded MJPEG soak/rapid-switch client. Token is read from disk, never URL.
Run with gateway requirements installed. Output is JSONL, one summary per connection.
"""
import argparse
import asyncio
import json
import random
import time
from pathlib import Path
import aiohttp


async def main(a):
    headers={'Authorization':'Bearer '+a.token_file.read_text().strip()}
    timeout=aiohttp.ClientTimeout(total=None,connect=3,sock_read=10)
    end=time.monotonic()+a.seconds
    async with aiohttp.ClientSession(headers=headers,timeout=timeout) as session:
        async with session.get(a.gateway+'/v1/cameras') as response:
            response.raise_for_status(); cameras=(await response.json())['cameras']
        if not cameras: raise RuntimeError('empty catalog')
        index=0
        while time.monotonic()<end:
            c=cameras[index%len(cameras)];index+=1
            start=time.monotonic(); first=None; frames=0; size=0; error=None
            dwell=random.uniform(.05,.35) if a.rapid else a.dwell
            try:
                async with session.get(a.gateway+'/v1/stream/'+c['id']) as r:
                    r.raise_for_status(); reader=aiohttp.MultipartReader.from_response(r)
                    while time.monotonic()-start<dwell:
                        part=await reader.next()
                        if part is None: break
                        n=int(part.headers.get('Content-Length','0'))
                        if n<4 or n>196608: raise ValueError('invalid frame size')
                        data=bytearray()
                        while not part.at_eof():
                            data.extend(await part.read_chunk(4096))
                            if len(data)>196608: raise ValueError('oversized frame')
                        if len(data)!=n or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                            raise ValueError('corrupt frame')
                        if first is None: first=time.monotonic()-start
                        frames+=1;size+=n
                        if a.slow: await asyncio.sleep(2)
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError) as e:
                error=type(e).__name__
            elapsed=time.monotonic()-start
            print(json.dumps(dict(camera=c['id'],elapsed_s=elapsed,first_frame_s=first,
                                  frames=frames,fps=frames/elapsed,bytes=size,error=error)),flush=True)
            await asyncio.sleep(.025)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--gateway',required=True);p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=86400);p.add_argument('--dwell',type=float,default=30)
    p.add_argument('--rapid',action='store_true');p.add_argument('--slow',action='store_true')
    asyncio.run(main(p.parse_args()))
