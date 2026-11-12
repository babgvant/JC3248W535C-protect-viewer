#!/usr/bin/env python3
"""Manage the Pi's Docker deployment using Python's standard library only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gateway'))
from config import load_config


class SetupError(Exception):
    """An actionable prerequisite failure, with no configuration contents."""


def find_tool(name):
    found = shutil.which(name)
    if found: return found
    if sys.platform.startswith('linux'):
        for directory in ('/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'):
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK): return str(candidate)
    return None


def probe_command(argv):
    try:
        env = os.environ.copy(); env['LC_ALL'] = 'C'
        return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              env=env, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        raise SetupError('prerequisite check timed out; check Docker/service responsiveness') from None


class Prerequisites:
    def __init__(self):
        self.updated = False

    def privileged(self, argv):
        if hasattr(os, 'geteuid') and os.geteuid() == 0: return argv
        sudo = find_tool('sudo')
        if not sudo: raise SetupError('sudo is required to install missing packages or start Docker; run setup as an administrator')
        return [sudo] + argv

    def run_admin(self, argv):
        result = subprocess.run(self.privileged(argv), check=False)
        if result.returncode:
            raise SetupError('prerequisite installation/service command failed; see its output above, correct the issue, and rerun')

    def apt_update(self):
        if self.updated: return
        if not sys.platform.startswith('linux') or not find_tool('apt-get'):
            raise SetupError('automatic prerequisite installation supports Debian/Raspberry Pi OS/Ubuntu with apt; install missing tools manually on other systems')
        self.run_admin([find_tool('apt-get'), '-o', 'Acquire::Retries=3', 'update'])
        self.updated = True

    def install(self, choices):
        # Use existing package sources, preferring the existing Docker distribution.
        # --no-remove prevents apt from replacing a conflicting Docker/container stack.
        self.apt_update()
        for package in choices:
            result = probe_command([find_tool('apt-cache') or 'apt-cache', 'policy', package])
            import re
            candidate = re.search(r'Candidate:\s*(\S+)', result.stdout)
            if not candidate or candidate.group(1) == '(none)': continue
            if package == 'docker-compose' and not re.match(r'(?:\d+:)?[2-9]\d*\.', candidate.group(1)):
                continue  # Never install legacy Python Compose v1.
            print(f'Installing missing prerequisite: {package}', flush=True)
            self.run_admin([find_tool('apt-get'), '-o', 'DPkg::Lock::Timeout=120', '-o', 'Acquire::Retries=3',
                            'install', '-y', '--no-upgrade', '--no-remove',
                            '--no-install-recommends', package])
            return
        raise SetupError('no compatible prerequisite package in configured apt repositories: ' + ', '.join(choices))

    def ensure_tool(self, name, packages, version_flag='--version'):
        tool = find_tool(name)
        if tool:
            if probe_command([tool, version_flag]).returncode:
                raise SetupError(f'existing {name} executable is not working; it was left unchanged')
            print(f'{name}: already installed; keeping existing installation.', flush=True)
            return tool
        self.install(packages)
        tool = find_tool(name)
        if not tool or probe_command([tool, version_flag]).returncode:
            raise SetupError(f'{name} still unavailable after package installation; check package output and PATH')
        return tool

    def ensure_plugin(self, docker, name, packages):
        if probe_command([docker, name, 'version']).returncode == 0:
            print(f'Docker {name}: already installed; keeping existing installation.', flush=True)
            return
        self.install(packages)
        if probe_command([docker, name, 'version']).returncode:
            raise SetupError(f'Docker {name} plugin is still unavailable after installation')

    def docker_access(self, docker):
        info = [docker, 'info', '--format', '{{.ServerVersion}}']
        if probe_command(info).returncode == 0: return [docker]
        endpoint = os.environ.get('DOCKER_HOST')
        if not endpoint:
            context = probe_command([docker, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
            if context.returncode:
                raise SetupError('Docker context cannot be inspected; check docker context ls and Docker configuration')
            endpoint = context.stdout.strip()
        if endpoint not in ('unix:///var/run/docker.sock', 'unix:///run/docker.sock'):
            raise SetupError('configured Docker endpoint is unavailable; remote/rootless contexts are preserved, check docker info')
        systemctl = find_tool('systemctl')
        if not systemctl: raise SetupError('local Docker daemon unavailable and systemctl not found; start your Docker daemon and rerun')
        state = probe_command([systemctl, 'show', 'docker.service', '--property=LoadState', '--value'])
        if state.stdout.strip() == 'not-found':
            self.install(['docker-ce', 'docker.io'])
        if probe_command([systemctl, 'is-active', '--quiet', 'docker.service']).returncode:
            print('Starting the local Docker service.', flush=True)
            self.run_admin([systemctl, 'enable', '--now', 'docker.service'])
        if probe_command(info).returncode == 0: return [docker]
        # Existing login sessions need not be restarted or added to a privileged group.
        elevated = self.privileged(info)
        print('Checking local Docker access through sudo (no logout required).', flush=True)
        result = subprocess.run(elevated, stdout=subprocess.DEVNULL, check=False)
        if result.returncode: raise SetupError('local Docker daemon is still inaccessible; inspect sudo docker info and systemctl status docker')
        return self.privileged([docker])

    def ensure(self, install=True):
        if not install:
            docker = find_tool('docker')
            if not docker: raise SetupError('Docker is missing; run python3 tools/deploy.py setup or up')
            return self.docker_access(docker)
        # Debian 13 splits docker.io (daemon) from docker-cli (a recommendation).
        # Install the CLI explicitly; docker_access separately ensures a local
        # engine only when required. Older Debian can fall back to bundled docker.io.
        docker = self.ensure_tool('docker', ['docker-ce-cli', 'docker-cli', 'docker.io'])
        self.ensure_plugin(docker, 'compose', ['docker-compose-plugin', 'docker-compose-v2', 'docker-compose'])
        self.ensure_plugin(docker, 'buildx', ['docker-buildx-plugin', 'docker-buildx'])
        self.ensure_tool('ffmpeg', ['ffmpeg'], '-version')
        self.ensure_tool('ffprobe', ['ffmpeg'], '-version')
        access = self.docker_access(docker)
        # Sudo can have different per-user plugin discovery; verify the actual CLI.
        for plugin, packages in [('compose',['docker-compose-plugin','docker-compose-v2','docker-compose']),
                                 ('buildx',['docker-buildx-plugin','docker-buildx'])]:
            if probe_command(access + [plugin, 'version']).returncode:
                print(f'Docker {plugin}: installing the missing system-wide plugin for daemon access.', flush=True)
                self.install(packages)
                if probe_command(access + [plugin, 'version']).returncode:
                    raise SetupError(f'Docker {plugin} is still unavailable for the account accessing the daemon')
        print('Prerequisites ready.', flush=True)
        return access


def docker_invocation(command, runtime, env):
    if len(runtime) == 1: return runtime + command[1:]
    # Preserve the unprivileged runtime UID and bind address through sudo, without
    # passing source URLs/tokens or changing the configuration's ownership.
    return runtime[:-1] + ['env'] + [f'{k}={env[k]}' for k in ('LAN_IP','VIEWER_UID','VIEWER_GID')] + runtime[-1:] + command[1:]


def private_write(path, data):
    # Exclusive creation prevents accidentally replacing an existing configuration.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    if hasattr(os, 'getuid') and os.getuid() == 0:
        os.chown(path, 10001, 10001)


def initialize(path, lan_ip):
    if path.exists(): raise ValueError('configuration already exists; edit it instead of reinitializing')
    if not lan_ip: raise ValueError('init requires --lan-ip with the Pi LAN address')
    data = json.loads((ROOT / 'gateway/config.example.json').read_text(encoding='utf-8'))
    data['lan_ip'] = lan_ip
    data['token'] = secrets.token_hex(32)
    # Validate before writing, without printing the generated token.
    import ipaddress
    address = ipaddress.IPv4Address(lan_ip)
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError('use a specific LAN IPv4 address')
    private_write(path, data)
    print('Created gateway/config.json with a generated viewer token and private permissions.')
    print('Edit its cameras/URLs and LAN address, then run: python3 tools/deploy.py up')


def migrate(path, lan_ip):
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError: raise ValueError('configuration must be valid JSON') from None
    if 'token_file' not in data and not any('url_file' in c for c in data.get('cameras', [])):
        raise ValueError('configuration already uses inline secrets; no migration needed')
    def secret_file(value):
        p = Path(value)
        # Translate the old container secret mount to its host location.
        if str(value).startswith('/run/secrets/'):
            p = path.parent / 'secrets' / Path(value).name
        elif not p.is_absolute(): p = path.parent / p
        return p.read_text(encoding='utf-8').strip()
    if 'token_file' in data: data['token'] = secret_file(data.pop('token_file'))
    for c in data.get('cameras', []):
        if 'url_file' in c: c['url'] = secret_file(c.pop('url_file'))
    if lan_ip: data['lan_ip'] = lan_ip
    if not data.get('lan_ip'): raise ValueError('migration requires --lan-ip')
    temporary = path.with_name(path.name + '.tmp')
    backup = path.with_name(path.name + '.legacy')
    created = False
    try:
        private_write(temporary, data); created = True
        load_config(temporary)
        private_write(backup, json.loads(path.read_text(encoding='utf-8')))
        os.replace(temporary, path)
    finally:
        if created: temporary.unlink(missing_ok=True)
    print('Migrated gateway/config.json; saved the original as config.json.legacy.')
    print('Existing secret files were preserved. Run up to recreate the container with the new mount.')


def compose_command(action, overrides=()):
    commands = {
        'up': ['up', '-d', '--build', '--force-recreate', '--wait', '--wait-timeout', '90', 'gateway'],
        'update': ['up', '-d', '--build', '--force-recreate', '--wait', '--wait-timeout', '90', 'gateway'],
        'restart': ['up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '90', 'gateway'],
        'down': ['down', '--remove-orphans'],
        'status': ['ps'],
        'logs': ['logs', '--tail', '100', '--follow', 'gateway'],
    }
    base = ['docker', 'compose', '--project-directory', str(ROOT), '-f', str(ROOT / 'compose.yaml')]
    for override in overrides: base += ['-f', str(override.resolve())]
    return base + commands[action]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','migrate','check','setup','up','update','restart','down','status','logs'])
    parser.add_argument('--lan-ip', help='Pi LAN IPv4 address for init/migrate')
    parser.add_argument('--override', type=Path, action='append', default=[], help='additional Compose file, repeatable')
    args = parser.parse_args(argv)
    path = ROOT / 'gateway/config.json'
    operation = {'init': 'creating configuration', 'migrate': 'migrating configuration'}.get(args.action, 'reading gateway/config.json')
    try:
        if args.action == 'init': initialize(path, args.lan_ip); return 0
        if args.action == 'migrate': migrate(path, args.lan_ip); return 0
        if args.action == 'setup':
            Prerequisites().ensure()
            return 0
        env = os.environ.copy()
        if args.action in ('check','up','update','restart'):
            cfg = load_config(path)
            if not cfg.get('lan_ip'): raise ValueError('set lan_ip in gateway/config.json')
            if cfg['token'].startswith('REPLACE_') or any('PROTECT_HOST' in c['url'] for c in cfg['cameras']):
                raise ValueError('replace the example token and camera URLs before deployment')
            if os.name == 'posix' and path.stat().st_mode & 0o077:
                raise ValueError('configuration contains secrets; run chmod 600 gateway/config.json')
            env['LAN_IP'] = cfg['lan_ip']
            if args.action == 'check':
                print(f"Configuration valid: {len(cfg['cameras'])} cameras; secrets omitted.")
                return 0
        else:
            # Stop/status/logs must still work when configuration is broken or missing.
            env['LAN_IP'] = env.get('LAN_IP', '127.0.0.1')
        uid = os.getuid() if hasattr(os, 'getuid') else 10001
        gid = os.getgid() if hasattr(os, 'getgid') else 10001
        env['VIEWER_UID'] = str(uid or 10001)
        env['VIEWER_GID'] = str(gid if uid else 10001)
        operation = 'checking deployment prerequisites'
        runtime = Prerequisites().ensure(install=args.action in ('up','update','restart'))
        operation = 'starting Docker Compose'
        result = subprocess.run(docker_invocation(compose_command(args.action, args.override), runtime, env), cwd=ROOT, env=env, check=False)
        if result.returncode:
            print('Docker operation failed. Inspect status/logs; existing configuration was preserved.', file=sys.stderr)
        return result.returncode
    except (ValueError, SetupError) as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        return 1
    except FileNotFoundError:
        if operation == 'starting Docker Compose':
            message = 'Docker executable not found in PATH. Run docker --version and docker compose version on the Pi; Docker Engine and the Compose plugin must be installed and accessible.'
        elif operation == 'reading gateway/config.json':
            message = 'gateway/config.json was not found. Run python3 tools/deploy.py init --lan-ip YOUR_PI_LAN_IP, then edit the camera URLs.'
        else:
            message = f'a required file is missing while {operation}. For migration, confirm the original secret files still exist.'
        print(f'Deployment failed: {message}', file=sys.stderr)
        return 1
    except PermissionError:
        if operation == 'starting Docker Compose':
            message = 'permission denied executing Docker. Check the Docker executable permissions. Docker daemon access errors are reported separately by Docker itself.'
        else:
            message = f'permission denied while {operation}. Check ls -l gateway/config.json and use the account that owns the configuration; its permissions should be 600.'
        print(f'Deployment failed: {message}', file=sys.stderr)
        return 1
    except IsADirectoryError:
        print('Deployment failed: expected a configuration/secret file but found a directory. Check gateway/config.json and any legacy secret paths.', file=sys.stderr)
        return 1
    except FileExistsError:
        print('Deployment failed: a configuration, migration backup, or temporary file already exists. Existing files were preserved; inspect them before retrying.', file=sys.stderr)
        return 1
    except OSError as error:
        # Report the operation and errno, never exception text or secret contents.
        print(f'Deployment failed: OS error {error.errno} while {operation}. Check filesystem access and Docker availability.', file=sys.stderr)
        return 1
    except (KeyError, TypeError):
        print('Deployment failed: a configuration field is missing or has the wrong type. Compare the structure with gateway/config.example.json; do not share stream URLs or tokens.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__': raise SystemExit(main())
