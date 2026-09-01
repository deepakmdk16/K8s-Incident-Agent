## Root cause

The `orders-api` Deployment (namespace `orders`) ships a readiness probe pointed at the wrong port. The container serves HTTP on **8080** (the declared container port, and what the app logs), but the readiness probe does an HTTP GET against **:8081**, where nothing is listening. Every probe gets `connection refused`, so both replicas stay `Ready: False` forever. Because Service endpoints only include ready pods, `service/orders-api` has no backends, and the checkout-gateway's calls to the orders backend never complete — surfacing as 5xx at the gateway for all shoppers.

Verdict: **confirmed** — the probe failure message names the exact port mismatch, and the app log names the port actually being served.

## Evidence chain

1. **The app is healthy and listening on 8080.**
   - `kubectl logs orders-api-7cc5bcf4c7-lst42 -c api`: `orders-api: serving on :8080`
   - `kubectl logs orders-api-7cc5bcf4c7-pcspl -c api`: `orders-api: serving on :8080`
   - Both pods show `State: Running`, `Restart Count: 0` — the process is up and stable, not crashing.

2. **The readiness probe targets 8081, not 8080.**
   - `describe deployment.apps/orders-api`, Pod Template: `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`, while the same container block declares `Port: 8080/TCP (http)`.
   - Same mismatch is baked into the ReplicaSet template (`describe replicaset.apps/orders-api-7cc5bcf4c7`) and both live pods.

3. **The probe is failing precisely because of that port.**
   - `describe pod/orders-api-7cc5bcf4c7-lst42`, Events: `Warning Unhealthy ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`
   - `describe pod/orders-api-7cc5bcf4c7-pcspl`, Events: same message on `10.244.0.123:8081`.
   - `connection refused` (not timeout, not 404) = the port is closed on a reachable pod IP — consistent with a listener on 8080 only.

4. **Pods therefore never become Ready.**
   - Both pod describes: `Ready: False`, conditions `Ready False` / `ContainersReady False`.
   - `kubectl get all -A`: `pod/orders-api-7cc5bcf4c7-lst42 0/1 Running` and `pod/orders-api-7cc5bcf4c7-pcspl 0/1 Running`.

5. **Zero ready pods ⇒ Service has no endpoints ⇒ gateway 5xx.**
   - `describe deployment.apps/orders-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`.
   - `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP ... SELECTOR app=orders-api` — the selector matches the pods, but unready pods are excluded from the endpoint slice, so traffic to the ClusterIP has nowhere to go. This matches the page's "calls to the orders backend are not completing."

6. **Timing matches the page.**
   - `describe deployment`: `Events: Normal ScalingReplicaSet ... Scaled up replica set orders-api-7cc5bcf4c7 from 0 to 2`, `deployment.kubernetes.io/revision: 1`, deployment AGE `5s` in this snapshot — the fresh rollout referenced in the page ("released about 30 minutes ago") is the same object that has never gone Available.

## Investigation ledger

- **Gateway itself is broken** — ruled out. `kubectl get all -A` shows `pod/checkout-gateway-7b867bfc46-fgfqx 1/1 Running`, `RESTARTS 0`, and `deployment.apps/checkout-gateway 1/1 ... AVAILABLE 1`. The gateway is healthy; it is reporting a downstream failure.
- **Image pull / bad image** — ruled out. Pod events: `Pulled ... Container image "busybox:1.36" already present on machine`, followed by `Created` and `Started`. No `ErrImagePull`/`ImagePullBackOff`.
- **App crash-looping or exiting on startup** — ruled out. `Restart Count: 0`, `State: Running`, and the app emitted `orders-api: serving on :8080`. A crashed app would show restarts, `CrashLoopBackOff`, or a terminated state.
- **Missing/failed ConfigMap volume (`orders-api-scripts`, `Optional: false`)** — ruled out. A missing ConfigMap would leave the pod in `ContainerCreating` with a `FailedMount` event; instead the container started and executed `/app/run.sh` successfully enough to log its startup banner.
- **Scheduling / capacity / node problems** — ruled out. Both pods have `PodScheduled True` and `Normal Scheduled Successfully assigned ... to incident-lab-control-plane`; the single node runs all control-plane pods `1/1 Running`.
- **Service selector mismatch (Service pointing at wrong labels)** — ruled out as the cause. `service/orders-api` selector is `app=orders-api` and the pods carry `Labels: app=orders-api`. The selector matches; the exclusion is due to readiness, not labels. (Note the Service also targets 8080, agreeing with the app's actual listener — it is the probe, not the Service, that is off.)
- **Service targetPort wrong** — ruled out. Service `PORT(S) 8080/TCP` aligns with the container's declared `Port: 8080/TCP (http)` and the logged listener.
- **Cluster networking (CNI/DNS/kube-proxy) failure** — ruled out. `kindnet`, `kube-proxy` DaemonSets are `1/1` desired/ready and `coredns` is `2/2`; also the kubelet reached the pod IP well enough to get an active `connection refused` rather than a timeout, proving pod networking works.
- **Liveness probe killing the container** — ruled out. No liveness probe is defined in the pod/deployment spec, and restart count is 0.
- **Probe too aggressive (needs longer initialDelay / slow start)** — ruled out. A slow-starting app would eventually pass; here the app already logged that it is serving, and the refusal is on a different port than the one it serves. Loosening timings would never fix a closed port.

## Verification recipe

```bash
# 1. Confirm the Service has zero ready endpoints (the direct cause of gateway 5xx)
kubectl get endpointslices -n orders -l kubernetes.io/service-name=orders-api -o wide
kubectl get endpoints orders-api -n orders          # expect ENDPOINTS: <none>

# 2. Confirm the probe port differs from the port actually listening
kubectl get deploy orders-api -n orders \
  -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}{"  vs container port: "}{.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'
# expect: 8081  vs container port: 8080

# 3. Prove 8080 answers and 8081 refuses, from inside the pod's network
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8080/ ; echo "8080 rc=$?"
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8081/ ; echo "8081 rc=$?"
```

**Remediation:** patch the Deployment's readiness probe to the port the app actually serves (8080), ideally by name so it can't drift again:

```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":"http"}]'
kubectl rollout status deploy/orders-api -n orders
```

Since revision 1 never reached Available, there is no healthy prior revision to roll back to — fixing the probe port forward is the correct action. Follow-up: add a pre-merge check that every probe port matches a declared `containerPort`, and gate the checkout gateway on `orders-api` availability so a never-Ready rollout fails the deploy instead of paging.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's readiness probe does an HTTP GET on port 8081, but the container listens only on 8080, so every probe returns 'connection refused' and both replicas stay Ready:False. Unready pods are excluded from the orders-api Service endpoints, leaving the ClusterIP with no backends, so calls to the orders backend never complete and surface as 5xx at checkout.",
  "verdict": "confirmed"
}
```