import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import probe


class ProbeDiagnosticTests(unittest.TestCase):
    def test_error_classification_never_copies_source_secrets(self):
        cases = [
            ("Unrecognized option 'tls_verify'",'unsupported_option'),
            ("Option rw_timeout not found",'unsupported_option'),
            ('Peer certificate is not trusted','tls_verification'),
            ('method DESCRIBE failed: 401 Unauthorized','unauthorized'),
            ('Server returned 403 Forbidden','forbidden'),
            ('Server returned 404 Not Found','stream_not_found'),
            ('Connection refused','connection_refused'),
            ('Name or service not known','dns'),
            ('No route to host','network_unreachable'),
            ('Connection timed out','connection_timeout'),
            ("Unknown decoder 'hevc_v4l2request'",'decoder_unavailable'),
            ('Error reinitializing filters','filter_error'),
            ('unclassified failure','media_error'),
        ]
        for error, category in cases:
            with self.subTest(category=category):
                stderr=(error+' rtsps://secret-user:secret-password@private-host/private-token\nprivate-token').encode()
                result=probe.diagnostic(subprocess.CompletedProcess([],1,stdout=b'',stderr=stderr))
                self.assertEqual(result['category'],category)
                for secret in ('secret-user','secret-password','private-host','private-token'):
                    self.assertNotIn(secret,json.dumps(result))

    def test_timeout_and_missing_executable_are_safe(self):
        with patch.object(probe.subprocess,'run',side_effect=subprocess.TimeoutExpired(['ffmpeg','secret-url'],30)):
            result=probe.run(['ffmpeg','secret-url'])
        self.assertEqual(result.args,[])
        self.assertEqual(probe.diagnostic(result)['category'],'timeout')
        with patch.object(probe.subprocess,'run',side_effect=FileNotFoundError()):
            result=probe.run(['ffprobe','secret-url'])
        self.assertEqual(probe.diagnostic(result)['category'],'missing_executable')

    def test_report_and_console_include_categories_without_raw_stderr(self):
        cfg={'cameras':[dict(id='doorbell',url='rtsps://private-host/private-token',decoder='software',
                            fps=10,quality=5,mode='fit',rotation=0)]}
        def run(args,timeout=30):
            if 'rtsps://private-host/private-token' in args:
                return subprocess.CompletedProcess([],1,stdout=b'',stderr=b'Unrecognized option tls_verify for rtsps://private-host/private-token')
            return subprocess.CompletedProcess([],0,stdout=b'inventory',stderr=b'')
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'; console=io.StringIO()
            with patch.object(probe,'load_config',return_value=cfg),patch.object(probe,'run',side_effect=run),contextlib.redirect_stdout(console):
                self.assertEqual(probe.main(['--camera','doorbell','--output',str(output)]),1)
            report=json.loads(output.read_text())
            self.assertFalse(report['source_probe_ok']);self.assertFalse(report['pipeline_ok'])
            self.assertEqual(report['frames'],0)
            self.assertEqual(report['source_diagnostic']['category'],'unsupported_option')
            self.assertEqual(report['pipeline_diagnostic']['category'],'unsupported_option')
            self.assertNotIn('private-token',output.read_text()+console.getvalue())
            self.assertNotIn('private-host',output.read_text()+console.getvalue())


if __name__=='__main__': unittest.main()
