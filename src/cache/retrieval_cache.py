import hashlib
import json
import logging

from src.config import get_config
from src.cache.redis_client import get_sync_client

_log = logging.getLogger(__name__)
settings = get_config()

def _json_default(o):
    """numpy 标量(float32/int64, faiss 与 rank_bm25 产物) -> python 原生类型"""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

def build_key(query, category, top_n, vector_weight, doc_ids):
    norm = " ".join(query.split()).strip().lower()      # 归一化:折叠空白
    dids = ",".join(sorted(doc_ids)) if doc_ids else ""
    raw = f"{norm}|{category}|{top_n}|{vector_weight}|{dids}"
    return settings.redis.key_prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

def cache_get(key):
    try:
        r = get_sync_client()
        res = r.get(key)
        if res is not None:
            return json.loads(res)
        else:
            return None
    except Exception:
        _log.exception("Error retrieving from cache: %s", key)
        return None

def cache_set(key, value, ttl):
    try:
        r = get_sync_client()
        r.set(key, json.dumps(value, default=_json_default), ex=ttl)
    except Exception:
        _log.exception("Error setting cache: key=%s, value_length=%s, ttl=%s", key, len(value), ttl)
