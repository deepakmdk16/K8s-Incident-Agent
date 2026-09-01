## Root cause

**`Service/search` in namespace `search` has a selector that matches no pods.** The Service selects `app=search-api`, but the search pods produced by `Deployment/search` carry the label `app=search`. With no matching pods, the Service's EndpointSlice is empty, so every connection the web gateway opens to `search.search.svc:8080` has nowhere to be routed — kube-proxy has no backend to DNAT to, and the calls hang until the gateway's client timeout fires. That is exactly the paged symptom: "calls to the search backend are not completing" while "all replicas Ready" (the pods are healthy; they are simply not wired into the Service).

Verdict: **confirmed** — the selector/label mismatch is directly visible in the provided output and is sufficient on its own to produce a black-holed Service.

## Evidence chain

1. **The Service selector** — from `kubectl get all -A`, services section:
   `search   service/search   ClusterIP   10.96.24.225   <none>   8080/TCP   4m9s   app=search-api`
   The Service selects pods labeled `app=search-api`.

2. **The actual pod labels** — from `kubectl get all -A`, deployment section:
   `search   deployment.apps/search   2/2   2   2   4m9s   api   busybox:1.36   app=search`
   and replicaset section:
   `search   replicaset.apps/search-5478989674   2   2   2   4m9s   ...   app=search,pod-template-hash=5478989674`
   The pods `search-5478989674-6rxcp` and `search-5478989674-pswt8` therefore carry `app=search`, **not** `app=search-api`. No object anywhere in the output carries the label `app=search-api`.

3. **The pods themselves are healthy** — `pod/search-5478989674-6rxcp   1/1   Running   0` and `pod/search-5478989674-pswt8   1/1   Running   0`, and `deployment.apps/search   2/2   2   2`. This matches the page's note that "the search deployment reports all replicas Ready" — readiness is a pod-level property and is unaffected by a broken Service selector, which is precisely why the symptom presents as a mystery.

4. **The client is co-located and healthy** — `search   pod/web-gateway-557b9db57b-65gxl   1/1   Running   0` and `deployment.apps/web-gateway   1/1   1   1`. The gateway is up and issuing calls; the failure is in the path between it and the backend, not in the gateway itself.

5. **Timeout signature rather than error signature** — the page says calls "are not completing" (timeouts), not "connection refused". A Service with zero endpoints black-holes traffic (packets dropped / no backend to DNAT to) rather than actively rejecting it, which produces hangs and timeouts. This is consistent with the observed gateway latency alert.

6. **Contrast with every other Service in the cluster** — e.g. `ab-testing service/experiment-api ... app=experiment-api` vs `deployment.apps/experiment-api ... app=experiment-api`; `session-store service/session-cache ... app=session-cache` vs `deployment.apps/session-cache ... app=session-cache`. Every other Service's selector exactly equals its Deployment's selector. `search` is the only Service in the entire listing whose selector does not match its workload's labels.

## Investigation ledger

- **`report-generator` CrashLoopBackOff in `analytics-batch`** — ruled out. It is loud but unrelated. Log line: `FATAL: EXPORT_BUCKET not configured; nightly report export cannot start`. It is a batch report exporter in a different namespace, it has **no Service** at all (absent from the services list), so nothing can call it, and nothing in the search path depends on it. Its `describe` shows `Environment: <none>` — a genuine config bug worth its own low-priority ticket, but it cannot cause storewide search timeouts. Age `4m9s` matching the search deployment is coincidental (the whole fixture was created together).

- **Search pods crashed / not serving** — ruled out. Both search pods show `1/1 Running` with `0` restarts, and `deployment.apps/search` shows `2/2 ... 2 AVAILABLE`. No restarts, no CrashLoopBackOff, no `Unhealthy` events.

- **Wrong port on the Service (port/targetPort mismatch)** — ruled out as the primary cause. The Service exposes `8080/TCP`, identical to every other app Service in the cluster, and the search containers are the same `busybox:1.36` pattern as the working peers. Even if the port were wrong, the selector mismatch alone already guarantees zero endpoints, so the port cannot be the operative fault.

