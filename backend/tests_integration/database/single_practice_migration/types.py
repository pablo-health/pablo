# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Types for the identity-seeding fixture."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class IdentitySeeder(Protocol):
    def __call__(self, email: str, *, as_user: bool = True, with_mapping: bool = False) -> str: ...


type IdentityFixture = tuple[Engine, IdentitySeeder]
