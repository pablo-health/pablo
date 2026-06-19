"""Unit test for the Vertex client factory's import guard."""

from __future__ import annotations

import builtins

import pytest

from backend.app.services.vertex_client import vertex_genai_client


def test_runtime_error_when_genai_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def boom(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google"):
            raise ImportError("simulated missing google-genai")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", boom)
    with pytest.raises(RuntimeError, match="google-genai"):
        vertex_genai_client()
