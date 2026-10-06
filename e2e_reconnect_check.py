"""端到端联调:验证「POST 生成中断线 → Redis 缓冲 → GET 带 Last-Event-ID 重连重放」真实链路。

前提:docker compose -f docker-compose.dev.yml up 已起(后端 8000 + Redis 6380 + DB 5433)。
用法:python e2e_reconnect_check.py

重点验证 mock 测试测不到的真实行为:
  客户端断开后,uvicorn 下同步生成器的 close()/GC 是否触发 finally 补 interrupted 终态。
"""
import json
import time
import uuid

import httpx
import redis

BASE = "http://localhost:8000"
REDIS_URL = "redis://localhost:6380/0"
PROMPT = "你好，请用一句话介绍你能帮我做什么"
READ_FRAMES_BEFORE_DISCONNECT = 4   # POST 读够几帧就主动断线


def parse_sse(line_iter):
    """把 SSE 行流解析为帧 dict {id, event, data};空行产出一帧。"""
    cur = {"id": None, "event": None, "data": None}
    for line in line_iter:
        if line == "":
            if cur["event"] is not None or cur["data"] is not None:
                yield cur
            cur = {"id": None, "event": None, "data": None}
            continue
        if line.startswith("id:"):
            cur["id"] = line[3:].strip()
        elif line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            try:
                cur["data"] = json.loads(raw)
            except json.JSONDecodeError:
                cur["data"] = raw
    if cur["event"] is not None or cur["data"] is not None:
        yield cur


def dump_buf(r, stream_id, label):
    buf = r.lrange(f"chat:stream:{stream_id}", 0, -1)
    print(f"[Redis:{label}] 缓冲 {len(buf)} 条:")
    last_ev = None
    for raw in buf:
        e = json.loads(raw)
        last_ev = e["event"]
        print(f"    seq={e['seq']} event={e['event']} data={str(e['data'])[:44]}")
    return last_ev


def main():
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    print(f"[Redis] ping={r.ping()}")

    # 1. 注册 + 登录
    u = uuid.uuid4().hex[:10]
    email = f"e2e_{u}@qq.com"
    reg = httpx.post(f"{BASE}/api/auth/register",
                     json={"username": f"e2e_{u}", "email": email, "password": "password123"},
                     timeout=30)
    print(f"[注册] {reg.status_code} {reg.text[:80]}")
    login = httpx.post(f"{BASE}/api/auth/login",
                       json={"email": email, "password": "password123"}, timeout=30)
    if login.status_code != 200:
        print(f"[登录] 失败 {login.status_code} {login.text[:120]}")
        return
    tok = login.json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    print(f"[登录] 200 token={tok[:12]}...")

    # 2. POST 流式,读 N 帧后主动断线
    stream_id = None
    got = []
    print(f"\n[POST] 流式对话,读够 {READ_FRAMES_BEFORE_DISCONNECT} 帧就断开...")
    t0 = time.time()
    with httpx.stream("POST", f"{BASE}/api/chat",
                      json={"prompt": PROMPT}, headers=H, timeout=60) as resp:
        print(f"[POST] HTTP {resp.status_code}")
        for frame in parse_sse(resp.iter_lines()):
            got.append((frame["id"], frame["event"]))
            if frame["event"] == "conversation":
                stream_id = (frame["data"] or {}).get("stream_id")
                print(f"[POST] seq={frame['id']} conversation stream_id={stream_id} "
                      f"conv_id={(frame['data'] or {}).get('conversation_id')}")
            else:
                print(f"[POST] seq={frame['id']} event={frame['event']} data={str(frame['data'])[:44]}")
            if len(got) >= READ_FRAMES_BEFORE_DISCONNECT:
                print(f"[POST] 已读 {len(got)} 帧 → 主动断开")
                break
    print(f"[POST] 断开,耗时 {time.time()-t0:.2f}s,收到 seq={[g[0] for g in got]}")

    if not stream_id:
        print("!! 没拿到 stream_id,终止")
        return

    # 3. 查 Redis 缓冲:断线后 finally 是否补了 interrupted
    print(f"[Redis] 归属映射 :conv = {r.get(f'chat:stream:{stream_id}:conv')}")
    time.sleep(0.5)
    last1 = dump_buf(r, stream_id, "断线后0.5s")
    if last1 not in ("done", "error"):
        print("[Redis] 末尾非终态,再等 2.5s 看 GC/close 是否补帧...")
        time.sleep(2.5)
        last1 = dump_buf(r, stream_id, "断线后3s")

    # 4. GET 重连,带 Last-Event-ID = 断点最后 seq
    last_seq = got[-1][0] or "-1"
    print(f"\n[GET] 重连 Last-Event-ID={last_seq}(期望只重放 seq>{last_seq})")
    replay = []
    try:
        with httpx.stream("GET", f"{BASE}/api/chat/{stream_id}/stream",
                          headers={**H, "Last-Event-ID": str(last_seq)}, timeout=20) as resp:
            print(f"[GET] HTTP {resp.status_code}")
            for frame in parse_sse(resp.iter_lines()):
                replay.append((frame["id"], frame["event"]))
                print(f"[GET] 重放 seq={frame['id']} event={frame['event']} data={str(frame['data'])[:44]}")
                if frame["event"] in ("done", "error"):
                    print("[GET] 读到终态,收流")
                    break
    except httpx.ReadTimeout:
        print("[GET] !! 20s 内没读到终态(说明断线未补 interrupted,重连在轮询空等)")

    # 5. 结论
    print("\n===== 联调结论 =====")
    print(f"POST 收到 seq : {[g[0] for g in got]}")
    print(f"Redis 末尾事件: {last1}  (终态={'是' if last1 in ('done','error') else '否'})")
    print(f"GET  重放 seq : {[x[0] for x in replay]}")
    nums = [int(x[0]) for x in replay if x[0] not in (None, "")]
    print(f"重放均 > Last-Event-ID({last_seq}): {all(n > int(last_seq) for n in nums) if nums else 'N/A'}")
    print(f"重放收到终态: {'是' if replay and replay[-1][1] in ('done','error') else '否'}")


if __name__ == "__main__":
    main()
