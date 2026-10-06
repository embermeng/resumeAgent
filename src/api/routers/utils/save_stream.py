import json, logging, redis
from src.cache.redis_client import get_sync_client

_log = logging.getLogger(__name__)

def _buf_key(sid): return f"chat:stream:{sid}"
def conv_key(sid): return f"chat:stream:{sid}:conv"      # 归属映射

def last_is_terminal(sid):
    try:
        r = get_sync_client()
        last = r.lindex(_buf_key(sid), -1)
        if not last:                 # 空列表 / None 守卫
            return True
        parsed = json.loads(last)
        # 终态看 event：正常 done、错误 error、截断 interrupted 都算终态
        return parsed.get("event") in ("done", "error")
    except redis.RedisError:
        return True

def buffer_event(sid, seq, event, data, ttl):
    try:
        r = get_sync_client()
        r.rpush(_buf_key(sid), json.dumps({"seq": seq, "event": event, "data": data}, ensure_ascii=False))
        r.expire(_buf_key(sid), ttl)                          # 每次刷新 TTL
    except redis.RedisError:
        _log.error("buffer_event 写失败,降级跳过(不影响对话)", exc_info=True)

def read_buffer(sid, after):                              # 重放 seq>after,已按 rpush 顺序升序
    try:
        r = get_sync_client()
        return [e for e in map(json.loads, r.lrange(_buf_key(sid), 0, -1)) if e["seq"] > after]
    except redis.RedisError:
        return []

def save_conv_mapping(sid, conv_id, ttl):
    try:
        get_sync_client().setex(conv_key(sid), ttl, str(conv_id))
    except redis.RedisError:
        _log.error("save_conv_mapping 写失败,降级跳过(不影响对话)", exc_info=True)