"""FastMCP server exposing Isovalent Enterprise (Cilium, Tetragon, Timescape) tools."""

from __future__ import annotations

import functools
import logging
from typing import Annotated, Any, Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import Settings
from .k8s import Cluster, ClusterError, KubeCluster
from .tools import inventory as inv

log = logging.getLogger("isovalent_mcp")

INSTRUCTIONS = """\
Tools for an Isovalent Enterprise Kubernetes cluster (Cilium, Hubble, Tetragon, Timescape).
Start with get_platform_status to see which components are installed and healthy,
then get_inventory for workloads/services per namespace, and list_policies for existing
Cilium network policies and Tetragon tracing policies. All current tools are read-only.
"""

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

settings = Settings.from_env()
mcp = FastMCP("isovalent-enterprise", instructions=INSTRUCTIONS, version=__version__)

_cluster: Cluster | None = None


def get_cluster() -> Cluster:
    """Connect lazily, so the server starts (and can be inspected) without a cluster."""
    global _cluster
    if _cluster is None:
        try:
            _cluster = KubeCluster()
        except Exception as exc:  # config errors, missing kubeconfig, ...
            raise ToolError(f"Cannot connect to Kubernetes: {exc}") from exc
    return _cluster


def cluster_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn cluster/validation errors into clean ToolErrors for the model."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ClusterError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


@mcp.tool(annotations=READ_ONLY)
@cluster_tool
def get_platform_status() -> dict:
    """Show which Isovalent components (Cilium agent/operator, Hubble Relay/UI, Tetragon,
    Timescape) run in the cluster, their readiness, node coverage and image versions."""
    return inv.platform_status(get_cluster())


@mcp.tool(annotations=READ_ONLY)
@cluster_tool
def get_inventory(
    namespace: Annotated[
        str | None, Field(description="Limit to one namespace. Omit for all application namespaces.")
    ] = None,
    include_system: Annotated[
        bool, Field(description="Also include system namespaces such as kube-system.")
    ] = False,
) -> dict:
    """Inventory of workloads (Deployments, StatefulSets, DaemonSets), their pod labels and
    ports, Services, and which namespaces have network policies, grouped by namespace."""
    return inv.inventory(
        get_cluster(),
        settings.system_namespaces,
        namespace=namespace,
        include_system=include_system,
    )


@mcp.tool(annotations=READ_ONLY)
@cluster_tool
def list_policies(
    namespace: Annotated[
        str | None,
        Field(description="Limit namespaced policies to this namespace. Cluster-wide policies are always shown."),
    ] = None,
    kinds: Annotated[
        list[str] | None,
        Field(description=f"Policy kinds to list. Default: all of {list(inv.POLICY_KINDS)}."),
    ] = None,
    include_spec: Annotated[
        bool, Field(description="Include the full policy spec, not just a summary.")
    ] = False,
) -> dict:
    """List Cilium network policies (namespaced and cluster-wide), Kubernetes NetworkPolicies
    and Tetragon TracingPolicies with a short summary of what each one selects and does."""
    return inv.policies(get_cluster(), namespace=namespace, kinds=kinds, include_spec=include_spec)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if settings.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="http", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
