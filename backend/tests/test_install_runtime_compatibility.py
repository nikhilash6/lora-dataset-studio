"""V2 distribution files and lifecycle ownership across supported launchers."""
from pathlib import Path

import pytest

from app.plugins.routes import restart_payload
from app.services import updater


@pytest.mark.parametrize('action, desired, blocked', [
    (None, True, False), ('update', True, False),
    ('disable', False, True), ('remove', True, True), ('update', False, True),
])
def test_restart_only_checks_retained_resource_blockers_when_disabling(action, desired, blocked):
    from types import SimpleNamespace
    from app.plugins.restart import _plugin_blockers, RestartBlocked

    registry = SimpleNamespace(hooks={'plugin.disable_blockers': [
        ('owner', lambda reasons, pid: reasons + ['Retained rental needs this plugin.'])]})
    changes = [{'id': 'owner', 'pending_action': action, 'desired_enabled': desired}]
    if blocked:
        with pytest.raises(RestartBlocked, match='Retained rental'):
            _plugin_blockers(registry, changes)
    else:
        _plugin_blockers(registry, changes)


def test_restart_still_checks_active_plugin_gpu_work():
    from types import SimpleNamespace
    from app.plugins.restart import _plugin_blockers, RestartBlocked

    registry = SimpleNamespace(hooks={'comfyui.restart_blockers': [
        ('owner', lambda reasons: reasons + ['A render is running.'])]})
    with pytest.raises(RestartBlocked, match='render is running'):
        _plugin_blockers(registry)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['Dockerfile', 'Dockerfile.gpu'])
def test_images_include_the_public_store_trust_files(name):
    # Copy only the shipped public store files, never an operator's store tree.
    source = (ROOT / name).read_text(encoding='utf-8')
    assert 'COPY store/bootstrap.json store/public-root.json store/patreon.json store/' in source
    for name in ('bootstrap.json', 'public-root.json', 'patreon.json'):
        assert (ROOT / 'store' / name).is_file()


@pytest.mark.parametrize(('runtime', 'supervisor', 'mode'), [
    ('docker', '', 'container'),
    ('docker-external-comfy', '', 'container'),
    ('docker-gpu', '', 'container'),
    ('docker-gpu', 'supervisor', 'self'),
    ('pinokio', '', 'pinokio'),
    ('pinokio', 'supervisor', 'pinokio'),
    ('', 'supervisor', 'self'),
    ('', '', 'manual'),
])
def test_plugin_restart_belongs_to_the_launcher(monkeypatch, runtime, supervisor, mode):
    monkeypatch.setenv('LDS_RUNTIME', runtime)
    monkeypatch.setenv('LDS_RESTART_MODE', supervisor)
    payload = restart_payload()
    assert payload['mode'] == mode
    assert payload['can_apply'] is (mode == 'self')


@pytest.mark.parametrize(('runtime', 'command'), [
    ('docker', 'docker compose up -d --build'),
    ('docker-gpu', 'docker compose -f docker-compose.gpu.yml up -d --build'),
    ('docker-external-comfy', 'docker compose -f docker-compose.yml '
     '-f docker-compose.external-comfy.yml '
     '-f .docker-compose.external-comfy.override.yml up -d --build'),
])
def test_all_docker_lanes_refuse_in_place_code_updates(client, monkeypatch, runtime, command):
    monkeypatch.setenv('LDS_RUNTIME', runtime)

    def forbidden(*args, **kwargs):
        raise AssertionError('A Docker update must not download or replace live code.')

    monkeypatch.setattr(updater, 'is_git_checkout', forbidden)
    monkeypatch.setattr(updater, 'apply_update', forbidden)
    monkeypatch.setattr(updater, 'start_zip_update', forbidden)
    response = client.post('/api/update/apply')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['install_mode'] == 'docker'
    assert payload['ok'] is False and payload['can_apply'] is False
    assert payload['instructions'][-1] == command


