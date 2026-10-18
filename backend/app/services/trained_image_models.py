"""Shared model discovery, preparation and readiness for trained image families.

Only ComfyUI loader-relative names leave this module. Discovery, Settings and
the installer use the same candidates so an existing asset is not downloaded
again just because the user stores it under an extra model root.
"""
from __future__ import annotations

import ntpath
import os
import re

from .. import config as cfg
from ..utils.trained_image_workflows import ASSET_SPECS, REQUIRED_NODES
from . import comfy_model_paths, model_integrity

FAMILY_LABELS = {'flux': 'FLUX.1', 'anima': 'Anima', 'qwenimage21': 'Qwen-Image 2.1', 'moss': 'MOSS-Image'}


def _specs(family):
    if family not in ASSET_SPECS:
        raise ValueError('Unknown image generation family')
    return ASSET_SPECS[family]


def _relative_name(value):
    name = str(value or '').replace('\\', '/')
    if (not name or ntpath.isabs(name) or ':' in name or any(ord(c) < 32 for c in name)
            or ntpath.splitdrive(name)[0]
            or any(p in ('', '.', '..') for p in name.split('/'))):
        return None
    return name


def _key(name):
    return str(name).replace('\\', '/').casefold()


def _candidates(kind):
    return [(rel, ab) for rel, ab in comfy_model_paths.list_models(kind)
            if _relative_name(rel) and rel.lower().endswith(('.safetensors', '.sft'))]


def _looks_like_family(family, name):
    basename = str(name).replace('\\', '/').rsplit('/', 1)[-1]
    norm = re.sub(r'[^a-z0-9]+', '', basename.lower())
    if family == 'moss':
        return 'mossimage' in norm and not any(s in norm for s in ('int8', 'fp8', 'gptq'))
    if family == 'flux':
        return ('flux' in norm and not any(s in norm for s in ('flux2', 'klein', 'krea')))
    if family == 'anima':
        return 'anima' in norm
    # Qwen Image, Qwen Image Edit and Qwen 2.1 have different architectures.
    return 'qwenimage21' in norm and not any(s in norm for s in ('edit', 'rgba'))


def _pin(family, slot):
    return cfg.get(f'studio_models.{family}.{slot}', '') or ''


def list_family_models(family):
    """Installed base models, including a valid explicit pin for a renamed build."""
    _specs(family)
    pinned = _relative_name(_pin(family, 'diffusion_model'))
    names = [rel for rel, _ in _candidates('diffusion_models')
             if _looks_like_family(family, rel) or (pinned and _key(rel) == _key(pinned))]
    preferred = [_key(n) for n in _specs(family)['diffusion_model']['alternatives']]
    def priority(name):
        basename = _key(os.path.basename(name.replace('\\', '/')))
        return (0 if pinned and _key(name) == _key(pinned) else 1,
                preferred.index(basename) if basename in preferred else len(preferred),
                name.casefold())
    return [{'filename': name, 'displayName': os.path.basename(name.replace('\\', '/'))}
            for name in sorted(set(names), key=priority)]


def _resolve_slot(family, slot, chosen=None):
    spec = _specs(family)[slot]
    candidates = _candidates(spec['kind'])
    explicit = chosen if chosen is not None else _pin(family, slot)
    if explicit:
        normalized = _relative_name(explicit)
        if not normalized:
            raise ValueError(f'{FAMILY_LABELS[family]} {spec["label"]}: choose a model name inside ComfyUI model folders')
        exact = next((rel for rel, _ in candidates if _key(rel) == _key(normalized)), None)
        # Do not silently replace an absent explicit pin with another build.
        return exact or normalized
    alternatives = spec.get('alternatives') or (spec['filename'],)
    for preferred in alternatives:
        match = next((rel for rel, _ in candidates
                      if _key(os.path.basename(rel.replace('\\', '/'))) == _key(preferred)), None)
        if match:
            return match
    if slot == 'diffusion_model':
        models = list_family_models(family)
        if models:
            return models[0]['filename']
    # A diagnostic graph still needs a concrete expected filename. Readiness
    # and Studio's existing preflight block submission when it is absent.
    return spec['filename']


def resolve_family_assets(family, base_model=None):
    return {slot: _resolve_slot(family, slot, base_model if slot == 'diffusion_model' else None)
            for slot in _specs(family)}


generation_assets = resolve_family_assets


def install_action(family, slot):
    return f'studio_{family}_{slot}'


