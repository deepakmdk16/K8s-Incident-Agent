## Root cause

**Deployment `orders-api` in namespace `orders`** ships a readiness probe pointed at the wrong port. The container listens on **:8080** (the declared container port, and what the app logs at startup), but the readiness probe is `http-get http://:8081/`. Nothing is bound to 8081, so every probe gets `connection refused`, both replicas stay `Ready: False` forever, and the `orders-api` Service (selector `app=orders-api`) therefore has **no ready endpoints**. The checkout gateway's calls to `orders-api:8080` have no backend to land on, which surfaces at the gateway as 5xx / non-completing calls to the orders backend.

**Verdict: confirmed.**

## Evidence chain

- Probe targets a port nothing listens on — `describe deployment.apps/orders-api -n orders`, pod template: `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`, while the same template declares `Port: 8080/TCP (http)`.
- The app really is on 8080, not 8081 — log line from both pods: `orders-api: serving on :8080` (pod `orders-api-7cc5bcf4c7-lst42` and `...-pcspl`). Note the process started cleanly and did not crash or restart.
- The probe fails with refusal, not timeout/500 (i.e. no listener, not a sick listener) — `describe pod/orders-api-7cc5bcf4c7-lst42`: `Warning Unhealthy ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`. Identical event on `...-pcspl` for `10.244.0.123:8081`. Both replicas, same failure ⇒ systemic config, not a one-off pod.
- Pods are alive but never ready — `kubectl get all -A`: `pod/orders-api-7cc5bcf4c7-lst42 0/1 Running 0` and `pod/orders-api-7cc5bcf4c7-pcspl 0/1 Running 0`, `RESTARTS 0`. Pod conditions: `Ready False`, `ContainersReady False`, `Initialized True`.
- The workload is rolled out fine but reports zero availability — `describe deployment.apps/orders-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`. ReplicaSet: `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed` — creation succeeded, readiness did not.
- Link to the paged symptom: `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP ... app=orders-api`. Kubernetes only puts **ready** pods in a Service's ready endpoints; with both `app=orders-api` pods `Ready: False`, the Service has no backends, so gateway calls to the orders backend cannot complete.
- Timing matches the page: Service, Deployment and pods all `AGE 5s` / `deployment.kubernetes.io/revision: 1`, consistent with "the orders-api deployment was released about 30 minutes ago" being the change that introduced this.

## Investigation ledger

- **Gateway (`checkout-gateway`) itself is broken** — ruled out: `pod/checkout-gateway-7b867bfc46-fgfqx 1/1 Running 0` with no restarts and no failure events anywhere in the output. The alert source is the gateway's 5xx monitor, but the gateway is healthy; it is reporting a downstream failure.
- **App crash / CrashLoopBackOff / bad image** — ruled out: `RESTARTS 0`, `State: Running`, `Started`, and `Pulled ... "busybox:1.36" already present on machine`. Logs show a clean `serving on :8080` with no error output.
- **Scheduling / capacity / node problem** — ruled out: `Normal Scheduled Successfully assigned orders/... to incident-lab-control-plane`, `PodScheduled True`, both pods have IPs, and all `kube-system` control-plane and CNI pods are `1/1 Running`.
- **Missing/failed ConfigMap `orders-api-scripts` mount** — ruled out: `Optional: false` volume mounted at `/app` and the container successfully executed `sh /app/run.sh` (it logged its startup line), so the script was present and readable.
- **Service selector/port mismatch (Service pointing at wrong labels or targetPort)** — ruled out as the *root* cause: Service selector `app=orders-api` exactly matches the pod labels `app=orders-api`, and the Service port `8080/TCP` matches the container's actual listener. Endpoints are empty solely because the pods are not `Ready`.
- **Liveness probe killing the container** — ruled out: no liveness probe is defined in the pod template, and restart count is 0.
- **DNS failure between gateway and orders-api** — ruled out: both `coredns` pods are `1/1 Running` for 10h and `kube-dns` Service is intact; nothing in the output shows resolution errors.
- **Network policy blocking traffic** — ruled out by absence: no NetworkPolicy objects appear in `kubectl get all -A`, and the probe failure is `connection refused` from the kubelet on the pod's own IP, which is a local no-listener signal rather than a filtered/dropped packet.

## Verification recipe

```bash
# 1. Endpoints for the Service should be empty / notReadyAddresses only -> proves no backends for the gateway
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o yaml

# 2. Show the probe port vs the real container port side by side
kubectl get deploy orders-api -n orders \
  -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}{" <- probe | container: "}{.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'

# 3. Prove 8080 answers and 8081 refuses from inside a pod
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8080/ ; \
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8081/
```

Remediation: patch the readiness probe to the port the app actually serves (8080, or the named port `http`), then confirm `2/2` ready and non-empty endpoints:

```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":"http"}]'
kubectl rollout status deploy/orders-api -n orders
```

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's pod template defines a readiness probe against http://:8081/ while the container actually listens on :8080, so every probe is refused and both replicas stay Ready=False. Because Kubernetes only adds ready pods to a Service's endpoints, the orders-api Service has no backends and calls from the checkout gateway to the orders backend never complete, surfacing as 5xx at checkout.",
  "verdict": "confirmed"
}
```