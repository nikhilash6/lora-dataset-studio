"""Namespaced MOSS loaders; dependencies are imported only when used by ComfyUI."""
from .weights import split_qkv


def load_moss_model(path):
    import torch
    from comfy import utils
    from .vendor.loaders import load_model_state

    state, metadata = utils.load_torch_file(path, safe_load=True, return_metadata=True)
    split_qkv(state)
    expected = set(state)
    model = load_model_state(state, torch.bfloat16, disable_dynamic=True, metadata=metadata)
    if expected != set(model.model.diffusion_model.state_dict()):
        raise ValueError('MOSS diffusion weights do not match this model. Select a complete BF16 build.')
    model.cached_patcher_init = (load_moss_model, (path,))
    return model


class LDSMossUNETLoader:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        return {'required': {'unet_name': (folder_paths.get_filename_list('diffusion_models'),)}}

    RETURN_TYPES = ('MODEL',)
    FUNCTION = 'load'
    CATEGORY = 'LDS/MOSS'

    def load(self, unet_name):
        import folder_paths
        return (load_moss_model(folder_paths.get_full_path_or_raise('diffusion_models', unet_name)),)


class LDSMossCLIPLoader:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        return {'required': {'clip_name': (folder_paths.get_filename_list('text_encoders'),)}}

    RETURN_TYPES = ('CLIP',)
    FUNCTION = 'load'
    CATEGORY = 'LDS/MOSS'

    def load(self, clip_name):
        import folder_paths
        from .vendor.loaders import load_clip
        path = folder_paths.get_full_path_or_raise('text_encoders', clip_name)
        return (load_clip(path, 'default', disable_dynamic=True),)


try:
    # Do not advertise readiness on an older ComfyUI lacking the Qwen3.5 runtime.
    from .vendor import loaders as _runtime
    if not hasattr(_runtime.sd.TEModel, 'QWEN35_9B'):
        raise ImportError('Qwen3.5 runtime unavailable')
except (ImportError, AttributeError):
    import logging
    logging.getLogger(__name__).warning('MOSS loaders need ComfyUI with Qwen3.5 support. Update ComfyUI and restart.')
    NODE_CLASS_MAPPINGS = {}
else:
    NODE_CLASS_MAPPINGS = {'LDSMossUNETLoader': LDSMossUNETLoader, 'LDSMossCLIPLoader': LDSMossCLIPLoader}
NODE_DISPLAY_NAME_MAPPINGS = {
    'LDSMossUNETLoader': 'MOSS BF16 Diffusion Model (LDS)',
    'LDSMossCLIPLoader': 'MOSS Qwen3.5 Text Encoder (LDS)',
}
