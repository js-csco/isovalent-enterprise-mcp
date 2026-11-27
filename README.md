# isovalent-enterprise-mcp

This is joschwei trying to build an Isovalent Enterprise MCP - please use with caution.

An MCP server (Python, [FastMCP](https://gofastmcp.com)) that lets an AI assistant inspect,
and later control, an Isovalent Enterprise cluster: Cilium, Hubble, Tetragon and Timescape.

## Architecture

```
Cursor / Claude ──MCP (streamable HTTP)──► isovalent-mcp (pod in namespace "mcp")
                                              ├─► Kubernetes API   inventory, Cilium & Tetragon policies   [phase 1 ✅]
                                              ├─► Hubble Relay     live flows                              [phase 2]
                                              └─► Timescape        historical flows                        [phase 2]

Hubble flows + Tetragon events ──export──► Splunk Enterprise   correlation, thresholds, NDR detections
Cursor ──MCP──► Splunk MCP server          query findings & detections (separate server, composed in the client)
```

Design rules:

- **The server does deterministic work** (collecting, aggregating, classifying, generating YAML / SPL);
  **the model does the judgment** (naming tiers, explaining risk, proposing next steps).
- **Read tools by default.** Write tools are only registered with `ENABLE_WRITE_TOOLS=true`, are annotated
  `destructiveHint`, and always do a dry-run first. The default RBAC is read-only.
- **Splunk does the stateful detection** (thresholds over time windows, risk scores per pod).
  This server does not reimplement it.

## Tools

| Tool | Type | What it does |
|---|---|---|
| `get_platform_status` | read | Cilium / Hubble / Tetragon / Timescape components, readiness, versions, enterprise images |
| `get_inventory` | read | Workloads, pod labels, ports, Services and policy coverage per namespace |
| `list_policies` | read | CiliumNetworkPolicy, CiliumClusterwideNetworkPolicy, NetworkPolicy, TracingPolicy(Namespaced) |

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q                                  # uses an in-memory fake cluster
isovalent-mcp                              # stdio, uses your ~/.kube/config
MCP_TRANSPORT=http isovalent-mcp           # http://localhost:8000/mcp
```

### Test with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Choose transport **Streamable HTTP** and URL `http://<node-ip>:30810/mcp` (or `http://localhost:8000/mcp`).

## Deploy to the lab cluster

The GitHub Action tests the code and pushes a multi-arch image to
`ghcr.io/js-csco/isovalent-enterprise-mcp:<branch>` (branch slashes become dashes,
e.g. `claude-gallant-planck-8z9its`). Set that tag in `deploy/k8s/deployment.yaml` until it's merged to `main`.

```bash
kubectl create namespace mcp   # skip if it already exists
kubectl apply -f deploy/k8s/rbac.yaml -f deploy/k8s/deployment.yaml
```

In Portainer you can paste both files into *Create from manifest*. If the GHCR package is private, either
make it public or create a `ghcr` docker-registry secret in `mcp` and uncomment `imagePullSecrets`.

> ⚠️ The HTTP endpoint has no authentication yet. Keep it on your lab network.

### Cursor

`%USERPROFILE%\.cursor\mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "isovalent": { "url": "http://<node-ip>:30810/mcp" }
  }
}
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` (`http` in the image) | `stdio` or `http` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | HTTP bind address |
| `ENABLE_WRITE_TOOLS` | `false` | Register write tools (none exist yet) |
| `SYSTEM_NAMESPACES` | kube-system,… | Hidden from `get_inventory` unless `include_system=true` |
| `KUBE_CONTEXT` | current context | kubeconfig context when running outside the cluster |

## Roadmap

1. ✅ Skeleton, inventory, platform status, policy listing
2. Communication summary: Hubble Relay / Timescape flows, aggregated per workload pair
3. Microsegmentation: `analyze_segmentation` (tier inference) → `generate_cilium_policy` → gated `apply_policy`
4. Tetragon: `generate_tracing_policy` from a watchlist of attacker commands (MITRE ATT&CK)
5. Splunk: export flows and Tetragon execs, `generate_splunk_detection` (SPL for the "N of M commands" threshold
   and network anomalies), investigation tools that pivot pod → flows → process tree

## Port convention (namespace `mcp`)

NodePorts are cluster-wide (range 30000–32767), so this convention is kept by hand:

| NodePort | MCP server |
|---|---|
| 30810 | isovalent-mcp |
| 30811 | (next server) |
