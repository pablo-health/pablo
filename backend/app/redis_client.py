# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Redis client singleton.

Returns None when USE_REDIS=false (self-hosted / single-instance mode).
Callers should always check for None before using the client.
"""

import logging
from functools import lru_cache

import redis

from .settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis | None:
    """Get a shared Redis client, or None if Redis is disabled."""
    settings = get_settings()
    if not settings.use_redis:
        return None

    client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value() or None,
        db=settings.redis_db,
        ssl=settings.redis_ssl,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )

    try:
        client.ping()
        logger.info("Redis connected: %s:%s", settings.redis_host, settings.redis_port)
    except redis.ConnectionError:
        logger.warning(
            "Redis unavailable at %s:%s — falling back to in-memory stores",
            settings.redis_host,
            settings.redis_port,
        )
        return None

    return client
