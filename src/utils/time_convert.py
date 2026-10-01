from datetime import datetime


def to_epoch(v):
    if v is None:
        return None                      # 还没设置(如运行中的 finished_at)
    if isinstance(v, datetime):
        return v.timestamp()             # B 路:datetime → epoch float
    return float(v)                      # A 路:本来就是 epoch float,原样返回
