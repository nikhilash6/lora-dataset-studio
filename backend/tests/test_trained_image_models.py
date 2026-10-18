"""Preparation must resolve the same files Studio will load, without a GPU."""
import json
import struct

import pytest

from app.services import trained_image_models as models


def weights(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({'weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]}}).encode()
    path.write_bytes(struct.pack('<Q', len(header)) + header + b'\0' * 4)
    return path


@pytest.fixture
def model_tree(tmp_path, monkeypatch):
    pins = {}
    roots = {kind: [str(tmp_path / kind), str(tmp_path / 'shared' / kind)]
             for kind in ('diffusion_models', 'text_encoders', 'vae')}
    monkeypatch.setattr(models.comfy_model_paths, 'search_roots', lambda kind: roots[kind])
    monkeypatch.setattr(models.cfg, 'get', lambda key, default=None: pins.get(key, default))
    return tmp_path, pins


def test_qwen21_does_not_offer_old_qwen_or_edit(model_tree):
    root, _ = model_tree
    for name in ('qwen_image.safetensors', 'qwen_image_edit_2511.safetensors',
                 'qwen_image_2.1_int8_convrot.safetensors', 'flux2_klein.safetensors'):
        weights(root / 'diffusion_models' / 'qwen' / name)
    assert [m['filename'].replace('\\', '/') for m in models.list_family_models('qwenimage21')] == [
        'qwen/qwen_image_2.1_int8_convrot.safetensors']


def test_flux_does_not_offer_flux2_klein_or_krea(model_tree):
    root, _ = model_tree
    for name in ('flux1-dev.safetensors', 'flux2-dev.safetensors',
                 'flux2-klein.safetensors', 'flux-krea.safetensors'):
        weights(root / 'diffusion_models' / name)
    assert [m['filename'] for m in models.list_family_models('flux')] == ['flux1-dev.safetensors']


def test_moss_reuses_shared_files_and_reports_its_loader_preparation(model_tree, monkeypatch):
    root, _ = model_tree
    for spec in models.ASSET_SPECS['moss'].values():
        weights(root / 'shared' / spec['kind'] / spec['filename'])
    for filename in ('moss_image_base_bf16.safetensors', 'moss_image_sft_int8_convrot.safetensors',
                     'moss_image_fp8.safetensors', 'other.safetensors'):
        weights(root / 'diffusion_models' / filename)
    offered = [item['filename'] for item in models.list_family_models('moss')]
    assert offered == ['moss_image_sft_bf16.safetensors', 'moss_image_base_bf16.safetensors']
    from app.utils import comfyui
    required = set(models.REQUIRED_NODES['moss'])
    monkeypatch.setattr(comfyui, 'fetch_object_info_classes', lambda: required - {'LDSMossUNETLoader'})
    status = models.generation_readiness('moss')
    assert status['models_ready'] and status['downloads'] == []
    assert status['node_packs'][0]['install_action'] == 'moss_nodes'
    from app import setup_installer
    assert setup_installer.known_action('moss_nodes')
    assert setup_installer._krea_asset_already_installed('studio_moss_text_encoder')


def test_family_folder_does_not_reclassify_an_unrelated_model(model_tree):
    root, _ = model_tree
    weights(root / 'diffusion_models' / 'anima' / 'z_image_turbo.safetensors')
    assert models.list_family_models('anima') == []


def test_encoder_variants_in_deep_shared_folders_are_reused(model_tree):
    root, _ = model_tree
    path = weights(root / 'shared' / 'text_encoders' / 'my-encoders' / 'qwen3vl_8b_bf16.safetensors')
    result = models.resolve_family_assets('qwenimage21')
    assert result['text_encoder'].replace('\\', '/') == 'my-encoders/' + path.name
    assert 'studio_qwenimage21_text_encoder' not in models.missing_assets('qwenimage21')


def test_explicit_pin_is_prioritized_and_missing_pin_preserved(model_tree):
    root, pins = model_tree
    weights(root / 'diffusion_models' / 'flux1-dev.safetensors')
    weights(root / 'diffusion_models' / 'renamed.safetensors')
    pins['studio_models.flux.diffusion_model'] = 'renamed.safetensors'
    assert models.list_family_models('flux')[0]['filename'] == 'renamed.safetensors'
    assert models.resolve_family_assets('flux')['diffusion_model'] == 'renamed.safetensors'
    pins['studio_models.flux.diffusion_model'] = 'missing.safetensors'
    assert models.resolve_family_assets('flux')['diffusion_model'] == 'missing.safetensors'
    assert models.generation_readiness('flux', check_nodes=False)['missing_assets'][0]['name'] == 'missing.safetensors'
    status = models.generation_readiness('flux', check_nodes=False)
    assert 'studio_flux_diffusion_model' not in status['install_actions']
    assert status['pin_warnings'][0]['slot'] == 'diffusion_model'


@pytest.mark.parametrize('pin', ('../outside.safetensors', '/tmp/model.safetensors',
                                 'C:\\models\\x.safetensors', 'bad\nname.safetensors'))
def test_unsafe_pins_are_actionable_without_exposing_paths(model_tree, pin):
    _, pins = model_tree
    pins['studio_models.anima.vae'] = pin
    with pytest.raises(ValueError, match='inside ComfyUI model folders'):
        models.resolve_family_assets('anima')
    status = models.generation_readiness('anima')
    assert not status['ready'] and status['config_error']
    assert pin not in status['config_error']


def test_corrupt_existing_file_is_not_treated_as_installed(model_tree):
    root, _ = model_tree
    spec = models.ASSET_SPECS['anima']['text_encoder']
    path = root / spec['kind'] / spec['filename']
    path.parent.mkdir(parents=True)
    path.write_text('<!doctype html><title>Access denied</title>')
    assert models.invalid_assets('anima')[0]['asset'] == 'studio_anima_text_encoder'
    assert not models.generation_readiness('anima', check_nodes=False)['models_ready']


@pytest.mark.parametrize('family', ('flux', 'anima', 'qwenimage21', 'moss'))
def test_readiness_requires_assets_and_actual_native_nodes(model_tree, monkeypatch, family):
    root, _ = model_tree
    for spec in models.ASSET_SPECS[family].values():
        weights(root / spec['kind'] / spec['filename'])
    from app.utils import comfyui
    monkeypatch.setattr(comfyui, 'fetch_object_info_classes', lambda: None)
    status = models.generation_readiness(family)
    assert status['models_ready'] and not status['ready'] and not status['nodes_checked']
    required = set(models.REQUIRED_NODES[family])
    monkeypatch.setattr(comfyui, 'fetch_object_info_classes', lambda: required)
    assert models.generation_readiness(family)['ready']
    required.remove('KSampler')
    assert models.generation_readiness(family)['missing_nodes'] == ['KSampler']


def test_install_catalog_registered_and_reuses_compatible_existing_file(model_tree):
    root, _ = model_tree
    from app import setup_installer
    downloads = models.model_downloads()
    assert downloads.keys() <= set(setup_installer.INSTALL_ACTIONS)
    spec = models.ASSET_SPECS['qwenimage21']['text_encoder']
    weights(root / 'shared' / spec['kind'] / 'existing' / spec['alternatives'][-1])
    assert setup_installer._krea_asset_already_installed('studio_qwenimage21_text_encoder')
    assert downloads['studio_anima_diffusion_model']['min_free_gb'] < 10


def test_settings_catalog_preserves_controls_when_one_pin_is_invalid(model_tree, monkeypatch):
    _, pins = model_tree
    from app.utils import comfyui
    monkeypatch.setattr(comfyui, 'fetch_object_info_classes', lambda: None)
    pins['studio_models.anima.vae'] = '../bad.safetensors'
    catalog = models.settings_catalog()
    anima = next(f for f in catalog if f['family'] == 'anima')
    assert anima['config_error'] and len(anima['slots']) == 3
    assert len(catalog) == 4


def test_download_details_offer_only_actions_that_repair_the_resolved_family(model_tree):
    root, pins = model_tree
    status = models.generation_readiness('qwenimage21', check_nodes=False)
    assert {item['action'] for item in status['downloads']} == set(status['install_actions'])
    assert len(status['downloads']) == 3
    assert status['downloads'][0] == {
        'action': 'studio_qwenimage21_diffusion_model', 'slot': 'diffusion_model',
        'label': 'Qwen-Image 2.1 diffusion model',
        'filename': 'qwen_image_2.1_int8_convrot.safetensors',
        'size_bytes': 7256783064, 'source_url': 'https://huggingface.co/Comfy-Org/Qwen-Image-2.1',
        'repair': False,
    }
    weights(root / 'text_encoders' / 'qwen3vl_8b_int8_convrot.safetensors')
    corrupt = root / 'vae' / 'qwen_image_2.1_vae_bf16.safetensors'
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text('<!doctype html><title>Access denied</title>')
    pins['studio_models.qwenimage21.diffusion_model'] = 'custom/missing.safetensors'
    status = models.generation_readiness('qwenimage21', check_nodes=False)
    # A download fixes the corrupt automatic VAE, not a custom pin or a valid encoder.
    assert status['install_actions'] == ['studio_qwenimage21_vae']
    assert [(item['slot'], item['repair']) for item in status['downloads']] == [('vae', True)]
    pins['studio_models.qwenimage21.diffusion_model'] = '../unsafe.safetensors'
    status = models.generation_readiness('qwenimage21', check_nodes=False)
    assert status['config_error'] and status['downloads'] == [] and status['install_actions'] == []
