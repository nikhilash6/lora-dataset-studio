"""LDS core updates through the Patreon channel (PATREON-PROTOCOL v1, bridge).

The Patreon catalog is a TUF repository (plugins/store/patreon.py). Its signed
``core-releases.json`` lists core releases. Every artifact's length and SHA-256
come from the TUF target metadata alone, never from that list or a grant; the
bytes come through a membership-checked download grant (product ``lds_core``).

* Git checkout, including Pinokio: fetch the verified bundle into a private
  ref, then fast-forward only. Local commits or modified tracked files refuse,
  like ``git pull --ff-only``. Pinokio restarts the app from its own launcher.
* ZIP install: the verified archive goes through the existing staged swap with
  its backup and rollback (``updater.apply_zip_update``).
* Docker: never applied from inside the container; see
  docs/plugins/patreon-access.md for the host-side limit.

Nothing is applied while LDS has work running: the idle gate of plugin Apply
(plugins/restart.py) is frozen before the first file changes and held until
the process exits.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import current_app, jsonify

from .. import config as cfg
from ..config import REPO_ROOT
from ..plugins import restart
from ..plugins.storage import managed_path
from ..plugins.store.client import MAX_CATALOG_BYTES, StoreError, StoreNotConfigured, StoreSession
from ..plugins.store.patreon import (VERIFICATION_UNAVAILABLE, MembershipRequired, PatreonClient,
                                     VerificationUnavailable, access_summary, patreon_core_source)
from . import updater

CORE_PRODUCT = 'lds_core'
CORE_RELEASES_TARGET = 'core-releases.json'
CORE_RELEASES_FORMAT = 'lds-core-releases/1'
CORE_MAX_BYTES = 512 * 1024 * 1024
BUNDLE_REF = 'refs/heads/patreon'
INCOMING_REF = 'refs/lds/patreon/incoming'
PUBLIC_BRANCH = 'v2'
STATUS_TTL_SECONDS = 600
IDLE_ACTION = 'installing the LDS update'
# APP_VERSION's own scheme, zero-padded, so that plain string comparison is
# version comparison exactly as in updater.apply_zip_update.
_VERSION = re.compile(r'\d{4}\.\d{2}\.\d{2}(?:\.\d+)?\Z')
_SHA = re.compile(r'[0-9a-f]{40}\Z')

DOCKER_LIMIT = ('Patreon updates cannot be installed in a Docker container yet: the container cannot '
                'replace its own image, and the host update script does not apply Patreon releases '
                'yet. Your installed version keeps working.')
PINOKIO_HOLD = ('An LDS update was installed. Stop the app in Pinokio, click Update, then Start it '
                'to finish.')


class CoreChannelError(StoreError):
    """A user-facing reason the Patreon core update stops; no file changed."""


@dataclass(frozen=True)
class CoreRelease:
    version: str
    commit: str
    base: str
    published_at: str
    notes: tuple
    bundle: str
    archive: str

    def payload(self):
        return {'version': self.version, 'published_at': self.published_at,
                'notes': list(self.notes), 'commit': self.commit[:12]}


@dataclass(frozen=True)
class Channel:
    release: CoreRelease
    bundle_info: object
    archive_info: object


def parse_core_releases(value):
    """Strict ``lds-core-releases/1``; newest first. Never carries lengths or hashes."""
    try:
        if not isinstance(value, dict) or value.get('format') != CORE_RELEASES_FORMAT:
            raise ValueError
        releases = value.get('releases')
        if not isinstance(releases, list) or len(releases) > 500:
            raise ValueError
        result, versions = [], set()
        for item in releases:
            if not isinstance(item, dict):
                raise ValueError
            version, commit, base = item.get('version'), item.get('commit'), item.get('base')
            if (not isinstance(version, str) or not _VERSION.fullmatch(version) or version in versions
                    or not isinstance(commit, str) or not _SHA.fullmatch(commit)
                    or not isinstance(base, str) or not _SHA.fullmatch(base) or base == commit):
                raise ValueError
            published = item.get('published_at')
            parsed = datetime.fromisoformat(published.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError
            notes = item.get('notes', [])
            if (not isinstance(notes, list) or len(notes) > 50
                    or any(not isinstance(note, str) or len(note) > 1000 for note in notes)):
                raise ValueError
            if (item.get('bundle') != f'core/lds-core-{version}.bundle'
                    or item.get('archive') != f'core/lds-core-{version}.zip'):
                raise ValueError
            versions.add(version)
            result.append(CoreRelease(version, commit, base, published, tuple(notes),
                                      item['bundle'], item['archive']))
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise StoreError('The Patreon core release list is invalid.') from None
    return sorted(result, key=lambda release: release.version, reverse=True)


def read_channel():
    """Latest Patreon core release and its TUF target infos, from one fresh session."""
    with StoreSession(patreon_core_source()) as session:
        path, _ = session.target(CORE_RELEASES_TARGET, maximum=MAX_CATALOG_BYTES)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('The Patreon core release list is invalid.') from None
        releases = parse_core_releases(value)
        if not releases:
            return None
        latest = releases[0]
        return Channel(latest, session.target_info(latest.bundle, maximum=CORE_MAX_BYTES),
                       session.target_info(latest.archive, maximum=CORE_MAX_BYTES))


def install_mode(root=None):
    root = Path(root or REPO_ROOT)
    if updater.is_docker_runtime():
        return 'docker'
    if updater.is_git_checkout(root):
        return 'pinokio' if updater.is_pinokio_runtime() else 'git'
    return 'zip'


def _git(root, *args):
    try:
        return updater._git(root, *args)
    except FileNotFoundError:
        raise CoreChannelError('git is not installed / not on PATH; install Git to update this checkout.') from None
    except subprocess.SubprocessError:
        raise CoreChannelError('A git command timed out. Your files were not changed.') from None


def _head(root):
    return (_git(root, 'rev-parse', 'HEAD').stdout or '').strip()


def _has_commit(root, sha):
    return _git(root, 'cat-file', '-e', sha + '^{commit}').returncode == 0


def _dirty(root):
    return bool((_git(root, 'status', '--porcelain', '--untracked-files=no').stdout or '').strip())


def _git_has_release(root, release):
    """True when HEAD already contains the release commit."""
    head = _head(root)
    return head == release.commit or (_has_commit(root, release.commit)
                                      and updater._is_ancestor(root, release.commit, 'HEAD'))


_status_cache = {'ts': 0.0, 'data': None}
_installed = {}
_apply_lock = threading.Lock()


def forget_status():
    _status_cache.update(ts=0.0, data=None)


def status(root=None, *, force=False):
    """What the Updates card shows for the Patreon channel. Never raises."""
    now = time.time()
    if not force and _status_cache['data'] is not None and now - _status_cache['ts'] < STATUS_TTL_SECONDS:
        return _status_cache['data']
    root = Path(root or REPO_ROOT)
    from ..version import APP_VERSION
    mode = install_mode(root)
    result = {'status': 'ready', 'message': '', 'install_mode': mode, 'current': APP_VERSION,
              'current_sha': updater.current_sha(root), 'on_channel': False, 'release': None,
              'update_available': False, 'access': None, 'entitled': False, 'can_apply': False,
              'blocked_reason': None, 'restart_required': bool(_installed)}
    try:
        if mode in ('git', 'pinokio'):
            marker = updater.patreon_marker(root)
            result['on_channel'] = bool(marker and updater._is_ancestor(root, marker, 'HEAD'))
        channel = read_channel()
        try:
            access = access_summary()
        except StoreNotConfigured:
            access = {'status': 'not_configured', 'patreon': None}
        except (StoreError, OSError):
            access = {'status': 'unavailable', 'patreon': None}
        result['access'] = access
        result['entitled'] = bool(access['patreon'] and access['patreon']['status'] == 'active')
        if channel is not None:
            release = channel.release
            result['release'] = release.payload()
            if mode in ('git', 'pinokio'):
                result['update_available'] = not _git_has_release(root, release)
                if result['update_available'] and _dirty(root):
                    result['blocked_reason'] = ('Modified tracked files would be overwritten. Commit or '
                                                'discard them, then check again.')
            else:
                result['update_available'] = release.version > APP_VERSION
            if mode == 'docker' and result['update_available']:
                result['blocked_reason'] = DOCKER_LIMIT
        result['can_apply'] = bool(result['update_available'] and result['entitled']
                                   and not result['blocked_reason'] and not _installed)
    except StoreNotConfigured as exc:
        result.update(status='not_configured', message=str(exc))
    except (StoreError, OSError, ValueError) as exc:
        result.update(status='unavailable', message=str(exc) if isinstance(exc, StoreError)
                      else 'The Patreon update channel could not be checked.')
    _status_cache.update(ts=now, data=result)
    return result


def _download(release, target, info, *, destination, on_progress=None):
    return PatreonClient().download_target(CORE_PRODUCT, release.version, target, info, destination,
                                           maximum=CORE_MAX_BYTES, on_progress=on_progress)


def _fetch_bundle(root, bundle, release):
    """Verified bundle bytes into a private ref; nothing visible changes yet."""
    heads = _git(root, 'bundle', 'list-heads', str(bundle))
    lines = [line.split() for line in (heads.stdout or '').splitlines() if line.strip()]
    if heads.returncode != 0 or lines != [[release.commit, BUNDLE_REF]]:
        raise CoreChannelError('The Patreon update bundle does not contain the published release. '
                               'Your files were not changed.')
    if not _has_commit(root, release.base):
        # The bundle starts at a public commit. Fetching the public branch is
        # the same network access as an ordinary update check.
        _git(root, 'fetch', '--quiet', '--no-tags', 'origin', PUBLIC_BRANCH)
        if not _has_commit(root, release.base):
            raise CoreChannelError('This update builds on public LDS history that this checkout does not '
                                   'have yet. Check the connection to the public repository and retry.')
    fetch = _git(root, 'fetch', '--quiet', '--no-tags', str(bundle), f'+{BUNDLE_REF}:{INCOMING_REF}')
    fetched = (_git(root, 'rev-parse', '--verify', '--quiet', INCOMING_REF + '^{commit}').stdout or '').strip()
    if fetch.returncode != 0 or fetched != release.commit:
        raise CoreChannelError('git could not read the verified Patreon update. Your files were not changed.')
    if not updater._is_ancestor(root, release.base, release.commit):
        raise CoreChannelError('The Patreon release does not build on its published public base. '
                               'Your files were not changed.')


def _update_ref(root, value):
    args = ('update-ref', updater.PATREON_MARKER_REF, value) if value else ('update-ref', '-d', updater.PATREON_MARKER_REF)
    if _git(root, *args).returncode != 0:
        raise CoreChannelError('The Patreon channel marker could not be written. Your files were not changed.')


def _fast_forward(root, release):
    """Fast-forward only, under the idle gate. Marker before HEAD moves."""
    if _dirty(root):
        raise CoreChannelError('Modified tracked files would be overwritten. Commit or discard them, then retry.')
    if not updater._is_ancestor(root, 'HEAD', release.commit):
        raise CoreChannelError('This checkout has local commits or follows another history, so it cannot '
                               'fast-forward to the Patreon release. Your files were not changed.')
    before = _head(root)
    old = updater.patreon_marker(root)
    # A marker already in HEAD's history keeps protecting the new HEAD, which
    # descends from it. Otherwise set the marker first, so that no moment
    # exists where HEAD is ahead on the channel without one.
    preset = old is None or not updater._is_ancestor(root, old, 'HEAD')
    if preset:
        _update_ref(root, release.commit)
    merge = _git(root, 'merge', '--ff-only', release.commit)
    if merge.returncode != 0:
        if preset:
            _update_ref(root, old)
        raise CoreChannelError('git could not fast-forward to the Patreon release (an untracked file may be '
                               'in the way). Your files were not changed.')
    _update_ref(root, release.commit)
    after = _head(root)
    names = (_git(root, 'diff', '--name-only', before, after).stdout or '')
    return {'ok': True, 'changed': before != after, 'from': before[:8], 'to': after[:8],
            'version': release.version,
            'deps_changed': any('requirements' in name for name in names.splitlines())}


def apply_git(root, channel, *, pinokio=False):
    """Download, verify and fetch; then freeze the idle gate and fast-forward.

    Returns ``(payload, gate)``: a non-None gate is frozen and owned by the
    calling thread, which must keep it until the process exits."""
    release = channel.release
    if _dirty(root):
        raise CoreChannelError('Modified tracked files would be overwritten. Commit or discard them, then retry.')
    if _git_has_release(root, release):
        return {'ok': True, 'changed': False, 'version': release.version}, None
    folder = managed_path(cfg.data_dir(), 'plugin-store', 'core')
    bundle = _download(release, release.bundle, channel.bundle_info,
                       destination=folder / (channel.bundle_info.hashes['sha256'] + '.bundle'))
    try:
        _fetch_bundle(root, bundle, release)
    finally:
        Path(bundle).unlink(missing_ok=True)
    if not updater._is_ancestor(root, 'HEAD', release.commit):
        raise CoreChannelError('This checkout has local commits or follows another history, so it cannot '
                               'fast-forward to the Patreon release. Your files were not changed.')
    gate = restart.freeze_when_idle(IDLE_ACTION)
    try:
        result = _fast_forward(root, release)
    except BaseException:
        gate.release()
        raise
    if not result['changed']:
        gate.release()
        return result, None
    _installed.update(version=release.version, pinokio=pinokio)
    forget_status()
    return result, gate


def apply_zip(root, channel):
    """Start the async staged swap of the verified archive (packaged install)."""
    from ..version import APP_VERSION
    release, info = channel.release, channel.archive_info
    if not release.version > APP_VERSION:
        return {'ok': True, 'changed': False, 'version': release.version}
    # Fail before a long download when work is already running; the worker
    # checks again, holding the gate, right before the first file moves.
    restart.freeze_when_idle(IDLE_ACTION).release()
    app = current_app._get_current_object()

    def download(destination, on_bytes):
        _download(release, release.archive, info, destination=destination, on_progress=on_bytes)

    def reserve():
        return restart.freeze_when_idle(IDLE_ACTION, app=app)

    forget_status()
    return updater.start_verified_zip_update(root, {'version': release.version, 'zip_size': info.length},
                                             download=download, reserve=reserve)


def apply_response(root=None):
    """Body of ``POST /api/update/patreon/apply`` (admin and CSRF checked by the route)."""
    root = Path(root or REPO_ROOT)

    def refuse(reason, code='update_refused'):
        # `error` is what the shared fetch client shows; `reason` matches /update/apply.
        return jsonify(ok=False, code=code, reason=reason, error=reason), 409

    if not _apply_lock.acquire(blocking=False):
        return refuse('An LDS update is already being prepared.')
    try:
        if updater.zip_update_in_progress():
            return refuse('Another update is already in progress.')
        if _installed:
            return refuse('An LDS update is already installed. Restart LDS to finish it.')
        mode = install_mode(root)
        if mode == 'docker':
            return refuse(DOCKER_LIMIT, 'docker_limit')
        channel = read_channel()
        if channel is None:
            return jsonify(ok=True, changed=False, reason='No Patreon release is published yet.')
        access = access_summary()
        membership = (access['patreon'] or {}).get('status')
        if membership == 'verification_required':
            return refuse(VERIFICATION_UNAVAILABLE, 'verification_required')
        if membership != 'active':
            return refuse('This update is available to Patreon members. Connect Patreon or join, '
                          'then check again.', 'patreon_required')
        if mode == 'zip':
            result = apply_zip(root, channel)
            if not result.get('ok'):
                return refuse(result.get('reason') or 'The update could not start.')
            return jsonify(result)
        result, gate = apply_git(root, channel, pinokio=mode == 'pinokio')
        if gate is None:
            return jsonify(result)
        if mode == 'pinokio':
            # Pinokio owns the process: keep admissions closed until the user
            # stops the app there, so no new work runs on mixed code.
            gate.message = PINOKIO_HOLD
            response = jsonify({**result, 'restarting': False, 'restart_required': True,
                                'install_mode': 'pinokio',
                                'instructions': list(updater.PINOKIO_UPDATE_INSTRUCTIONS)})
        else:
            try:
                updater.schedule_restart(install_requirements=bool(result.get('deps_changed')))
                response = jsonify({**result, 'restarting': True})
            except Exception:
                response = jsonify({**result, 'restarting': False, 'restart_required': True,
                                    'reason': 'The update was installed, but the automatic restart could not '
                                              'be scheduled. Restart LDS manually.'})
        # Werkzeug closes the response after flushing it. The gate's locks
        # belong to this thread, which must stay alive until the process exits.
        response.call_on_close(gate.finished.wait)
        return response
    except restart.RestartBlocked as exc:
        return refuse(str(exc), 'update_blocked')
    except MembershipRequired as exc:
        forget_status()
        return refuse(str(exc), 'patreon_required')
    except VerificationUnavailable as exc:
        return refuse(str(exc), 'verification_required')
    except updater.UpdateInProgressError:
        return refuse('Another update is already in progress.')
    except StoreNotConfigured as exc:
        return refuse(str(exc), 'not_configured')
    except (StoreError, OSError, ValueError) as exc:
        return refuse(str(exc) if isinstance(exc, StoreError)
                      else 'The Patreon update could not be prepared. Your files were not changed.')
    finally:
        _apply_lock.release()
