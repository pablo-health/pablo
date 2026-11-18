# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Choose the Whisper inference device and compute type.

'auto' asks CTranslate2, the engine faster-whisper runs on, how many CUDA
devices it can see. torch is deliberately not consulted: it is not a
dependency of this image (it would add roughly 2 GB), so an ``import torch``
probe always fails and puts a GPU host on the CPU.
"""


def resolve_device(requested: str, compute_type: str) -> tuple[str, str]:
    """Return the (device, compute_type) to load the model with.

    'auto' becomes 'cuda' when CTranslate2 reports at least one CUDA device
    and 'cpu' otherwise. float16 has no CPU kernel, so on the CPU it is
    replaced with int8.
    """
    device = requested
    if device == "auto":
        import ctranslate2  # noqa: PLC0415 - a faster-whisper dependency; imported where used

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"

    if device == "cpu" and compute_type == "float16":
        compute_type = "int8"
    return device, compute_type
