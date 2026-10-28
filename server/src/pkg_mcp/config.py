from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from environment variables (or a .env file in the working directory)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"

    # Transport. stdio for local clients that spawn the server, http for remote ones.
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_path: str = "/mcp"

    # HTTP mode only. Set MCP_AUTH_TOKEN_NEXT during rotation; both are accepted.
    mcp_auth_token: SecretStr | None = None
    mcp_auth_token_next: SecretStr | None = None

    # Error reporting (pkg_mcp.observability). Unset = off.
    sentry_dsn: SecretStr | None = None
    sentry_environment: str = "local"

    def auth_tokens(self) -> list[str]:
        return [t.get_secret_value() for t in (self.mcp_auth_token, self.mcp_auth_token_next) if t]
