"""Bounded image2pipe framing and deterministic FFmpeg argument construction."""
MAX_FRAME = 196608


class JPEGFrames:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        frames = []
        while True:
            start = self.buf.find(b'\xff\xd8')
            if start < 0:
                self.buf[:] = self.buf[-1:]
                break
            if start:
                del self.buf[:start]
            end = self.buf.find(b'\xff\xd9', 2)
            nested = self.buf.find(b'\xff\xd8', 2)
            if nested >= 0 and (end < 0 or nested < end):
                # Our FFmpeg profile has no embedded JPEG thumbnails.
                del self.buf[:nested]
                continue
            if end < 0:
                if len(self.buf) > MAX_FRAME:
                    raise ValueError('oversize JPEG')
                break
            if end + 2 > MAX_FRAME:
                raise ValueError('oversize JPEG')
            frames.append(bytes(self.buf[:end + 2]))
            del self.buf[:end + 2]
        return frames


def rtsp_input_options(camera, url):
    # RTSP is a demuxer, rather than a URL protocol that consumes rw_timeout.
    # Its socket I/O timeout is in microseconds in the supported FFmpeg builds.
    args = ['-rtsp_transport', 'tcp', '-timeout', '5000000']
    if url.startswith('rtsps://'):
        args += ['-tls_verify', '1']
        if camera.get('source_ca_file'):
            args += ['-ca_file', camera['source_ca_file']]
    return args


def command(camera, url):
    # Decoder override is an administrator choice after tools/probe.py succeeds.
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-threads', '2'] + rtsp_input_options(camera, url) + [
            '-fflags', 'nobuffer', '-flags', 'low_delay']
    if camera['decoder'] != 'software':
        args += ['-c:v', camera['decoder']]
    args += ['-i', url, '-map', '0:v:0', '-an', '-sn', '-dn']
    filters = []
    if camera.get('hw_download', False):
        filters += ['hwdownload', 'format=nv12']
    filters += [f"fps={camera['fps']}"]
    if camera['rotation'] == 90:
        filters += ['transpose=1']
    elif camera['rotation'] == 270:
        filters += ['transpose=2']
    elif camera['rotation'] == 180:
        filters += ['hflip', 'vflip']
    if camera['mode'] == 'fill':
        filters += ['scale=480:320:force_original_aspect_ratio=increase', 'crop=480:320']
    else:
        # min() prevents upscaling; padding maintains an exact decoder canvas.
        filters += ["scale=w='min(480,iw)':h='min(320,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    'pad=480:320:(ow-iw)/2:(oh-ih)/2:black']
    # Rotate canvas at gateway: native panel is portrait, viewed clockwise.
    filters += ['setsar=1', 'transpose=2', 'format=yuvj420p']
    return args + ['-vf', ','.join(filters), '-c:v', 'mjpeg', '-threads', '1',
                   '-q:v', str(camera['quality']), '-pix_fmt', 'yuvj420p',
                   '-f', 'image2pipe', '-flush_packets', '1', 'pipe:1']
