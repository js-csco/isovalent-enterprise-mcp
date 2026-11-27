"""In-memory stand-in for a cluster: Cilium Enterprise + Tetragon + Timescape and a
few astronomy-shop (OpenTelemetry demo) workloads."""

from __future__ import annotations


def pod(name, ns, labels, image, node="node-1", ready=True, owner=True):
    meta = {"name": name, "namespace": ns, "labels": labels}
    if owner:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "name": name.rsplit("-", 1)[0]}]
    return {
        "metadata": meta,
        "spec": {"nodeName": node, "containers": [{"name": "c", "image": image}]},
        "status": {
            "phase": "Running" if ready else "Pending",
            "containerStatuses": [{"ready": ready}],
        },
    }


def deployment(name, ns, labels, image, ports, replicas=1, ready=1):
    return {
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "replicas": replicas,
            "template": {
                "metadata": {"labels": {**labels, "pod-template-hash": "abc"}},
                "spec": {
                    "serviceAccountName": name,
                    "containers": [
                        {"name": name, "image": image, "ports": [{"containerPort": p, "name": "http"} for p in ports]}
                    ],
                },
            },
        },
        "status": {"readyReplicas": ready},
    }


def service(name, ns, selector, port, target):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"type": "ClusterIP", "selector": selector, "ports": [{"port": port, "targetPort": target}]},
    }


class FakeCluster:
    def __init__(self, with_tetragon_crds: bool = True):
        self.with_tetragon_crds = with_tetragon_crds
        self.namespaces = ["kube-system", "tetragon", "hubble-timescape", "astronomy-shop", "mcp"]
        self.pods = [
            pod("cilium-aaaaa", "kube-system", {"k8s-app": "cilium"}, "quay.io/isovalent/cilium:v1.18.3-cee.1", "node-1"),
            pod("cilium-bbbbb", "kube-system", {"k8s-app": "cilium"}, "quay.io/isovalent/cilium:v1.18.3-cee.1", "node-2"),
            pod("cilium-operator-1", "kube-system", {"io.cilium/app": "operator", "app.kubernetes.io/name": "cilium-operator"},
                "quay.io/isovalent/operator-generic:v1.18.3-cee.1"),
            pod("hubble-relay-1", "kube-system", {"k8s-app": "hubble-relay"}, "quay.io/isovalent/hubble-relay:v1.18.3-cee.1",
                ready=False),
            pod("tetragon-xxxxx", "tetragon", {"app.kubernetes.io/name": "tetragon"}, "quay.io/isovalent/tetragon:v1.5.0"),
            pod("hubble-timescape-0", "hubble-timescape", {"app.kubernetes.io/name": "hubble-timescape"},
                "quay.io/isovalent/hubble-timescape:v1.6.0"),
            pod("frontend-7d9-x1", "astronomy-shop", {"app.kubernetes.io/name": "frontend"}, "otel/demo:frontend"),
            pod("cart-5f4-x1", "astronomy-shop", {"app.kubernetes.io/name": "cart"}, "otel/demo:cart"),
            pod("valkey-cart-0", "astronomy-shop", {"app.kubernetes.io/name": "valkey-cart"}, "valkey/valkey:8"),
            pod("debug", "astronomy-shop", {"run": "debug"}, "busybox", owner=False),
        ]
        self.workloads = [
            deployment("frontend", "astronomy-shop", {"app.kubernetes.io/name": "frontend"}, "otel/demo:frontend", [8080]),
            deployment("cart", "astronomy-shop", {"app.kubernetes.io/name": "cart"}, "otel/demo:cart", [8080]),
            deployment("valkey-cart", "astronomy-shop", {"app.kubernetes.io/name": "valkey-cart"}, "valkey/valkey:8", [6379]),
        ]
        self.services = [
            service("frontend", "astronomy-shop", {"app.kubernetes.io/name": "frontend"}, 8080, 8080),
            service("valkey-cart", "astronomy-shop", {"app.kubernetes.io/name": "valkey-cart"}, 6379, 6379),
        ]
        self.crds = {
            "CiliumNetworkPolicy": [
                {
                    "metadata": {"name": "cart-ingress", "namespace": "astronomy-shop"},
                    "spec": {
                        "endpointSelector": {"matchLabels": {"app.kubernetes.io/name": "cart"}},
                        "ingress": [{"fromEndpoints": [{"matchLabels": {"app.kubernetes.io/name": "frontend"}}]}],
                    },
                }
            ],
            "CiliumClusterwideNetworkPolicy": [],
            "TracingPolicy": [
                {
                    "metadata": {"name": "suspicious-exec"},
                    "spec": {
                        "kprobes": [
                            {
                                "call": "sys_execve",
                                "selectors": [{"matchActions": [{"action": "Post"}]}],
                            }
                        ]
                    },
                }
            ],
            "TracingPolicyNamespaced": [],
        }

    def _filter(self, objs, namespace):
        return [o for o in objs if namespace is None or o["metadata"].get("namespace") == namespace]

    def list_namespaces(self):
        return [{"metadata": {"name": n}} for n in self.namespaces]

    def list_pods(self, namespace=None):
        return self._filter(self.pods, namespace)

    def list_workloads(self, namespace=None):
        return self._filter(self.workloads, namespace)

    def list_services(self, namespace=None):
        return self._filter(self.services, namespace)

    def list_network_policies(self, namespace=None):
        return []

    def list_crd(self, kind, namespace=None):
        if kind.startswith("Tracing") and not self.with_tetragon_crds:
            return None
        return self._filter(self.crds[kind], namespace)
