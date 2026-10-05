"""The Patreon catalog serves its namespaces: a members-only plugin published
after a core release reaches the Store, its plan and its updates with no core
update. Real signed catalogs on loopback servers; only the grant download is
replaced, so no Patreon account is involved."""
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from tuf.api.metadata import TargetFile

from app.plugins import storage
from app.plugins.manifest import parse_manifest
from app.plugins.store import client, patreon, service
from app.plugins.store.catalog import parse_catalog
from app.plugins.store.client import StoreConfig, StoreError
from public_store_fixtures import contract
from test_private_plugin_sources import PID, package, private_source  # noqa: F401
from test_public_store_service import acquisition  # noqa: F401
from test_public_store_tuf import QuietHandler, operator, repository  # noqa: F401

PATREON_PRICE = {'kind': 'paid', 'product': 'lds_member', 'provider': 'patreon'}


def publish_patreon(state, *releases):
    """Sign the Patreon catalog. As on the real service, archive bytes are not
    public: only their TUF target metadata is."""
    catalog, targets = {'schema_version': 1, 'products': []}, {}
    for plugin_id, version in releases:
        value, artifacts = package(version, plugin_id)
        value['products'][0]['releases'][0]['price'] = dict(PATREON_PRICE)
        catalog['products'].extend(value['products'])
        for name, data in artifacts.items():
            targets[name] = TargetFile.from_data(name, data, ['sha256'])
            state.archives[name] = data
    plan = {'history_path': state.keys / 'published.json', 'history': b'{}', 'writes': [],
            'targets': targets, 'registry_path': None, 'registry': b''}
    operator._publish(state.repo, state.keys, catalog, {}, prepared=plan)
    state.catalog = catalog


@pytest.fixture
def patreon_catalog(acquisition, monkeypatch):
    _public, _keys, public_config, root = acquisition
    folder = root / 'patreon-service'
    state = SimpleNamespace(root=root, public=public_config, repo=folder / 'tuf',
                            keys=root / 'patreon-keys', archives={})
    operator.initialize(state.repo, state.keys)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(folder)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = root / 'patreon.json'
    path.write_text(json.dumps({
        'format': 'lds-patreon/1', 'service_url': f'http://127.0.0.1:{server.server_port}',
        'checkout_origin': 'https://www.patreon.com', 'join_url': 'https://www.patreon.com/c/example',
        'catalog': {'root_path': str(state.repo / 'bootstrap.root.json'), 'first_party_ids': [],
                    'plugin_ids': [], 'plugin_namespaces': ['patreon']}}), encoding='utf-8')
    monkeypatch.setenv(patreon.CONFIG_ENV, str(path))
    # Listed nowhere in the configuration: only the namespace can serve them.
    publish_patreon(state, ('patreon.toolkit', '1.1.0'), ('studio.other', '1.0.0'), ('manga', '1.0.0'))
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_a_new_namespace_plugin_appears_in_the_store(patreon_catalog):
    catalog = service.browse()
    assert catalog['status'] == 'ready'
    assert [p['id'] for p in catalog['products']] == ['patreon.toolkit']
    # The Store marks "For Patreon members" from this label (isPatreonRelease).
    assert catalog['products'][0]['releases'][0]['price'] == PATREON_PRICE


def test_install_plan_and_download_go_through_the_patreon_catalog(patreon_catalog, monkeypatch):
    plan = service.preview_plan(None, 'patreon.toolkit')
    assert [(p['manifest']['id'], p['manifest']['version'], p['action']) for p in plan['packages']] == [
        ('patreon.toolkit', '1.1.0', 'install')]
    assert plan['patreon_required'] == ['patreon.toolkit']
    assert plan['patreon_access']['status'] == 'not_connected'
    grants = []

    def granted(release, target_info, destination, *, provider=''):
        grants.append((release.manifest.id, provider))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(patreon_catalog.archives[release.target])
        return destination
    monkeypatch.setattr(service, 'download_release', granted)
    assert service.prepare(None, 'patreon.toolkit', None, plan['plan_id'])['ok']
    assert grants == [('patreon.toolkit', 'patreon')]
    pending, errors = storage.pending(patreon_catalog.root / 'installed')
    assert set(pending) == {'patreon.toolkit'} and errors == []