def model_downloads():
    """Installer catalog, derived from the graph's official model specifications."""
    result = {}
    for family, specs in ASSET_SPECS.items():
        for slot, spec in specs.items():
            is_base = slot == 'diffusion_model'
            result[install_action(family, slot)] = {
                'url': spec['url'],
                'dest': (spec['kind'], family, spec['filename']) if is_base
                        else (spec['kind'], spec['filename']),
                'legacy_names': tuple(n for n in spec.get('alternatives', ()) if n != spec['filename']),
                'label': f'{FAMILY_LABELS[family]} {spec["label"]}',
                'min_free_gb': spec['min_free_gb'],
                'min_bytes': 1024 ** 3 if is_base else 1024 ** 2,
                'gated': bool(spec.get('gated', False)),
                'license_url': f'https://huggingface.co/{spec["repo_id"]}',
            }
    return result


def _asset_status(family, assets):
    missing, invalid = [], []
    for slot, spec in _specs(family).items():
        name = assets[slot]
        # Match full loader names, not the resolver's basename fallback: two
        # subfolders may hold different models with the same basename.
        path = next((ab for rel, ab in _candidates(spec['kind']) if _key(rel) == _key(name)), None)
        action = install_action(family, slot)
        if not path:
            missing.append({'slot': slot, 'name': name, 'kind': spec['kind'],
                            'label': spec['label'], 'action': action})
            continue
        check = model_integrity.validate_model_file(path)
        if check.get('blocking'):
            invalid.append({'asset': action, 'slot': slot, 'name': name,
                            'reason': check['reason'], 'blocking': True})
    return missing, invalid


def missing_assets(family):
    missing, _ = _asset_status(family, resolve_family_assets(family))
    return [item['action'] for item in missing]


def invalid_assets(family):
    _, invalid = _asset_status(family, resolve_family_assets(family))
    return invalid


def generation_readiness(family, base_model=None, *, check_nodes=True):
    _specs(family)
    try:
        assets = resolve_family_assets(family, base_model)
    except ValueError as exc:
        return {'family': family, 'label': FAMILY_LABELS[family], 'assets': {},
                'ready': False, 'models_ready': False, 'nodes_checked': False,
                'missing_assets': [], 'invalid_assets': [], 'missing_nodes': [],
                'install_actions': [], 'downloads': [], 'config_error': str(exc)}
    missing, invalid = _asset_status(family, assets)
    classes = None
    if check_nodes:
        from ..utils.comfyui import fetch_object_info_classes
        classes = fetch_object_info_classes()
    missing_nodes = sorted(set(REQUIRED_NODES[family]) - classes) if classes is not None else []
    broken_slots = {item['slot'] for item in (*missing, *invalid)}
    pin_warnings = [
        {'slot': slot, 'message': 'The selected file is unavailable or invalid. Choose another file, '
         'or clear this selection and save to install the recommended model.'}
        for slot in broken_slots if _pin(family, slot)]
    pinned_slots = {item['slot'] for item in pin_warnings}
    # A canonical download cannot repair a custom pin pointing somewhere else.
    actions = ([m['action'] for m in missing if m['slot'] not in pinned_slots]
               + [m['asset'] for m in invalid if m['slot'] not in pinned_slots])
    actions = list(dict.fromkeys(actions))
    repairs = {item['asset'] for item in invalid}
    downloads = [
        {'action': action, 'slot': slot, 'label': spec['label'],
         'filename': spec['filename'], 'size_bytes': spec['size_bytes'],
         'source_url': f'https://huggingface.co/{spec["repo_id"]}',
         'repair': action in repairs}
        for slot, spec in _specs(family).items()
        if (action := install_action(family, slot)) in actions]
    from .lora_test_studio import studio_missing_node_hints
    return {'family': family, 'label': FAMILY_LABELS[family], 'assets': assets,
            'ready': not missing and not invalid and not missing_nodes and classes is not None,
            'models_ready': not missing and not invalid,
            'nodes_checked': classes is not None,
            'missing_assets': missing, 'invalid_assets': invalid,
            'missing_nodes': missing_nodes, 'node_packs': studio_missing_node_hints(missing_nodes),
            'install_actions': actions, 'downloads': downloads,
            'pin_warnings': pin_warnings}


def settings_catalog():
    """Small on-demand Settings payload; never starts a download or a render."""
    out = []
    for family, specs in ASSET_SPECS.items():
        status = generation_readiness(family)
        out.append({**status, 'slots': [
            {'key': slot, 'label': spec['label'], 'filename': spec['filename'],
             'kind': spec['kind'], 'action': install_action(family, slot),
             'source_url': f'https://huggingface.co/{spec["repo_id"]}',
             'files': ([m['filename'] for m in list_family_models(family)] if slot == 'diffusion_model'
                       else [rel for rel, _ in _candidates(spec['kind'])])}
            for slot, spec in specs.items()]})
    return out
