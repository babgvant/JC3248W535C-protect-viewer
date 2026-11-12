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


def run(args, timeout=30):
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # Do not let TimeoutExpired print its argv containing the source token.
        return subprocess.CompletedProcess([],124,stdout=b'')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url-file', type=Path, required=True)
    p.add_argument('--decoder', default='software', help='e.g. hevc_v4l2request, only if installed')
    p.add_argument('--hw-download', action='store_true', help='download DRM/device frames before CPU filters')
    p.add_argument('--source-ca-file', type=Path)
    p.add_argument('--output', type=Path, default=Path('probe.json'))
    a = p.parse_args()
    url = a.url_file.read_text().strip()
    report = {'platform': platform.platform(), 'model': '', 'decoder_candidate': a.decoder}
    model = Path('/proc/device-tree/model')
    if model.exists(): report['model'] = model.read_text().strip('\0')
    for name, args in [('version',['-version']),('hwaccels',['-hwaccels']),
                       ('decoders',['-decoders']),('encoders',['-encoders'])]:
        report[name] = run(['ffmpeg','-hide_banner']+args).stdout.decode(errors='replace')
    probe_args=['ffprobe','-v','error','-rtsp_transport','tcp','-rw_timeout','5000000']
    if url.startswith('rtsps://'):
        probe_args+=['-tls_verify','1']
        if a.source_ca_file: probe_args+=['-ca_file',str(a.source_ca_file)]
    result = run(probe_args+[
                  '-select_streams','v:0','-show_entries',
                  'stream=codec_name,profile,width,height,pix_fmt,avg_frame_rate,r_frame_rate',
                  '-of','json',url], 15)
    report['source_probe_ok'] = result.returncode == 0
    if result.returncode == 0: report['source'] = json.loads(result.stdout)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
    from media import command
    camera = dict(fps=10,quality=5,mode='fit',rotation=0,decoder=a.decoder,hw_download=a.hw_download)
    if a.source_ca_file: camera['source_ca_file']=str(a.source_ca_file)
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
