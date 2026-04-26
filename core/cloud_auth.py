from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from core.config import Settings


@dataclass(slots=True)
class CloudPrincipal:
    user_id: str
    auth_type: str
    claims: dict[str, Any]


class CloudAuthError(RuntimeError):
    pass


class CloudAuth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwks_client: jwt.PyJWKClient | None = None

    def _bearer_token(self, authorization_header: str | None) -> str:
        if not authorization_header:
            raise CloudAuthError("Missing Authorization header.")
        parts = authorization_header.strip().split(" ", maxsplit=1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise CloudAuthError("Authorization must be Bearer <token>.")
        return parts[1].strip()

    def _authenticate_clerk(self, token: str) -> CloudPrincipal:
        if not self.settings.clerk_auth_enabled:
            raise CloudAuthError("Clerk auth is disabled.")
        if not self.settings.clerk_jwks_url:
            raise CloudAuthError("CLERK_JWKS_URL is not configured.")

        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(self.settings.clerk_jwks_url)

        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        verify_aud = bool(self.settings.clerk_audience)
        verify_iss = bool(self.settings.clerk_issuer)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.settings.clerk_audience if verify_aud else None,
            issuer=self.settings.clerk_issuer if verify_iss else None,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": verify_aud,
                "verify_iss": verify_iss,
            },
        )

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise CloudAuthError("Token is missing subject claim.")

        return CloudPrincipal(user_id=subject, auth_type="clerk", claims=claims)

    def authenticate(self, authorization_header: str | None) -> CloudPrincipal:
        token = self._bearer_token(authorization_header)
        return self._authenticate_clerk(token)
