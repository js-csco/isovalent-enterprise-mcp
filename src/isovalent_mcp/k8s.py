"""Thin Kubernetes access layer.

Everything returned here is plain JSON-shaped dicts (camelCase keys, exactly as
the API server sends them). That keeps the tool logic independent of the
kubernetes client's model classes and easy to test with fixture data.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

Obj = dict[str, Any]

# CRDs we care about: (group, version, plural, namespaced)
CRDS: dict[str, tuple[str, str, str, bool]] = {
    "CiliumNetworkPolicy": ("cilium.io", "v2", "ciliumnetworkpolicies", True),
    "CiliumClusterwideNetworkPolicy": ("cilium.io", "v2", "ciliumclusterwidenetworkpolicies", False),
    "TracingPolicy": ("cilium.io", "v1alpha1", "tracingpolicies", False),
    "TracingPolicyNamespaced": ("cilium.io", "v1alpha1", "tracingpoliciesnamespaced", True),
}


class ClusterError(RuntimeError):
    """A Kubernetes call failed; the message is meant to be shown to the model."""


class Cluster(Protocol):
    def list_namespaces(self) -> list[Obj]: ...
    def list_pods(self, namespace: str | None = None) -> list[Obj]: ...
    def list_workloads(self, namespace: str | None = None) -> list[Obj]: ...
    def list_services(self, namespace: str | None = None) -> list[Obj]: ...
    def list_network_policies(self, namespace: str | None = None) -> list[Obj]: ...
    def list_crd(self, kind: str, namespace: str | None = None) -> list[Obj] | None: ...


def _explain(exc: ApiException, what: str) -> ClusterError:
    if exc.status == 403:
        return ClusterError(
            f"Forbidden while listing {what}. The MCP server's service account is "
            "missing RBAC permissions (see deploy/k8s/rbac.yaml)."
        )
    if exc.status == 401:
        return ClusterError(f"Unauthorized while listing {what}: check kubeconfig / token.")
    return ClusterError(f"Kubernetes API error while listing {what}: {exc.status} {exc.reason}")


class KubeCluster:
    """Real cluster access. Uses in-cluster config when running as a pod,
    otherwise the local kubeconfig (KUBECONFIG / ~/.kube/config)."""

    def __init__(self) -> None:
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
        else:
            config.load_kube_config(context=os.getenv("KUBE_CONTEXT") or None)
        self._api = client.ApiClient()
        self._core = client.CoreV1Api(self._api)
        self._apps = client.AppsV1Api(self._api)
        self._net = client.NetworkingV1Api(self._api)
        self._custom = client.CustomObjectsApi(self._api)

    def _items(self, result: Any) -> list[Obj]:
        return self._api.sanitize_for_serialization(result).get("items", [])

    def _list(self, what: str, ns_fn, all_fn, namespace: str | None) -> list[Obj]:
        try:
            result = ns_fn(namespace) if namespace else all_fn()
        except ApiException as exc:
            raise _explain(exc, what) from exc
        return self._items(result)

    def list_namespaces(self) -> list[Obj]:
        try:
            return self._items(self._core.list_namespace())
        except ApiException as exc:
            raise _explain(exc, "namespaces") from exc

    def list_pods(self, namespace: str | None = None) -> list[Obj]:
        return self._list(
            "pods",
            self._core.list_namespaced_pod,
            self._core.list_pod_for_all_namespaces,
            namespace,
        )

    def list_workloads(self, namespace: str | None = None) -> list[Obj]:
        out: list[Obj] = []
        for kind, ns_fn, all_fn in (
            ("Deployment", self._apps.list_namespaced_deployment, self._apps.list_deployment_for_all_namespaces),
            ("StatefulSet", self._apps.list_namespaced_stateful_set, self._apps.list_stateful_set_for_all_namespaces),
            ("DaemonSet", self._apps.list_namespaced_daemon_set, self._apps.list_daemon_set_for_all_namespaces),
        ):
            for item in self._list(kind, ns_fn, all_fn, namespace):
                item["kind"] = kind  # list items don't carry kind
                out.append(item)
        return out

    def list_services(self, namespace: str | None = None) -> list[Obj]:
        return self._list(
            "services",
            self._core.list_namespaced_service,
            self._core.list_service_for_all_namespaces,
            namespace,
        )

    def list_network_policies(self, namespace: str | None = None) -> list[Obj]:
        return self._list(
            "networkpolicies",
            self._net.list_namespaced_network_policy,
            self._net.list_network_policy_for_all_namespaces,
            namespace,
        )

    def list_crd(self, kind: str, namespace: str | None = None) -> list[Obj] | None:
        """List a custom resource. Returns None if the CRD isn't installed."""
        group, version, plural, namespaced = CRDS[kind]
        try:
            if namespaced and namespace:
                result = self._custom.list_namespaced_custom_object(group, version, namespace, plural)
            else:
                result = self._custom.list_cluster_custom_object(group, version, plural)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise _explain(exc, plural) from exc
        return result.get("items", [])
