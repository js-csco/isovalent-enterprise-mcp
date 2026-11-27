"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SYSTEM_NAMESPACES = (
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "local-path-storage",
    "portainer",
)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # "stdio" for local clients, "http" (streamable HTTP) when running as a container.
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 8000
    # Write tools (apply policies, ...) are registered only when this is true.
    enable_write_tools: bool = False
    # Namespaces hidden from the inventory unless include_system=True.
    system_namespaces: tuple[str, ...] = field(default=DEFAULT_SYSTEM_NAMESPACES)

    @classmethod
    def from_env(cls) -> "Settings":
        ns = os.getenv("SYSTEM_NAMESPACES")
        return cls(
            transport=os.getenv("MCP_TRANSPORT", "stdio"),
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8000")),
            enable_write_tools=_bool(os.getenv("ENABLE_WRITE_TOOLS")),
            system_namespaces=(
                tuple(s.strip() for s in ns.split(",") if s.strip())
                if ns
                else DEFAULT_SYSTEM_NAMESPACES
            ),
        )
