## Root cause

**Verdict: confirmed.**

The `search` Service in namespace `search` selects `app=search-api`, but the pods produced by the `search` Deployment carry the label `app=search`. The selector matches nothing, so the Service has no backing endpoints. Traffic sent by `web-gateway` to `search.search.svc:8080` lands on a ClusterIP with an empty endpoint set — connections are never answered and the gateway's calls hang until its timeout, producing storewide "search is unavailable" fallbacks while the Deployment legitimately reports `2/2` Ready. The failing spec is the Service's selector, not the pods.

## Evidence chain

1. **Selector mismatch (the causal fact).** From `kubectl get all -A`, services table:
   `search   service/search   ClusterIP   10.96.24.225   <none>   8080/TCP   4m9s   app=search-api`
   From the same output, deployments table:
   `search   deployment.apps/search   2/2   2   2   ...   app=search`
   And the replicaset:
   `search   replicaset.apps/search-5478989674   ...   app=search,pod-template-hash=5478989674`
   The Service wants `app=search-api`; the only labels in existence on those pods are `app=search` (+ pod-template-hash). No object in the entire listing carries `app=search-api`.

2. **The backends are healthy, which matches the page text.** `pod/search-5478989674-6rxcp` and `pod/search-5478989674-pswt8` are both `1/1 Running`, `0` restarts, with IPs `10.244.0.150` / `10.244.0.149`. The Deployment shows `2/2 ... AVAILABLE 2`. This is exactly the paged paradox: "search deployment reports all replicas Ready" while calls never complete — consistent with a routing/endpoint failure rather than a pod failure.

3. **The caller is up and is the reporter.** `search   pod/web-gateway-557b9db57b-65gxl   1/1 Running   0 restarts`, and `deployment.apps/web-gateway 1/1`. The gateway itself is not crashing; the alert source is "gateway latency monitor", i.e. the gateway is observing outbound calls that hang. A ClusterIP with zero endpoints produces exactly hangs/timeouts (rather than immediate connection-refused you'd see from a live-but-broken backend).

4. **Symptom scope matches.** Every query fails storewide, not a fraction — consistent with *all* endpoints missing, not with one bad replica. Both search replicas are Ready, so a partial-outage explanation is unavailable.

5. **Every other namespace is intact.** All other Services (`app=experiment-api`, `app=edge-cache`, etc.) have selectors that match their corresponding Deployment selectors in the same listing. `search` is the sole mismatch.

## Investigation ledger

- **`report-generator` CrashLoopBackOff in `analytics-batch` (the loud decoy).** Ruled out. It is in a different namespace, it has **no Service at all** in the services table, so nothing can route to it and no other workload can be depending on it via cluster DNS. Its log line is `FATAL: EXPORT_BUCKET not configured; nightly report export cannot start` — a nightly batch export job, unrelated to synchronous query serving. `describe deployment.apps/report-generator` shows `Environment: <none>`, a genuine but separate config bug. It cannot cause gateway→search calls to hang; it is a second, lower-severity issue.

- **Search pods crashed / not actually serving.** Ruled out. `kubectl get all -A` shows both search pods `1/1 Running` with `RESTARTS 0` and `AGE 4m9s`; the Deployment reads `2/2` with `AVAILABLE 2`. Readiness probes are passing.

- **`web-gateway` itself is broken (bad image, crash, OOM).** Ruled out. `web-gateway-557b9db57b-65gxl` is `1/1 Running`, `0` restarts, and `deployment.apps/web-gateway 1/1 1 1`. It is healthy enough to emit latency telemetry, which is what paged us.

- **Cluster DNS failure preventing name resolution.** Ruled out. `kube-system` shows `coredns-559f6c778d-9sqc8` and `coredns-559f6c778d-t9nfq` both `1/1 Running` with `0` restarts for `10h`, `deployment.apps/coredns 2/2`, and `service/kube-dns` present with matching selector `k8s-app=kube-dns` against replicaset labels `k8s-app=kube-dns,pod-template-hash=559f6c778d`. Also, DNS would resolve fine here anyway — the Service object exists and has a ClusterIP; only its endpoint set is empty. (The `internal-dns/dns-forwarder` pod is likewise `1/1 Running`.)

- **Network dataplane / kube-proxy broken.** Ruled out. `daemonset.apps/kube-proxy` is `1/1 ... READY 1` and `daemonset.apps/kindnet` is `1/1 ... READY 1`, both `10h` old with the pods `kube-proxy-6ndq6` and `kindnet-88ckx` `1/1 Running`, `0` restarts. A dataplane outage would also break every other service, not just search.

- **Node pressure / scheduling problem.** Ruled out. All workloads are scheduled and Running on the single node `incident-lab-control-plane`; search pods show `PodScheduled`-equivalent success by virtue of being Running with assigned IPs, and no Pending pods appear anywhere.

- **Wrong port on the Service (8080 vs. container port).** Not ruled out by direct evidence, but it is not the operative cause: even a correct port cannot help when the selector matches zero pods. This should be re-checked after the selector fix (see Verification recipe step 3), but the endpoint list being empty is sufficient and necessary to explain a total, storewide hang.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints at all.
kubectl get endpoints search -n search -o wide
kubectl get endpointslice -n search -l kubernetes.io/service-name=search

# 2. Prove the label mismatch side by side.
kubectl get svc search -n search -o jsonpath='{.spec.selector}{"\n"}'
kubectl get pods -n search -l app=search-api --show-labels   # expect: "No resources found"
kubectl get pods -n search -l app=search   --show-labels     # expect: the 2 Ready search pods

# 3. Confirm the gateway's call hangs on the ClusterIP but succeeds direct to a pod IP.
kubectl exec -n search deploy/web-gateway -- timeout 3 wget -qO- http://search.search.svc.cluster.local:8080/ ; echo "exit=$?"
kubectl exec -n search deploy/web-gateway -- timeout 3 wget -qO- http://10.244.0.150:8080/ ; echo "exit=$?"
```

Expected: step 1 shows `ENDPOINTS <none>`; step 2 shows zero pods for `app=search-api` and two for `app=search`; step 3 times out against the Service but returns against the pod IP.

**Remediation:** patch the Service selector to match the pods that actually exist —
`kubectl patch svc search -n search --type=merge -p '{"spec":{"selector":{"app":"search"}}}'` — then confirm `kubectl get endpoints search -n search` lists `10.244.0.149:8080,10.244.0.150:8080`. Fix it in the source manifest so the next apply doesn't revert it. Follow-up (separate, non-SEV2 ticket): supply `EXPORT_BUCKET` to the `analytics-batch/report-generator` Deployment. Preventive: add a CI/admission check that every Service selector matches at least one pod in its namespace, and an alert on `kube_endpoint_address_available == 0` so a zero-endpoint Service pages directly instead of surfacing as a downstream latency alarm.

```json
{
  "case_id": "t3-quiet-selector-loud-crashloop",
  "failing_resource": {"kind": "Service", "namespace": "search", "name": "search"},
  "mechanism": "The search Service's selector is app=search-api, but the pods created by the search Deployment are labeled app=search, so the selector matches no pods and the Service has an empty endpoint set. Requests from web-gateway to the search ClusterIP therefore have nowhere to be routed and hang until the caller times out, producing storewide 'search is unavailable' fallbacks even though both search replicas are Running and Ready.",
  "verdict": "confirmed"
}
```