"""auth 路由全链路测试(SQLite 隔离,见 conftest)。

对齐 docs/specs/api-contract.md §4.11~4.18。重点固化两条回归价值最高的行为:
  - jti 轮换 + 复用检测触发 family 连坐(test_refresh_*);
  - logout 真吊销 family(test_logout_*)——用副作用验证,不只看 204。
"""
import uuid

# cookie 名与关键 detail 文案(照抄代码,契约与之一致)
COOKIE = "refresh-token"


def _sfx():
    return uuid.uuid4().hex[:10]


def _register(client, username=None, email=None, password="password123"):
    username = username or f"u_{_sfx()}"
    email = email or f"{username}@qq.com"
    r = client.post("/api/auth/register",
                    json={"username": username, "email": email, "password": password})
    return r, username, email


def _login(client, email, password="password123"):
    """返回 (access_token, refresh_cookie_value)。用 per-request cookie 精确控制,清掉 jar 避免串味。"""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    access = r.json()["access_token"]
    refresh = r.cookies.get(COOKIE)
    client.cookies.clear()
    return access, refresh


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- 注册 ----------

def test_register_returns_201_user_private(client):
    r, username, email = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == username
    assert body["email"] == email          # email 小写归一后回显
    assert "password_hash" not in body     # 绝不泄露哈希


def test_register_email_lowercased(client):
    r, _, _ = _register(client, email=f"MiXeD_{_sfx()}@QQ.com")
    assert r.status_code == 201
    assert r.json()["email"] == r.json()["email"].lower()


def test_register_duplicate_username_409(client):
    uname = f"dup_{_sfx()}"
    _register(client, username=uname, email=f"a_{uname}@qq.com")
    r, _, _ = _register(client, username=uname, email=f"b_{uname}@qq.com")
    assert r.status_code == 409
    assert r.json()["detail"] == "Username already exists"


def test_register_duplicate_email_409(client):
    email = f"e_{_sfx()}@qq.com"
    _register(client, email=email)
    r, _, _ = _register(client, email=email.upper())   # 大小写不敏感查重
    assert r.status_code == 409
    assert r.json()["detail"] == "Email already registered"


def test_register_weak_password_422(client):
    r = client.post("/api/auth/register",
                    json={"username": f"w_{_sfx()}", "email": f"w_{_sfx()}@qq.com", "password": "short"})
    assert r.status_code == 422   # password < 8


# ---------- 登录 ----------

def test_login_returns_token_and_sets_cookie(client):
    _, _, email = _register(client)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"access_token", "token_type"}
    assert r.json()["token_type"] == "bearer"
    sc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in sc and "path=/api/auth" in sc and "samesite=strict" in sc
    assert r.cookies.get(COOKIE)


def test_login_wrong_password_401(client):
    _, _, email = _register(client)
    r = client.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"


def test_login_case_insensitive_email(client):
    _, _, email = _register(client)
    r = client.post("/api/auth/login", json={"email": email.upper(), "password": "password123"})
    assert r.status_code == 200


# ---------- me / 401 矩阵 ----------

