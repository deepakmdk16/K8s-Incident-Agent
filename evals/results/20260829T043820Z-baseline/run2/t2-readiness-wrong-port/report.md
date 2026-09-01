## Root cause

**Deployment `orders/orders-api`** ships a readiness probe pointed at the wrong port. The container serves HTTP on **:8080** (the declared container port and what the app logs), but the readiness probe does an HTTP GET against **:8081**, where nothing is listening. Every probe gets `connection refused`, so no `orders-api` pod ever becomes Ready. Because the `orders-api` Service selects `app=orders-api` and only Ready pods are placed in its endpoints, the Service has zero backing endpoints — the checkout gateway's calls to `orders-api:8080` never reach a backend, and it returns 5xx for all order placements.

Verdict: **confirmed**.

## Evidence chain

- **Probe target vs. listen port (the defect itself)** — `describe deployment.apps/orders-api -n orders`, pod template:
  - `Port:       8080/TCP (http)`
  - `Readiness:    http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`
  The probe port (8081) does not match the port the container exposes (8080).
- **The app really is listening on 8080, not 8081** — log line from both pods:
  - `orders-api: serving on :8080` (pod `orders-api-7cc5bcf4c7-lst42`)
  - `orders-api: serving on :8080` (pod `orders-api-7cc5bcf4c7-pcspl`)
  No log line mentions 8081, and there are no error/crash lines — the process is healthy.
- **Probe failure mechanism is "nothing bound to that port", not "app broken"** — `describe pod/orders-api-7cc5bcf4c7-lst42`:
  - `Warning  Unhealthy  5s (x4 over 15s)  kubelet  Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`
  Identical on the sibling: `dial tcp 10.244.0.123:8081: connect: connection refused`. `connection refused` (not timeout, not 404/503) means no listener on 8081 — the process is up and the kernel actively rejects.
- **Container is running, never restarted** — `describe pod/...-lst42`: `State: Running`, `Ready: False`, `Restart Count: 0`; the probe is readiness-only (no liveness probe listed), so failures gate traffic rather than kill the pod. Same for `...-pcspl`.
- **Zero Ready replicas ⇒ empty Service endpoints** — `kubectl get all -A`:
  - `pod/orders-api-7cc5bcf4c7-lst42   0/1   Running` and `pod/orders-api-7cc5bcf4c7-pcspl   0/1   Running`
  - `deployment.apps/orders-api   0/2   2   0`
  - `replicaset.apps/orders-api-7cc5bcf4c7   DESIRED 2 / CURRENT 2 / READY 0`
  - `service/orders-api ClusterIP 10.96.113.141 8080/TCP  SELECTOR app=orders-api`
  The Service selector matches the pods' label `app=orders-api`, but with 0 Ready pods the endpoint set is empty.
- **Deployment agrees it is unavailable** — `describe deployment.apps/orders-api`:
  - `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`
  - `Available  False  MinimumReplicasUnavailable`
- **Symptom linkage** — the page says the gateway's "calls to the orders backend are not completing." `pod/checkout-gateway-7b867bfc46-fgfqx  1/1  Running` — the gateway itself is healthy and Ready, so the failure is downstream of it, at the `orders-api` Service, which has no endpoints.
- **Timing** — the page says the release was ~30 min ago; the Deployment/ReplicaSet/Service all show `AGE 5s` in this snapshot with `deployment.kubernetes.io/revision: 1` and `OldReplicaSets: <none>`, i.e. this is the first and only revision of this object. The misconfiguration was present from the moment the workload was created; there is no prior good revision embedded in this Deployment's history.

## Investigation ledger

