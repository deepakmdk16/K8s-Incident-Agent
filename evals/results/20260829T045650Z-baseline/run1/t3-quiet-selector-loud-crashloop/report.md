## Root cause

The `search` Service in namespace `search` selects `app=search-api`, but the search pods carry the label `app=search` (that is the label the `search` Deployment/ReplicaSet stamps onto its pods). No pod in the cluster matches `app=search-api`, so the Service has an empty endpoint set. Connections from `web-gateway` to `search:8080` therefore land on a ClusterIP with no backends — kube-proxy has nothing to DNAT to, so requests hang until the gateway's client timeout fires, producing the storewide "search is unavailable" fallback. The pods themselves are healthy, which is exactly why the deployment reports 2/2 Ready while traffic never arrives.

Verdict: **confirmed** — the selector/label mismatch is directly visible in the provided output and fully explains "all replicas Ready but calls never complete."

## Evidence chain

- Service selector, from `kubectl get all -A` (services block):
  `search   service/search   ClusterIP   10.96.24.225   <none>   8080/TCP   4m9s   app=search-api`
  → the Service targets pods labelled `app=search-api`.
- Pod labels of the actual backends, from `kubectl get all -A` (replicaset block):
  `search   replicaset.apps/search-5478989674   2   2   2   4m9s   ... SELECTOR app=search,pod-template-hash=5478989674`
  → the pods produced by that ReplicaSet are labelled `app=search`, **not** `app=search-api`.
- Deployment selector confirms the same, from `kubectl get all -A` (deployments block):
  `search   deployment.apps/search   2/2   2   2   4m9s   api   busybox:1.36   app=search`
- No workload anywhere in the cluster carries `app=search-api`: scanning every SELECTOR column in the deployments/replicasets output, the only search-related label is `app=search`. Hence the Service's endpoint list is necessarily empty.
- Backends are healthy, ruling out a pod-side fault as the cause of the timeout:
  `search   pod/search-5478989674-6rxcp   1/1   Running   0   4m9s` and
  `search   pod/search-5478989674-pswt8   1/1   Running   0   4m9s`
- Caller is up and in the same namespace, so this is a service-routing failure, not a caller outage:
  `search   pod/web-gateway-557b9db57b-65gxl   1/1   Running   0   4m9s`
- Symptom shape matches: page says "web gateway reports its calls to the search backend are not completing" while "the search deployment reports all replicas Ready" — the signature of a ClusterIP with zero endpoints (connect blackholes/times out rather than returning a fast connection-refused from a live pod).

## Investigation ledger

- **`report-generator` CrashLoopBackOff in `analytics-batch` (the loud failure).** Ruled out as the cause of the page. It lives in a different namespace, has no Service at all (absent from the services list in `kubectl get all -A`), and nothing in the search path references it. Its own log states a self-contained config problem: `FATAL: EXPORT_BUCKET not configured; nightly report export cannot start`, and `describe deployment.apps/report-generator` shows `Environment: <none>` — a missing env var on a nightly batch job. It is a real but separate, non-customer-facing defect.
- **Search pods crashing / not really serving.** Ruled out: both search pods show `1/1 Running` with `0` restarts in `kubectl get all -A`; the deployment shows `2/2` READY/AVAILABLE.
- **Insufficient replicas / scale-down.** Ruled out: `deployment.apps/search 2/2 2 2` — desired, current, updated and available all agree.
- **Image pull or scheduling failure for search.** Ruled out: search pods are `Running` on `incident-lab-control-plane` with assigned IPs (10.244.0.149/150); no Pending/ImagePull states appear.
- **Cluster networking or DNS broken.** Ruled out: `kindnet` and `kube-proxy` DaemonSets are `1/1` desired/ready, and both `coredns` pods are `1/1 Running` with `deployment.apps/coredns 2/2`. DNS would also resolve `search.search.svc` fine here — the ClusterIP exists; it just has no endpoints.
- **Wrong port on the Service.** Considered: Service exposes `8080/TCP`, consistent with every other service in the fleet. Even if the target port were wrong, that would not explain the mismatch already proven; and a port error is moot when the selector matches zero pods. Not the primary defect.
- **Caller (`web-gateway`) misconfigured/down.** Ruled out as root cause: it is `1/1 Running` with `0` restarts, and the failure is storewide across every query — consistent with an empty backend set rather than a caller bug.

## Verification recipe

```bash
# 1. The decisive check: the Service has no endpoints.
kubectl get endpoints search -n search -o wide
kubectl get endpointslice -n search -l kubernetes.io/service-name=search

# 2. Show the selector/label mismatch side by side.
kubectl get svc search -n search -o jsonpath='{.spec.selector}{"\n"}'
kubectl get pods -n search -l app=search --show-labels
kubectl get pods -n search -l app=search-api        # expect: "No resources found"

# 3. Confirm the pods themselves serve fine when addressed directly.
kubectl exec -n search deploy/web-gateway -- wget -qO- --timeout=3 http://10.244.0.149:8080/ ; echo "direct-pod rc=$?"
kubectl exec -n search deploy/web-gateway -- wget -qO- --timeout=3 http://search:8080/ ; echo "via-svc rc=$?"
```

Expected: step 1 prints `ENDPOINTS  <none>`; step 2 prints selector `{"app":"search-api"}` against pods labelled `app=search` and finds nothing for `app=search-api`; step 3 succeeds pod-direct and times out through the Service.

**Remediation:** change the Service spec to match the pods —
`kubectl patch svc search -n search -p '{"spec":{"selector":{"app":"search"}}}'` — then re-check `kubectl get endpoints search -n search` for two ready addresses. (Persist the same edit in the Service manifest in git so the next apply doesn't reintroduce it.) Alternatively, if `app=search-api` is the intended contract, relabel the Deployment's pod template and selector instead; do not do both. Separately, file a follow-up ticket to supply `EXPORT_BUCKET` to the `report-generator` deployment in `analytics-batch`.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {"kind": "Service", "namespace": "search", "name": "search"},
  "mechanism": "The search Service's selector is app=search-api, but the search pods are labelled app=search, so no pod matches and the Service's endpoint list is empty. The web-gateway's requests to search:8080 hit a ClusterIP with zero backends and hang until client timeout, producing storewide 'search is unavailable' fallbacks even though all search replicas are Running and Ready.",
  "verdict": "confirmed"
}
```