"""TUF verifies remote metadata and bytes; the app owns the initial trust root.

Configuration is installed by the operator, never accepted from the catalog or
an uploaded ZIP. Each root/origin pair has its own rollback-protected metadata
cache. Expiration can stop new downloads, never the installed application.
"""
from __future__ import annotations
from ...timeout_settings import network_timeout

import hashlib
import ipaddress
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests
from filelock import FileLock
from tuf.api.exceptions import DownloadHTTPError
from tuf.ngclient import Updater
from tuf.ngclient.fetcher import FetcherInterface

from ... import config as cfg
from ..install import MAX_TOTAL_BYTES, _entry_relpath
from ..official import OFFICIAL_IDS
from ..manifest import EXTERNAL_ID
from ..storage import managed_path

_LOCK = threading.RLock()
CATALOG_TARGET = 'catalog.json'
MAX_CATALOG_BYTES = 2 * 1024 * 1024
# Publisher namespaces no source may be granted whole. ``lds`` publishes the
# first-party products, which only an explicit identifier grants (manifest.py,
# official.py); a Windows device name cannot start a plugin folder or a package
# target (install.py, store/tools/repository.py).
RESERVED_NAMESPACES = frozenset({'lds', 'aux', 'con', 'nul', 'prn',
                                 *(f'com{i}' for i in range(10)), *(f'lpt{i}' for i in range(10))})


def namespace_of(plugin_id):
    """The publisher part of an external ``publisher.plugin`` identifier, else None."""
    if isinstance(plugin_id, str) and EXTERNAL_ID.fullmatch(plugin_id):
        return plugin_id.split('.', 1)[0]
    return None


def valid_namespace(value):
    """The publisher part of EXTERNAL_ID (``<value>.plugin`` is an external
    identifier, so both grammars cannot drift apart), and not reserved."""
    return (isinstance(value, str) and namespace_of(value + '.plugin') == value
            and value not in RESERVED_NAMESPACES)


class StoreError(ValueError):
    """A public, path- and credential-free reason to stop an operation."""


class StoreNotConfigured(StoreError):
    pass


def _base_url(value):
    if not isinstance(value, str):
        raise StoreError('The store URL is invalid.')
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Validate malformed/out-of-range ports before opening a connection.
    except ValueError as exc:
        raise StoreError('The store URL is invalid.') from exc
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or '%' in parsed.path or '\\' in value or any(ord(c) < 33 for c in value)
            or '..' in parsed.path.split('/')):
        raise StoreError('The store URL is invalid.')
    local = False
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
        raise StoreError('A store requires HTTPS. Only loopback test servers may use HTTP.')
    return value.rstrip('/') + '/'


