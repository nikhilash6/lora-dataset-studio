"""Patreon access client and routes: shipped configuration, strict reading of
the membership object, pinned Patreon addresses, administration + CSRF, and
the routing of Patreon products to the Patreon service. No network."""
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flask import Blueprint, Flask, jsonify
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
import pytest

from app.plugins.store import commerce, patreon, service
from app.plugins.store.catalog import parse_catalog
from app.plugins.store.client import StoreConfig, StoreError, StoreNotConfigured, patreon_source
from public_store_fixtures import contract

SECRET = 's' * 43
CLIENT_ID = 'a' * 32
SERVICE = 'https://patreon-service.example.test'
JOIN = 'https://www.patreon.com/c/example'
MEMBER = {'linked': True, 'status': 'active', 'tier': 'member', 'valid_until': '2026-11-04T00:00:00Z',
          'join_url': JOIN, 'request_url': None}
PRO = {**MEMBER, 'tier': 'pro', 'request_url': 'https://requests.example.test/custom-plugin'}
CONFIG = patreon.PatreonConfig(SERVICE, 'https://www.patreon.com', JOIN, None, frozenset(), frozenset())


class Response:
    def __init__(self, body, status=200):
        self.body = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        self.status_code = status

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        pass


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv('LDS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('LDS_CONFIG', str(tmp_path / 'config.json'))
    monkeypatch.setattr(patreon, 'load_patreon_config', lambda: CONFIG)
    replies, calls = [], []

    class Session:
        trust_env = True

        def request(self, method, url, **options):
            assert self.trust_env is False and options['allow_redirects'] is False
            calls.append((method, url, options))
            return replies.pop(0) if replies else Response({'error': 'unavailable'}, 503)

        def close(self):
            pass
    monkeypatch.setattr(commerce.requests, 'Session', Session)
    return replies, calls


def connected(remote):
    remote[0].append(Response({'client_id': CLIENT_ID, 'client_secret': SECRET}, 201))
    client = patreon.PatreonClient()
    client.credential(enroll=True)
    return client


def write_config(tmp_path, monkeypatch, **over):
    value = {'format': 'lds-patreon/1', 'service_url': 'https://store.example.test',
             'checkout_origin': 'https://www.patreon.com', 'join_url': JOIN,
             'catalog': {'root': {'signed': {'_type': 'root'}}, 'first_party_ids': ['creature_battle', 'manga'],
                         'plugin_ids': []}}
    value.update(over)
    path = tmp_path / 'patreon-test.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    monkeypatch.setenv(patreon.CONFIG_ENV, str(path))
    return path


# --- Configuration -------------------------------------------------------------

def test_shipped_configuration_waits_for_the_real_service(monkeypatch):
    monkeypatch.delenv(patreon.CONFIG_ENV, raising=False)
    shipped = json.loads((patreon.cfg.REPO_ROOT / 'store' / 'patreon.json').read_text(encoding='utf-8'))
    assert shipped['service_url'] == 'https://store.example.invalid'
    assert shipped['checkout_origin'] == 'https://www.patreon.com'
    # Installations that stop updating keep this file: later members-only
    # plugins reach them only through the namespace it ships.
    assert shipped['catalog']['plugin_namespaces'] == ['patreon']
    with pytest.raises(StoreNotConfigured):
        patreon.load_patreon_config()


@pytest.mark.parametrize('url,ok', [('http://127.0.0.1:8765', True), ('https://store.example.test', True),
                                    ('http://localhost:8765', False), ('http://192.0.2.10:8765', False),
                                    ('https://store.example.test/api', False),
                                    ('https://user:pass@store.example.test', False)])
def test_test_instances_may_use_only_loopback_http(tmp_path, monkeypatch, url, ok):
    write_config(tmp_path, monkeypatch, service_url=url)
    if ok:
        assert patreon.load_patreon_config().service_url == url
    else:
        with pytest.raises(StoreError):
            patreon.load_patreon_config()


@pytest.mark.parametrize('over', [
    {'checkout_origin': 'https://checkout.example.test'},
    {'join_url': 'https://patreon.example.test/c/example'},
    {'join_url': 'http://www.patreon.com/c/example'},
    {'format': 'lds-patreon/2'},
    {'catalog': {'first_party_ids': ['not_an_lds_product']}},
    {'catalog': {'plugin_ids': ['missing_publisher']}},
    {'catalog': {'root': {}, 'root_path': 'root.json'}},
    # Namespaces: the publisher part of EXTERNAL_ID, never one the code reserves.
    {'catalog': {'plugin_namespaces': 'patreon'}},
    {'catalog': {'plugin_namespaces': ['patreon', 'patreon']}},
    {'catalog': {'plugin_namespaces': ['patreon.tools']}},
    {'catalog': {'plugin_namespaces': ['Patreon']}},
    {'catalog': {'plugin_namespaces': ['lds']}},
    {'catalog': {'plugin_namespaces': ['con']}},
])
def test_configuration_pins_patreon_and_an_explicit_scope(tmp_path, monkeypatch, over):
    write_config(tmp_path, monkeypatch, **over)
    with pytest.raises(StoreError):
        patreon.load_patreon_config()


def test_catalog_needs_its_root_and_cannot_replace_public_products(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, catalog={'root': None, 'first_party_ids': ['manga']})
    with pytest.raises(StoreNotConfigured):
        patreon.patreon_core_source()
    write_config(tmp_path, monkeypatch, catalog={'root': {'x': 1}, 'first_party_ids': ['video']})
    with pytest.raises(StoreError, match='cannot replace'):
        patreon.patreon_core_source()


def test_operator_sources_keep_the_identifiers_they_claim(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch)
    private = SimpleNamespace(selected_ids=frozenset({'manga'}))
    source = patreon_source([private])
    assert source.provider == 'patreon' and source.selected_ids == {'creature_battle'}
    assert source.metadata_url == 'https://store.example.test/tuf/metadata/'
    assert source.target_url == 'https://store.example.test/tuf/targets/'
    assert patreon_source([SimpleNamespace(selected_ids=frozenset({'manga', 'creature_battle'}))]) is None


def test_only_the_patreon_source_can_label_patreon_products():
    def catalog(price):
        data = contract(id='manga', name='Manga')
        return {'schema_version': 1, 'products': [{'id': 'manga', 'releases': [{
            'manifest': data, 'manifest_sha256': 'a' * 64, 'target': 'manga/1.0.0.ldsplugin', 'price': price}]}]}
    root = b'{}'
    member_price = {'kind': 'paid', 'product': 'patreon_lds', 'provider': 'patreon'}
    patreon_config = StoreConfig('https://s.example.test/m/', 'https://s.example.test/t/', root,
                                 frozenset({'manga'}), frozenset(), frozenset({'manga'}), (), provider='patreon')
    other = StoreConfig('https://s.example.test/m/', 'https://s.example.test/t/', root,
                        frozenset({'manga'}), frozenset(), frozenset({'manga'}))
    assert parse_catalog(catalog(member_price), patreon_config)['manga'][0].price == member_price
    for price in ({'kind': 'free'}, {'kind': 'paid', 'product': 'patreon_lds'}):
        with pytest.raises(StoreError):
            parse_catalog(catalog(price), patreon_config)
    with pytest.raises(StoreError):
        parse_catalog(catalog(member_price), other)


# --- Membership object and link ---------------------------------------------------

INACTIVE = {'linked': True, 'status': 'inactive', 'tier': None, 'valid_until': None, 'join_url': JOIN, 'request_url': None}


@pytest.mark.parametrize('value', [MEMBER, PRO, INACTIVE, {**INACTIVE, 'status': 'verification_required'},
                                   {**INACTIVE, 'linked': False}])
def test_valid_membership_objects_are_kept_exactly(value):
    assert patreon.parse_membership(value) == value


@pytest.mark.parametrize('value', [
    None, {**MEMBER, 'linked': 'yes'}, {**MEMBER, 'status': 'paused'}, {**MEMBER, 'tier': 'gold'},
    {**MEMBER, 'tier': None}, {**MEMBER, 'linked': False},
    # Contract: tier and valid_until (next charge date) are null unless active.
    {**MEMBER, 'status': 'inactive'}, {**INACTIVE, 'valid_until': '2026-11-04T00:00:00Z'},
    {**MEMBER, 'status': 'verification_required'},
    {**MEMBER, 'valid_until': '2026-11-04T00:00:00'}, {**MEMBER, 'valid_until': '2026-11-04T00:00:00+02:00'},
    {**MEMBER, 'join_url': 'https://evil.example.test/join'}, {**MEMBER, 'join_url': None},
    {**MEMBER, 'request_url': 'https://requests.example.test/form'},
    {**PRO, 'request_url': None}, {**PRO, 'request_url': 'http://requests.example.test/form'},
    {**PRO, 'request_url': 'https://requests.example.test/form#frag'},
])
def test_membership_object_is_read_strictly(value):
    with pytest.raises(StoreError):
        patreon.parse_membership(value)


def link_reply(url, seconds=600):
    return Response({'authorize_url': url,
                     'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()})


def test_link_enrolls_then_returns_the_pinned_authorization_page(remote):
    url = 'https://www.patreon.com/oauth2/authorize?response_type=code&client_id=x&state=y'
    remote[0].extend([Response({'client_id': CLIENT_ID, 'client_secret': SECRET}, 201), link_reply(url)])
    result = patreon.PatreonClient().link()
    assert result['authorize_url'] == url
    method, address, options = remote[1][-1]
    assert (method, address) == ('POST', SERVICE + '/v1/patreon/link')
    assert options['json'] == {} and options['headers']['Authorization'] == 'Bearer ' + CLIENT_ID + '.' + SECRET


@pytest.mark.parametrize('url', [
    'https://www.patreon.com/oauth2/authorize', 'http://www.patreon.com/oauth2/authorize?x=1',
    'https://patreon.com/oauth2/authorize?x=1', 'https://www.patreon.com.example.test/oauth2/authorize?x=1',
    'https://www.patreon.com:8443/oauth2/authorize?x=1', 'https://user@www.patreon.com/oauth2/authorize?x=1',
    'https://www.patreon.com/oauth2/authorize/../token?x=1', 'https://www.patreon.com/oauth2/authorize?x=1#f',
    'https://www.patreon.com/oauth2/authorize?x=1\\y', 'https://www.patreon.com/oauth2/authorize?x= 1', None])
def test_link_refuses_any_other_authorization_address(remote, url):
    client = connected(remote)
    remote[0].append(link_reply(url))
    with pytest.raises(StoreError):
        client.link()


@pytest.mark.parametrize('seconds', [-5, 3600])
def test_link_expiry_must_be_short_and_in_the_future(remote, seconds):
    client = connected(remote)
    remote[0].append(link_reply('https://www.patreon.com/oauth2/authorize?state=y', seconds))
    with pytest.raises(StoreError):
        client.link()


def test_library_never_enrolls_and_unlink_without_link_needs_no_network(remote):
    client = patreon.PatreonClient()
    library = client.library()
    assert library['status'] == 'not_connected' and library['patreon']['join_url'] == JOIN
    assert client.unlink() == {'status': 'unlinked'}
    assert remote[1] == []


def test_unlink_requires_the_service_confirmation(remote):
    client = connected(remote)
    remote[0].append(Response({'status': 'unlinked'}))
    assert client.unlink() == {'status': 'unlinked'}
    assert remote[1][-1][1] == SERVICE + '/v1/patreon/unlink' and remote[1][-1][2]['json'] == {}
    remote[0].append(Response({'status': 'linked'}))
    with pytest.raises(StoreError):
        client.unlink()


def test_library_reads_items_and_membership(remote):
    client = connected(remote)
    item = {'plugin_id': 'manga', 'status': 'active', 'acquisition': 'all_published', 'updates': 'while_active',
            'offline': 'installed_continues', 'valid_until': None}
    remote[0].append(Response({'items': [item], 'patreon': PRO}))
    assert client.library() == {'status': 'ready', 'items': [item], 'patreon': PRO}
    remote[0].append(Response({'items': [item], 'patreon': {**PRO, 'request_url': None}}))
    with pytest.raises(StoreError):
        client.library()


def test_library_accepts_external_plugin_identifiers(remote):
    # Patreon sells external plugins too; their identifiers are publisher.name.
    client = connected(remote)
    external = {'plugin_id': 'studio.member_tool', 'status': 'active', 'acquisition': 'all_published',
                'updates': 'while_active', 'offline': 'installed_continues', 'valid_until': None}
    remote[0].append(Response({'items': [external], 'patreon': PRO}))
    assert client.library()['items'] == [external]
    remote[0].append(Response({'items': [{**external, 'plugin_id': 'studio.member.tool'}], 'patreon': PRO}))
    with pytest.raises(StoreError):
        client.library()


# --- Routes ---------------------------------------------------------------------------

def test_routes_require_admin_and_csrf_and_never_return_credentials(remote):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='test-session-signing-only')
    CSRFProtect(app)
    bp = Blueprint('patreon_test', __name__, url_prefix='/api/plugins/store')
    patreon.register_patreon_routes(bp)
    app.register_blueprint(bp)
    app.add_url_rule('/csrf', view_func=lambda: jsonify(token=generate_csrf()))
    client = app.test_client()
    url = 'https://www.patreon.com/oauth2/authorize?state=abc'
    remote[0].extend([Response({'client_id': CLIENT_ID, 'client_secret': SECRET}, 201), link_reply(url)])
    assert client.post('/api/plugins/store/patreon/link', json={}).status_code == 400
    token = client.get('/csrf').json['token']
    assert client.get('/api/plugins/store/patreon', environ_base={'REMOTE_ADDR': '198.51.100.8'}).status_code == 403
    assert client.post('/api/plugins/store/patreon/link', json={'next': 'x'},
                       headers={'X-CSRFToken': token}).status_code == 409
    response = client.post('/api/plugins/store/patreon/link', json={}, headers={'X-CSRFToken': token})
    assert response.status_code == 200 and response.json['authorize_url'] == url
    assert SECRET not in response.get_data(as_text=True)
    assert response.headers['Cache-Control'] == 'no-store'
    status = client.get('/api/plugins/store/patreon')
    assert status.status_code == 409 and status.json['status'] == 'unavailable'  # No reply queued.


def test_route_reports_not_configured_on_the_shipped_placeholder(client, monkeypatch):
    monkeypatch.delenv(patreon.CONFIG_ENV, raising=False)
    response = client.get('/api/plugins/store/patreon')
    assert response.status_code == 200 and response.json['status'] == 'not_configured'


# --- Plugins: the owning source selects the acquisition service -------------------

def test_patreon_grants_go_to_the_patreon_service(remote, tmp_path):
    client = connected(remote)
    content = b'patreon plugin archive'
    digest = hashlib.sha256(content).hexdigest()
    release = SimpleNamespace(target='manga/1.0.0.ldsplugin', manifest=SimpleNamespace(id='manga', version='1.0.0'))
    info = SimpleNamespace(path=release.target, length=len(content), hashes={'sha256': digest})
    remote[0].extend([Response({'token': 'g' * 43, 'target': release.target, 'sha256': digest,
                                'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                                'download_url': SERVICE + '/v1/artifacts/' + release.target}),
                      Response(content)])
    assert client.credential() is not None
    path = commerce.download_release(release, info, tmp_path / 'manga.ldsplugin', provider='patreon')
    assert path.read_bytes() == content
    assert [call[1] for call in remote[1][-2:]] == [SERVICE + '/v1/grants', SERVICE + '/v1/artifacts/' + release.target]


def test_plan_asks_patreon_only_for_products_of_the_patreon_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv('LDS_CONFIG', str(tmp_path / 'config.json'))

    def release(pid):
        return SimpleNamespace(target=pid + '.ldsplugin', price={'kind': 'paid', 'product': 'p'},
                               manifest=SimpleNamespace(id=pid, version='1.0.0'),
                               payload=lambda pid=pid: {'manifest': {'id': pid, 'version': '1.0.0'}})

    @contextmanager
    def sessions(_plugin_id):
        yield []
    owners = {'manga': SimpleNamespace(config=SimpleNamespace(provider='patreon')),
              'paid_tool': SimpleNamespace(config=SimpleNamespace(provider=''))}
    monkeypatch.setattr(service, '_plan_sessions', sessions)
    monkeypatch.setattr(service, '_plan', lambda *a, **k: ([release('manga'), release('paid_tool')], 'x' * 64,
                                                            {'manga', 'paid_tool'}, owners))
    inactive = {'linked': True, 'status': 'inactive', 'tier': None, 'valid_until': None,
                'join_url': JOIN, 'request_url': None}

    class Patreon:
        def library(self):
            return {'status': 'ready', 'items': [], 'patreon': inactive}

    class Commerce:
        def library(self):
            return {'status': 'ready', 'items': [{'plugin_id': 'paid_tool', 'status': 'active'},
                                                 {'plugin_id': 'manga', 'status': 'active'}]}
    monkeypatch.setattr(service, 'PatreonClient', Patreon)
    monkeypatch.setattr(service, 'CommerceClient', Commerce)
    plan = service.preview_plan(None, ['manga', 'paid_tool'])
    # A purchase recorded by ANOTHER service never unlocks a Patreon product.
    assert plan['patreon_required'] == ['manga'] and plan['purchase_required'] == ['manga']
    assert plan['patreon_access'] == {'status': 'ready', 'patreon': inactive}


@pytest.mark.parametrize('status,code,expected', [
    (403, 'acquisition_denied', patreon.MembershipRequired),
    (503, 'verification_required', patreon.VerificationUnavailable),
    (403, 'invalid_grant', commerce.ServiceRefused),
])
def test_grant_refusals_keep_their_meaning(remote, tmp_path, status, code, expected):
    """Inactive membership (Join / Connect) and stale verification (retry) are
    different problems for the person reading the message."""
    client = connected(remote)
    info = SimpleNamespace(path='manga/1.ldsplugin', length=4, hashes={'sha256': 'b' * 64})
    remote[0].append(Response({'error': code}, status))
    with pytest.raises(expected) as error:
        client.download_target('manga', '1.0.0', 'manga/1.ldsplugin', info, tmp_path / 'manga.ldsplugin')
    assert type(error.value) is expected
    assert not (tmp_path / 'manga.ldsplugin').exists()
