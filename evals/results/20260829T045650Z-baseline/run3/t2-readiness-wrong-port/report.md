# Incident Report — SEV2 OrdersAPIErrors (orders)

## Root cause
The `orders-api` Deployment in namespace `orders` ships a readiness probe pointed at the wrong port: `http-get http://:8081/`, while the application actually listens on `:8080`. Nothing is bound on 8081, so every readiness check gets `connection refused`, both replicas stay `Ready: False`, and the `orders-api` Service (`app=orders-api`, port 8080) therefore has **zero ready endpoints**. The checkout gateway's calls to the orders backend hit a Service with no backends and never complete, surfacing as 5xx at the gateway. Verdict: **confirmed**.

## Evidence chain
- **Symptom localization** — `kubectl get all -A`: `orders pod/orders-api-7cc5bcf4c7-lst42 0/1 Running` and `orders pod/orders-api-7cc5bcf4c7-pcspl 0/1 Running`; `deployment.apps/orders-api 0/2 ... 0 AVAILABLE`. Both replicas are *Running* (process alive) but *not Ready*.
- **The probe target** — `describe deployment.apps/orders-api`, Pod Template: `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`. The same line appears in `describe replicaset.apps/orders-api-7cc5bcf4c7` and in both pod describes, so the wrong port originates in the Deployment's pod template — not in a pod-level edit.
- **The port the app actually serves** — `kubectl logs orders-api-7cc5bcf4c7-lst42 -c api`: `orders-api: serving on :8080`, and identically for `-pcspl`: `orders-api: serving on :8080`. The container's declared port also agrees: `Port: 8080/TCP (http)`.
- **Mechanism, directly observed** — `describe pod/orders-api-7cc5bcf4c7-lst42` Events: `Warning Unhealthy ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`. Identical event on `-pcspl` at `10.244.0.123:8081`. `connection refused` (not timeout) means the port is closed — nothing is listening on 8081, exactly as the log line predicts.
- **Path from not-Ready to gateway 5xx** — `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP  SELECTOR app=orders-api`. Kubernetes only places *Ready* pods into a Service's ready endpoint set; both selected pods report `Ready: False` / `ContainersReady: False` in their Conditions blocks. So the Service has no ready endpoints and gateway requests to it cannot complete — matching "the gateway reports its calls to the orders backend are not completing."
- **Timing corroboration** — Deployment `AGE 5s` in `get all -A`, and its only event is `ScalingReplicaSet ... Scaled up replica set orders-api-7cc5bcf4c7 from 0 to 2`, `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`. This is the newly released workload named in the page.
- **The app itself is healthy** — no restarts (`Restart Count: 0`, `RESTARTS 0`), `State: Running`, and the server logged a successful bind. The failure is purely in the health-check configuration, not the code path.

## Investigation ledger
- **Application crash / CrashLoopBackOff** — ruled out: `RESTARTS 0` for both pods, `State: Running` with `Started: Sat, 29 Aug 2026 07:40:38`, and the log shows a successful bind (`serving on :8080`). No `Error`/`OOMKilled`/`BackOff` anywhere.
- **Image pull failure or bad image** — ruled out: `Normal Pulled ... Container image "busybox:1.36" already present on machine`, followed by `Created` and `Started`. No `ImagePullBackOff`.
- **Missing ConfigMap `orders-api-scripts`** — ruled out: the volume is `Optional: false`, so a missing ConfigMap would block startup with a `FailedMount` event; instead the container started and executed `/app/run.sh` successfully enough to log its startup banner.
- **Service selector / label mismatch** — ruled out: `service/orders-api` selector is `app=orders-api` and both pods carry `Labels: app=orders-api`. The pods *are* selected; they are simply excluded from the ready endpoint set because they are not Ready.
- **Service port / targetPort misconfiguration** — ruled out as the *cause*: the Service exposes `8080/TCP` and the container declares `Port: 8080/TCP (http)`; these agree. Even a perfect Service cannot route to a pod that never becomes Ready.
- **Scheduling / capacity pressure** — ruled out: `Normal Scheduled Successfully assigned ... to incident-lab-control-plane`, `PodScheduled True`, no `FailedScheduling` or `Unschedulable` events, and `Pods Status: 2 Running / 0 Waiting` on the ReplicaSet.
- **Cluster infrastructure fault (DNS, CNI, kube-proxy)** — ruled out: `coredns` 2/2 Running, `kindnet` and `kube-proxy` daemonsets 1/1 ready, all control-plane pods `1/1 Running` with 0 restarts and 10h uptime. Also, the probe failure is `connection refused` from the kubelet to the pod's own IP — a local, in-pod-network result, not a networking outage (which would present as timeout/no route).
- **Fault in the checkout-gateway itself** — ruled out as root cause: `checkout-gateway-7b867bfc46-fgfqx 1/1 Running`, `deployment.apps/checkout-gateway 1/1 ... 1 AVAILABLE`, 0 restarts. The gateway is healthy and is correctly reporting that its dependency is unreachable.
- **Probe too aggressive (needs `initialDelaySeconds`) rather than wrong port** — ruled out: a slow-start pod would eventually pass once bound, but the app already logged `serving on :8080` at start time while the probe still gets `connection refused` on 8081 four consecutive times (`x4 over 15s`). Timing tuning would not help a port that is never opened.

## Verification recipe

```bash
# 1. Prove the Service has no ready backends -> gateway calls cannot complete.
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o yaml
#    Expect: endpoints present but "conditions: {ready: false}" (or an empty/notReadyAddresses-only set).

# 2. Prove 8080 answers and 8081 is closed inside a live pod.
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- \
  sh -c 'wget -qS -O- -T2 http://127.0.0.1:8080/ ; echo "--- now 8081 ---" ; wget -qS -O- -T2 http://127.0.0.1:8081/'
#    Expect: 8080 responds; 8081 fails with "Connection refused".

# 3. Confirm the wrong port lives in the Deployment spec (the thing to change).
kubectl get deploy orders-api -n orders \
  -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}{"  vs containerPort="}{.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'
#    Expect: "8081  vs containerPort=8080".
```

**Remediation:** patch the Deployment's readiness probe to target the serving port (ideally by name, since the container port is already named `http`):

```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":"http"}]'
kubectl rollout status deploy/orders-api -n orders
```

Pods should go `1/1 Ready`, the `orders-api` EndpointSlice should populate with ready addresses, and gateway 5xx should clear. Follow-up: this is revision 1 with `OldReplicaSets: <none>`, so there is no prior good revision to roll back to — the fix must go forward. Add a pre-merge check that a readiness probe's port matches a declared `containerPort`, and consider `minReadySeconds` plus a rollout-status gate in CI/CD so a release that never reaches Ready fails the pipeline instead of the checkout path.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's pod template defines a readiness probe against http://:8081/ while the container actually listens on :8080, so every probe is refused (\"dial tcp 10.244.0.122:8081: connect: connection refused\") and both replicas remain not Ready. Because Kubernetes only adds Ready pods to a Service's ready endpoints, the orders-api Service is left with no backends, so calls to the orders backend never complete and surface as 5xx at the checkout gateway.",
  "verdict": "confirmed"
}
```