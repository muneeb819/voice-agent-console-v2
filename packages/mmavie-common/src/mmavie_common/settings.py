"""Base settings every MMA-VIE service inherits.

All configuration comes from environment variables. Services fail closed: they refuse to
start without service tokens unless auth is explicitly disabled in a dev/test environment.
"""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]

# Tokens with this prefix are for local development only and are refused outside dev/test.
DEV_TOKEN_PREFIX = "dev-"  # noqa: S105 - a marker prefix, not a secret


class ConfigError(RuntimeError):
    """Raised when a service is started with an unsafe or incomplete configuration."""


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MMAVIE_", extra="ignore")

    service_name: str = "mmavie-service"
    environment: Environment = "dev"
    log_level: str = "INFO"

    # Inbound auth: comma-separated "caller:secret" pairs accepted as Bearer tokens.
    service_tokens: str = ""
    auth_disabled: bool = False

    def token_map(self) -> dict[str, str]:
        """Return {secret: caller_name}. Bare secrets get the caller name 'client'."""
        out: dict[str, str] = {}
        for raw in self.service_tokens.split(","):
            item = raw.strip()
            if not item:
                continue
            name, sep, secret = item.partition(":")
            if not sep:
                name, secret = "client", item
            name, secret = name.strip(), secret.strip()
            if not secret:
                raise ConfigError(f"Empty secret for caller '{name}' in MMAVIE_SERVICE_TOKENS")
            out[secret] = name
        return out

    @property
    def is_production_like(self) -> bool:
        return self.environment in ("staging", "prod")

    def validate_for_start(self) -> None:
        if self.auth_disabled:
            if self.is_production_like:
                raise ConfigError("MMAVIE_AUTH_DISABLED is not allowed in staging/prod")
            return
        tokens = self.token_map()
        if not tokens:
            raise ConfigError("Set MMAVIE_SERVICE_TOKENS (or MMAVIE_AUTH_DISABLED=true in dev/test only)")
        if self.is_production_like:
            weak = [name for secret, name in tokens.items() if secret.startswith(DEV_TOKEN_PREFIX) or len(secret) < 32]
            if weak:
                raise ConfigError(
                    f"Service tokens for {sorted(weak)} are dev tokens or shorter than 32 characters; "
                    "use secrets from AWS Secrets Manager in staging/prod"
                )
