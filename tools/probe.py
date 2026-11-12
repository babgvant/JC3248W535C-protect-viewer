"""Run ON THE PI. URLs remain out of stdout/report; FFmpeg stderr is suppressed.

Inventory is evidence of installed capabilities, not proof of working acceleration.
Only the 30-frame decode/filter/encode test validates a candidate decoder path.
"""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
from config import load_config


def run(args, timeout=30):
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # Do not let TimeoutExpired print its argv containing the source token.
        return subprocess.CompletedProcess([],124,stdout=b'')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'gateway/config.json')
    p.add_argument('--camera', required=True, help='camera ID in the shared configuration')
    p.add_argument('--decoder', help='override configured decoder, e.g. hevc_v4l2request')
    p.add_argument('--hw-download', action=argparse.BooleanOptionalAction, default=None, help='download DRM/device frames before CPU filters')
    p.add_argument('--source-ca-file', type=Path)
    p.add_argument('--output', type=Path, default=Path('probe.json'))
    a = p.parse_args()
    cfg=load_config(a.config)
    camera=next((dict(c) for c in cfg['cameras'] if c['id']==a.camera),None)
    if camera is None: p.error('camera ID not found in configuration')
    if a.decoder is not None: camera['decoder']=a.decoder
    if a.hw_download is not None: camera['hw_download']=a.hw_download
    if a.source_ca_file: camera['source_ca_file']=str(a.source_ca_file)
    url = camera['url']
    report = {'platform': platform.platform(), 'model': '', 'decoder_candidate': camera['decoder']}
    model = Path('/proc/device-tree/model')
    if model.exists(): report['model'] = model.read_text().strip('\0')
    for name, args in [('version',['-version']),('hwaccels',['-hwaccels']),
                       ('decoders',['-decoders']),('encoders',['-encoders'])]:
        report[name] = run(['ffmpeg','-hide_banner']+args).stdout.decode(errors='replace')
    probe_args=['ffprobe','-v','error','-rtsp_transport','tcp','-rw_timeout','5000000']
    if url.startswith('rtsps://'):
        probe_args+=['-tls_verify','1']
        if camera.get('source_ca_file'): probe_args+=['-ca_file',camera['source_ca_file']]
    result = run(probe_args+[
                  '-select_streams','v:0','-show_entries',
                  'stream=codec_name,profile,width,height,pix_fmt,avg_frame_rate,r_frame_rate',
                  '-of','json',url], 15)
    report['source_probe_ok'] = result.returncode == 0
    if result.returncode == 0: report['source'] = json.loads(result.stdout)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
    from media import command
    args = command(camera,url)
    args = args[:-1] + ['-frames:v','30','pipe:1']
    try:
        result = run(args,30)
        from media import JPEGFrames
        frames = JPEGFrames().feed(result.stdout)
        report['pipeline_ok'] = result.returncode == 0 and len(frames) == 30
        report['frames'] = len(frames)
        report['bytes'] = len(result.stdout)
    except ValueError:
        report['pipeline_ok'] = False
    a.output.write_text(json.dumps(report,indent=2))
    print('Saved redacted report:',a.output,'pipeline_ok=',report['pipeline_ok'])
    return 0 if report['pipeline_ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
