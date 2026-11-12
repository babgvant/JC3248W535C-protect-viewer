import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import deploy


def result(code=0, stdout=''):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr='')


class PrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_healthy_installation_never_runs_apt_or_sudo(self):
        with patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', return_value=result()), patch.object(deploy.Prerequisites, 'install') as install, patch.object(deploy.subprocess, 'run') as run:
            setup = deploy.Prerequisites()
            self.assertEqual(setup.ensure(), ['/usr/bin/docker'])
            self.assertEqual(setup.ensure(), ['/usr/bin/docker'])
            install.assert_not_called(); run.assert_not_called()

    def test_existing_pi_ffmpeg_is_kept_when_only_docker_missing(self):
        tools = {'ffmpeg': '/usr/local/bin/ffmpeg', 'ffprobe': '/usr/local/bin/ffprobe'}
        def install(choices): tools['docker'] = '/usr/bin/docker'
        with patch.object(deploy, 'find_tool', side_effect=lambda name: tools.get(name)), patch.object(deploy, 'probe_command', return_value=result()), patch.object(deploy.Prerequisites, 'install', side_effect=install) as install_mock:
            deploy.Prerequisites().ensure()
            self.assertEqual(install_mock.call_args_list, [unittest.mock.call(['docker-ce','docker.io'])])
            self.assertEqual(tools['ffmpeg'], '/usr/local/bin/ffmpeg')

    def test_only_missing_ffmpeg_package_is_installed(self):
        media_present = False
        def find(name):
            if name in ('ffmpeg','ffprobe') and not media_present: return None
            return '/usr/bin/'+name
        def install(choices):
            nonlocal media_present
            self.assertEqual(choices, ['ffmpeg']); media_present = True
        with patch.object(deploy, 'find_tool', side_effect=find), patch.object(deploy, 'probe_command', return_value=result()), patch.object(deploy.Prerequisites, 'install', side_effect=install) as install_mock:
            deploy.Prerequisites().ensure()
            install_mock.assert_called_once_with(['ffmpeg'])

    def test_only_missing_compose_plugin_is_installed(self):
        compose_present = False
        def probe(argv): return result(1 if 'compose' in argv and not compose_present else 0)
        def install(choices):
            nonlocal compose_present
            self.assertEqual(choices[0], 'docker-compose-plugin'); compose_present = True
        with patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', side_effect=probe), patch.object(deploy.Prerequisites, 'install', side_effect=install) as install_mock:
            deploy.Prerequisites().ensure()
            self.assertEqual(install_mock.call_count, 1)

    def test_stopped_local_daemon_is_started(self):
        info_calls = 0
        def probe(argv):
            nonlocal info_calls
            if 'info' in argv:
                info_calls += 1; return result(0 if info_calls > 1 else 1)
            if 'context' in argv: return result(stdout='unix:///var/run/docker.sock\n')
            if 'show' in argv: return result(stdout='loaded\n')
            if 'is-active' in argv: return result(1)
            return result()
        with patch.dict(deploy.os.environ, {'DOCKER_HOST':''}), patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', side_effect=probe), patch.object(deploy.Prerequisites, 'run_admin') as admin:
            self.assertEqual(deploy.Prerequisites().docker_access('/usr/bin/docker'), ['/usr/bin/docker'])
            admin.assert_called_once_with(['/usr/bin/systemctl','enable','--now','docker.service'])

    def test_sudo_access_does_not_change_group_membership(self):
        def probe(argv):
            if 'info' in argv: return result(1)
            if 'context' in argv: return result(stdout='unix:///var/run/docker.sock\n')
            if 'show' in argv: return result(stdout='loaded\n')
            return result()
        with patch.dict(deploy.os.environ, {'DOCKER_HOST':''}), patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', side_effect=probe), patch.object(deploy.Prerequisites, 'privileged', side_effect=lambda argv: ['/usr/bin/sudo']+argv), patch.object(deploy.subprocess, 'run', return_value=result()) as run:
            self.assertEqual(deploy.Prerequisites().docker_access('/usr/bin/docker'), ['/usr/bin/sudo','/usr/bin/docker'])
            self.assertIn('info', run.call_args.args[0])
            self.assertNotIn('usermod', str(run.call_args))

    def test_remote_context_failure_does_not_install_or_start_local_engine(self):
        with patch.dict(deploy.os.environ, {'DOCKER_HOST':'ssh://remote.example'}), patch.object(deploy, 'probe_command', return_value=result(1)), patch.object(deploy.Prerequisites, 'run_admin') as admin, patch.object(deploy.Prerequisites, 'install') as install:
            with self.assertRaisesRegex(deploy.SetupError, 'remote/rootless'): deploy.Prerequisites().docker_access('docker')
            admin.assert_not_called(); install.assert_not_called()

    def test_distro_compose_v2_selected_without_replacing_engine(self):
        with patch.object(deploy.Prerequisites, 'apt_update'), patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', side_effect=[result(stdout='Candidate: (none)'),result(stdout='Candidate: 2.26.1-4')]), patch.object(deploy.Prerequisites, 'run_admin') as admin:
            deploy.Prerequisites().install(['docker-compose-plugin','docker-compose'])
            args = admin.call_args.args[0]
            self.assertEqual(args[-1], 'docker-compose')
            self.assertIn('--no-remove', args); self.assertIn('--no-upgrade', args)

    def test_legacy_compose_v1_not_installed(self):
        with patch.object(deploy.Prerequisites, 'apt_update'), patch.object(deploy, 'probe_command', return_value=result(stdout='Candidate: 1.29.2-1')), patch.object(deploy.Prerequisites, 'run_admin') as admin:
            with self.assertRaises(deploy.SetupError): deploy.Prerequisites().install(['docker-compose'])
            admin.assert_not_called()

    def test_failed_install_stops_deployment(self):
        with patch.object(deploy.Prerequisites, 'privileged', side_effect=lambda argv: argv), patch.object(deploy.subprocess, 'run', return_value=result(100)):
            with self.assertRaisesRegex(deploy.SetupError, 'failed'): deploy.Prerequisites().run_admin(['apt-get','install','docker.io'])

    def test_sudo_preserves_container_uid_and_bind_ip_without_secrets(self):
        env = dict(LAN_IP='10.10.4.220',VIEWER_UID='1000',VIEWER_GID='1000',TOKEN='private')
        args = deploy.docker_invocation(['docker','compose','up'], ['sudo','/usr/bin/docker'], env)
        self.assertEqual(args[:2], ['sudo','env'])
        self.assertIn('VIEWER_UID=1000', args)
        self.assertIn('LAN_IP=10.10.4.220', args)
        self.assertNotIn('private', str(args))

    def test_broken_existing_executable_is_not_overwritten(self):
        with patch.object(deploy, 'find_tool', return_value='/usr/bin/ffmpeg'), patch.object(deploy, 'probe_command', return_value=result(1)), patch.object(deploy.Prerequisites, 'install') as install:
            with self.assertRaisesRegex(deploy.SetupError, 'left unchanged'): deploy.Prerequisites().ensure_tool('ffmpeg',['ffmpeg'],'-version')
            install.assert_not_called()

    def test_client_only_installation_gets_missing_local_engine(self):
        installed = False
        def probe(argv):
            if 'info' in argv: return result(0 if installed else 1)
            if 'context' in argv: return result(stdout='unix:///var/run/docker.sock')
            if 'show' in argv: return result(stdout='not-found')
            return result()
        def install(packages):
            nonlocal installed
            installed = True
        with patch.dict(deploy.os.environ, {'DOCKER_HOST':''}), patch.object(deploy, 'find_tool', side_effect=lambda name: '/usr/bin/'+name), patch.object(deploy, 'probe_command', side_effect=probe), patch.object(deploy.Prerequisites, 'install', side_effect=install) as install_mock:
            self.assertEqual(deploy.Prerequisites().docker_access('docker'), ['docker'])
            install_mock.assert_called_once_with(['docker-ce','docker.io'])


if __name__ == '__main__': unittest.main()
