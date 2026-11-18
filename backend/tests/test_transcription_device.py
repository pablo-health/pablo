# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Device choice for the transcription worker (services/transcription/device.py).

The worker image carries CTranslate2 (via faster-whisper) and not torch, so
'auto' must be answered by CTranslate2. CTranslate2 is not installed in the
backend environment; each test installs a stand-in module whose CUDA device
count it controls, and blocks ``import torch`` so a regression that reaches
for torch again fails here rather than silently picking the CPU.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_DEVICE_PY = Path(__file__).resolve().parents[2] / "services" / "transcription" / "device.py"


def _load_device_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("transcription_device", _DEVICE_PY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


device = _load_device_module()


@pytest.fixture
def cuda_devices(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Install a ctranslate2 stand-in; set ``cuda_devices[0]`` to the count it reports."""
    count = [0]
    fake = ModuleType("ctranslate2")
    fake.get_cuda_device_count = lambda: count[0]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    # None in sys.modules makes ``import torch`` raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)
    return count


def test_auto_picks_cuda_when_ctranslate2_sees_a_gpu(cuda_devices: list[int]) -> None:
    cuda_devices[0] = 1
    assert device.resolve_device("auto", "float16") == ("cuda", "float16")


def test_auto_falls_back_to_cpu_int8_without_a_gpu(cuda_devices: list[int]) -> None:
    cuda_devices[0] = 0
    assert device.resolve_device("auto", "float16") == ("cpu", "int8")


def test_auto_on_cpu_keeps_a_non_float16_compute_type(cuda_devices: list[int]) -> None:
    cuda_devices[0] = 0
    assert device.resolve_device("auto", "float32") == ("cpu", "float32")


def test_explicit_device_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ModuleType("ctranslate2")

    def _unexpected() -> int:
        raise AssertionError("an explicit device must not probe CUDA")

    fake.get_cuda_device_count = _unexpected  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert device.resolve_device("cuda", "float16") == ("cuda", "float16")
    assert device.resolve_device("cpu", "float16") == ("cpu", "int8")
