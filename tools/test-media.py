"""Exercise the production output filters with a real FFmpeg and synthetic source.
Dev dependency: imageio-ffmpeg==0.6.0 (or set FFMPEG to a native executable).
No camera credentials or hardware needed. Writes one sample JPEG to artifacts/.
"""
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
from media import command, JPEGFrames

binary=os.environ.get('FFMPEG')
if not binary:
    import imageio_ffmpeg
    binary=imageio_ffmpeg.get_ffmpeg_exe()
for mode in ('fit','fill'):
    for rotation in (0,90,180,270):
        camera=dict(fps=10,quality=5,mode=mode,rotation=rotation,decoder='software')
        args=command(camera,'rtsp://placeholder')
        # Replace RTSP input only; use the production filter/encode arguments unchanged.
        output=args[args.index('-map'):]
        output=output[:-1]+['-frames:v','3','pipe:1']
        result=subprocess.run([binary,'-hide_banner','-loglevel','error','-f','lavfi',
                               '-i','testsrc2=size=640x360:rate=10']+output,
                              capture_output=True,timeout=20)
        assert result.returncode==0,result.stderr.decode(errors='replace')
        frames=JPEGFrames().feed(result.stdout)
        assert len(frames)==3
        # Check SOF0 precision, native dimensions and 4:2:0 sampling.
        for data in frames:
            i=data.index(b'\xff\xc0')
            assert data[i+4:i+10]==bytes([8,1,224,1,64,3])
            assert [data[i+11],data[i+14],data[i+17]]==[0x22,0x11,0x11]
        Path('artifacts').mkdir(exist_ok=True)
        Path('artifacts/sample-native.jpg').write_bytes(frames[0])
        print(mode,rotation,'3 valid baseline 320x480 YUV420 frames',len(frames[0]),'bytes')
