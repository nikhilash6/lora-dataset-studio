"""Patreon core channel against REAL git repositories, a REAL `git bundle`, real
TUF signatures and a local fake of the Patreon service (no Patreon account,
no network beyond loopback).

Named risks pinned here:
* the public-history resync never moves a checkout that is ahead on the
  Patreon channel back to the public branch;
* nothing is applied while LDS has work running (the plugin Apply idle gate);
* artifact length and SHA-256 come from the TUF target metadata only.
"""
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

pytest.importorskip('cryptography', reason='Signing proofs use the separate operator environment.')
from tuf.api.metadata import TargetFile  # noqa: E402

from app.plugins import restart  # noqa: E402
from app.services import patreon_core, updater  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git not on PATH')

_spec = importlib.util.spec_from_file_location(
    'lds_repository_tool_patreon', Path(__file__).resolve().parents[2] / 'store/tools/repository.py')
operator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(operator)

CLIENT_ID, SECRET = 'c' * 32, 'k' * 43
BEARER = 'Bearer ' + CLIENT_ID + '.' + SECRET
VERSION = '2099.01.02'
BUNDLE = f'core/lds-core-{VERSION}.bundle'
ARCHIVE = f'core/lds-core-{VERSION}.zip'
SOURCE = f'core/lds-core-{VERSION}.source.zip'
ACTIVE = {'linked': True, 'status': 'active', 'tier': 'member', 'valid_until': None,
          'join_url': 'https://www.patreon.com/c/example', 'request_url': None, 'pending': False, 'reason': None}


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """The developer's git config is not an input; process-lifetime state resets."""
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    monkeypatch.delenv('LDS_RUNTIME', raising=False)
    patreon_core.forget_status()
    patreon_core._installed.clear()
    yield
    patreon_core.forget_status()
    patreon_core._installed.clear()
    with updater._zip_lock:
        updater._zip_state.clear()
        updater._zip_state.update(phase='idle')
    with updater._restart_lock:
        updater._restart_scheduled = False


def git(cwd, *args):
    out = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f'git {" ".join(args)} failed: {(out.stderr or out.stdout)[-600:]}'
    return out.stdout.strip()


def commit(repo, files, message):
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


def identity(repo):
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'Test')


def head(repo):
    return git(repo, 'rev-parse', 'HEAD')


def entry(commit_sha, base, **extra):
    return {'version': VERSION, 'commit': commit_sha, 'base': base, 'published_at': '2099-01-02T00:00:00Z',
            'notes': ['Members feature'], 'bundle': BUNDLE, 'archive': ARCHIVE, **extra}


