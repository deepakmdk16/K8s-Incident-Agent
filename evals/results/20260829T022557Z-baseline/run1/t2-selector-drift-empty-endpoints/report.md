## Root cause

The Service `shop/catalog` selects `app=catalog-api`, but the pods produced by Deployment `shop/catalog` carry the label `app=catalog`. No pod matches the Service selector, so the Service's EndpointSlice is empty and every connection the storefront gateway opens to `catalog.shop.svc:8080` has no backend to be load-balanced to — it hangs/times out rather than being refused. The catalog pods themselves are healthy, which is exactly why the deployment reports all replicas Ready while traffic never reaches them. Verdict: **confirmed**.

## Evidence chain

- Service selector does not match the workload's pod labels — the causal mismatch:
  - `kubectl get all -A`, Services block: `shop  service/catalog  ClusterIP  10.96.96.22  <none>  8080/TCP  5s  app=catalog-api`
  - `kubectl get all -A`, ReplicaSet block: `shop  replicaset.apps/catalog-65498fccb  ... SELECTOR  app=catalog,pod-template-hash=65498fccb`
  - `kubectl get all -A`, Deployment block: `shop  deployment.apps/catalog  2/2  2  2  ... SELECTOR  app=catalog`
  - `app=catalog-api` ≠ `app=catalog`; label selection is exact-match, so zero pods are selected.
- The catalog pods are up and healthy, so the failure is not the workload but the routing layer:
  - `shop  pod/catalog-65498fccb-g52gl  1/1  Running  0  5s  10.244.0.120`
  - `shop  pod/catalog-65498fccb-lbrwn  1/1  Running  0  5s  10.244.0.119`
  - Matches the page text "Catalog deployment shop/catalog reports all replicas Ready."
- The consumer exists and is itself healthy, so the symptom is on the call path, not in the gateway:
  - `shop  pod/storefront-gateway-6785fd7b5d-cq9bt  1/1  Running  0  5s  10.244.0.121`
  - `shop  deployment.apps/storefront-gateway  1/1  1  1` with `SELECTOR app=storefront-gateway`, matching its own pod — the gateway's own Service/selector wiring is not implicated.
- "Timeouts / spinner hangs" rather than instant connection-refused is the signature of a ClusterIP with no endpoints: kube-proxy has no backend to DNAT to, so packets are dropped/blackholed. DNS still resolves the ClusterIP because the Service object exists (`10.96.96.22`), which is why the gateway reports "calls not completing" instead of a name-resolution error.

## Investigation ledger

- **Catalog pods crashing / not ready** — ruled out: both `pod/catalog-...` rows show `1/1 Running` with `RESTARTS 0`, and `deployment.apps/catalog` shows `2/2 ... AVAILABLE 2`.
- **Insufficient replicas / capacity saturation causing intermittent latency** — ruled out: `DESIRED 2, CURRENT 2, READY 2` on `replicaset.apps/catalog-65498fccb`; no pending or evicted pods anywhere in the listing, and the failure mode (no backend at all) is not load-dependent.
- **DNS failure (CoreDNS down)** — ruled out: `coredns-559f6c778d-9sqc8` and `-t9nfq` are both `1/1 Running` with 0 restarts, `deployment.apps/coredns 2/2`, and `service/kube-dns` has selector `k8s-app=kube-dns` matching the CoreDNS pod template — DNS resolution of `catalog.shop.svc` would succeed and return `10.96.96.22`.
- **Cluster networking / kube-proxy or CNI broken** — ruled out: `daemonset.apps/kube-proxy 1/1 READY 1` and `daemonset.apps/kindnet 1/1 READY 1`; all pods have routable `10.244.0.x` IPs on the single node. A cluster-wide dataplane fault would not spare CoreDNS and the control plane.
- **Wrong Service port / port-name mismatch** — not ruled out from this output alone (container ports are not shown), but it is not needed to explain the symptom: with a selector that matches zero pods, the endpoint set is empty regardless of port configuration. Port correctness should be re-checked after the selector fix.
- **Gateway misconfigured to call the wrong hostname** — ruled out as the primary cause: a Service named `catalog` exists in the same namespace `shop` and the page states the gateway's calls to the catalog backend do not complete (i.e., it reaches a ClusterIP and hangs), which is consistent with resolving this Service and finding it backend-less.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints despite Ready pods.
kubectl -n shop get endpointslices -l kubernetes.io/service-name=catalog -o wide
kubectl -n shop get endpoints catalog          # expect ENDPOINTS: <none>

# 2. Show the selector/label mismatch side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'   # app=catalog-api
kubectl -n shop get pods -l app=catalog --show-labels                  # pods labelled app=catalog
kubectl -n shop get pods -l app=catalog-api                            # expect "No resources found"
```

**Remediation:** patch the Service selector to match the pods the Deployment actually produces:

```bash
kubectl -n shop patch svc catalog --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'
kubectl -n shop get endpoints catalog   # should now list 10.244.0.119:8080, 10.244.0.120:8080
```

(Equivalent alternative: relabel the Deployment pod template to `app: catalog-api` — but that forces a rollout of both replicas and requires changing the immutable-ish `spec.selector`, so patching the Service is the faster, lower-risk fix. Follow up by codifying the label contract in the manifests/Helm values so the two cannot drift again.)

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "Service shop/catalog has selector app=catalog-api while the catalog pods are labelled app=catalog, so the selector matches zero pods and the Service's EndpointSlice is empty. The storefront gateway resolves the ClusterIP 10.96.96.22 but kube-proxy has no backend to forward to, so its catalog calls hang until timeout and product pages render empty or spin.",
  "verdict": "confirmed"
}
```