- **Application crash / bad image** — ruled out. `Restart Count: 0`, `State: Running`, image `busybox:1.36` pulled successfully (`Container image "busybox:1.36" already present on machine`), and the app logged `orders-api: serving on :8080`. No CrashLoopBackOff, no OOMKill, no error logs.
- **Image pull failure** — ruled out. Event `Normal Pulled ... already present on machine`, followed by `Created` and `Started`.
- **Scheduling / capacity / node pressure** — ruled out. Both pods show `Normal Scheduled ... Successfully assigned` to `incident-lab-control-plane`, `PodScheduled True`, no `FailedScheduling`, no taint/toleration or nodeSelector constraints (`Node-Selectors: <none>`).
- **Service selector mismatch / label typo** — ruled out. `service/orders-api` selector is `app=orders-api`; both pods carry `Labels: app=orders-api`. Selection is fine; the pods are excluded from endpoints purely because they are not Ready (`0/1`).
- **Service targetPort pointing at the wrong container port** — ruled out as the cause of the page. Service publishes `8080/TCP` and the container port is `8080/TCP (http)` — consistent. Even if it were wrong, it could not explain `0/1` READY, which is decided by the kubelet probe alone.
- **Probe timing too aggressive (app slow to start, needs initialDelay)** — ruled out. A slow-starting app yields connection refused that *stops* once it binds; here the app already logged `serving on :8080` at container start and the refusals continue against a *different* port (8081) that it never binds. Also `x4 over 15s` failures with a probe interval of 5s means it has failed continuously since t=0. The error is port-specific, not time-specific.
- **Liveness probe killing the container mid-request** — ruled out. No liveness probe is defined in the pod spec (only `Readiness:` appears), and `Restart Count: 0`.
- **DNS failure between gateway and orders-api** — ruled out. Both `coredns` pods are `1/1 Running` for 10h and `service/kube-dns` exists. DNS would resolve `orders-api` fine; the problem is that the resolved Service has no endpoints.
- **Missing ConfigMap `orders-api-scripts`** — ruled out. Volume is `Optional: false`; a missing ConfigMap would have blocked the container at `ContainerCreating`. Instead `Initialized True`, `PodReadyToStartContainers True`, and the script clearly ran (it produced the `serving on :8080` log).
- **Checkout gateway itself broken** — ruled out. `checkout-gateway-7b867bfc46-fgfqx  1/1  Running`, deployment `1/1  READY`, no probe failures or restarts reported.
- **Cluster/control-plane infrastructure fault** — ruled out. All `kube-system` components (`etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet`) are `1/1 Running` with 0 restarts for 10h; reconciliation is working (RS created both pods on schedule).

## Verification recipe

```bash
# 1. Confirm the Service has no endpoints -> gateway gets 5xx (the symptom).
kubectl get endpointslices -n orders -l kubernetes.io/service-name=orders-api -o wide
kubectl get endpoints orders-api -n orders

# 2. Confirm the probe port is 8081 while the container port/listener is 8080 (the defect).
kubectl get deploy orders-api -n orders \
  -o jsonpath='probePort={.spec.template.spec.containers[0].readinessProbe.httpGet.port}{"\n"}containerPort={.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'

# 3. Prove 8080 answers and 8081 refuses, from inside the pod's netns.
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8080/ ; echo "8080 rc=$?"
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8081/ ; echo "8081 rc=$?"
```

Expected: empty/`<none>` endpoints; `probePort=8081` vs `containerPort=8080`; 8080 returns a response while 8081 fails with connection refused.

**Remediation:** patch the Deployment's readiness probe to target the port the app actually serves.

```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":8080}]'
kubectl rollout status deploy/orders-api -n orders --timeout=120s
```

Pods should report `1/1 READY`, the Deployment `2/2 available`, endpoints populate with both pod IPs, and gateway 5xx should clear. Follow-ups: pin the probe to the named port (`port: http`) so it can never drift from `containerPort`; add a CI/admission check that readiness probe ports resolve to a declared container port; and make the rollout gate on `Available` (`minReadySeconds` + `kubectl rollout status`) so a release where zero replicas become Ready fails the pipeline instead of reaching the gateway.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's pod template defines a readiness probe against http://:8081/ while the container declares and actually listens on port 8080, so every probe fails with 'connection refused' and no replica ever becomes Ready. With 0/2 Ready pods, the orders-api Service has no endpoints, so requests to the orders backend never reach a pod and fail at submission.",
  "verdict": "confirmed"
}
```