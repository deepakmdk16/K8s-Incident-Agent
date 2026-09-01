## Root cause

**Service `shop/catalog` has a selector that matches no pods.** The Service selects `app=catalog-api`, but the catalog Deployment stamps its pods with `app=catalog`. Because no pod carries the `catalog-api` label, the Service's EndpointSlice is empty, so every storefront-gateway connection to `catalog.shop.svc:8080` is routed to zero backends — kube-proxy has no destination to DNAT to, and the connection hangs until the client times out. The catalog pods themselves are healthy and Ready, which is exactly why the Deployment reports `2/2` while the gateway sees nothing complete.

Verdict: **confirmed** — the label mismatch is directly visible in the provided output and is a sufficient cause of "backend Ready but calls never complete".

## Evidence chain

1. **The Service selector** — from `kubectl get all -A`, services table:
   `shop  service/catalog  ClusterIP  10.96.96.22  <none>  8080/TCP  5s  app=catalog-api`
   The Service targets pods labeled `app=catalog-api`.

2. **The pods' actual labels** — from the same output, replicaset row:
   `shop  replicaset.apps/catalog-65498fccb  ...  SELECTOR: app=catalog,pod-template-hash=65498fccb`
   The ReplicaSet's selector is the pod template's label set, so `catalog-65498fccb-g52gl` and `catalog-65498fccb-lbrwn` are labeled `app=catalog` (plus the hash). Neither carries `app=catalog-api`.

3. **Deployment selector confirms the same label** — deployments table:
   `shop  deployment.apps/catalog  2/2  2  2  5s  api  busybox:1.36  app=catalog`
   The owning workload consistently uses `app=catalog`; `catalog-api` appears nowhere except in the Service.

4. **Symptom matches "empty endpoints", not "unhealthy backend"** — pods table:
   `shop  pod/catalog-65498fccb-g52gl  1/1  Running  0  5s` and `...-lbrwn  1/1  Running  0  5s`, both with `0` restarts and real pod IPs `10.244.0.120` / `10.244.0.119`.
   This corroborates the page's statement that "Catalog deployment shop/catalog reports all replicas Ready" — the backend is fine; the *routing layer* is broken. An empty Service produces hangs/timeouts (spinner hangs, empty grids) rather than fast connection-refused or 5xx from a crashing app, which matches the reported symptom.

5. **Gateway exists and is healthy, so the caller is not the problem** — `shop  pod/storefront-gateway-6785fd7b5d-cq9bt  1/1  Running  0  5s` and `deployment.apps/storefront-gateway  1/1  1  1`. The failure is on the path from gateway → `catalog` Service.

6. **Only one Service fronts catalog** — the services table lists exactly three Services cluster-wide (`kubernetes`, `kube-dns`, `shop/catalog`). There is no second, correctly-selecting catalog Service that could be absorbing the traffic, so this mismatch is on the sole path to the backend.

## Investigation ledger

- **Catalog pods crashing / not Ready (classic backend outage)** — ruled out: both catalog pods show `1/1  Running` with `RESTARTS 0`, and `deployment.apps/catalog` shows `READY 2/2`, `AVAILABLE 2`.
- **Image pull or scheduling failure** — ruled out: no `ImagePullBackOff`/`Pending` states; all `shop` pods have assigned node `incident-lab-control-plane` and pod IPs.
- **Cluster DNS broken (gateway can't resolve `catalog.shop.svc`)** — ruled out: `deployment.apps/coredns` is `2/2`, both `coredns-559f6c778d-*` pods are `1/1 Running` with 0 restarts, and `service/kube-dns` exists with selector `k8s-app=kube-dns` matching those pods. Also, DNS failure would fail fast (NXDOMAIN) rather than hang; and the Service object *does* exist, so its A record resolves — resolution succeeds, connection has nowhere to go.
- **kube-proxy / CNI dataplane failure** — ruled out: `daemonset.apps/kube-proxy` is `1/1 READY 1` and `daemonset.apps/kindnet` is `1/1 READY 1`, both `1/1 Running` with 0 restarts on the single node. A node-wide dataplane failure would also break `kube-dns` and control-plane traffic, which are healthy.
- **Wrong Service port / targetPort mismatch** — considered and not the paged cause: a port mismatch produces connection-refused or reset from a real pod IP, but here there are no backend IPs at all to connect to. The selector mismatch is upstream of and sufficient for the timeout symptom. (Port correctness can't be fully judged without `-o yaml`, but it cannot be the operative fault while endpoints are empty.)
- **Storefront-gateway misconfiguration (wrong upstream host)** — ruled out as the primary cause: the gateway is `1/1 Running` and the page states it *is* reaching for the catalog backend; a self-inflicted wrong hostname would be a DNS failure, not a hang. The one concrete, provable defect in the output is the Service/pod label divergence.
- **NetworkPolicy blocking gateway→catalog** — not evidenced in the provided output (no NetworkPolicy objects appear in `get all`, though `get all` would not list them). Set aside because the selector mismatch already fully explains the symptom without needing an additional fault.

## Verification recipe

```bash
# 1) The smoking gun: the Service should have zero endpoints.
kubectl -n shop get endpointslices -l kubernetes.io/service-name=catalog -o wide
kubectl -n shop get endpoints catalog          # expect ENDPOINTS: <none>

# 2) Prove the label divergence side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'   # app=catalog-api
kubectl -n shop get pods -l app=catalog --show-labels                  # 2 pods, app=catalog
kubectl -n shop get pods -l app=catalog-api                            # "No resources found"
```

**Remediation:** point the Service at the labels the workload actually has —
`kubectl -n shop patch svc catalog -p '{"spec":{"selector":{"app":"catalog"}}}'` — then re-check `kubectl -n shop get endpoints catalog` for two ready addresses (`10.244.0.119`, `10.244.0.120`) and confirm gateway latency recovers. (Equivalently, relabel the Deployment's pod template to `app: catalog-api`, but patching the Service is non-disruptive and avoids a pod rollout; fix it in the manifest/GitOps source so the drift doesn't reappear on next deploy.)

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "The catalog Service selects pods labeled app=catalog-api, but the catalog pods are labeled app=catalog, so the selector matches nothing and the Service's EndpointSlice is empty. With zero backend addresses, kube-proxy has no destination for connections to catalog.shop.svc:8080, so the storefront gateway's calls hang until they time out, producing spinner hangs and empty product grids even though every catalog replica is Ready.",
  "verdict": "confirmed"
}
```