"""Prepare a consented set of verified packages, with no import during download."""
from __future__ import annotations

import hashlib
import json
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

from packaging.version import Version

from ... import config as cfg
from .. import official, storage
from ..install import inspect_archive, inspect_plugin_zip
from ..loader import external_dir
from .catalog import parse_catalog, plan_digest, resolve
from .client import (StoreError, StoreNotConfigured, StoreSession, config_for_plugins, load_config,
                     load_private_configs, patreon_source)
from .commerce import CommerceClient, download_release
from .patreon import PatreonClient


def installed_manifests(registry):
    return {pid: r.manifest for pid, r in registry.records.items() if r.manifest and r.legacy is None} if registry else {}


def _browse_path(config):
    return storage.managed_path(cfg.data_dir(), 'plugin-store', 'catalogs', config.identity, 'browse.json')


def browse():
    try:
        payload = _browse(load_config())
    except StoreError as exc:
        payload = {'status': 'unavailable', 'checked_at': None, 'message': str(exc), 'products': []}
    messages = [payload['message']] if payload['message'] else []
    try:
        sources = load_private_configs()
    except StoreError as exc:
        sources = []
        messages.append(str(exc))
    try:
        patreon = patreon_source(sources)
    except StoreError:
        patreon = None
        messages.append('The Patreon catalog configuration is invalid. Other catalogs remain available.')
    for source in sources + ([patreon] if patreon else []):
        name = 'The Patreon catalog' if source.provider == 'patreon' else 'A private plugin catalog'
        try:
            additional = _browse(source)
            payload['products'].extend(additional['products'])
            if additional['status'] == 'ready' or payload['status'] == 'unavailable':
                payload['status'] = additional['status']
                payload['checked_at'] = additional['checked_at']
            if additional['message']:
                messages.append(name + ' is offline; showing its last verified releases.')
        except StoreError:
            messages.append(name + ' is unavailable. Other catalogs remain available.')
    payload['message'] = ' '.join(dict.fromkeys(messages))
    return payload


def _browse(config):
    try:
        with StoreSession(config) as session:
            raw = session.catalog()
            catalog = parse_catalog(raw, config)
        checked = datetime.now(timezone.utc).isoformat()
        storage._write_json(_browse_path(config), {'catalog': raw, 'checked_at': checked})
        return _browse_payload(catalog, checked, False)
    except StoreError:
        # Display-only offline cache. Every plan and install opens a fresh TUF
        # session and cannot promote these cached display fields into trust.
        try:
            cached = json.loads(_browse_path(config).read_text(encoding='utf-8'))
            return _browse_payload(parse_catalog(cached['catalog'], config), cached['checked_at'], True)
        except (OSError, ValueError, KeyError, TypeError):
            raise StoreError('The store is unavailable and no verified catalog has been cached yet.') from None


def _browse_payload(catalog, checked, offline):
    return {'status': 'offline' if offline else 'ready', 'checked_at': checked,
            'message': 'Showing the last verified catalog. Connect to the store to install or update.' if offline else '',
            'products': [{'id': pid, 'releases': [r.payload() for r in releases]} for pid, releases in catalog.items()]}


@contextmanager
def _plan_sessions(plugin_id):
    requested = [plugin_id] if isinstance(plugin_id, str) else plugin_id
    if not isinstance(requested, (list, tuple)) or not 1 <= len(requested) <= 50:
        raise StoreError('Select valid plugins to update or install.')
    configs = {}
    for pid in requested:
        config = config_for_plugins(pid) or load_config()
        configs[config.identity] = config
    # Keep each source's trust scope and target namespace separate, even when
    # two catalogs use the same relative archive path.
    with ExitStack() as stack:
        sessions = [stack.enter_context(StoreSession(configs[key])) for key in sorted(configs)]
        yield sessions


