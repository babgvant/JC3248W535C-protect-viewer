#!/usr/bin/env python3
"""Manage the Pi's Docker deployment using Python's standard library only."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gateway'))
from config import load_config


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
    parser.add_argument('action', choices=['init','migrate','check','up','update','restart','down','status','logs'])
    parser.add_argument('--lan-ip', help='Pi LAN IPv4 address for init/migrate')
    parser.add_argument('--override', type=Path, action='append', default=[], help='additional Compose file, repeatable')
    args = parser.parse_args(argv)
    path = ROOT / 'gateway/config.json'
    try:
        if args.action == 'init': initialize(path, args.lan_ip); return 0
        if args.action == 'migrate': migrate(path, args.lan_ip); return 0
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
        result = subprocess.run(compose_command(args.action, args.override), cwd=ROOT, env=env, check=False)
        if result.returncode:
            print('Docker operation failed. Inspect status/logs; existing configuration was preserved.', file=sys.stderr)
        return result.returncode
    except ValueError as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError):
        # Avoid tracebacks that might echo malformed secret input.
        print('Deployment failed: check config format, required fields, private permissions, Docker access, and existing backup/temp files.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__': raise SystemExit(main())
