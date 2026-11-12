"""Shared validation for the gateway, deployment manager and probe tool."""
import ipaddress
import json
import re
from pathlib import Path


def load_config(path):
    try:
        cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        raise ValueError('configuration must be valid JSON') from None
    if not isinstance(cfg, dict): raise ValueError('configuration must be an object')
    if 'token_file' in cfg: raise ValueError('legacy token_file: run deploy.py migrate')
    token = cfg.get('token')
    if not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii() or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in token):
        raise ValueError('token must contain 32..128 printable ASCII characters without whitespace')
    if 'lan_ip' in cfg:
        try: address = ipaddress.IPv4Address(cfg['lan_ip'])
        except (ValueError, TypeError): raise ValueError('lan_ip must be a specific IPv4 LAN address') from None
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError('lan_ip must be a specific IPv4 LAN address')
    cameras = cfg.get('cameras')
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 16:
        raise ValueError('configure 1..16 cameras')
    ids = set()
    for c in cameras:
        if not isinstance(c, dict): raise ValueError('camera must be an object')
        if 'url_file' in c: raise ValueError('legacy url_file: run deploy.py migrate')
        c.setdefault('fps', 10); c.setdefault('quality', 5)
        c.setdefault('mode', 'fit'); c.setdefault('rotation', 0); c.setdefault('decoder', 'software')
        c.setdefault('hw_download', False)
        if not isinstance(c['hw_download'], bool): raise ValueError('hw_download must be boolean')
        c.setdefault('source_tls_verify', True)
        if not isinstance(c['source_tls_verify'], bool): raise ValueError('source_tls_verify must be boolean')
        camera_id = c.get('id')
        if not isinstance(camera_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', camera_id) or camera_id in ids:
            raise ValueError('invalid or duplicate camera ID')
        ids.add(camera_id)
        if type(c['fps']) is not int or type(c['quality']) is not int or not 5 <= c['fps'] <= 15 or not 2 <= c['quality'] <= 15 or c['mode'] not in ('fit', 'fill') or type(c['rotation']) is not int or c['rotation'] not in (0,90,180,270):
            raise ValueError('invalid output profile')
        if not isinstance(c['decoder'], str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,64}', c['decoder']):
            raise ValueError('invalid decoder')
        name = c.get('name')
        if not isinstance(name, str) or not name.isascii() or not 1 <= len(name) <= 31 or not name.isprintable():
            raise ValueError('use 1..31 printable ASCII characters for display names')
        url = c.get('url')
        if not isinstance(url, str) or not url.startswith(('rtsp://','rtsps://')) or any(c.isspace() or ord(c)<32 for c in url):
            raise ValueError('expected RTSP(S) URL without whitespace')
    for key, default, low, high in [('idle_seconds',5,0,60),('max_workers',2,1,8),('max_clients',8,1,32)]:
        cfg.setdefault(key, default)
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError('invalid resource limits')
    return cfg
