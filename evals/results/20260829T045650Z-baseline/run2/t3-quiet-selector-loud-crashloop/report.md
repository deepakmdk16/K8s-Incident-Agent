# SEV2 SearchTimeouts — root cause report

**Case:** `t3-quiet-selector-loud-crashloop`
**Verdict: confirmed**

## Root cause

The `search` Service in namespace `search` selects `app=search-api`, but the pods produced by the `search` Deployment carry the label `app=search`. No pod matches the Service selector, so the Service has an empty EndpointSlice. Traffic sent by `web-gateway` to `search.search.svc:8080` hits a ClusterIP with zero backends — connections are never answered and hang until the caller's timeout fires, which surfaces to shoppers as "search is unavailable". The search pods themselves are healthy; they are simply unreachable through the Service VIP.

The resource whose spec must change is the Service `search/search` (its `.spec.selector`).

## Evidence chain

- **Service selector**, from `kubectl get all -A`, services table:
  `search   service/search   ClusterIP   10.96.24.225   <none>   8080/TCP   4m9s   app=search-api`
  → the Service matches only pods labeled `app=search-api`.
- **Actual pod labels**, from the same output, deployments table:
  `search   deployment.apps/search   2/2 ... SELECTOR app=search`
  and replicasets table:
  `search   replicaset.apps/search-5478989674   2   2   2 ... SELECTOR app=search,pod-template-hash=5478989674`
  → the pods `search-5478989674-6rxcp` / `-pswt8` are labeled `app=search`, not `app=search-api`. No object in the entire listing carries `app=search-api`.
- **Backends are healthy, so the failure is routing, not the app**: pods table shows
  `search   pod/search-5478989674-6rxcp   1/1   Running   0   4m9s` and `pod/search-5478989674-pswt8   1/1   Running   0`, and the Deployment reads `search   deployment.apps/search   2/2   2   2`. This matches the page's own note that "the search deployment reports all replicas Ready."
- **Symptom shape matches an empty Service**: the page says gateway "calls to the search backend are not completing" (hang/timeout), which is the signature of a ClusterIP with no endpoints (packets dropped / connection never established), rather than an error response from a live backend.
- **Caller is present and healthy**: `search   pod/web-gateway-557b9db57b-65gxl   1/1   Running   0   4m9s` — the gateway is up and issuing the calls that time out.

## Investigation ledger

- **`report-generator` CrashLoopBackOff in `analytics-batch`** — ruled out. It is the loudest signal but is in a different namespace, has no Service at all (absent from the services table), and its log line is `FATAL: EXPORT_BUCKET not configured; nightly report export cannot start` — a nightly batch export job, no path to storefront search queries. Nothing in the `search` namespace references it. Pure decoy.
- **Search pods crashed / not Ready** — ruled out by pods table: both search pods `1/1 Running`, `RESTARTS 0`, and Deployment `2/2` available.
- **Image pull / scheduling failure for search** — ruled out: search pods are `Running` on `incident-lab-control-plane` with an assigned IP (`10.244.0.150`, `10.244.0.149`); no Pending/ImagePullBackOff states.
- **Cluster DNS broken (gateway can't resolve `search`)** — ruled out: `kube-system pod/coredns-559f6c778d-9sqc8` and `-t9nfq` are both `1/1 Running`, `service/kube-dns` exists with selector `k8s-app=kube-dns` matching the coredns replicaset selector. Also, DNS resolution would still succeed here — the ClusterIP exists; it's the endpoint set that is empty.
- **Node / network plane failure** — ruled out: `kindnet` and `kube-proxy` DaemonSets are `1/1 DESIRED/READY`, and every other workload in the cluster is Running with no reported connectivity complaints.
- **Wrong Service port (8080 vs container port)** — considered; not the operative fault. Port mismatch would still produce endpoints (connection refused/reset, a fast failure), whereas the observed symptom is non-completion, and the selector mismatch guarantees zero endpoints regardless of port. Port correctness can be confirmed alongside the fix.

## Verification recipe

```bash
# 1. The smoking gun: Service selector vs. actual pod labels — expect a mismatch
kubectl -n search get svc search -o jsonpath='{.spec.selector}{"\n"}'
kubectl -n search get pods -l app=search --show-labels

# 2. Prove the Service has no backends — expect "<none>" / no endpoints
kubectl -n search get endpoints search
kubectl -n search get endpointslices -l kubernetes.io/service-name=search

# 3. Confirm the pods answer directly (bypassing the Service VIP)
kubectl -n search run curl-probe --rm -it --restart=Never --image=busybox:1.36 \
  -- wget -qO- --timeout=3 http://10.244.0.150:8080/ ; echo "exit=$?"
```

Expected: step 1 prints `{"app":"search-api"}` against pods labeled `app=search`; step 2 prints `ENDPOINTS <none>`; step 3 succeeds against the pod IP, proving the backends are alive and only the Service mapping is broken.

**Remediation:** patch the Service selector to match the pods —
`kubectl -n search patch svc search -p '{"spec":{"selector":{"app":"search"}}}'`
then re-check `kubectl -n search get endpoints search` for two ready addresses. (Equivalent alternative, if the Service selector is the intended contract, is relabeling the Deployment pod template to `app=search-api`; the Service patch is the lower-risk, non-rolling change during an active SEV2.) Follow-up: add an alert on `kube_endpoint_address_available == 0` for user-facing Services so a selector typo pages directly instead of surfacing as gateway timeouts.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {"kind": "Service", "namespace": "search", "name": "search"},
  "mechanism": "The search Service's selector is app=search-api while the pods created by the search Deployment are labeled app=search, so no pod matches and the Service's endpoint list is empty. Requests from web-gateway to the search ClusterIP have no backend to be forwarded to and hang until the caller times out, producing storewide 'search is unavailable' fallbacks even though all search replicas are Running and Ready.",
  "verdict": "confirmed"
}
```