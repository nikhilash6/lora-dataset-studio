"""Lossless Comfy-Org BF16 → OpenMOSS projection layout, with no tensor copies."""


def split_qkv(state):
    for key in list(state):
        if not (key.startswith('layers.') and key.endswith('.proj.qkv.weight')):
            continue
        tensor = state[key]
        names = [key.replace('.qkv.', f'.{name}.') for name in ('q', 'k', 'v')]
        if (len(tensor.shape) != 2 or tensor.shape[0] != 3 * tensor.shape[1]
                or any(name in state for name in names)):
            raise ValueError('MOSS needs complete BF16 diffusion weights with an unambiguous QKV layout.')
        # Quantized fused matrices need a different transform for their scales.
        if any(k.startswith(key.removesuffix('weight')) and k != key for k in state):
            raise ValueError('Select the BF16 MOSS diffusion model; quantized diffusion is not supported.')
        for name, value in zip(names, tensor.chunk(3, dim=0)):
            state[name] = value
        del state[key]
    return state
