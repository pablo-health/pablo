# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Provider-agnostic calendar integration.

Callers ask a provider for capabilities — write my sessions out, tell me
when I'm busy, read my events once so I can import them. Only the provider
knows which of its own OAuth scopes satisfy one, so scope strings never
appear above this layer.

Import the submodules directly (``.capabilities``, ``.provider``,
``.registry``, ``.consent_copy``); this package intentionally re-exports
nothing, because the registry has to import provider implementations and
those implementations import the vocabulary defined here.
"""
