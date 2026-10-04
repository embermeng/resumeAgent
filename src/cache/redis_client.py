import redis
from functools import lru_cache

from src.config import get_config


@lru_cache
def get_sync_client() -> redis.Redis:
    return redis.Redis.from_url(get_config().redis.url, decode_responses=True)

@lru_cache
def get_async_client() -> redis.asyncio.Redis:
    return redis.asyncio.Redis.from_url(get_config().redis.url, decode_responses=True)

def ping() -> bool:
    try:
        return get_sync_client().ping()
    except redis.RedisError:
        return False