def test_me_returns_user_private(client):
    _, _, email = _register(client)
    tok, _ = _login(client, email)
    r = client.get("/api/auth/me", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["email"] == email
    assert "password_hash" not in r.text


def test_me_missing_header_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_me_invalid_token_401(client):
    r = client.get("/api/auth/me", headers=_auth("garbage.token.here"))
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired token"


def test_me_refresh_token_as_access_401(client):
    _, _, email = _register(client)
    _, refresh = _login(client, email)
    r = client.get("/api/auth/me", headers=_auth(refresh))   # 拿 refresh 当 access
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired token"


# ---------- refresh 轮换 + 复用检测(family 连坐) ----------

def test_refresh_rotates_cookie(client):
    _, _, email = _register(client)
    _, old = _login(client, email)
    r = client.post("/api/auth/refresh", cookies={COOKIE: old})
    assert r.status_code == 200
    assert "access_token" in r.json()
    new = r.cookies.get(COOKIE)
    assert new and new != old          # cookie 已轮换


def test_refresh_reuse_old_jti_revokes_family(client):
    _, _, email = _register(client)
    _, old = _login(client, email)
    # 正常轮换拿到 new
    r = client.post("/api/auth/refresh", cookies={COOKIE: old})
    new = r.cookies.get(COOKIE)
    # 复用已吊销的 old → 泄露信号 → 401 revoked
    r2 = client.post("/api/auth/refresh", cookies={COOKIE: old})
    assert r2.status_code == 401
    assert r2.json()["detail"] == "Refresh token is revoked"
    # family 连坐:刚轮换出来的 new 也被吊销
    r3 = client.post("/api/auth/refresh", cookies={COOKIE: new})
    assert r3.status_code == 401
    assert r3.json()["detail"] == "Refresh token is revoked"


def test_refresh_missing_cookie_401(client):
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"] == "Refresh token is missing"


def test_refresh_invalid_cookie_401(client):
    r = client.post("/api/auth/refresh", cookies={COOKIE: "not.a.jwt"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid refresh token"


# ---------- logout(副作用验证:吊销后 refresh 必 401) ----------

def test_logout_revokes_family(client):
    _, _, email = _register(client)
    _, refresh = _login(client, email)
    r = client.post("/api/auth/logout", cookies={COOKIE: refresh})
    assert r.status_code == 204
    # logout 后同一 refresh 再用 → 已吊销
    r2 = client.post("/api/auth/refresh", cookies={COOKIE: refresh})
    assert r2.status_code == 401
    assert r2.json()["detail"] == "Refresh token is revoked"


def test_logout_without_cookie_is_204(client):
    r = client.post("/api/auth/logout")   # 幂等:无 cookie 也 204
    assert r.status_code == 204


# ---------- 用户查询 / 修改 / 删除 ----------

def test_get_user_public_no_email(client):
    r, username, _ = _register(client)
    uid = r.json()["id"]
    r2 = client.get(f"/api/auth/user/{uid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["username"] == username
    assert "email" not in body            # 公开视图不含 email


def test_get_user_404(client):
    r = client.get("/api/auth/user/99999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "User not found"


def test_patch_self_updates_username(client):
    r, _, email = _register(client)
    uid = r.json()["id"]
    tok, _ = _login(client, email)
    newname = f"renamed_{_sfx()}"
    r2 = client.patch(f"/api/auth/{uid}", headers=_auth(tok), json={"username": newname})
    assert r2.status_code == 200
    assert r2.json()["username"] == newname
    assert r2.json()["email"] == email    # 未改的字段保留


def test_patch_other_user_403(client):
    r1, _, email1 = _register(client)
    uid1 = r1.json()["id"]
    _, _, email2 = _register(client)
    tok2, _ = _login(client, email2)
    r = client.patch(f"/api/auth/{uid1}", headers=_auth(tok2), json={"username": "hacked"})
    assert r.status_code == 403
    assert r.json()["detail"] == "Not authorized to update this user"


def test_delete_self_then_gone(client):
    r, _, email = _register(client)
    uid = r.json()["id"]
    tok, _ = _login(client, email)
    r2 = client.delete(f"/api/auth/{uid}", headers=_auth(tok))
    assert r2.status_code == 204
    r3 = client.get(f"/api/auth/user/{uid}")
    assert r3.status_code == 404          # 已删除


def test_delete_other_user_403(client):
    r1, _, email1 = _register(client)
    uid1 = r1.json()["id"]
    _, _, email2 = _register(client)
    tok2, _ = _login(client, email2)
    r = client.delete(f"/api/auth/{uid1}", headers=_auth(tok2))
    assert r.status_code == 403
    assert r.json()["detail"] == "Not authorized to delete this user"