def _plan(sessions, registry, plugin_id, version, *, update_only=False):
    catalog, owners = {}, {}
    for session in sessions:
        source = parse_catalog(session.catalog(), session.config)
        if catalog.keys() & source.keys():
            raise StoreError('The selected catalogs contain overlapping plugin identities.')
        catalog.update(source)
        owners.update({pid: session for pid in source})
    installed = installed_manifests(registry)
    active = {pid for pid, record in registry.records.items() if record.state == 'loaded'} if registry else set()
    requested = {plugin_id} if isinstance(plugin_id, str) else set(plugin_id)
    flags = cfg.get('plugins.enabled') or {}
    if update_only:
        if not requested <= installed.keys():
            raise StoreError('Only installed plugins can be updated together.')
        # Preserve disabled plugins and never turn an update into a downgrade.
        active = {pid for pid in installed if flags.get(pid, True)}
        catalog = {pid: [r for r in releases if pid not in installed or
                        Version(r.manifest.version) >= Version(installed[pid].version)]
                   for pid, releases in catalog.items()}
    solution = resolve(catalog, plugin_id, installed, version=version, active=active,
                       enable_requested=not update_only)
    by_id = {r.manifest.id: r for r in solution}
    def closure(roots):
        needed = set(roots)
        while True:
            expanded = needed | {dep for pid in needed for dep in by_id[pid].manifest.requires}
            if expanded == needed:
                return needed
            needed = expanded

    # A replacement kept ON can introduce its own dependencies. Include their
    # activation in consent, while an old owner kept OFF has no such effect.
    # Already-active roots retain their flag rather than becoming dependencies
    # of the requested independent product in the displayed plan.
    needed = closure(requested & active if update_only else requested) | (closure(active) - active)
    changes = [r for r in solution if r.target and (
        r.manifest.id not in installed or installed[r.manifest.id].version != r.manifest.version
        or installed[r.manifest.id].bundled)]
    # Even a same-version reinstall comes from a newly authenticated target.
    if not changes and not update_only:
        changes = [r for r in solution if r.manifest.id in requested and r.target]
    changes += [r for r in solution if r not in changes and r.manifest.id in needed
                and flags.get(r.manifest.id) is False]
    identity = plan_digest(solution, installed, sorted(session.config.identity for session in sessions))
    # A concurrent toggle/removal or prepared package invalidates the consent.
    operations, errors = storage.pending(external_dir())
    if operations or errors:
        raise StoreError('Apply or cancel the pending plugin changes before preparing another package set.')
    consent = {'flags': flags, 'requested': sorted(requested), 'version': version, 'update_only': update_only}
    identity = hashlib.sha256((identity + json.dumps(consent, sort_keys=True)).encode()).hexdigest()
    return changes, identity, needed, owners


def preview_plan(registry, plugin_id, version=None, *, update_only=False):
    """The reviewed plan. Update all (``update_only`` over several plugins)
    leaves out the members-only plugins this installation may not download,
    instead of blocking every other update: they keep their installed version
    and are listed in ``excluded``. ``requested`` then names what the
    confirmation must send back."""
    plan = _preview_plan(registry, plugin_id, version, update_only=update_only)
    if update_only and not isinstance(plugin_id, str):
        locked = [pid for pid in plan['patreon_required'] if pid in plugin_id]
        remaining = [pid for pid in plugin_id if pid not in locked]
        if locked and remaining:
            names = {package['manifest']['id']: package['manifest'].get('name') or package['manifest']['id']
                     for package in plan['packages']}
            plan = _preview_plan(registry, remaining, version, update_only=True)
            plan['excluded'] = [{'id': pid, 'name': names.get(pid, pid)} for pid in locked]
    return plan


