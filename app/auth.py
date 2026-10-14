from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException
from jwt import PyJWKClient


def _settings() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not base_url or not anon_key:
        raise HTTPException(
            status_code=503,
            detail="SaaS authentication is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY.",
        )
    parsed = urlparse(base_url)
    if not parsed.hostname or (
        parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise HTTPException(status_code=503, detail="SUPABASE_URL must use HTTPS outside localhost.")
    if parsed.scheme not in {"https", "http"} or parsed.query or parsed.fragment:
        raise HTTPException(status_code=503, detail="SUPABASE_URL must be a clean HTTP(S) base URL.")
    return base_url, anon_key


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # PyJWT caches the JWKS response briefly and refreshes keys as they rotate.
    return PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)


def verify_access_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token; never trust unverified JWT claims."""
    if not token or len(token) > 16_384:
        raise HTTPException(status_code=401, detail="Missing or invalid access token.")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid access token.") from None

    algorithm = header.get("alg")
    if algorithm not in {"ES256", "RS256", "HS256"}:
        raise HTTPException(status_code=401, detail="Unsupported access token algorithm.")

    try:
        base_url, anon_key = _settings()
        issuer = f"{base_url}/auth/v1"

        if algorithm in {"ES256", "RS256"}:
            signing_key = _jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
                leeway=10,
            )
            if not isinstance(claims.get("sub"), str) or not claims["sub"]:
                raise HTTPException(status_code=401, detail="Invalid access token subject.")
            if claims.get("role") != "authenticated":
                raise HTTPException(status_code=401, detail="Token is not a user access token.")
            return claims

        # Legacy Supabase JWT secrets are symmetric. Do not keep that secret here;
        # ask Supabase Auth to validate these tokens instead.
        response = httpx.get(
            f"{issuer}/user",
            headers={"apikey": anon_key, "Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired access token.")
        user = response.json()
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="Invalid access token subject.")
        return {
            "sub": user_id,
            "email": user.get("email"),
            "role": "authenticated",
            "iss": issuer,
            "aud": "authenticated",
        }
    except HTTPException:
        raise
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="Could not reach the Supabase signing-key endpoint.") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Could not reach Supabase Auth.") from None
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired access token.") from None
