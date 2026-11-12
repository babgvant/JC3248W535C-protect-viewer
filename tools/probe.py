"""Run ON THE PI. URLs remain out of stdout/report; errors use fixed safe messages.

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
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
        return subprocess.CompletedProcess([], result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired as error:
        # Do not let TimeoutExpired print its argv containing the source token.
        return subprocess.CompletedProcess([],124,stdout=error.output or b'',stderr=error.stderr or b'')
    except FileNotFoundError:
        return subprocess.CompletedProcess([],127,stdout=b'',stderr=b'')


def diagnostic(result):
    """Never copy arbitrary FFmpeg output into reports: it can contain credentials."""
    if result.returncode == 0: return {'category':'ok','returncode':0}
    text = (result.stderr or b'').decode(errors='replace').lower()
    category, message = 'media_error', 'Media command failed; no raw stderr is included because it may contain credentials.'
    if result.returncode == 127:
        category, message = 'missing_executable', 'FFmpeg or ffprobe executable was not found.'
    elif result.returncode == 124:
        category, message = 'timeout', 'The media command exceeded its time limit.'
    elif any(s in text for s in ('unrecognized option','option not found','failed to set value','error setting option')) or ('option ' in text and ' not found' in text):
        category, message = 'unsupported_option', 'This FFmpeg build rejected a command option. Check its RTSP/TLS option support.'
        # Only report known flag names, never arbitrary strings from the source.
        for flag in ('tls_verify','ca_file','rw_timeout','rtsp_transport'):
            if flag in text:
                message = f'This FFmpeg build rejected the {flag} option. A compatible FFmpeg build is required.'
                break
    elif any(s in text for s in ('certificate verify failed','certificate verification failed','peer certificate','certificate is not trusted','error in the certificate','unknown ca','self-signed certificate')):
        category, message = 'tls_verification', 'Source TLS certificate verification failed. Configure the trusted source CA and correct hostname; verification remains enabled.'
    elif '401' in text and any(s in text for s in ('unauthorized','server returned','method ')):
        category, message = 'unauthorized', 'Source returned 401 Unauthorized. Verify the current Protect stream URL/token.'
    elif '403' in text and any(s in text for s in ('forbidden','server returned','method ')):
        category, message = 'forbidden', 'Source returned 403 Forbidden. Verify stream permissions and the Protect stream URL.'
    elif any(s in text for s in ('404 not found','server returned 404')):
        category, message = 'stream_not_found', 'Source returned 404. Verify the exact stream profile URL.'
    elif 'connection refused' in text:
        category, message = 'connection_refused', 'The source refused the connection. Verify its address, port and local stream service.'
    elif any(s in text for s in ('name or service not known','temporary failure in name resolution','failed to resolve','nodename nor servname')):
        category, message = 'dns', 'The source hostname could not be resolved from this host.'
    elif any(s in text for s in ('no route to host','network is unreachable')):
        category, message = 'network_unreachable', 'No network route to the source. Check routing and VLAN/firewall access.'
    elif any(s in text for s in ('timed out','connection timeout')):
        category, message = 'connection_timeout', 'The source connection timed out. Check address, port and network access.'
    elif any(s in text for s in ('unknown decoder','decoder not found','no device available','device creation failed','failed to initialise','failed to initialize')):
        category, message = 'decoder_unavailable', 'The configured decoder or hardware device is unavailable.'
    elif any(s in text for s in ('error reinitializing filters','failed to configure output pad','no such filter','impossible to convert')):
        category, message = 'filter_error', 'The output filter pipeline failed. Check frame format, resize settings and hardware download compatibility.'
    return {'category':category,'message':message,'returncode':result.returncode}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'gateway/config.json')
    p.add_argument('--camera', required=True, help='camera ID in the shared configuration')
    p.add_argument('--decoder', help='override configured decoder, e.g. hevc_v4l2request')
    p.add_argument('--hw-download', action=argparse.BooleanOptionalAction, default=None, help='download DRM/device frames before CPU filters')
    p.add_argument('--source-ca-file', type=Path)
    p.add_argument('--output', type=Path, default=Path('probe.json'))
    a = p.parse_args(argv)
    cfg=load_config(a.config)
    camera=next((dict(c) for c in cfg['cameras'] if c['id']==a.camera),None)
    if camera is None: p.error('camera ID not found in configuration')
    if a.decoder is not None: camera['decoder']=a.decoder
    if a.hw_download is not None: camera['hw_download']=a.hw_download
    if a.source_ca_file: camera['source_ca_file']=str(a.source_ca_file)
    url = camera['url']
    report = {'platform': platform.platform(), 'model': '', 'decoder_candidate': camera['decoder'],
              'source_transport':url.split(':',1)[0]}
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
    report['source_diagnostic'] = diagnostic(result)
    report['source_probe_ok'] = False
    if result.returncode == 0:
        try:
            source = json.loads(result.stdout)
            report['source_probe_ok'] = isinstance(source,dict) and bool(source.get('streams'))
            if report['source_probe_ok']: report['source'] = source
            else: report['source_diagnostic'] = {'category':'no_video','message':'No video stream metadata was returned. Verify the selected camera profile.'}
        except ValueError:
            report['source_diagnostic'] = {'category':'invalid_probe_output','message':'ffprobe did not return valid JSON metadata.'}
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
    from media import command
    args = command(camera,url)
    args = args[:-1] + ['-frames:v','30','pipe:1']
    try:
        result = run(args,30)
        report['pipeline_diagnostic'] = diagnostic(result)
        from media import JPEGFrames
        frames = JPEGFrames().feed(result.stdout)
        report['pipeline_ok'] = result.returncode == 0 and len(frames) == 30
        report['frames'] = len(frames)
        report['bytes'] = len(result.stdout)
    except ValueError:
        report['pipeline_ok'] = False
        report['pipeline_diagnostic'] = {'category':'invalid_jpeg','message':'Output contained an invalid or oversized JPEG.'}
    a.output.write_text(json.dumps(report,indent=2))
    print('Saved redacted report:',a.output,'pipeline_ok=',report['pipeline_ok'])
    for key in ('source_diagnostic','pipeline_diagnostic'):
        info=report.get(key,{})
        if info.get('category')!='ok': print(key+':',info.get('category'),'-',info.get('message',''))
    return 0 if report['pipeline_ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
