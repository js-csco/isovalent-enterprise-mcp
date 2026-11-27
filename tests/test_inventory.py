import pytest

from isovalent_mcp.config import DEFAULT_SYSTEM_NAMESPACES
from isovalent_mcp.tools import inventory as inv

from fake_cluster import FakeCluster

SYSTEM = DEFAULT_SYSTEM_NAMESPACES + ("tetragon", "hubble-timescape")


def components(status):
    return {c["component"]: c for c in status["components"]}


def test_platform_status_detects_components():
    status = inv.platform_status(FakeCluster())
    comps = components(status)
    assert comps["cilium-agent"]["pods_ready"] == "2/2"
    assert comps["cilium-agent"]["nodes"] == 2
    assert comps["cilium-agent"]["enterprise"] is True
    assert comps["hubble-relay"]["not_ready_pods"] == ["hubble-relay-1"]
    assert "tetragon" in comps
    assert "hubble-timescape" in comps
    assert status["not_found"] == []


def test_platform_status_reports_missing():
    cluster = FakeCluster()
    cluster.pods = [p for p in cluster.pods if "tetragon" not in p["metadata"]["name"]]
    assert "tetragon" in inv.platform_status(cluster)["not_found"]


def test_inventory_hides_system_namespaces():
    result = inv.inventory(FakeCluster(), SYSTEM)
    names = [n["namespace"] for n in result["namespaces"]]
    assert names == ["astronomy-shop", "mcp"]
    assert result["summary"]["namespaces_without_policy"] == ["mcp"]


def test_inventory_workload_details():
    result = inv.inventory(FakeCluster(), SYSTEM, namespace="astronomy-shop")
    (ns,) = result["namespaces"]
    frontend = next(w for w in ns["workloads"] if w["name"] == "frontend")
    assert frontend["pod_labels"] == {"app.kubernetes.io/name": "frontend"}  # hash label stripped
    assert frontend["container_ports"] == ["8080/TCP (http)"]
    assert ns["pods"]["unmanaged"] == ["debug"]
    assert ns["policy_coverage"]["cilium_network_policies"] == ["cart-ingress"]


def test_policies_summary():
    result = inv.policies(FakeCluster())
    cnp = result["policies"]["CiliumNetworkPolicy"][0]
    assert cnp["rules"][0]["ingress"] == 1
    tp = result["policies"]["TracingPolicy"][0]
    assert tp["hooks"] == ["kprobe:sys_execve"]
    assert tp["actions"] == ["Post"]
    assert "spec" not in cnp


def test_policies_handles_missing_crds():
    result = inv.policies(FakeCluster(with_tetragon_crds=False))
    assert set(result["crds_not_installed"]) == {"TracingPolicy", "TracingPolicyNamespaced"}


def test_policies_rejects_unknown_kind():
    with pytest.raises(ValueError):
        inv.policies(FakeCluster(), kinds=["Bogus"])