@dataclass(frozen=True)
class StoreConfig:
    metadata_url: str
    target_url: str
    root: bytes
    official_ids: frozenset
    external_ids: frozenset = frozenset()
    scoped_ids: frozenset = frozenset()
    additional_catalog_targets: tuple = ()
    # Set only by the shipped Patreon configuration (store/patreon.py). It
    # selects the grant service for this source's paid releases; a catalog
    # cannot claim it for itself.
    provider: str = ''
    # Patreon only as well: publisher namespaces whose external identifiers
    # this source serves without listing them, except the identifiers an
    # operator source (sources.json) claims.
    external_namespaces: frozenset = frozenset()
    excluded_ids: frozenset = frozenset()

    def __post_init__(self):
        if self.provider not in ('', 'patreon'):
            raise StoreError('The store trust configuration cannot be verified.')
        object.__setattr__(self, 'metadata_url', _base_url(self.metadata_url))
        object.__setattr__(self, 'target_url', _base_url(self.target_url))
        if (not isinstance(self.root, bytes) or not 0 < len(self.root) <= 512000
                or not isinstance(self.official_ids, (set, frozenset))
                or any(pid not in OFFICIAL_IDS for pid in self.official_ids)):
            raise StoreError('The store trust configuration cannot be verified.')
        object.__setattr__(self, 'official_ids', frozenset(self.official_ids))
        if (not isinstance(self.external_ids, (set, frozenset))
                or any(not isinstance(pid, str) or not EXTERNAL_ID.fullmatch(pid) for pid in self.external_ids)):
            raise StoreError('External catalog permissions must name exact publisher.plugin identifiers.')
        object.__setattr__(self, 'external_ids', frozenset(self.external_ids))
        if (not isinstance(self.scoped_ids, (set, frozenset))
                or (self.scoped_ids and self.scoped_ids != self.official_ids | self.external_ids)):
            raise StoreError('Catalog permissions must match its selected plugin identifiers.')
        object.__setattr__(self, 'scoped_ids', frozenset(self.scoped_ids))
        namespaces, excluded = self.external_namespaces, self.excluded_ids
        if (not isinstance(namespaces, (set, frozenset)) or not isinstance(excluded, (set, frozenset))
                or (namespaces and self.provider != 'patreon')
                or any(not valid_namespace(name) for name in namespaces)
                or any(namespace_of(pid) not in namespaces or pid in self.selected_ids for pid in excluded)):
            raise StoreError('Catalog namespaces must name unreserved publishers and exclude only their own identifiers.')
        object.__setattr__(self, 'external_namespaces', frozenset(namespaces))
        object.__setattr__(self, 'excluded_ids', frozenset(excluded))
        targets = self.additional_catalog_targets
        if (not isinstance(targets, (tuple, list)) or len(targets) > 4
                or any(not isinstance(name, str)
                       or not re.fullmatch(r'catalog-[a-z0-9][a-z0-9_.-]{0,80}\.json', name)
                       for name in targets) or len(set(targets)) != len(targets)):
            raise StoreError('Additional catalogs must name distinct signed catalog targets.')
        object.__setattr__(self, 'additional_catalog_targets', tuple(targets))

    @property
    def selected_ids(self):
        return self.scoped_ids or self.external_ids

    @property
    def scoped(self):
        """False for a catalog its root alone authorizes (the public one)."""
        return bool(self.selected_ids or self.external_namespaces)

    def serves(self, plugin_id):
        """Listed, or an external identifier of a served namespace that no
        operator source claims. A namespace never reaches an LDS identifier."""
        return plugin_id in self.selected_ids or (
            namespace_of(plugin_id) in self.external_namespaces and plugin_id not in self.excluded_ids)

    @property
    def identity(self):
        scope = json.dumps(sorted(self.selected_ids)).encode() if self.selected_ids else b''
        if self.external_namespaces:
            # Absent otherwise: existing sources keep their identity and cache.
            scope += b'\0' + json.dumps({'namespaces': sorted(self.external_namespaces),
                                         'excluded': sorted(self.excluded_ids)}).encode()
        if self.additional_catalog_targets:
            scope += b'\0' + json.dumps(self.additional_catalog_targets).encode()
        return hashlib.sha256(self.root + self.metadata_url.encode() + b'\0' + self.target_url.encode() + scope).hexdigest()


