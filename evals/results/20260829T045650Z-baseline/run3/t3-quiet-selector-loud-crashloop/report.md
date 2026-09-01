# SEV2 SearchTimeouts — RCA (case `t3-quiet-selector-loud-crashloop`)

## Root cause
**Verdict: confirmed.**

The `search` Service in namespace `search` has selector `app=search-api`, but the pods produced by `deployment.apps/search` carry the label `app=search`. No pod in the cluster matches `app=search-api`, so the Service has an empty endpoint set. Traffic from `web-gateway` to the `search` ClusterIP (`10.96.24.225:8080`) is accepted by the kube-proxy virtual IP but has nowhere to be forwarded, so connections hang until the gateway's client timeout fires — producing storewide "search is unavailable" fallbacks while every search replica is genuinely healthy and `Ready`. The resource whose spec must change is the **Service `search/search`** (its `.spec.selector`).

## Evidence chain

- **Selector mismatch (the causal fact).** From `kubectl get all -A`, services table:
  `search   service/search   ClusterIP   10.96.24.225   <none>   8080/TCP   4m9s   app=search-api`
  From the same output, deployments table:
  `search   deployment.apps/search   2/2   2   2   4m9s   api   busybox:1.36   app=search`
  and replicaset table: `search   replicaset.apps/search-5478989674 ... app=search,pod-template-hash=5478989674`.
  The Service selects `app=search-api`; the workload labels pods `app=search`. These do not intersect.
- **No pod anywhere carries `app=search-api`.** Scanning every deployment/replicaset selector in `kubectl get all -A`, the only `search`-namespace selectors present are `app=search` and `app=web-gateway`. Therefore the Service's endpoint list is necessarily empty.
- **The backend itself is healthy — matches the page's "all replicas Ready".** `pod/search-5478989674-6rxcp` and `pod/search-5478989674-pswt8` are `1/1 Running`, `0` restarts; `deployment.apps/search` shows `2/2` ready/available. So the failure is not in the pods, it is in the routing layer in front of them.
- **Symptom shape fits a black-holed ClusterIP.** The page says "the web gateway reports its calls to the search backend are not completing" (hangs/timeouts) rather than connection-refused or 5xx. A ClusterIP with zero endpoints produces exactly that: the VIP exists, the connection is not actively rejected in a way the client distinguishes, and the caller times out.
- **The gateway is co-located and running,** so the caller is not the problem: `search   pod/web-gateway-557b9db57b-65gxl   1/1   Running   0`, `deployment.apps/web-gateway   1/1   1   1`.

## Investigation ledger

- **`report-generator` CrashLoopBackOff in `analytics-batch` (the loud decoy) — ruled out.** It is in a different namespace (`analytics-batch`), has no Service at all (absent from the services table in `kubectl get all -A`), and nothing in the `search` path references it. Its failure is self-contained and self-explained: `log line: "FATAL: EXPORT_BUCKET not configured; nightly report export cannot start"`, with `describe deployment.apps/report-generator` confirming `Environment: <none>` — a missing config for a nightly batch export job. A batch report exporter with no Service cannot be in the synchronous request path of storewide site search. Worth a separate low-severity ticket, not this page.
- **Search pods crashed / not actually serving — ruled out.** `kubectl get all -A` shows both search pods `1/1 Running` with `RESTARTS 0`, and the deployment `2/2` available. Consistent with the page text "search deployment reports all replicas Ready."
- **Wrong Service port / port-name mismatch — ruled out as the primary cause.** The Service exposes `8080/TCP`, the same port convention used by every other healthy service in the cluster (`8080/TCP` across `ab-testing`, `cdn-edge`, `feature-flags`, etc.). Even if the port were correct, the empty selector match alone is sufficient to break routing, so this is not needed to explain the symptom.
- **Cluster networking / DNS broken — ruled out.** `kube-system` is fully healthy: both `coredns` pods `1/1 Running` (`deployment.apps/coredns 2/2`), `kube-proxy-6ndq6 1/1 Running`, `kindnet-88ckx 1/1 Running`, all with `0` restarts and `10h` uptime. A cluster-wide network fault would also not spare the ~20 other services.
- **Node pressure / eviction / scheduling — ruled out.** Every workload is scheduled on `incident-lab-control-plane`; no `Pending` pods, no eviction or `FailedScheduling` events, and the search pods are `BestEffort`-class survivors with zero restarts.
- **Recent bad rollout of `search` (old replicaset serving stale pods) — ruled out.** `deployment.apps/search` has exactly one replicaset, `search-5478989674` (2/2 ready), with no old replicasets listed; there is no partial-rollout split to explain the outage.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints (expect empty / <none>).
kubectl get endpoints search -n search
kubectl get endpointslice -n search -l kubernetes.io/service-name=search

# 2. Show the mismatch side by side: Service selector vs actual pod labels.
kubectl get svc search -n search -o jsonpath='{.spec.selector}{"\n"}'   # -> {"app":"search-api"}
kubectl get pods -n search --show-labels                                # -> app=search

# 3. Confirm the pods are fine and only the selector is wrong.
kubectl get pods -n search -l app=search-api        # -> "No resources found"
kubectl get pods -n search -l app=search            # -> the 2 Ready search pods
```

**Remediation:** patch the Service selector to match the pods (safest, no pod churn):

```bash
kubectl patch svc search -n search --type=merge -p '{"spec":{"selector":{"app":"search"}}}'
kubectl get endpoints search -n search   # should now list 10.244.0.149:8080 and 10.244.0.150:8080
```

Then fix the same value in the Git/Helm source of truth so it is not reverted on the next apply. (The alternative — relabeling the Deployment's pod template to `app=search-api` — requires changing the immutable Deployment `.spec.selector`, i.e. recreating the Deployment, and causes a full restart of search; prefer the Service patch during the incident.) Follow-ups: add an alert on `kube_endpoint_address_available == 0` for production Services, and a CI check that every Service selector matches at least one workload's pod labels.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {"kind": "Service", "namespace": "search", "name": "search"},
  "mechanism": "The Service 'search' selects pods with label app=search-api, but the search Deployment's pods are labeled app=search, so no pod matches and the Service's endpoint list is empty. Requests from web-gateway to the search ClusterIP 10.96.24.225:8080 therefore have no backend to be forwarded to and hang until the caller's timeout, surfacing as storewide 'search is unavailable' fallbacks even though all search replicas are Running and Ready.",
  "verdict": "confirmed"
}
```