@pytest.mark.parametrize('busy', [False, True])
def test_gpu_plugin_apply_checks_work_before_supervised_restart(client, app, monkeypatch, busy):
    from app import setup_installer
    from app.plugins import restart, routes
    from app.plugins import environment

    # These process-wide registries can contain fake jobs left by other test
    # modules. This case varies work owned by LDS, which must still block exit.
    monkeypatch.setattr(setup_installer, '_runs', {'test': {'state': 'running'}} if busy else {})
    monkeypatch.setattr(setup_installer, '_pip_current', None)
    monkeypatch.setattr(setup_installer, '_pip_queue', [])
    monkeypatch.setattr(environment, '_RUNNING', {})
    monkeypatch.setenv('LDS_RUNTIME', 'docker-gpu')
    monkeypatch.setenv('LDS_RESTART_MODE', 'supervisor')
    monkeypatch.setattr(routes, 'lifecycle_payload', lambda registry: {
        'pending_restart': True, 'boot_id': 'test-boot'})
    checked = []

    def comfy_warning():
        checked.append('comfy')
        return 'ComfyUI is not running. LDS will restart anyway.'

    def schedule(**kwargs):
        checked.append('restart')
        assert kwargs == {'block_during_update': True}

    monkeypatch.setattr(restart, '_comfy_restart_warning', comfy_warning)
    monkeypatch.setattr(updater, 'schedule_restart', schedule)
    gate = app.extensions['lds_plugin_restart_gate']
    try:
        response = client.post('/api/plugins/apply')
        assert response.status_code == (409 if busy else 200), response.get_json()
        assert checked == ([] if busy else ['comfy', 'restart'])
        if busy:
            assert response.get_json()['code'] == 'restart_blocked'
        else:
            assert response.get_json()['warnings']
    finally:
        gate.release()
        gate.finished.set()


@pytest.mark.parametrize('comfy_state', [
    'stopped', 'remote-offline', 'timeout', 'http-error', 'invalid-json',
    'invalid-queue', 'invalid-url', 'running', 'pending', 'idle',
])
def test_external_comfyui_state_never_blocks_plugin_apply(client, app, monkeypatch, comfy_state):
    import errno
    from types import SimpleNamespace

    import requests
    from app import config, setup_installer
    from app.plugins import environment, restart, routes

    monkeypatch.setattr(setup_installer, '_runs', {})
    monkeypatch.setattr(setup_installer, '_pip_current', None)
    monkeypatch.setattr(setup_installer, '_pip_queue', [])
    monkeypatch.setattr(environment, '_RUNNING', {})
    monkeypatch.setenv('LDS_RUNTIME', '')
    monkeypatch.setenv('LDS_RESTART_MODE', 'supervisor')
    monkeypatch.setattr(routes, 'lifecycle_payload', lambda registry: {
        'pending_restart': True, 'boot_id': 'test-boot'})
    comfy_url = ('invalid' if comfy_state == 'invalid-url' else
                 'http://comfy.invalid:8188' if comfy_state == 'remote-offline' else
                 'http://127.0.0.1:8188')
    # A stopped ComfyUI must work even if installation was never skipped.
    config_get = config.get
    overrides = {'comfyui.api_url': comfy_url, 'comfyui.setup_skipped': False}
    monkeypatch.setattr(config, 'get', lambda key, *args, **kw:
                        overrides[key] if key in overrides else config_get(key, *args, **kw))

    def queue_response(url, **kwargs):
        assert url.endswith('/queue')
        assert kwargs['allow_redirects'] is False
        if comfy_state in ('stopped', 'remote-offline'):
            raise requests.ConnectionError(ConnectionRefusedError(errno.ECONNREFUSED, 'offline'))
        if comfy_state == 'timeout':
            raise requests.Timeout('unavailable')

        def read_json():
            if comfy_state == 'invalid-json':
                raise ValueError('invalid response')
            if comfy_state == 'invalid-queue':
                return {'queue_running': None, 'queue_pending': []}
            return {'queue_running': [['external-prompt']] if comfy_state == 'running' else [],
                    'queue_pending': [['external-prompt']] if comfy_state == 'pending' else []}

        return SimpleNamespace(status_code=503 if comfy_state == 'http-error' else 200, json=read_json)

    monkeypatch.setattr(restart.requests, 'get', queue_response)
    scheduled = []
    monkeypatch.setattr(updater, 'schedule_restart', lambda **kw: scheduled.append(kw))
    gate = app.extensions['lds_plugin_restart_gate']
    try:
        response = client.post('/api/plugins/apply')
        payload = response.get_json()
        assert response.status_code == 200, payload
        assert payload['ok'] and payload['restarting']
        assert scheduled == [{'block_during_update': True}]
        assert bool(payload['warnings']) is (comfy_state != 'idle')
        if comfy_state == 'stopped':
            assert 'not running' in payload['warnings'][0]
        assert gate.frozen, 'admissions must stay closed until the server exits'
    finally:
        gate.release()
