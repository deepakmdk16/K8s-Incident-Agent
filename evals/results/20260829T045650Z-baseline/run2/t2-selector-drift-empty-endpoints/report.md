## Root cause

**`Service shop/catalog` has a selector that matches no pods.** The Service's selector is `app=catalog-api`, but the catalog Deployment stamps its pods with `app=catalog`. Because label selection is exact-match, the Service's EndpointSlice is empty, so `catalog.shop.svc.cluster.local:8080` resolves to a ClusterIP with zero backends. Connections from the storefront gateway to that ClusterIP are never DNAT'd to a real pod — they hang until the client times out, which surfaces as spinner hangs and empty product grids. The catalog pods themselves are healthy, which is exactly why the Deployment reports all replicas Ready while traffic still fails.

Verdict: **confirmed**.

## Evidence chain

1. **Service selector** — from `kubectl get all -A`, Services table:
   `shop   service/catalog   ClusterIP   10.96.96.22   <none>   8080/TCP   5s   app=catalog-api`
   The selector column reads `app=catalog-api`.

2. **Actual pod labels** — from the same output, Deployments table:
   `shop   deployment.apps/catalog   2/2   2   2   5s   api   busybox:1.36   app=catalog`
   and ReplicaSet table:
   `shop   replicaset.apps/catalog-65498fccb   2   2   2   5s   ... app=catalog,pod-template-hash=65498fccb`
   The pods produced by this ReplicaSet therefore carry `app=catalog`, not `app=catalog-api`.

3. **Mismatch ⇒ empty endpoints** — `app=catalog-api` ≠ `app=catalog`. Kubernetes label selectors are exact string matches with no prefix/substring semantics, so no pod in `shop` satisfies the Service selector and the endpoints set is empty.

4. **Symptom shape matches empty endpoints, not pod failure** — pods are healthy:
   `shop   pod/catalog-65498fccb-g52gl   1/1   Running   0   5s   10.244.0.120`
   `shop   pod/catalog-65498fccb-lbrwn   1/1   Running   0   5s   10.244.0.119`
   `0` restarts, `1/1` ready, `deployment.apps/catalog   2/2 ... AVAILABLE 2`. This reconciles the page's paradox ("all replicas Ready" yet calls do not complete): readiness is a property of the pod, endpoint membership is a property of the Service selector, and only the latter is broken.

5. **Client is present and healthy, so the failure is on the path, not the caller** — `shop   pod/storefront-gateway-6785fd7b5d-cq9bt   1/1   Running   0   5s   10.244.0.121`, and `deployment.apps/storefront-gateway   1/1   1   1`. The gateway is up and issuing calls; it is the destination resolution that dead-ends.

6. **"Intermittent"/timeout rather than instant refusal** — a ClusterIP with zero endpoints has no kube-proxy DNAT rule pointing anywhere, so traffic is dropped/blackholed rather than RST'd; clients experience hangs until their own timeout fires. That matches "timing out intermittently … spinner hangs" from the alert text.

## Investigation ledger

- **Catalog pods crashlooping / not ready** — ruled out. `pod/catalog-65498fccb-g52gl` and `-lbrwn` are `1/1 Running` with `RESTARTS 0`, and `deployment.apps/catalog` shows `READY 2/2`, `AVAILABLE 2`. A not-ready pod would also be excluded from endpoints, but these pods are ready, so readiness is not the exclusion cause.
- **Insufficient replicas / scaled to zero** — ruled out. `deployment.apps/catalog 2/2` and `replicaset.apps/catalog-65498fccb DESIRED 2 CURRENT 2 READY 2`.
- **Image pull failure or bad image** — ruled out. Both workloads run `busybox:1.36` and are `Running`, not `ImagePullBackOff`/`ErrImagePull`.
- **Wrong Service port / port-name mismatch** — not supported and not needed. The Service exposes `8080/TCP`; a port mismatch would still produce a populated endpoint list with a wrong target port, and would typically yield connection refused rather than a hang. The selector mismatch alone fully explains zero backends, and it is visible directly in the output.
- **DNS failure (CoreDNS down)** — ruled out. `coredns-559f6c778d-9sqc8` and `-t9nfq` are both `1/1 Running` with `0` restarts, `deployment.apps/coredns 2/2`, and `service/kube-dns` exists at `10.96.0.10`. Also, cluster-wide DNS failure would break far more than one storefront path.
- **CNI / kube-proxy / node-level networking outage** — ruled out. `daemonset.apps/kindnet 1/1 READY 1` and `daemonset.apps/kube-proxy 1/1 READY 1`; all pods including control plane are `Running` with `0` restarts on the single node `incident-lab-control-plane`. A datapath outage would not spare the rest of the cluster.
- **NetworkPolicy blocking gateway→catalog** — no NetworkPolicy appears anywhere in `kubectl get all -A` output, and a policy drop would not explain the selector string visibly differing from the pod labels. Not required to explain the symptom.
- **Cross-namespace / wrong Service name in gateway config** — the gateway and catalog are both in `shop` and a `service/catalog` exists in `shop`, so name resolution to the right Service object is fine; the defect is downstream of resolution.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no backends despite Ready pods.
kubectl -n shop get endpoints catalog -o wide
kubectl -n shop get endpointslice -l kubernetes.io/service-name=catalog -o wide
#    Expect: ENDPOINTS "<none>" / no addresses.

# 2. Show the selector vs. the actual pod labels side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'
kubectl -n shop get pods --show-labels -l app=catalog
#    Expect: selector {"app":"catalog-api"} vs pods labeled app=catalog; and
kubectl -n shop get pods -l app=catalog-api
#    Expect: "No resources found in shop namespace."

# 3. Confirm the client-visible symptom is a hang to the ClusterIP.
kubectl -n shop exec deploy/storefront-gateway -- timeout 5 wget -qO- http://catalog:8080/ ; echo "exit=$?"
#    Expect: timeout/no response (non-zero exit), not "connection refused".
```

Remediation: change the Service spec to match the pods — `kubectl -n shop patch svc catalog --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'` — then re-check `kubectl -n shop get endpoints catalog` for two addresses (`10.244.0.119`, `10.244.0.120`). Relabeling the Deployment's pod template to `app=catalog-api` would also work but forces a full pod rollout; patching the Service is the faster, non-disruptive fix. Follow up by adding a CI check or admission policy that fails when a Service selector matches zero pods in its namespace.

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "The Service shop/catalog selects pods with label app=catalog-api, but the catalog pods are labeled app=catalog, so the selector matches nothing and the Service's EndpointSlice is empty. Traffic sent by the storefront gateway to the catalog ClusterIP 10.96.96.22:8080 has no backend to be forwarded to and is blackholed, so calls hang until the client times out and product pages render empty.",
  "verdict": "confirmed"
}
```