def load_config():
    path = Path(os.environ.get('LDS_STORE_CONFIG') or cfg.REPO_ROOT / 'store' / 'bootstrap.json')
    if not path.is_file():
        raise StoreNotConfigured('The store has not been connected to a trusted catalog yet.')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return _read_config(data, path.parent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StoreError('The store trust configuration cannot be verified.') from exc


def _read_config(data, directory, *, external_ids=frozenset(), scoped_ids=frozenset()):
    try:
        root_path = Path(data['root_path'])
        if not root_path.is_absolute():
            root_path = directory / root_path
        root = root_path.read_bytes()
        ids = data.get('official_ids', [])
        if (not root or len(root) > 512000 or not isinstance(ids, list)
                or any(not isinstance(pid, str) or pid not in OFFICIAL_IDS for pid in ids)):
            raise ValueError('invalid trust configuration')
        return StoreConfig(_base_url(data['metadata_url']), _base_url(data['target_url']), root,
                           frozenset(ids), external_ids, scoped_ids,
                           data.get('additional_catalog_targets', ()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StoreError('The store trust configuration cannot be verified.') from exc


def load_private_configs():
    """Explicit operator roots and scopes; private catalogs cannot grant themselves rights."""
    path = cfg.data_dir() / 'plugin-store' / 'sources.json'
    if not path.exists():
        return []
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError('source list too large')
        sources = json.loads(path.read_text(encoding='utf-8'))['sources']
        if not isinstance(sources, list) or len(sources) > 20:
            raise ValueError('invalid sources')
        result, claimed = [], set()
        for source in sources:
            if 'first_party_ids' in source:
                external, official = source['plugin_ids'], source['first_party_ids']
                if (not isinstance(external, list) or not isinstance(official, list)
                        or any(not isinstance(pid, str) or not EXTERNAL_ID.fullmatch(pid) for pid in external)
                        or 'official_ids' in source):
                    raise ValueError('invalid first-party source permissions')
                # Older clients keep their external scope and ignore this new
                # field until the core update; no temporary catalog outage.
                source = {**source, 'plugin_ids': external + official, 'official_ids': official}
            ids = source['plugin_ids']
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                    or any(not isinstance(pid, str) or not (EXTERNAL_ID.fullmatch(pid) or pid in OFFICIAL_IDS)
                           for pid in ids)
                    or len(set(ids)) != len(ids) or claimed.intersection(ids)):
                raise ValueError('invalid or overlapping source permissions')
            selected = frozenset(ids)
            official = source.get('official_ids', [])
            if (not isinstance(official, list)
                    or any(not isinstance(pid, str) or pid not in OFFICIAL_IDS for pid in official)
                    or len(set(official)) != len(official)
                    or frozenset(official) != selected.intersection(OFFICIAL_IDS)):
                raise ValueError('first-party permissions must be explicit and match the scope')
            if official and load_config().official_ids.intersection(official):
                raise ValueError('a private catalog cannot replace primary first-party products')
            result.append(_read_config(source, path.parent, external_ids=selected.difference(official),
                                       scoped_ids=selected))
            claimed.update(ids)
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StoreError('The private plugin sources configuration cannot be verified.') from exc


def patreon_source(private_sources):
    """The shipped Patreon catalog, minus identifiers an operator source claims.

    ``sources.json`` is an explicit local operator decision, so it keeps the
    identifiers it lists; the Patreon catalog serves the remaining ones. None
    when Patreon is not configured or has nothing left to serve."""
    from .patreon import patreon_plugin_source
    claimed = frozenset(pid for source in private_sources for pid in source.selected_ids)
    try:
        return patreon_plugin_source(claimed)
    except StoreNotConfigured:
        return None


def load_additional_configs():
    """Operator sources first, then Patreon; an invalid Patreon file never hides them."""
    sources = load_private_configs()
    try:
        patreon = patreon_source(sources)
    except StoreError:
        patreon = None
    return sources + ([patreon] if patreon else [])


def config_for_plugins(plugin_ids):
    """None preserves the default Store session and its existing trust root."""
    requested = [plugin_ids] if isinstance(plugin_ids, str) else plugin_ids
    if (not isinstance(requested, (list, tuple)) or not 1 <= len(requested) <= 50
            or any(not isinstance(pid, str) for pid in requested)):
        raise StoreError('Select valid plugin identifiers.')
    sources = load_additional_configs()
    selected = {next((index for index, source in enumerate(sources) if source.serves(pid)), -1)
                for pid in requested}
    if len(selected) > 1:
        raise StoreError('Update plugins from different catalogs separately.')
    index = next(iter(selected), -1)
    return sources[index] if index >= 0 else None


class _Fetcher(FetcherInterface):
    """Public TUF transport: no redirects, credentials or paid download grants."""
    def __init__(self, config):
        self.config = config

    def _fetch(self, url):
        # TUF generates metadata URLs and validated target paths. Enforce the
        # same portable path profile here before requests can normalize a URL.
        normalized = _base_url(url).rstrip('/')
        if normalized != url.rstrip('/'):
            raise StoreError('A store download contains an invalid URL.')
        if not any(url.startswith(base) for base in (self.config.metadata_url, self.config.target_url)):
            raise StoreError('A store download left its configured origin.')
        headers = {'User-Agent': 'LDS-Plugin-Store/1', 'Accept-Encoding': 'identity'}
        with requests.Session() as session:
            # Public metadata/archives must never inherit NETRC credentials,
            # authenticated proxies, cookies or custom CA settings from the host.
            session.trust_env = False
            with session.get(url, headers=headers, stream=True, timeout=network_timeout((10, 30)), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise DownloadHTTPError('The store download failed.', response.status_code)
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk


class StoreSession:
    """One fresh TUF update under the cache lock, never shared across requests."""
    def __init__(self, config=None):
        self.config = config or load_config()
        self.updater = None
        self.cache_lock = None

    def __enter__(self):
        _LOCK.acquire()
        try:
            self.folder = managed_path(cfg.data_dir(), 'plugin-store', 'catalogs', self.config.identity)
            metadata = managed_path(self.folder, 'metadata')
            metadata.mkdir(parents=True, exist_ok=True)
            self.cache_lock = FileLock(str(managed_path(self.folder, 'update.lock')), timeout=30)
            self.cache_lock.acquire()
            self.updater = Updater(str(metadata), self.config.metadata_url,
                                   target_base_url=self.config.target_url,
                                   bootstrap=self.config.root, fetcher=_Fetcher(self.config))
            self.updater.refresh()
            return self
        except Exception as exc:
            if self.cache_lock is not None:
                self.cache_lock.release()
            _LOCK.release()
            raise StoreError('The catalog could not be authenticated or is unavailable. Installed plugins are unaffected.') from exc

    def __exit__(self, *args):
        self.cache_lock.release()
        _LOCK.release()

    def target_info(self, name, *, maximum=MAX_TOTAL_BYTES):
        if (not isinstance(name, str) or _entry_relpath(name) != name
                or any(c in name for c in ('%', '?', '#', '\\'))):
            raise StoreError('The catalog contains an invalid target path.')
        info = self.updater.get_targetinfo(name)
        if info is None or not 0 < info.length <= maximum or 'sha256' not in info.hashes:
            raise StoreError('This release is unavailable or exceeds the supported package size.')
        return info

    def target_destination(self, info):
        # Hash filenames avoid both target path traversal and cache aliasing.
        return managed_path(self.folder, 'targets', info.hashes['sha256'])

    def target(self, name, *, maximum=MAX_TOTAL_BYTES):
        info = self.target_info(name, maximum=maximum)
        dest = self.target_destination(info)
        try:
            if not self.updater.find_cached_target(info, str(dest)):
                self.updater.download_target(info, str(dest))
            return dest, info
        except Exception as exc:
            raise StoreError('The package download failed verification. No installed plugin was changed.') from exc

    def catalog(self):
        catalog = self._catalog_target(CATALOG_TARGET)
        if not self.config.additional_catalog_targets:
            return catalog
        for name in self.config.additional_catalog_targets:
            extra = self._catalog_target(name)
            for value in (catalog, extra):
                if (not isinstance(value, dict) or value.get('schema_version') != 1
                        or not isinstance(value.get('products'), list)):
                    raise StoreError('The catalog format is not supported by this app.')
            catalog = {**catalog, 'products': [*catalog['products'], *extra['products']]}
        return catalog

    def _catalog_target(self, name):
        path, _ = self.target(name, maximum=MAX_CATALOG_BYTES)
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise StoreError('The authenticated catalog is not valid JSON.') from exc