def _preview_plan(registry, plugin_id, version=None, *, update_only=False):
    with _plan_sessions(plugin_id) as sessions:
        changes, identity, needed, owners = _plan(sessions, registry, plugin_id, version, update_only=update_only)
        paid = [r.manifest.id for r in changes if r.target and r.price['kind'] == 'paid']
        # The owning source, not the release label, selects the acquisition service.
        patreon_paid = [pid for pid in paid if owners[pid].config.provider == 'patreon']
        acquired, commerce_status, patreon_access = set(), 'not_required', None
        other_paid = [pid for pid in paid if pid not in patreon_paid]
        if other_paid:
            try:
                library = CommerceClient().library()
                commerce_status = library['status']
                acquired |= {item['plugin_id'] for item in library['items']
                             if item['status'] == 'active' and item['plugin_id'] in other_paid}
            except StoreError:
                commerce_status = 'unavailable'
        if patreon_paid:
            try:
                library = PatreonClient().library()
                patreon_access = {'status': library['status'], 'patreon': library['patreon']}
                acquired |= {item['plugin_id'] for item in library['items']
                             if item['status'] == 'active' and item['plugin_id'] in patreon_paid}
            except StoreNotConfigured:
                patreon_access = {'status': 'not_configured', 'patreon': None}
            except StoreError:
                patreon_access = {'status': 'unavailable', 'patreon': None}
        installed = installed_manifests(registry)
        flags = cfg.get('plugins.enabled') or {}
        requested = {plugin_id} if isinstance(plugin_id, str) else set(plugin_id)
        return {'plan_id': identity, 'requested': plugin_id, 'version': version, 'update_only': update_only,
                'packages': [{**r.payload(), 'action': 'install' if r.target else 'enable',
                              'will_enable': r.manifest.id in needed,
                              'previous_version': installed[r.manifest.id].version if r.manifest.id in installed else None,
                              'reason': 'requested' if r.manifest.id in requested else
                                        'dependency' if r.manifest.id in needed else 'compatibility_update',
                              'remains_disabled': r.manifest.id not in needed and flags.get(r.manifest.id) is False,
                              } for r in changes], 'restart_required': True,
                'paid_packages': paid, 'commerce_status': commerce_status,
                'purchase_required': [pid for pid in paid if pid not in acquired],
                'patreon_required': [pid for pid in patreon_paid if pid not in acquired],
                'patreon_access': patreon_access}


def prepare(registry, plugin_id, version, accepted_plan, *, check_change=None, update_only=False):
    if not isinstance(accepted_plan, str) or len(accepted_plan) != 64:
        raise StoreError('Review the installation plan before confirming it.')
    with _plan_sessions(plugin_id) as sessions:
        changes, identity, needed, owners = _plan(sessions, registry, plugin_id, version, update_only=update_only)
        if identity != accepted_plan:
            raise StoreError('The catalog or installed plugins changed. Review the new installation plan.')
        if not changes:
            raise StoreError('These plugins are already up to date.')
        prepared = []
        for release in changes:
            if check_change:
                check_change(release.manifest.id)
            if not release.target:
                prepared.append({'manifest': release.manifest, 'enable_only': True, 'desired_enabled': True})
                continue
            session = owners[release.manifest.id]
            if release.price['kind'] == 'paid':
                target = session.target_info(release.target)
                # A cached paid archive is not an entitlement. Every acquisition
                # gets a fresh server grant bound to these authenticated bytes.
                path = download_release(release, target, session.target_destination(target),
                                        provider=session.config.provider)
            else:
                path, target = session.target(release.target)
            m, prefix = inspect_plugin_zip(path, official=release.manifest.id in session.config.official_ids)
            with zipfile.ZipFile(path) as archive:
                _, found_prefix = inspect_archive(archive)
                digest = hashlib.sha256(archive.read(found_prefix + 'plugin.json')).hexdigest()
            if (m.id != release.manifest.id or m.version != release.manifest.version
                    or digest != release.manifest_sha256
                    or m.summary() != release.manifest.summary()):
                raise StoreError('The downloaded package does not match the reviewed catalog manifest.')
            provenance = None
            if official.is_official_id(m.id):
                provenance = {'id': m.id, 'publisher': 'lds', 'source': 'verified_store',
                              'manifest_sha256': digest, 'archive_sha256': target.hashes['sha256'],
                              'store': session.config.identity, 'target': release.target}
            elif not session.config.serves(m.id):
                raise StoreError('This publisher is not approved for automatic installation.')
            old = registry.records.get(m.id) if registry else None
            flags = cfg.get('plugins.enabled') or {}
            desired = flags.get(m.id)
            prepared.append({'archive': path, 'manifest': m, 'prefix': prefix,
                             'replacing': bool(old and not old.bundled),
                             'desired_enabled': True if m.id in needed or desired is None else bool(desired),
                             'provenance': provenance})
        # This is the only mutating boundary. A whole dependency set is one
        # durable transaction; publication is completed while the server is down.
        transaction = storage.prepare_batch(external_dir(), prepared)
        return {'ok': True, 'transaction': transaction['id'],
                'packages': [r.manifest.summary() for r in changes]}
