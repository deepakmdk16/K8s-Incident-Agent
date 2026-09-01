## Root cause

**Service `shop/catalog` has a label selector that matches no pods.** The Service selects `app=catalog-api`, but every catalog pod produced by `deployment.apps/catalog` carries the label `app=catalog`. Because no pod matches, the Service's EndpointSlice is empty, so `catalog.shop.svc:8080` resolves but has zero backends — connections from the storefront gateway hang until they time out. The catalog pods themselves are perfectly healthy, which is exactly why the deployment reports all replicas Ready while the gateway's calls never complete.

Verdict: **confirmed**.

## Evidence chain

- **Service selector (source: `kubectl get all -A`, Services block):**
  `shop service/catalog ClusterIP 10.96.96.22 <none> 8080/TCP 5s app=catalog-api`
  → the Service routes only to pods labeled `app=catalog-api`.
- **Actual pod labels (source: same output, Deployments/ReplicaSets block):**
  `shop deployment.apps/catalog ... SELECTOR app=catalog` and
  `shop replicaset.apps/catalog-65498fccb ... SELECTOR app=catalog,pod-template-hash=65498fccb`
  → the pods `catalog-65498fccb-g52gl` and `catalog-65498fccb-lbrwn` are labeled `app=catalog`, **not** `app=catalog-api`. No workload anywhere in the output emits pods with `app=catalog-api`.
- **Mismatch ⇒ empty endpoints:** `app=catalog` ≠ `app=catalog-api`; Kubernetes selectors are exact-match on the full label value, so the intersection is empty and the Service has no ready addresses to program into kube-proxy.
- **Symptom shape matches empty endpoints, not sick pods:**
  - `pod/catalog-65498fccb-g52gl 1/1 Running 0` and `pod/catalog-65498fccb-lbrwn 1/1 Running 0`, `deployment.apps/catalog 2/2 2 2` → backends are healthy and Ready, consistent with the page text "Catalog deployment shop/catalog reports all replicas Ready."
  - `pod/storefront-gateway-6785fd7b5d-cq9bt 1/1 Running 0` → the caller is up; the failure is purely in the path between caller and backends.
  - A Service with zero endpoints accepts the DNS lookup and then blackholes/refuses the connection, which surfaces as hangs and timeouts ("spinner hangs, empty product grids") rather than immediate hard failures on every request.
- **Cluster plumbing is intact, so the gap is not infrastructural:** `pod/kube-proxy-6ndq6 1/1 Running 0`, `pod/kindnet-88ckx 1/1 Running 0`, `deployment.apps/coredns 2/2`, both CoreDNS pods `1/1 Running 0`.

## Investigation ledger

- **Catalog pods crashing / not Ready** — ruled out: both catalog pods show `1/1 Running 0` restarts and the deployment shows `2/2 2 2` (READY/UP-TO-DATE/AVAILABLE).
- **Insufficient replicas / scaled to zero** — ruled out: `deployment.apps/catalog 2/2` with two distinct pods holding IPs `10.244.0.119` and `10.244.0.120`.
- **Storefront gateway itself broken** — ruled out as the paged cause: `storefront-gateway-6785fd7b5d-cq9bt 1/1 Running 0` restarts, and its deployment reports `1/1 1 1`. Its selector `app=storefront-gateway` matches its own ReplicaSet labels, so it is internally consistent.
- **DNS resolution failure** — ruled out: `deployment.apps/coredns 2/2` with both pods `1/1 Running 0` restarts, and `service/kube-dns` present at `10.96.0.10` with a selector (`k8s-app=kube-dns`) that does match the CoreDNS pod labels.
- **CNI / node networking fault** — ruled out: `kindnet` DaemonSet `1 1 1 1 1` and `kindnet-88ckx 1/1 Running 0`; all pods hold routable `10.244.0.x` addresses on the single node `incident-lab-control-plane`.
- **kube-proxy not programming rules** — ruled out: `daemonset.apps/kube-proxy 1 1 1 1 1`, pod `1/1 Running 0`. Even a perfect kube-proxy has nothing to program when the endpoint set is empty.
- **Wrong Service port / port-name mismatch** — not supported by the evidence: the Service exposes `8080/TCP`, and nothing in the output indicates a port conflict; regardless, the selector mismatch alone is sufficient to produce zero endpoints and fully explains the symptom.
- **NetworkPolicy blocking gateway→catalog traffic** — no NetworkPolicy objects appear anywhere in `kubectl get all -A` output; not the operative cause given the selector mismatch is directly visible.

## Verification recipe

```bash
# 1. The smoking gun: the Service should have zero endpoint addresses.
kubectl -n shop get endpointslices -l kubernetes.io/service-name=catalog -o wide
kubectl -n shop get endpoints catalog          # expect ENDPOINTS: <none>

# 2. Show the selector vs. the actual pod labels side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'   # app=catalog-api
kubectl -n shop get pods -l app=catalog --show-labels                  # pods labeled app=catalog
kubectl -n shop get pods -l app=catalog-api                            # expect "No resources found"

# 3. Confirm the hang is service-level, not pod-level.
kubectl -n shop exec deploy/storefront-gateway -- sh -c \
  'wget -T 3 -qO- http://catalog:8080/ ; echo "svc rc=$?" ; \
   wget -T 3 -qO- http://10.244.0.119:8080/ ; echo "pod rc=$?"'
# Service call times out; direct pod IP call succeeds → empty endpoints confirmed.
```

**Remediation:** align the Service selector with the pods it is meant to front —
`kubectl -n shop patch svc catalog --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'` —
then re-check that `kubectl -n shop get endpoints catalog` lists `10.244.0.119:8080` and `10.244.0.120:8080`. (Equivalently, relabel the workload's pod template to `app=catalog-api`, but patching the Service is the safe, non-disruptive fix since it does not roll the pods.) Longer term, add a CI check or admission policy that fails any Service whose selector matches zero pods in its namespace, and alert on `kube_endpoint_address_available == 0` for Services backing user-facing traffic.

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "The catalog Service's selector is app=catalog-api, but the catalog pods are labeled app=catalog, so the selector matches no pods and the Service's endpoint list is empty. Requests from the storefront gateway to catalog.shop.svc:8080 therefore have no backend to reach and hang until they time out, even though the catalog pods are all Running and Ready.",
  "verdict": "confirmed"
}
```