import secrets
from datetime import timedelta
from urllib.parse import urlparse, parse_qs

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select, func

from app.models import User, OAuthAttempt
from app.services.errors import DomainError
from app.services.telegram_auth import (
    pkce_challenge,
    validate_id_token,
    finish_login,
    start_login,
    consume_attempt,
    digest,
)
from app.utils.time import now
from app.routers import auth


@pytest.fixture
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public["kid"] = "test-rsa-key"
    return private, public


def signed(keypair, **overrides):
    private, public = keypair
    claims = {
        "iss": "https://oauth.telegram.org",
        "aud": "12345",
        "sub": "opaque-subject-555",
        "id": 100001,
        "iat": int(now().timestamp()),
        "exp": int((now() + timedelta(minutes=5)).timestamp()),
        "preferred_username": "developer",
        "given_name": "Developer",
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": public["kid"]})


def test_pkce_rfc7636_vector():
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_signature_valid_and_identity_uses_id(keypair, db):
    claims = validate_id_token(signed(keypair), [keypair[1]], "12345")
    session = {}
    first = finish_login(db, session, claims)
    assert first.telegram_id == 100001
    claims["preferred_username"] = "changed_name"
    second = finish_login(db, session, claims)
    assert first.id == second.id
    assert second.username == "changed_name"
    assert db.scalar(select(func.count(User.id))) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example"},
        {"aud": "another-client"},
        {"exp": 1},
        {"iat": 9999999999},
        {"id": -1},
        {"id": True},
        {"id": "opaque"},
        {"azp": "attacker"},
        {"aud": ["12345", "other"]},
        {"sub": ""},
    ],
)
def test_reject_invalid_claims(keypair, overrides):
    with pytest.raises(DomainError):
        validate_id_token(signed(keypair, **overrides), [keypair[1]], "12345")


def test_reject_tampered_token(keypair):
    token = signed(keypair)
    body = token.split(".")
    body[1] = body[1][:-4] + "AAAA"
    with pytest.raises(DomainError):
        validate_id_token(".".join(body), [keypair[1]], "12345")


def test_reject_wrong_key_and_algorithm(keypair):
    with pytest.raises(DomainError):
        validate_id_token(
            jwt.encode({"id": 1}, "test-only-hmac-key-with-more-than-32-characters", algorithm="HS256"),
            [keypair[1]],
            "12345",
        )
    second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = RSAAlgorithm.to_jwk(second.public_key(), as_dict=True)
    public["kid"] = keypair[1]["kid"]
    with pytest.raises(DomainError):
        validate_id_token(signed(keypair), [public], "12345")


def test_state_browser_binding_expiry_and_replay(db):
    session = {}
    url = start_login(db, session)
    query = parse_qs(urlparse(url).query)
    state = query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    with pytest.raises(DomainError):
        consume_attempt(db, {"oauth_browser": secrets.token_urlsafe(32)}, state)
    verifier = consume_attempt(db, session, state)
    assert pkce_challenge(verifier) == query["code_challenge"][0]
    with pytest.raises(DomainError):
        consume_attempt(db, session, state)
    other = {}
    query = parse_qs(urlparse(start_login(db, other)).query)
    row = db.get(OAuthAttempt, digest(query["state"][0]))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(DomainError):
        consume_attempt(db, other, query["state"][0])


def test_oidc_callback_cookie_logout(client, keypair, monkeypatch, db):
    async def exchange(code, verifier):
        assert code == "returned-code"
        assert len(verifier) >= 43
        return validate_id_token(signed(keypair), [keypair[1]], "12345")

    monkeypatch.setattr(auth.telegram_auth, "exchange_code", exchange)
    response = client.get("/auth/telegram/start", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)
    response = client.get("/auth/telegram/callback", params={"state": query["state"][0], "code": "returned-code"})
    assert response.status_code == 200 and "developer" in response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    import re

    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    assert client.post("/logout", data={"csrf_token": csrf}).status_code == 200
    assert client.get("/profile").status_code == 401
    assert (
        client.get("/auth/telegram/callback", params={"state": query["state"][0], "code": "returned-code"}).status_code
        == 400
    )


async def test_real_exchange_helper_uses_basic_pkce_and_jwks(keypair, monkeypatch):
    import base64
    from urllib.parse import parse_qs
    import httpx
    from app.services import telegram_auth

    token = signed(keypair)
    seen = []

    def transport(request):
        seen.append(str(request.url))
        if request.url.path == "/token":
            assert request.headers["authorization"] == "Basic " + base64.b64encode(b"12345:test-client-secret").decode()
            form = parse_qs(request.content.decode())
            assert form["code_verifier"] == ["test-verifier"]
            assert form["redirect_uri"] == ["http://testserver/auth/telegram/callback"]
            assert form["grant_type"] == ["authorization_code"]
            return httpx.Response(200, json={"id_token": token})
        assert request.url.path == "/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [keypair[1]]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        telegram_auth.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs),
    )
    monkeypatch.setitem(telegram_auth._jwks, "expires", 0)
    claims = await telegram_auth.exchange_code("test-code", "test-verifier")
    assert claims["id"] == 100001
    assert len(seen) == 2