def test_an_installed_namespace_plugin_updates_from_the_patreon_catalog(patreon_catalog):
    manifest = parse_manifest(contract(id='patreon.toolkit', name='Synthetic product', version='1.0.0',
                                       publisher={'id': 'studio', 'name': 'Studio'}), Path('.'))
    records = SimpleNamespace(records={'patreon.toolkit': SimpleNamespace(
        manifest=manifest, legacy=None, state='loaded', bundled=False)})
    assert client.config_for_plugins('patreon.toolkit').provider == 'patreon'
    plan = service.preview_plan(records, ['patreon.toolkit'], update_only=True)
    assert [(p['manifest']['id'], p['previous_version'], p['manifest']['version']) for p in plan['packages']] == [
        ('patreon.toolkit', '1.0.0', '1.1.0')]
    assert plan['patreon_required'] == ['patreon.toolkit']


def test_sources_json_keeps_a_namespace_identifier_it_claims(private_source, patreon_catalog):
    source = private_source
    artifacts = {}
    for plugin_id, version in (('patreon.toolkit', '1.0.1'), ('patreon.extra', '9.0.0')):
        catalog, archive = package(version, plugin_id)
        source.catalog['products'].extend(catalog['products'])
        artifacts.update(archive)
    operator.publish(source.private, source.keys, source.catalog, artifacts)
    # The local catalog claims patreon.toolkit; it merely lists patreon.extra.
    source.source['plugin_ids'] = [PID, 'patreon.toolkit']
    source.path.write_text(json.dumps({'sources': [source.source]}), encoding='utf-8')
    publish_patreon(patreon_catalog, ('patreon.toolkit', '1.1.0'), ('patreon.extra', '1.0.0'))

    assert client.config_for_plugins('patreon.toolkit') == client.load_private_configs()[0]
    assert client.config_for_plugins('patreon.extra').provider == 'patreon'
    products = service.browse()['products']
    assert sorted(p['id'] for p in products) == ['camera_angles', 'patreon.extra', 'patreon.toolkit', PID]
    releases = {p['id']: p['releases'][0] for p in products}
    assert (releases['patreon.toolkit']['manifest']['version'], releases['patreon.toolkit']['price']) == (
        '1.0.1', {'kind': 'free'})
    assert (releases['patreon.extra']['manifest']['version'], releases['patreon.extra']['price']) == (
        '1.0.0', PATREON_PRICE)
    # One review across both catalogs: neither one serves the other's identifier.
    plan = service.preview_plan(None, ['patreon.toolkit', 'patreon.extra'])
    assert {(p['manifest']['id'], p['manifest']['version']) for p in plan['packages']} == {
        ('patreon.toolkit', '1.0.1'), ('patreon.extra', '1.0.0')}
    assert plan['patreon_required'] == ['patreon.extra']


def test_no_undeclared_namespace_lds_identifier_or_other_source(patreon_catalog):
    # Same signed catalog: only the declared namespace is read from it.
    assert set(parse_catalog(patreon_catalog.catalog, client.patreon_source([]))) == {'patreon.toolkit'}
    for plugin_id in ('studio.other', 'manga'):
        assert client.config_for_plugins(plugin_id) is None
        with pytest.raises(StoreError, match='not available'):
            service.preview_plan(None, plugin_id)
    # The public catalog refuses a namespace product, even a free one, and no
    # source but the Patreon one can be given a namespace.
    free, _ = package('1.1.0', 'patreon.toolkit')
    with pytest.raises(StoreError):
        parse_catalog(free, patreon_catalog.public)
    with pytest.raises(StoreError):
        StoreConfig('https://s.example.test/m/', 'https://s.example.test/t/', b'{}', frozenset(),
                    external_namespaces=frozenset({'patreon'}))