@pytest.fixture
def repos(tmp_path):
    """Public branch v2, a user's clone of it, and the operator's Patreon line."""
    public = tmp_path / 'public'
    public.mkdir()
    git(public, 'init', '-q', '-b', 'v2')
    identity(public)
    base = commit(public, {'backend/requirements.txt': 'flask\n', 'app.txt': 'public 1\n'}, 'public bridge')
    user = tmp_path / 'user'
    git(tmp_path, 'clone', '-q', str(public), str(user))
    identity(user)
    line = tmp_path / 'operator'
    git(tmp_path, 'clone', '-q', str(public), str(line))
    identity(line)
    git(line, 'checkout', '-q', '-b', 'patreon')
    release = commit(line, {'patreon_feature.txt': 'members\n'}, 'patreon release')
    bundle = tmp_path / 'release.bundle'
    git(line, 'bundle', 'create', str(bundle), 'patreon', '^' + base)
    return SimpleNamespace(public=public, user=user, line=line, base=base, release=release,
                           bundle=bundle.read_bytes(), tmp=tmp_path)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, data, kind='application/json'):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        service = self.server.service
        service.calls.append(('GET', self.path, None))
        if self.path.startswith(('/tuf/metadata/', '/tuf/targets/')):
            kind, name = self.path[len('/tuf/'):].split('/', 1)
            path = service.tuf / kind / unquote(name)
            if '..' in name or not path.is_file():
                return self._send(404, {'error': 'not_found'})
            return self._send(200, path.read_bytes(), 'application/octet-stream')
        if self.headers.get('Authorization') != BEARER:
            return self._send(401, {'error': 'unauthorized'})
        if self.path == '/v1/library':
            return self._send(200, {'items': [], 'patreon': service.membership})
        if self.path.startswith('/v1/artifacts/'):
            target = unquote(self.path[len('/v1/artifacts/'):])
            if service.tokens.get(self.headers.get('X-LDS-Download-Grant')) != target:
                return self._send(403, {'error': 'invalid_grant'})
            return self._send(200, service.served.get(target, service.artifacts.get(target, b'')),
                              'application/octet-stream')
        self._send(404, {'error': 'not_found'})

    def do_POST(self):
        service = self.server.service
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        service.calls.append(('POST', self.path, body))
        if self.path == '/v1/installations':
            return self._send(201, {'client_id': CLIENT_ID, 'client_secret': SECRET})
        if self.headers.get('Authorization') != BEARER:
            return self._send(401, {'error': 'unauthorized'})
        if self.path == '/v1/grants':
            if service.deny:
                return self._send(*service.deny)
            if (service.membership['status'] != 'active' or body.get('plugin_id') != 'lds_core'
                    or body.get('target') not in service.artifacts):
                return self._send(403, {'error': 'acquisition_denied'})
            token = secrets.token_urlsafe(32)
            service.tokens[token] = body['target']
            return self._send(200, {
                'token': token, 'target': body['target'], 'sha256': body['sha256'],
                'download_url': service.origin + '/v1/artifacts/' + body['target'],
                'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()})
        self._send(404, {'error': 'not_found'})


class FakeService:
    """Operator service: TUF metadata + catalog targets, grants, artifacts."""

    def __init__(self, folder):
        self.tuf, self.keys = folder / 'served', folder / 'keys'
        operator.initialize(self.tuf, self.keys)
        self.artifacts, self.served, self.tokens, self.calls = {}, {}, {}, []
        self.membership = dict(ACTIVE)
        self.deny = None  # (status, body) for every grant request, when set
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.server.service = self
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, releases, artifacts):
        """Sign core-releases.json and the artifacts' TargetFile metadata.

        The artifact BYTES are not published under /tuf/targets/: like the
        real service, they are served only through grants."""
        data = json.dumps({'format': 'lds-core-releases/1', 'releases': releases}).encode()
        info = TargetFile.from_data('core-releases.json', data, ['sha256'])
        targets = {'core-releases.json': info,
                   **{name: TargetFile.from_data(name, value, ['sha256']) for name, value in artifacts.items()}}
        plan = {'history_path': self.keys / 'published.json', 'history': b'{}',
                'writes': [(self.tuf / 'targets' / (info.hashes['sha256'] + '.core-releases.json'), data)],
                'targets': targets, 'registry_path': None, 'registry': b''}
        operator._publish(self.tuf, self.keys, {'schema_version': 1, 'products': []}, {}, prepared=plan)
        self.artifacts = dict(artifacts)

    def grants(self):
        return [body for method, path, body in self.calls if method == 'POST' and path == '/v1/grants']

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def service(tmp_path, monkeypatch):
    fake = FakeService(tmp_path / 'operator-service')
    config = tmp_path / 'patreon.json'
    config.write_text(json.dumps({
        'format': 'lds-patreon/1', 'service_url': fake.origin, 'checkout_origin': 'https://www.patreon.com',
        'join_url': 'https://www.patreon.com/c/example',
        'catalog': {'root_path': str(fake.tuf / 'bootstrap.root.json'), 'first_party_ids': [], 'plugin_ids': []},
    }), encoding='utf-8')
    monkeypatch.setenv('LDS_STORE_PATREON_CONFIG', str(config))
    monkeypatch.setenv('LDS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('LDS_CONFIG', str(tmp_path / 'config.json'))
    yield fake
    fake.close()


class FakeGate:
    """Stands in for the plugin restart gate where a test needs a SUCCESSFUL apply."""

    def __init__(self):
        self.finished = threading.Event()
        self.finished.set()  # call_on_close(gate.finished.wait) must not block the test client.
        self.released = False
        self.message = None
        self.thread = threading.get_ident()

    def release(self):
        self.released = True


@pytest.fixture
def gates(monkeypatch):
    made = []

    def freeze(action, app=None):
        made.append(FakeGate())
        return made[-1]
    monkeypatch.setattr(restart, 'freeze_when_idle', freeze)
    return made


def enrolled():
    from app.plugins.store.patreon import PatreonClient
    return PatreonClient().credential(enroll=True)


def publish_release(service, repos, **extra):
    service.publish([entry(repos.release, repos.base, **extra)], {BUNDLE: repos.bundle, ARCHIVE: b'unused'})


# --- Git checkout: verified bundle, then fast-forward only -------------------

def test_git_checkout_fast_forwards_to_the_tuf_verified_bundle(repos, service, gates):
    publish_release(service, repos)
    enrolled()
    channel = patreon_core.read_channel()
    assert channel.release.commit == repos.release
    assert channel.bundle_info.length == len(repos.bundle)
    assert channel.bundle_info.hashes['sha256'] == hashlib.sha256(repos.bundle).hexdigest()

    result, gate = patreon_core.apply_git(repos.user, channel)

    assert result['changed'] is True and result['version'] == VERSION
    assert head(repos.user) == repos.release
    assert updater.patreon_marker(repos.user) == repos.release
    assert (repos.user / 'patreon_feature.txt').read_text(encoding='utf-8') == 'members\n'
    assert gate is gates[0] and gate.released is False  # held until the restart exits the process
    grant, = service.grants()
    assert grant == {'plugin_id': 'lds_core', 'version': VERSION, 'target': BUNDLE,
                     'sha256': hashlib.sha256(repos.bundle).hexdigest()}
    assert not list((Path(os.environ['LDS_DATA_DIR']) / 'plugin-store' / 'core').glob('*.bundle'))
    assert updater.on_patreon_channel(repos.user, 'v2') is True


@pytest.mark.parametrize('served', ['tampered', 'longer', 'shorter'])
def test_bytes_that_differ_from_tuf_metadata_never_reach_git(repos, service, gates, served):
    bad = {'tampered': repos.bundle[:-1] + bytes([repos.bundle[-1] ^ 1]),
           'longer': repos.bundle + b'x', 'shorter': repos.bundle[:-1]}[served]
    # The release list may claim anything: lengths and hashes come from TUF only.
    publish_release(service, repos, sha256=hashlib.sha256(bad).hexdigest(), length=len(bad))
    service.served[BUNDLE] = bad
    enrolled()
    with pytest.raises(patreon_core.StoreError):
        patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert head(repos.user) == repos.base
    assert updater.patreon_marker(repos.user) is None
    assert subprocess.run(['git', 'rev-parse', '--verify', '--quiet', patreon_core.INCOMING_REF],
                          cwd=repos.user, capture_output=True).returncode != 0
    assert gates == []


def test_release_list_cannot_name_a_commit_the_bundle_does_not_carry(repos, service, gates):
    service.publish([entry('0' * 40, repos.base)], {BUNDLE: repos.bundle, ARCHIVE: b'unused'})
    enrolled()
    with pytest.raises(patreon_core.CoreChannelError, match='does not contain the published release'):
        patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert head(repos.user) == repos.base and gates == []


@pytest.mark.parametrize('local', ['commit', 'modified'])
def test_local_work_refuses_and_changes_nothing(repos, service, gates, local):
    publish_release(service, repos)
    enrolled()
    if local == 'commit':
        mine = commit(repos.user, {'mine.txt': 'local work\n'}, 'local work')
    else:
        (repos.user / 'app.txt').write_text('edited\n', encoding='utf-8')
        mine = repos.base
    with pytest.raises(patreon_core.CoreChannelError):
        patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert head(repos.user) == mine
    assert updater.patreon_marker(repos.user) is None
    assert gates == []
    assert service.grants() == []  # Refused before any download.
    if local == 'modified':
        assert (repos.user / 'app.txt').read_text(encoding='utf-8') == 'edited\n'


def test_a_checkout_ahead_of_the_release_waits_for_the_next_one(repos, service, client, gates, monkeypatch):
    """The public branch moved past the release's base and this checkout took
    it: the release cannot fast-forward it. Say so before any download, instead
    of downloading the bundle and then refusing as if the user had local work."""
    newer = commit(repos.public, {'app.txt': 'public 2\n'}, 'public after the release base')
    git(repos.user, 'pull', '-q', '--ff-only', 'origin', 'v2')
    assert head(repos.user) == newer
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    publish_release(service, repos)
    enrolled()
    status = client.get('/api/update/patreon?force=1').json
    assert status['update_available'] is True and status['entitled'] is True
    assert status['can_apply'] is False and status['blocked_reason'] == patreon_core.AHEAD_OF_RELEASE
    assert 'ahead of the members release' in patreon_core.AHEAD_OF_RELEASE
    with pytest.raises(patreon_core.CoreChannelError, match='ahead of the members release'):
        patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert service.grants() == [] and gates == [] and head(repos.user) == newer


def test_the_next_release_fast_forwards_from_the_installed_one(repos, service, gates):
    """HEAD is the installed Patreon release, which is not behind the next
    public base: the check before the download must still let it through."""
    publish_release(service, repos)
    enrolled()
    patreon_core.apply_git(repos.user, patreon_core.read_channel())
    patreon_core._installed.clear()
    newer = commit(repos.public, {'app.txt': 'public 2\n'}, 'public bridge 2')
    git(repos.line, 'fetch', '-q', 'origin', 'v2')
    git(repos.line, 'merge', '-q', '--no-edit', 'origin/v2')
    second = commit(repos.line, {'patreon_feature.txt': 'members 2\n'}, 'patreon release 2')
    bundle = repos.tmp / 'second.bundle'
    git(repos.line, 'bundle', 'create', str(bundle), 'patreon', '^' + newer)
    version = '2099.01.03'
    names = {'bundle': f'core/lds-core-{version}.bundle', 'archive': f'core/lds-core-{version}.zip'}
    service.publish([entry(second, newer, version=version, **names)],
                    {names['bundle']: bundle.read_bytes(), names['archive']: b'unused'})
    assert patreon_core.status(repos.user, force=True)['blocked_reason'] is None
    result, _ = patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert result['changed'] is True and head(repos.user) == second
    assert updater.patreon_marker(repos.user) == second


def test_missing_public_base_is_fetched_before_reading_the_bundle(repos, service, gates):
    newer = commit(repos.public, {'app.txt': 'public 2\n'}, 'public bridge 2')
    git(repos.line, 'fetch', '-q', 'origin', 'v2')
    git(repos.line, 'merge', '-q', '--no-edit', 'origin/v2')
    release = head(repos.line)
    bundle = repos.tmp / 'second.bundle'
    git(repos.line, 'bundle', 'create', str(bundle), 'patreon', '^' + newer)
    service.publish([entry(release, newer)], {BUNDLE: bundle.read_bytes(), ARCHIVE: b'unused'})
    enrolled()
    assert subprocess.run(['git', 'cat-file', '-e', newer + '^{commit}'], cwd=repos.user).returncode != 0
    result, _ = patreon_core.apply_git(repos.user, patreon_core.read_channel())
    assert result['changed'] and head(repos.user) == release
    assert (repos.user / 'app.txt').read_text(encoding='utf-8') == 'public 2\n'


# --- The named risk: the public resync never moves a Patreon checkout back ---

def _patreon_checkout_and_public_lookalike(repos, service):
    """User on the Patreon release; the public branch then carries one commit
    with the SAME tree (same content, other commit) and later drops it again."""
    publish_release(service, repos)
    enrolled()
    patreon_core.apply_git(repos.user, patreon_core.read_channel())
    commit(repos.public, {'patreon_feature.txt': 'members\n'}, 'public lookalike')
    tip = commit(repos.public, {'patreon_feature.txt': None, 'fix.txt': 'public fix\n'}, 'public revert')
    git(repos.user, 'fetch', '-q', 'origin', 'v2')
    return tip


def test_history_resync_never_moves_a_patreon_checkout_back_to_public(repos, service, gates):
    _patreon_checkout_and_public_lookalike(repos, service)

    status = updater.git_update_status(repos.user)
    assert status['channel'] == 'patreon' and status['update_available'] is False
    applied = updater.apply_update(repos.user)
    assert applied['ok'] is False and applied['channel'] == 'patreon'
    assert 'reset' not in applied['reason']
    assert updater._resync_rewritten_history(repos.user, 'v2') is None
    assert head(repos.user) == repos.release
    assert (repos.user / 'patreon_feature.txt').is_file()


def test_the_marker_is_what_blocks_the_tree_resync(repos, service, gates):
    """Witness: without the channel marker, the historical tree-based resync
    WOULD reset this exact checkout onto the public tip and delete the
    Patreon feature. The guard above is therefore what protects it."""
    tip = _patreon_checkout_and_public_lookalike(repos, service)
    git(repos.user, 'update-ref', '-d', updater.PATREON_MARKER_REF)
    assert updater._resync_rewritten_history(repos.user, 'v2') == tip
    assert not (repos.user / 'patreon_feature.txt').exists()


def test_public_branch_that_contains_the_release_still_updates(repos, service, gates):
    publish_release(service, repos)
    enrolled()
    patreon_core.apply_git(repos.user, patreon_core.read_channel())
    git(repos.public, 'fetch', '-q', str(repos.line), 'patreon')
    git(repos.public, 'merge', '-q', '--ff-only', 'FETCH_HEAD')
    later = commit(repos.public, {'fix.txt': 'public fix\n'}, 'public after publication')
    git(repos.user, 'fetch', '-q', 'origin', 'v2')
    assert updater.on_patreon_channel(repos.user, 'v2') is False
    assert updater.apply_update(repos.user)['ok'] is True
    assert head(repos.user) == later


# --- Routes: entitlement, idle gate, restart ownership -----------------------

def test_route_requires_an_active_membership_before_any_grant(repos, service, client, gates):
    publish_release(service, repos)
    service.membership = {**ACTIVE, 'status': 'inactive', 'tier': None}
    enrolled()
    response = client.post('/api/update/patreon/apply', json={})
    assert response.status_code == 409 and response.json['code'] == 'patreon_required'
    assert service.grants() == [] and gates == []


@pytest.mark.parametrize('membership,deny,code', [
    ('verification_required', None, 'verification_required'),
    ('active', (403, {'error': 'acquisition_denied'}), 'patreon_required'),
    ('active', (503, {'error': 'verification_required'}), 'verification_required'),
])
def test_route_names_why_patreon_refused(repos, service, client, gates, monkeypatch, membership, deny, code):
    """The library may still say active when the grant is denied (membership
    ended meanwhile); either way the checkout is untouched."""
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    publish_release(service, repos)
    service.membership = ACTIVE if membership == 'active' else {**ACTIVE, 'status': membership, 'tier': None}
    service.deny = deny
    enrolled()
    response = client.post('/api/update/patreon/apply', json={})
    assert response.status_code == 409 and response.json['code'] == code
    assert response.json['error'] == response.json['reason']
    assert head(repos.user) == repos.base and gates == []


def test_route_refuses_while_a_render_is_queued(repos, service, app, monkeypatch):
    """The REAL plugin Apply gate: a queued render keeps every file in place."""
    from app.extensions import db
    from app.models import ImageGenerationQueue
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    publish_release(service, repos)
    enrolled()
    with app.app_context():
        db.session.add(ImageGenerationQueue(job_id='render-in-progress', status='processing'))
        db.session.commit()
    response = app.test_client().post('/api/update/patreon/apply', json={})
    assert response.status_code == 409 and response.json['code'] == 'update_blocked'
    assert 'before installing the LDS update' in response.json['reason']
    assert head(repos.user) == repos.base
    assert updater.patreon_marker(repos.user) is None
    assert app.extensions[restart._EXTENSION].frozen is False


def test_route_fast_forwards_and_restarts_once(repos, service, client, gates, monkeypatch):
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    restarts = []
    monkeypatch.setattr(updater, 'schedule_restart', lambda **kw: restarts.append(kw) or True)
    publish_release(service, repos)
    enrolled()
    response = client.post('/api/update/patreon/apply', json={})
    assert response.status_code == 200, response.json
    assert response.json['restarting'] is True and response.json['changed'] is True
    assert restarts == [{'install_requirements': False}]
    assert head(repos.user) == repos.release
    assert client.post('/api/update/patreon/apply', json={}).json['reason'].startswith('An LDS update is already installed')


def test_pinokio_installs_but_leaves_the_restart_to_pinokio(repos, service, client, gates, monkeypatch):
    monkeypatch.setenv('LDS_RUNTIME', 'pinokio')
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    monkeypatch.setattr(updater, 'schedule_restart', lambda **kw: pytest.fail('Pinokio owns the restart'))
    publish_release(service, repos)
    enrolled()
    response = client.post('/api/update/patreon/apply', json={})
    assert response.status_code == 200, response.json
    assert response.json['restart_required'] is True and response.json['restarting'] is False
    assert response.json['instructions'] == list(updater.PINOKIO_UPDATE_INSTRUCTIONS)
    assert gates[0].message == patreon_core.PINOKIO_HOLD and gates[0].released is False
    assert head(repos.user) == repos.release


def test_status_reports_the_channel_without_applying(repos, service, client, monkeypatch):
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    publish_release(service, repos)
    status = client.get('/api/update/patreon?force=1').json
    assert status['status'] == 'ready' and status['install_mode'] == 'git'
    assert status['release']['version'] == VERSION and status['update_available'] is True
    assert status['access']['status'] == 'not_connected' and status['can_apply'] is False
    assert service.grants() == [] and head(repos.user) == repos.base
    # Docker's bridge and LAN browsers read the channel too; only installing needs administration.
    remote = client.get('/api/update/patreon', environ_base={'REMOTE_ADDR': '198.51.100.8'})
    assert remote.status_code == 200 and remote.json['release']['version'] == VERSION
    assert remote.json['can_manage'] is False and status['can_manage'] is True
    assert client.post('/api/update/patreon/apply', json={},
                       environ_base={'REMOTE_ADDR': '198.51.100.8'}).status_code == 403


def test_cached_status_still_reads_a_new_membership(repos, service, client, monkeypatch):
    # Connecting happens on Patreon, outside LDS: the Updates card must offer the
    # update right away, not keep the cached "not connected" answer for ten minutes.
    monkeypatch.setattr(patreon_core, 'REPO_ROOT', repos.user)
    publish_release(service, repos)
    first = client.get('/api/update/patreon?force=1').json
    assert first['access']['status'] == 'not_connected' and first['can_apply'] is False
    enrolled()
    service.membership = ACTIVE
    cached = client.get('/api/update/patreon').json
    assert cached['release']['version'] == VERSION
    assert cached['entitled'] is True and cached['can_apply'] is True


@pytest.mark.parametrize('packaged', [True, False])
def test_docker_never_applies_inside_the_container(repos, service, client, monkeypatch, packaged):
    """A release with a source archive is installed from the host; without one
    the card keeps the Docker limit. The container itself never applies."""
    monkeypatch.setenv('LDS_RUNTIME', 'docker-gpu')
    if packaged:
        service.publish([entry(repos.release, repos.base, source=SOURCE)],
                        {BUNDLE: repos.bundle, ARCHIVE: b'unused', SOURCE: b'source tree'})
    else:
        publish_release(service, repos)
    enrolled()
    status = client.get('/api/update/patreon?force=1').json
    assert status['install_mode'] == 'docker' and status['can_apply'] is False
    if packaged:
        assert status['host_command'] == 'update-docker.bat patreon' and status['blocked_reason'] is None
    else:
        assert status['host_command'] is None and status['blocked_reason'] == patreon_core.DOCKER_LIMIT
    response = client.post('/api/update/patreon/apply', json={})
    assert response.status_code == 409 and response.json['code'] == 'docker_limit'
    assert 'update-docker.bat patreon' in response.json['reason']
    # The host command exists for Windows ZIP installations only, and the host
    # updater cannot wait for running work: the member must stop it first.
    assert 'Windows ZIP' in response.json['reason'] and 'stop running work' in response.json['reason']
    assert service.grants() == []


@pytest.mark.parametrize('source,valid', [
    (None, True), (SOURCE, True), ('core/lds-core-2099.01.01.source.zip', False),
    (ARCHIVE, False), ('', False), ('null', False)])
def test_release_source_field_is_read_strictly(source, valid):
    item = entry('1' * 40, '2' * 40)
    if source == 'null':
        item['source'] = None  # Present but empty is not "absent".
    elif source is not None:
        item['source'] = source
    document = {'format': 'lds-core-releases/1', 'releases': [item]}
    if not valid:
        with pytest.raises(patreon_core.StoreError, match='release list is invalid'):
            patreon_core.parse_core_releases(document)
        return
    release, = patreon_core.parse_core_releases(document)
    assert release.source == source


# --- Docker host updater: the disposable fetch-source command ----------------

def _source_release(service, repos, payload=b'PK source tree of the release'):
    service.publish([entry(repos.release, repos.base, source=SOURCE)],
                    {BUNDLE: repos.bundle, ARCHIVE: b'unused', SOURCE: payload})
    return payload


def _tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def test_fetch_source_writes_the_verified_archive_and_leaves_the_data_folder_alone(repos, service, tmp_path):
    payload = _source_release(service, repos)
    enrolled()
    data, out = Path(os.environ['LDS_DATA_DIR']), tmp_path / 'out'
    out.mkdir()
    before = _tree(data)

    manifest = patreon_core.fetch_source(out, data, newer_than='2026.01.01')

    digest = hashlib.sha256(payload).hexdigest()
    assert manifest == {'format': 'lds-core-source/1', 'version': VERSION, 'commit': repos.release,
                        'archive': 'lds-core-source.zip', 'length': len(payload), 'sha256': digest}
    assert sorted(p.name for p in out.iterdir()) == ['lds-core-source.zip', 'manifest.json']
    assert (out / 'lds-core-source.zip').read_bytes() == payload
    assert json.loads((out / 'manifest.json').read_text(encoding='utf-8')) == manifest
    grant, = service.grants()
    assert grant == {'plugin_id': 'lds_core', 'version': VERSION, 'target': SOURCE, 'sha256': digest}
    # Read-only mount: no TUF cache, lock or permission change lands in the data folder.
    assert _tree(data) == before
    assert os.environ['LDS_DATA_DIR'] == str(data)


@pytest.mark.parametrize('case', ['current', 'tampered', 'inactive', 'not_connected', 'no_source'])
def test_fetch_source_writes_nothing_it_cannot_vouch_for(repos, service, tmp_path, case):
    payload = b'PK source tree of the release'
    if case == 'no_source':
        publish_release(service, repos)
    else:
        _source_release(service, repos, payload)
    if case == 'tampered':
        service.served[SOURCE] = payload[:-1] + bytes([payload[-1] ^ 1])
    if case == 'inactive':
        service.membership = {**ACTIVE, 'status': 'inactive', 'tier': None}
    if case != 'not_connected':
        enrolled()
    out = tmp_path / 'out'
    out.mkdir()
    data = Path(os.environ['LDS_DATA_DIR'])
    if case == 'current':
        assert patreon_core.fetch_source(out, data, newer_than=VERSION) == {'status': 'current', 'version': VERSION}
    else:
        expected = {'tampered': patreon_core.StoreError, 'inactive': patreon_core.MembershipRequired,
                    'not_connected': patreon_core.MembershipRequired,
                    'no_source': patreon_core.CoreChannelError}[case]
        with pytest.raises(expected):
            patreon_core.fetch_source(out, data, newer_than='2026.01.01')
    assert list(out.iterdir()) == []
    assert len(service.grants()) == (1 if case == 'tampered' else 0)


def test_fetch_source_command_line_reports_through_its_exit_code(repos, service, tmp_path):
    payload = _source_release(service, repos)
    enrolled()
    backend = Path(__file__).resolve().parents[1]

    def run(out, *extra):
        out.mkdir()
        return subprocess.run([sys.executable, '-m', 'app.services.patreon_core', 'fetch-source', '--out', str(out),
                               '--data', os.environ['LDS_DATA_DIR'], *extra], cwd=backend, env=dict(os.environ),
                              capture_output=True, text=True, timeout=120)

    fetched = run(tmp_path / 'fetched', '--newer-than', '2026.01.01')
    assert fetched.returncode == 0, fetched.stdout + fetched.stderr
    assert f'Fetched LDS v{VERSION}' in fetched.stdout
    assert (tmp_path / 'fetched' / 'lds-core-source.zip').read_bytes() == payload
    current = run(tmp_path / 'current', '--newer-than', VERSION)
    assert current.returncode == 3 and not list((tmp_path / 'current').iterdir())
    service.membership = {**ACTIVE, 'status': 'inactive', 'tier': None}
    refused = run(tmp_path / 'refused', '--newer-than', '2026.01.01')
    assert refused.returncode == 4 and 'Patreon members' in refused.stderr


# --- ZIP install: verified archive through the existing staged swap ----------

def _packaged_install(root):
    for name, text in {'backend/app/version.py': "APP_VERSION = 'old'\n", 'backend/run.py': 'print(1)\n',
                       'backend/requirements.txt': 'flask\n', 'frontend/dist/index.html': 'old ui\n'}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode())  # Same bytes as the archive: no CRLF translation.


def _release_zip(path):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in {'backend/app/version.py': f"APP_VERSION = '{VERSION}'\n",
                           'backend/app/__init__.py': '', 'backend/run.py': 'print(2)\n',
                           'backend/requirements.txt': 'flask\n', 'frontend/dist/index.html': 'new ui\n'}.items():
            archive.writestr('LoRA-Dataset-Studio/' + name, text)
    return path.read_bytes()


def _wait_for_worker():
    for _ in range(300):
        phase = updater.zip_update_progress().get('phase')
        if phase in ('restarting', 'error', 'done'):
            return updater.zip_update_progress()
        time.sleep(0.1)
    raise AssertionError('the ZIP worker did not finish')


@pytest.mark.parametrize('tamper', [False, True])
def test_zip_install_swaps_only_the_verified_archive(repos, service, app, gates, monkeypatch, tmp_path, tamper):
    root = tmp_path / 'packaged'
    _packaged_install(root)
    archive = _release_zip(tmp_path / 'release.zip')
    service.publish([entry(repos.release, repos.base)], {BUNDLE: repos.bundle, ARCHIVE: archive})
    if tamper:
        service.served[ARCHIVE] = archive[:-1] + bytes([archive[-1] ^ 1])
    enrolled()
    restarts = []
    monkeypatch.setattr(updater, 'schedule_restart', lambda **kw: restarts.append(kw) or True)
    monkeypatch.setattr(updater.os, 'chdir', lambda path: None)
    with app.test_request_context('/api/update/patreon/apply', method='POST'):
        started = patreon_core.apply_zip(root, patreon_core.read_channel())
    assert started['ok'] is True and started['async'] is True
    progress = _wait_for_worker()
    version = (root / 'backend/app/version.py').read_text(encoding='utf-8')
    if tamper:
        assert progress['phase'] == 'error' and 'download failed' in progress['error']
        assert version == "APP_VERSION = 'old'\n" and restarts == []
        assert len(gates) == 1 and gates[0].released  # Only the pre-download idle check ran.
        assert updater.patreon_channel() is None
    else:
        # The data folder records the channel: no Git history carries it here.
        assert updater.patreon_channel() == {'version': VERSION, 'commit': repos.release}
        assert progress['phase'] == 'restarting', progress
        assert version == f"APP_VERSION = '{VERSION}'\n"
        assert (root / 'frontend/dist/index.html').read_text(encoding='utf-8') == 'new ui\n'
        backup = Path(os.environ['LDS_DATA_DIR']) / '_update' / 'backup'
        assert (backup / 'backend/app/version.py').read_text(encoding='utf-8') == "APP_VERSION = 'old'\n"
        assert restarts == [{'install_requirements': False}]
        assert gates[0].released and not gates[1].released  # Held from the swap until exit.
        assert gates[1].thread != threading.get_ident()  # Reserved by the worker that swaps.


# --- ZIP and Docker: the channel marker keeps public releases off ------------

def test_a_public_release_never_replaces_a_patreon_zip_installation(tmp_path, monkeypatch):
    """After a Patreon ZIP install the public in-app update refuses and names the
    Patreon channel. Installing the public release takes an explicit force; once
    it is installed the marker goes, and a failed attempt keeps it."""
    monkeypatch.setenv('LDS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(updater.os, 'chdir', lambda path: None)
    monkeypatch.setattr(updater, 'schedule_restart', lambda **kw: True)
    root = tmp_path / 'packaged'
    _packaged_install(root)
    updater.write_patreon_channel(VERSION, 'a' * 40)
    monkeypatch.setattr(updater, 'latest_release', lambda *a, **k: pytest.fail('a refusal needs no network'))
    refused = updater.start_zip_update(root)
    assert refused['ok'] is False and refused['channel'] == 'patreon' and refused['can_force'] is True
    assert 'Patreon channel' in refused['reason']
    assert updater.zip_update_progress()['phase'] == 'idle'

    archive = tmp_path / 'public.zip'
    _release_zip(archive)
    monkeypatch.setattr(updater, 'latest_release', lambda *a, **k: {
        'version': '2099.01.03', 'tag': 'v2099.01.03', 'zip_url': 'https://example.invalid/public.zip',
        'zip_size': archive.stat().st_size})

    def offline(url, dest, **kwargs):
        raise OSError('offline')
    monkeypatch.setattr(updater, '_download_file', offline)
    assert updater.start_zip_update(root, force_public=True)['async'] is True
    assert _wait_for_worker()['phase'] == 'error'
    assert updater.patreon_channel() == {'version': VERSION, 'commit': 'a' * 40}

    monkeypatch.setattr(updater, '_download_file', lambda url, dest, **kwargs: shutil.copyfile(archive, dest))
    assert updater.start_zip_update(root, force_public=True)['async'] is True
    assert _wait_for_worker()['phase'] == 'restarting'
    assert updater.patreon_channel() is None


class _Release:
    status_code = 200

    def json(self):
        return {'tag_name': 'v2099.12.31', 'html_url': 'https://example.invalid/releases/v2099.12.31',
                'assets': [{'name': 'LoRA-Dataset-Studio-windows.zip', 'size': 9,
                            'browser_download_url': 'https://example.invalid/windows.zip'}]}


@pytest.mark.parametrize('runtime', ['', 'docker-gpu'])
def test_public_update_check_offers_no_public_release_to_a_patreon_installation(client, monkeypatch, runtime):
    """The banner and the Updates card read this check: a ZIP or Docker install
    on the Patreon channel is told about the public release, never offered it."""
    import requests
    from app.routes import settings as routes
    if runtime:
        monkeypatch.setenv('LDS_RUNTIME', runtime)
    monkeypatch.setattr(updater, 'is_git_checkout', lambda root=None: False)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Release())
    routes._update_cache.update(ts=0.0, data=None)
    assert client.get('/api/update/check?force=1').json['update_available'] is True
    updater.write_patreon_channel(VERSION, 'a' * 40)
    status = client.get('/api/update/check').json
    assert status['update_available'] is False and status['channel'] == 'patreon'
    assert status['public_update_available'] is True and status['latest'] == '2099.12.31'
    assert status.get('can_force', False) is (not runtime)
    routes._update_cache.update(ts=0.0, data=None)


def test_update_route_forwards_only_an_explicit_force(client, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, 'is_git_checkout', lambda root=None: False)
    monkeypatch.setattr(updater, 'start_zip_update', lambda **kwargs: calls.append(kwargs) or {'ok': False})
    client.post('/api/update/apply', json={})
    client.post('/api/update/apply', json={'force_public': 'yes'})
    client.post('/api/update/apply', json={'force_public': True})
    assert calls == [{}, {}, {'force_public': True}]


def test_status_never_caches_a_failed_check(tmp_path, monkeypatch):
    """Only a `ready` answer is kept for ten minutes: once Patreon answers
    again, the next check must say so instead of repeating the failure."""
    monkeypatch.setenv('LDS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(patreon_core, 'install_mode', lambda root=None: 'zip')
    monkeypatch.setattr(patreon_core, 'access_summary', lambda: {'status': 'not_connected', 'patreon': None})

    def offline():
        raise patreon_core.StoreError('The Patreon catalog could not be reached.')
    monkeypatch.setattr(patreon_core, 'read_channel', offline)
    assert patreon_core.status(tmp_path)['status'] == 'unavailable'
    monkeypatch.setattr(patreon_core, 'read_channel', lambda: None)
    assert patreon_core.status(tmp_path)['status'] == 'ready'
    monkeypatch.setattr(patreon_core, 'read_channel', offline)
    assert patreon_core.status(tmp_path)['status'] == 'ready'  # A ready answer is cached.
