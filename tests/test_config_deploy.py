import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gateway'))
sys.path.insert(0, str(ROOT / 'tools'))
from config import load_config
import deploy


class ConfigDeploymentTests(unittest.TestCase):
    def setUp(self):
        # Deployment dispatch tests isolate host setup; it has its own tests below.
        prerequisite_patch = patch.object(deploy.Prerequisites, 'ensure', return_value=['docker'])
        self.prerequisites = prerequisite_patch.start()
        self.addCleanup(prerequisite_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'gateway').mkdir()
        self.path = self.root / 'gateway/config.json'
        self.cfg = json.loads((ROOT / 'gateway/config.example.json').read_text())
        self.cfg['token'] = 'x' * 64
        for camera in self.cfg['cameras']:
            camera['url'] = 'rtsp://camera.invalid/' + camera['id']
        (self.root / 'gateway/config.example.json').write_text(json.dumps(self.cfg))

    def write(self, cfg=None):
        self.path.write_text(json.dumps(self.cfg if cfg is None else cfg))
        self.path.chmod(0o600)

    def test_one_file_loads_all_urls_and_token(self):
        self.write()
        cfg = load_config(self.path)
        self.assertEqual(len(cfg['cameras']), 2)
        self.assertEqual(cfg['token'], self.cfg['token'])
        self.assertEqual(cfg['cameras'][1]['url'], 'rtsp://camera.invalid/doorbell')

    def test_invalid_config_rejected_without_echoing_secrets(self):
        for change in ('legacy','duplicate','missing_url','malformed_url','bad_token','bad_limit'):
            cfg = copy.deepcopy(self.cfg)
            if change == 'legacy': cfg['cameras'][0]['url_file'] = 'secret-url'
            if change == 'duplicate': cfg['cameras'][1]['id'] = cfg['cameras'][0]['id']
            if change == 'missing_url': del cfg['cameras'][0]['url']
            if change == 'malformed_url': cfg['cameras'][0]['url'] = 'secret-url\n'
            if change == 'bad_token': cfg['token'] = 'secret-token\n' * 4
            if change == 'bad_limit': cfg['max_clients'] = False
            self.write(cfg)
            with self.assertRaises(ValueError) as result: load_config(self.path)
            self.assertNotIn('secret-url', str(result.exception))
            self.assertNotIn('secret-token', str(result.exception))

    def test_init_private_unique_token_and_no_overwrite(self):
        with patch.object(deploy, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            deploy.initialize(self.path, '192.168.1.50')
            first = self.path.read_bytes()
            with self.assertRaises(ValueError): deploy.initialize(self.path, '192.168.1.51')
        self.assertEqual(self.path.read_bytes(), first)
        cfg = load_config(self.path)
        self.assertEqual(len(cfg['token']), 64)
        self.assertNotEqual(cfg['token'], self.cfg['token'])
        if os.name == 'posix': self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_hwaccel_defaults_and_validation(self):
        self.cfg['cameras'][0].pop('decoder', None)
        self.cfg['cameras'][0].pop('hwaccel', None)
        self.write()
        camera = load_config(self.path)['cameras'][0]
        self.assertEqual(camera['decoder'], 'hevc')
        self.assertEqual(camera['hwaccel'], 'drm')
        self.assertFalse(camera['hw_download'])
        self.cfg['cameras'][0]['decoder'] = 'software'
        self.write()
        self.assertEqual(load_config(self.path)['cameras'][0]['hwaccel'], 'none')
        self.cfg['cameras'][0].update(decoder='hevc', hwaccel='drm')
        self.write()
        self.assertEqual(load_config(self.path)['cameras'][0]['hwaccel'], 'drm')
        for invalid in ('auto', '', None, True, ['drm']):
            self.cfg['cameras'][0]['hwaccel'] = invalid
            self.write()
            with self.assertRaisesRegex(ValueError, 'hwaccel'):
                load_config(self.path)

    def test_migration_preserves_urls_token_and_original(self):
        secret_dir = self.path.parent / 'secrets'; secret_dir.mkdir()
        old = copy.deepcopy(self.cfg)
        (secret_dir / 'viewer_token').write_text(old.pop('token'))
        old['token_file'] = '/run/secrets/viewer_token'
        for c in old['cameras']:
            (secret_dir / c['id']).write_text(c.pop('url'))
            c['url_file'] = '/run/secrets/' + c['id']
        self.write(old)
        with contextlib.redirect_stdout(io.StringIO()): deploy.migrate(self.path, None)
        self.assertEqual(load_config(self.path)['token'], self.cfg['token'])
        self.assertEqual(load_config(self.path)['cameras'][0]['url'], self.cfg['cameras'][0]['url'])
        self.assertEqual(json.loads(self.path.with_name('config.json.legacy').read_text()), old)
        self.assertTrue((secret_dir / 'front').exists())
        self.assertFalse(self.path.with_name('config.json.tmp').exists())

    def test_up_uses_lan_ip_and_uid_without_exposing_config(self):
        self.write()
        with patch.object(deploy, 'ROOT', self.root), patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(deploy.main(['up']), 0)
        command = run.call_args.args[0]
        self.assertIn('--wait', command); self.assertIn('--force-recreate', command)
        env = run.call_args.kwargs['env']
        self.prerequisites.assert_called_once_with(install=True)
        self.assertEqual(env['LAN_IP'], self.cfg['lan_ip'])
        self.assertNotIn(self.cfg['token'], str(run.call_args))
        self.assertNotIn('rtsp://', str(run.call_args))

    def test_bad_config_prevents_docker_and_down_still_works(self):
        self.path.write_text('{broken JSON')
        with patch.object(deploy, 'ROOT', self.root), patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=0)) as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(deploy.main(['up']), 1); run.assert_not_called()
            self.assertEqual(deploy.main(['down']), 0)
            self.assertIn('down', run.call_args.args[0])

    def test_compose_failure_returned(self):
        self.write()
        with patch.object(deploy, 'ROOT', self.root), patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=7)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(deploy.main(['update']), 7)

    def test_invalid_migration_preserves_original_and_cleans_temp(self):
        old = copy.deepcopy(self.cfg)
        secret_dir = self.path.parent / 'secrets'; secret_dir.mkdir()
        (secret_dir / 'front').write_text('invalid source')
        old['cameras'][0].pop('url')
        old['cameras'][0]['url_file'] = '/run/secrets/front'
        self.write(old)
        with self.assertRaises(ValueError): deploy.migrate(self.path, None)
        self.assertEqual(json.loads(self.path.read_text()), old)
        self.assertFalse(self.path.with_name('config.json.tmp').exists())
        self.assertFalse(self.path.with_name('config.json.legacy').exists())

    def test_restart_preserves_compose_overrides_without_build(self):
        override = self.root / 'compose.hwa.yaml'
        args = deploy.compose_command('restart', [override])
        self.assertIn(str(override.resolve()), args)
        self.assertIn('--no-build', args)
        self.assertEqual(args[args.index('--ansi')+1], 'never')
        self.assertEqual(args[args.index('--progress')+1], 'plain')

    def test_missing_docker_is_distinguished_from_missing_config(self):
        self.write()
        stderr = io.StringIO()
        with patch.object(deploy, 'ROOT', self.root), patch.object(deploy.subprocess, 'run', side_effect=FileNotFoundError()), contextlib.redirect_stderr(stderr):
            self.assertEqual(deploy.main(['up']), 1)
        self.assertIn('Docker executable not found', stderr.getvalue())
        self.assertNotIn(self.cfg['token'], stderr.getvalue())
        self.path.unlink()
        stderr = io.StringIO()
        with patch.object(deploy, 'ROOT', self.root), contextlib.redirect_stderr(stderr):
            self.assertEqual(deploy.main(['check']), 1)
        self.assertIn('gateway/config.json was not found', stderr.getvalue())

    def test_config_permission_error_identifies_operation(self):
        stderr = io.StringIO()
        with patch.object(deploy, 'load_config', side_effect=PermissionError('sensitive contents')), contextlib.redirect_stderr(stderr):
            self.assertEqual(deploy.main(['check']), 1)
        self.assertIn('permission denied while reading gateway/config.json', stderr.getvalue())
        self.assertNotIn('sensitive contents', stderr.getvalue())

    def test_config_directory_is_reported(self):
        stderr = io.StringIO()
        with patch.object(deploy, 'load_config', side_effect=IsADirectoryError()), contextlib.redirect_stderr(stderr):
            self.assertEqual(deploy.main(['check']), 1)
        self.assertIn('found a directory', stderr.getvalue())

    def test_setup_does_not_require_config(self):
        with patch.object(deploy, 'ROOT', self.root):
            self.assertEqual(deploy.main(['setup']), 0)
        self.prerequisites.assert_called_once_with()
        self.assertFalse(self.path.exists())

    def test_prerequisite_failure_prevents_compose(self):
        self.write()
        self.prerequisites.side_effect = deploy.SetupError('package installation failed')
        with patch.object(deploy, 'ROOT', self.root), patch.object(deploy.subprocess, 'run') as run, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(deploy.main(['up']), 1)
            run.assert_not_called()
        self.assertIn('package installation failed', errors.getvalue())


if __name__ == '__main__': unittest.main()
