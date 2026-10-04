"""Local acquisition client. Installation credentials never cross the browser API."""
from __future__ import annotations
from ...timeout_settings import network_timeout

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from filelock import FileLock
from flask import jsonify, request
import requests

from ... import config as cfg
from ..admin import is_admin, require_admin
from ..install import MAX_TOTAL_BYTES, _entry_relpath
from ..storage import managed_path, _write_bytes
from .client import StoreError, StoreNotConfigured

MAX_RESPONSE = 256 * 1024
MAX_ERROR_RESPONSE = 4096
_SECRET = re.compile(r'[A-Za-z0-9_-]{43}\Z')
_ERROR_CODE = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{1,63}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def _origin(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise StoreError('The commerce origin is invalid.')
    try:
        parsed = urlsplit(value)
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback if parsed.hostname else False
        except ValueError:
            local = False
        if (not parsed.hostname or parsed.username or parsed.password or parsed.port == 0
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or ':' == parsed.netloc[-1:]
                or '\\' in value or any(ord(c) < 33 for c in value)
                or parsed.scheme != 'https' and not (parsed.scheme == 'http' and local)):
            raise ValueError
        return value.rstrip('/')
    except (ValueError, TypeError) as exc:
        raise StoreError('Commerce requires an HTTPS origin; only literal loopback test servers may use HTTP.') from exc


def load_commerce_config():
    path = Path(os.environ.get('LDS_STORE_COMMERCE_CONFIG') or cfg.REPO_ROOT / 'store/commerce.json')
    if not path.is_file():
        raise StoreNotConfigured('Purchases and license activation are not connected on this installation.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return {'service_url': _origin(value['service_url']), 'checkout_origin': _origin(value['checkout_origin'])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StoreError('The commerce connection configuration is invalid.') from exc


class ServiceRefused(StoreError):
    """The service answered with an error. ``code`` is its fixed ``{error}``
    code when the small JSON body could be read, otherwise None."""

    def __init__(self, message, status, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def _error_code(response):
    try:
        raw = bytearray()
        for chunk in response.iter_content(chunk_size=MAX_ERROR_RESPONSE):
            raw.extend(chunk)
            if len(raw) > MAX_ERROR_RESPONSE:
                return None
        code = json.loads(raw).get('error')
        return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None
    except (requests.RequestException, ValueError, TypeError, AttributeError, RecursionError):
        return None


def _dpapi(value, *, decrypt=False):
    """Windows user-bound protection; failure never falls back to cleartext."""
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    operation = getattr(ctypes.windll.crypt32, 'CryptUnprotectData' if decrypt else 'CryptProtectData')
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise StoreError('The local commerce credential could not be protected or opened.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        release = ctypes.windll.kernel32.LocalFree
        release.argtypes = [ctypes.c_void_p]
        release.restype = ctypes.c_void_p
        release(output.data)


class CommerceClient:
    def __init__(self, config=None):
        self.config = config or load_commerce_config()
        self.origin = _origin(self.config['service_url'])
        self.checkout_origin = _origin(self.config['checkout_origin'])
        identity = hashlib.sha256((self.origin + '\0' + self.checkout_origin).encode()).hexdigest()
        self.folder = managed_path(cfg.data_dir(), 'plugin-store', 'commerce', identity)

    def _response(self, method, path, *, data=None, credential=None, grant=None):
        # Paths are selected by this client; no URL from an API body is fetched.
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'LDS-Store-Commerce/1'}
        if credential:
            headers['Authorization'] = 'Bearer ' + credential
        if grant:
            headers['X-LDS-Download-Grant'] = grant
        session = requests.Session()
        session.trust_env = False  # Neither .netrc nor environment proxies supply credentials.
        try:
            response = session.request(method, self.origin + path, json=data, headers=headers,
                                       timeout=network_timeout((10, 30)), stream=True, allow_redirects=False)
            if response.status_code not in {200, 201}:
                code = _error_code(response)
                response.close()
                raise ServiceRefused('The commerce service refused the request or is unavailable. Installed plugins '
                                     'continue to work.', response.status_code, code)
            return session, response
        except requests.RequestException:
            session.close()
            raise StoreError('The commerce service could not be reached. Installed plugins continue to work.') from None
        except Exception:
            session.close()
            raise

    def _json(self, method, path, *, data=None, credential=None):
        session, response = self._response(method, path, data=data, credential=credential)
        try:
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if len(raw) + len(chunk) > MAX_RESPONSE:
                    raise StoreError('The commerce service response exceeded its supported size.')
                raw.extend(chunk)
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (requests.RequestException, ValueError, TypeError, RecursionError):
            raise StoreError('The commerce service returned an invalid response.') from None
        finally:
            response.close()
            session.close()

    def credential(self, *, enroll=False):
        self.folder.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            os.chmod(self.folder, 0o700)
        path = managed_path(self.folder, 'installation.json')
        with FileLock(str(managed_path(self.folder, 'installation.lock')), timeout=30):
            if path.exists():
                try:
                    if path.stat().st_nlink != 1 or path.stat().st_size > 16384:
                        raise ValueError
                    if os.name != 'nt':
                        os.chmod(path, 0o600)
                    encoded = json.loads(path.read_bytes())
                    if encoded.get('protection') == 'dpapi':
                        value = json.loads(_dpapi(base64.b64decode(encoded['value'], validate=True), decrypt=True))
                    elif os.name != 'nt' and encoded.get('protection') == 'file_permissions':
                        value = encoded['value']
                    else:
                        raise ValueError
                    return self._credential_value(value)
                except (OSError, ValueError, KeyError, TypeError):
                    raise StoreError('The saved commerce connection could not be opened. Keep its backup; it was not replaced.') from None
            if not enroll:
                return None
            value = self._json('POST', '/v1/installations', data={})
            credential = self._credential_value(value)
            saved = {key: value[key] for key in ('client_id', 'client_secret')}
            if os.name == 'nt':
                encoded = {'protection': 'dpapi', 'value': base64.b64encode(_dpapi(json.dumps(saved).encode())).decode()}
            else:
                encoded = {'protection': 'file_permissions', 'value': saved}
            _write_bytes(path, json.dumps(encoded).encode())
            if os.name != 'nt':
                os.chmod(path, 0o600)
            return credential

    @staticmethod
    def _credential_value(value):
        if (not isinstance(value, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('client_id', '')))
                or not _SECRET.fullmatch(str(value.get('client_secret', '')))):
            raise StoreError('The commerce installation credential is invalid.')
        return value['client_id'] + '.' + value['client_secret']

    def library(self):
        credential = self.credential()
        if credential is None:
            return {'status': 'not_connected', 'items': [], 'message': 'Connect this installation to view your purchases.'}
        value = self._json('GET', '/v1/library', credential=credential)
        items = value.get('items')
        if not isinstance(items, list) or len(items) > 1000:
            raise StoreError('The commerce library response is invalid.')
        return {'status': 'ready', 'items': [self._item(item) for item in items]}

    @staticmethod
    def _item(item):
        if (not isinstance(item, dict) or not _ID.fullmatch(str(item.get('plugin_id', '')))
                or item.get('status') not in {'active', 'revoked', 'verification_required'}
                or item.get('acquisition') not in {'all_published', 'through_purchase_date', None}
                or item.get('updates') not in {'included', 'none', 'while_active', None}
                or item.get('offline') != 'installed_continues'):
            raise StoreError('The commerce library response is invalid.')
        expiry = item.get('valid_until')
        if expiry is not None:
            _timestamp(expiry)
        return {key: item.get(key) for key in ('plugin_id', 'status', 'acquisition', 'updates', 'offline', 'valid_until')}

    def activate(self, plugin_id, license_key):
        _plugin(plugin_id)
        if not isinstance(license_key, str) or not 1 <= len(license_key) <= 2048:
            raise StoreError('Enter a valid license key.')
        value = self._json('POST', '/v1/licenses/activate', data={'plugin_id': plugin_id, 'license_key': license_key},
                           credential=self.credential(enroll=True))
        result = self._item(value)
        if result['plugin_id'] != plugin_id:
            raise StoreError('The license response names a different plugin.')
        return result

    def checkout(self, plugin_id):
        _plugin(plugin_id)
        value = self._json('POST', '/v1/checkouts', data={'plugin_id': plugin_id}, credential=self.credential(enroll=True))
        url = value.get('hosted_checkout')
        try:
            parsed = urlsplit(url)
            if (not isinstance(url, str) or len(url) > 4096 or parsed.fragment or parsed.username or parsed.password
                    or '\\' in url or any(ord(c) < 33 for c in url)
                    or _origin(parsed.scheme + '://' + parsed.netloc) != self.checkout_origin):
                raise ValueError
        except (TypeError, ValueError):
            raise StoreError('The checkout address is outside the configured payment service.') from None
        return {'hosted_checkout': url}

    def download(self, release, target_info, destination):
        """Grant never replaces TUF metadata: caller supplies authenticated length/hash."""
        return self.download_target(release.manifest.id, release.manifest.version, release.target,
                                    target_info, destination)

    def download_target(self, product_id, version, target, target_info, destination, *,
                        maximum=MAX_TOTAL_BYTES, on_progress=None):
        """Download one granted artifact whose length and SHA-256 come from TUF.

        ``product_id`` is a plugin identifier or ``lds_core``. The grant and the
        server response can never widen the authenticated length or hash."""
        digest = target_info.hashes.get('sha256')
        if (not isinstance(target, str) or _entry_relpath(target) != target
                or any(c in target for c in ('%', '?', '#', '\\'))
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or type(target_info.length) is not int or not 0 < target_info.length <= maximum
                or getattr(target_info, 'path', target) != target):
            raise StoreError('This release has no valid authenticated artifact identity.')
        credential = self.credential()
        if credential is None:
            raise StoreError('Connect this installation and acquire the plugin before downloading it.')
        body = {'plugin_id': product_id, 'version': version, 'target': target, 'sha256': digest}
        value = self._json('POST', '/v1/grants', data=body, credential=credential)
        path = '/v1/artifacts/' + quote(target, safe='/')
        if (value.get('download_url') != self.origin + path or value.get('target') != target
                or value.get('sha256') != digest or not _SECRET.fullmatch(str(value.get('token', '')))
                or not 0 < _timestamp(value.get('expires_at')) - datetime.now(timezone.utc).timestamp() <= 360):
            raise StoreError('The artifact grant does not match this authenticated release.')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.commerce-', dir=destination.parent)
        try:
            session, response = self._response('GET', path, credential=credential, grant=value['token'])
            try:
                size, hasher = 0, hashlib.sha256()
                with os.fdopen(descriptor, 'wb') as stream:
                    descriptor = None
                    for chunk in response.iter_content(chunk_size=65536):
                        size += len(chunk)
                        if size > target_info.length:
                            raise StoreError('The downloaded artifact exceeds its authenticated length.')
                        stream.write(chunk)
                        hasher.update(chunk)
                        if on_progress:
                            on_progress(size, target_info.length)
                    if size != target_info.length or hasher.hexdigest() != digest:
                        raise StoreError('The downloaded artifact failed its authenticated hash or length check.')
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                response.close()
                session.close()
            os.replace(temporary, destination)
            return destination
        except requests.RequestException:
            raise StoreError('The acquired artifact could not be downloaded. Existing packages were kept.') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise StoreError('The commerce response contains an invalid expiry.') from None


def _plugin(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StoreError('Select a valid plugin.')


def download_release(release, target_info, destination, *, provider=''):
    """The SOURCE catalog selects the grant service, never a price label alone."""
    if provider == 'patreon':
        from .patreon import PatreonClient
        return PatreonClient().download(release, target_info, destination)
    return CommerceClient().download(release, target_info, destination)


def register_commerce_routes(bp):
    """Register before the parent blueprint is attached; host CSRF stays enabled."""
    def invoke(operation):
        if (denied := require_admin()) is not None:
            return denied
        try:
            result = operation(CommerceClient())
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except StoreNotConfigured as exc:
            return jsonify(status='not_configured', items=[], message=str(exc), can_manage=is_admin()), 200
        except (StoreError, OSError):
            return jsonify(error='The commerce operation could not be completed. Check the connection or license and retry.',
                           status='unavailable', items=[]), 409

    @bp.get('/commerce/library')
    def commerce_library():
        return invoke(lambda client: {**client.library(), 'can_manage': is_admin()})

    @bp.post('/commerce/connect')
    def commerce_connect():
        return invoke(lambda client: {'ok': bool(client.credential(enroll=True))})

    def payload(fields):
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or set(value) != set(fields):
            raise StoreError('The commerce request is invalid.')
        return value

    @bp.post('/commerce/activate')
    def commerce_activate():
        def activate(client):
            value = payload(('plugin_id', 'license_key'))
            return client.activate(value['plugin_id'], value['license_key'])
        return invoke(activate)

    @bp.post('/commerce/checkout')
    def commerce_checkout():
        return invoke(lambda client: client.checkout(payload(('plugin_id',))['plugin_id']))
