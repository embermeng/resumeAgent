"""src/auth/security.py 纯函数单测(不依赖 DB/app)。

覆盖:密码哈希往返、错密码、access/refresh token 编解码、
type 区分、过期、篡改、缺 sub。这些是认证的最小可信单元。
"""
from datetime import UTC, datetime, timedelta

import jwt

from src.auth import security
from src.config import get_config


# ---------- 密码哈希(argon2id) ----------

def test_hash_verify_roundtrip():
    h = security.hash_password("s3cret-pass")
    assert h != "s3cret-pass"           # 不明文
    assert h.startswith("$argon2")       # pwdlib recommended = argon2id
    assert security.verify_password("s3cret-pass", h) is True


def test_verify_wrong_password_is_false():
    h = security.hash_password("correct-horse")
    assert security.verify_password("wrong-pass", h) is False


def test_hash_is_salted_each_time():
    a = security.hash_password("same-pass")
    b = security.hash_password("same-pass")
    assert a != b                        # 同密码不同盐 → 不同哈希
    assert security.verify_password("same-pass", a)
    assert security.verify_password("same-pass", b)


# ---------- access token ----------

def test_create_and_verify_access_token():
    tok = security.create_access_token({"sub": "42"})
    assert security.verify_access_token(tok) == "42"


def test_access_token_rejected_when_used_as_refresh():
    # access token 的 type=access,decode_refresh_token 应拒绝
    tok = security.create_access_token({"sub": "42"})
    assert security.decode_refresh_token(tok) is None


def test_expired_access_token_returns_none():
    tok = security.create_access_token({"sub": "1"}, expires_delta=timedelta(minutes=-5))
    assert security.verify_access_token(tok) is None


def test_tampered_access_token_returns_none():
    tok = security.create_access_token({"sub": "1"})
    head, _, tail = tok.rpartition(".")
    tampered = head + "." + ("A" + tail[1:] if tail[0] != "A" else "B" + tail[1:])
    assert security.verify_access_token(tampered) is None


def test_access_token_missing_sub_returns_none():
    cfg = get_config().auth
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    tok = jwt.encode({"exp": exp, "type": "access"}, cfg.secret_key, algorithm=cfg.algorithm)
    # options require exp+sub,缺 sub → InvalidTokenError → None
    assert security.verify_access_token(tok) is None


# ---------- refresh token ----------

def test_create_and_decode_refresh_token():
    tok = security.create_refresh_token({"sub": "7", "jti": "abc"})
    payload = security.decode_refresh_token(tok)
    assert payload is not None
    assert payload["sub"] == "7"
    assert payload["jti"] == "abc"
    assert payload["type"] == "refresh"


def test_refresh_token_rejected_when_used_as_access():
    tok = security.create_refresh_token({"sub": "7", "jti": "abc"})
    assert security.verify_access_token(tok) is None


def test_expired_refresh_token_returns_none():
    tok = security.create_refresh_token({"sub": "7", "jti": "x"}, expires_delta=timedelta(days=-1))
    assert security.decode_refresh_token(tok) is None
