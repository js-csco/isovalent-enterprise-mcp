"""Read-only inventory logic. Pure functions over a `Cluster`, so they can be
unit-tested with fixture data and no live cluster."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from ..k8s import Cluster, Obj

# Labels worth showing to the model: they're what policies select on later.
_NOISY_LABELS = {"pod-template-hash", "controller-revision-hash", "pod-template-generation"}

# Platform components, matched against the k8s-app / app.kubernetes.io/name / app labels.
_COMPONENTS = {
    "cilium": "cilium-agent",
    "cilium-operator": "cilium-operator",
    "cilium-envoy": "cilium-envoy",
    "hubble-relay": "hubble-relay",
    "hubble-ui": "hubble-ui",
    "hubble-enterprise": "hubble-enterprise",
    "tetragon": "tetragon",
    "tetragon-operator": "tetragon-operator",
}


def _meta(obj: Obj) -> Obj:
    return obj.get("metadata") or {}


def _labels(obj: Obj) -> dict[str, str]:
    return {k: v for k, v in (_meta(obj).get("labels") or {}).items() if k not in _NOISY_LABELS}


def _pod_ready(pod: Obj) -> bool:
    status = pod.get("status") or {}
    if status.get("phase") != "Running":
        return False
    statuses = status.get("containerStatuses") or []
    return bool(statuses) and all(cs.get("ready") for cs in statuses)


def _component_of(pod: Obj) -> str | None:
    labels = _meta(pod).get("labels") or {}
    name = _meta(pod).get("name", "")
    candidates = [labels.get("k8s-app"), labels.get("app.kubernetes.io/name"), labels.get("app")]
    for value in candidates:
        if value and "timescape" in value:
            return value  # e.g. hubble-timescape, hubble-timescape-ingester
        if value in _COMPONENTS:
            return _COMPONENTS[value]
    if "timescape" in name:
        return "timescape:" + name.rsplit("-", 2)[0]
    return None


# --------------------------------------------------------------------------- #
# get_platform_status
# --------------------------------------------------------------------------- #

def platform_status(cluster: Cluster) -> Obj:
    """Health and versions of Cilium, Hubble, Tetragon and Timescape components."""
    groups: dict[str, list[Obj]] = defaultdict(list)
    for pod in cluster.list_pods():
        comp = _component_of(pod)
        if comp:
            groups[comp].append(pod)

    components = []
    for comp, pods in sorted(groups.items()):
        images = sorted(
            {c.get("image", "") for p in pods for c in (p.get("spec") or {}).get("containers", [])}
        )
        not_ready = [_meta(p).get("name") for p in pods if not _pod_ready(p)]
        components.append(
            {
                "component": comp,
                "namespace": sorted({_meta(p).get("namespace") for p in pods}),
                "pods_ready": f"{len(pods) - len(not_ready)}/{len(pods)}",
                "nodes": len({(p.get("spec") or {}).get("nodeName") for p in pods}),
                "images": images,
                "enterprise": any("isovalent" in i for i in images),
                "not_ready_pods": not_ready,
            }
        )

    found = {c["component"] for c in components}
    missing = [
        name
        for name, present in (
            ("cilium-agent", "cilium-agent" in found),
            ("hubble-relay", "hubble-relay" in found),
            ("tetragon", "tetragon" in found),
            ("timescape", any("timescape" in c for c in found)),
        )
        if not present
    ]
    return {"components": components, "not_found": missing}


# --------------------------------------------------------------------------- #
# get_inventory
# --------------------------------------------------------------------------- #

def _ports(containers: Iterable[Obj]) -> list[str]:
    return sorted(
        {
            f"{p.get('containerPort')}/{p.get('protocol', 'TCP')}"
            + (f" ({p['name']})" if p.get("name") else "")
            for c in containers
            for p in c.get("ports") or []
        }
    )


def _workload_summary(w: Obj) -> Obj:
    spec = w.get("spec") or {}
    status = w.get("status") or {}
    template = spec.get("template") or {}
    containers = (template.get("spec") or {}).get("containers") or []
    if w["kind"] == "DaemonSet":
        desired, ready = status.get("desiredNumberScheduled", 0), status.get("numberReady", 0)
    else:
        desired, ready = spec.get("replicas", 1), status.get("readyReplicas", 0) or 0
    return {
        "kind": w["kind"],
        "name": _meta(w).get("name"),
        "ready": f"{ready}/{desired}",
        "pod_labels": _labels(template),
        "service_account": (template.get("spec") or {}).get("serviceAccountName", "default"),
        "images": [c.get("image") for c in containers],
        "container_ports": _ports(containers),
    }


def _service_summary(s: Obj) -> Obj:
    spec = s.get("spec") or {}
    return {
        "name": _meta(s).get("name"),
        "type": spec.get("type", "ClusterIP"),
        "selector": spec.get("selector") or {},
        "ports": [
            f"{p.get('port')}->{p.get('targetPort', p.get('port'))}/{p.get('protocol', 'TCP')}"
            + (f" ({p['name']})" if p.get("name") else "")
            for p in spec.get("ports") or []
        ],
    }


def inventory(
    cluster: Cluster,
    system_namespaces: Iterable[str],
    namespace: str | None = None,
    include_system: bool = False,
) -> Obj:
    """Workloads, services and policy coverage, grouped by namespace."""
    system = set(system_namespaces)
    if namespace:
        namespaces = [namespace]
    else:
        namespaces = sorted(_meta(n).get("name") for n in cluster.list_namespaces())
        if not include_system:
            namespaces = [n for n in namespaces if n not in system]
    wanted = set(namespaces)

    def by_ns(objs: list[Obj] | None) -> dict[str, list[Obj]]:
        out: dict[str, list[Obj]] = defaultdict(list)
        for o in objs or []:
            ns = _meta(o).get("namespace")
            if ns in wanted:
                out[ns].append(o)
        return out

    workloads = by_ns(cluster.list_workloads(namespace))
    services = by_ns(cluster.list_services(namespace))
    pods = by_ns(cluster.list_pods(namespace))
    cnps = by_ns(cluster.list_crd("CiliumNetworkPolicy", namespace))
    knps = by_ns(cluster.list_network_policies(namespace))

    result = []
    for ns in namespaces:
        ns_pods = pods.get(ns, [])
        unmanaged = [
            _meta(p).get("name") for p in ns_pods if not _meta(p).get("ownerReferences")
        ]
        policies = [_meta(p).get("name") for p in cnps.get(ns, [])]
        k8s_policies = [_meta(p).get("name") for p in knps.get(ns, [])]
        result.append(
            {
                "namespace": ns,
                "pods": {
                    "total": len(ns_pods),
                    "ready": sum(_pod_ready(p) for p in ns_pods),
                    "unmanaged": unmanaged,
                },
                "workloads": [_workload_summary(w) for w in workloads.get(ns, [])],
                "services": [_service_summary(s) for s in services.get(ns, [])],
                "policy_coverage": {
                    "cilium_network_policies": policies,
                    "k8s_network_policies": k8s_policies,
                    "has_any_policy": bool(policies or k8s_policies),
                },
            }
        )

    return {
        "namespaces": result,
        "summary": {
            "namespaces": len(result),
            "workloads": sum(len(n["workloads"]) for n in result),
            "namespaces_without_policy": [
                n["namespace"] for n in result if not n["policy_coverage"]["has_any_policy"]
            ],
        },
        "hidden_system_namespaces": sorted(system) if not include_system and not namespace else [],
    }


# --------------------------------------------------------------------------- #
# list_policies
# --------------------------------------------------------------------------- #

def _cilium_policy_summary(p: Obj) -> Obj:
    # A Cilium policy carries either a single `spec` or a list of `specs`.
    specs = ([p["spec"]] if p.get("spec") else []) + list(p.get("specs") or [])
    rules = []
    for s in specs:
        rules.append(
            {
                "endpointSelector": s.get("endpointSelector") or s.get("nodeSelector"),
                "ingress": len(s.get("ingress") or []),
                "ingressDeny": len(s.get("ingressDeny") or []),
                "egress": len(s.get("egress") or []),
                "egressDeny": len(s.get("egressDeny") or []),
                "description": s.get("description"),
            }
        )
    return {"name": _meta(p).get("name"), "namespace": _meta(p).get("namespace"), "rules": rules}


def _k8s_policy_summary(p: Obj) -> Obj:
    spec = p.get("spec") or {}
    return {
        "name": _meta(p).get("name"),
        "namespace": _meta(p).get("namespace"),
        "podSelector": spec.get("podSelector"),
        "policyTypes": spec.get("policyTypes"),
        "ingress": len(spec.get("ingress") or []),
        "egress": len(spec.get("egress") or []),
    }


def _tracing_policy_summary(p: Obj) -> Obj:
    spec = p.get("spec") or {}
    hooks: list[str] = []
    actions: set[str] = set()
    for section in ("kprobes", "tracepoints", "uprobes", "lsmhooks"):
        for hook in spec.get(section) or []:
            hooks.append(f"{section[:-1]}:{hook.get('call') or hook.get('event') or hook.get('hook') or '?'}")
            for sel in hook.get("selectors") or []:
                for act in sel.get("matchActions") or []:
                    actions.add(act.get("action", "?"))
    return {
        "name": _meta(p).get("name"),
        "namespace": _meta(p).get("namespace"),
        "hooks": hooks,
        "actions": sorted(actions) or ["Post (default)"],
        "podSelector": spec.get("podSelector"),
    }


POLICY_KINDS = (
    "CiliumNetworkPolicy",
    "CiliumClusterwideNetworkPolicy",
    "NetworkPolicy",
    "TracingPolicy",
    "TracingPolicyNamespaced",
)


def policies(
    cluster: Cluster,
    namespace: str | None = None,
    kinds: list[str] | None = None,
    include_spec: bool = False,
) -> Obj:
    """Summaries of network (Cilium + k8s) and runtime (Tetragon) policies."""
    kinds = kinds or list(POLICY_KINDS)
    unknown = [k for k in kinds if k not in POLICY_KINDS]
    if unknown:
        raise ValueError(f"Unknown policy kinds {unknown}; valid: {list(POLICY_KINDS)}")

    out: dict[str, Any] = {}
    not_installed = []
    for kind in kinds:
        if kind == "NetworkPolicy":
            items: list[Obj] | None = cluster.list_network_policies(namespace)
            summarize = _k8s_policy_summary
        else:
            items = cluster.list_crd(kind, namespace)
            summarize = (
                _tracing_policy_summary if kind.startswith("Tracing") else _cilium_policy_summary
            )
        if items is None:
            not_installed.append(kind)
            continue
        entries = []
        for item in items:
            entry = summarize(item)
            if include_spec:
                entry["spec"] = item.get("spec") or item.get("specs")
            entries.append(entry)
        out[kind] = entries

    return {
        "policies": out,
        "counts": {k: len(v) for k, v in out.items()},
        "crds_not_installed": not_installed,
    }