- **Cluster DNS failure (gateway can't resolve `search`)** — ruled out. `kube-system` shows `coredns-559f6c778d-9sqc8` and `coredns-559f6c778d-t9nfq` both `1/1 Running` with `0` restarts, `deployment.apps/coredns 2/2`, and `service/kube-dns` present with selector `k8s-app=kube-dns` correctly matching `replicaset.apps/coredns-559f6c778d ... k8s-app=kube-dns`. Also, a DNS outage would break every service in the cluster, not just search. Note the Service object *does* exist, so its DNS A record resolves fine — resolution succeeds and the connection then hangs, which is the classic empty-endpoints signature.

- **Node / CNI / kube-proxy failure** — ruled out. `daemonset.apps/kindnet 1/1` and `daemonset.apps/kube-proxy 1/1` are both `DESIRED 1, READY 1`; `kube-proxy-6ndq6` and `kindnet-88ckx` are `1/1 Running` with `0` restarts for `10h`. All pods have IPs in `10.244.0.0/16` and are scheduled on the single node. A datapath failure would affect all 20+ services, not one.

- **NetworkPolicy blocking gateway→search** — ruled out as far as this output allows. No NetworkPolicy objects appear anywhere, and both pods are in the *same* namespace `search`. More importantly, a policy would not explain the selector mismatch, which is independently sufficient. If the selector fix does not restore traffic, policies would be the next thing to check.

- **Resource exhaustion / throttling on search pods** — ruled out. `QoS Class: BestEffort` patterns and `0` restarts across search pods; no `OOMKilled`, no eviction events, and the node hosts all workloads without any `Failed`/`Pending` pods.

## Verification recipe

```bash
# 1. The decisive check: does the search Service have any endpoints?
#    Expect EMPTY / "<none>" — this is the black hole.
kubectl get endpoints search -n search
kubectl get endpointslice -n search -l kubernetes.io/service-name=search

# 2. Show the selector vs. the actual pod labels side by side.
kubectl get svc search -n search -o jsonpath='{.spec.selector}{"\n"}'   # -> {"app":"search-api"}
kubectl get pods -n search -l app=search --show-labels                   # -> pods labeled app=search
kubectl get pods -n search -l app=search-api                             # -> "No resources found"

# 3. Prove the backend pods are fine and it is purely the Service wiring
#    (direct pod IP works, Service VIP hangs).
kubectl exec -n search deploy/web-gateway -- sh -c 'wget -T3 -qO- http://10.244.0.149:8080/ ; echo "POD_RC=$?"'
kubectl exec -n search deploy/web-gateway -- sh -c 'wget -T3 -qO- http://search:8080/ ; echo "SVC_RC=$?"'
```

**Remediation:** change the Service's selector to match the workload — `kubectl patch svc search -n search -p '{"spec":{"selector":{"app":"search"}}}'` — then confirm `kubectl get endpoints search -n search` lists both pod IPs on port 8080. (Do *not* relabel the pods instead: that would churn the Deployment's own immutable selector.) Follow-ups: add a synthetic probe / alert on `kube_endpoint_address_available == 0` for customer-facing Services so a zero-endpoint Service pages directly instead of surfacing as client-side latency, and separately file a low-priority ticket to set `EXPORT_BUCKET` for `analytics-batch/report-generator`.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {"kind": "Service", "namespace": "search", "name": "search"},
  "mechanism": "The search Service's selector is app=search-api, but the pods backing it are labeled app=search, so the selector matches nothing and the Service's EndpointSlice is empty. Requests from the web gateway resolve the Service DNS name and connect to the ClusterIP, but kube-proxy has no backend to forward them to, so the connections are black-holed and hang until the client times out. The pods stay Ready throughout, which is why the deployment looks healthy while all search traffic fails.",
  "verdict": "confirmed"
}
```