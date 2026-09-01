## Root cause

**Service `shop/catalog` has a selector that matches no Pods.** The Service selects `app=catalog-api`, but the only backing workload (`deployment.apps/catalog` → `replicaset.apps/catalog-65498fccb`) labels its Pods `app=catalog`. With no label match, the Service's EndpointSlice is empty, so every connection the storefront gateway opens to `catalog.shop.svc:8080` lands on a ClusterIP with zero backends and hangs until the client times out — exactly the "spinner hangs / empty product grids / calls not completing" symptom. The catalog Pods themselves are healthy and Ready, which is why the deployment looks green while traffic is black-holed.

Verdict: **confirmed** — the selector/label mismatch is directly readable in the provided output and is sufficient to produce empty endpoints.

## Evidence chain

1. **Service selector** — from `kubectl get all -A`, services table:
   `shop   service/catalog   ClusterIP   10.96.96.22   <none>   8080/TCP   5s   app=catalog-api`
   The Service selects `app=catalog-api`.

2. **Actual Pod labels** — from the same output, replicaset row:
   `shop   replicaset.apps/catalog-65498fccb   2   2   2   5s   api   busybox:1.36   app=catalog,pod-template-hash=65498fccb`
   The pod template stamps `app=catalog`, not `app=catalog-api`. The deployment row confirms the same: `deployment.apps/catalog ... SELECTOR app=catalog`.

3. **No other workload could satisfy the Service** — the full `-A` listing contains only three pods in `shop`: `catalog-65498fccb-g52gl`, `catalog-65498fccb-lbrwn`, `storefront-gateway-6785fd7b5d-cq9bt`. No workload anywhere carries `app=catalog-api`, so the Service's endpoint set is necessarily empty.

4. **Symptom shape matches empty endpoints, not a crash** — `pod/catalog-65498fccb-g52gl 1/1 Running 0` and `pod/catalog-65498fccb-lbrwn 1/1 Running 0`, and `deployment.apps/catalog 2/2 2 2`. Backends are Ready with zero restarts, consistent with the page's note "Catalog deployment shop/catalog reports all replicas Ready" while gateway calls "are not completing."

5. **Client side is up** — `pod/storefront-gateway-6785fd7b5d-cq9bt 1/1 Running 0` and `deployment.apps/storefront-gateway 1/1 1 1`; the gateway is not itself failing, it is stalling on its dependency.

## Investigation ledger

- **Catalog pods crashlooping / not Ready** — ruled out: both catalog pods show `1/1 Running` with `RESTARTS 0`, and the deployment/replicaset show `2/2` and `READY 2`.
- **Image pull or scheduling failure** — ruled out: all `shop` pods have assigned IPs (`10.244.0.119/120/121`) and node (`incident-lab-control-plane`), status `Running`, no `Pending`/`ImagePullBackOff`.
- **Cluster DNS broken (gateway can't resolve `catalog.shop`)** — ruled out as the driver: `service/kube-dns` exists with selector `k8s-app=kube-dns`, and both `coredns-559f6c778d-9sqc8` / `-t9nfq` are `1/1 Running 0` under `deployment.apps/coredns 2/2`. DNS would also resolve fine here since the Service object itself exists; the failure is post-resolution.
- **kube-proxy / CNI dataplane outage** — ruled out: `daemonset.apps/kube-proxy 1/1 READY 1` and `daemonset.apps/kindnet 1/1 READY 1`, both `10h` old with no restarts; a node-wide dataplane fault would degrade `kube-dns` and control-plane traffic too, which are healthy.
- **Wrong Service port / port-name mismatch** — not ruled out by port data alone, but subordinate: the Service publishes `8080/TCP`, and even a correct port cannot help when the selector matches zero pods. Selector drift is sufficient and primary.
- **Gateway misconfigured with the wrong upstream host** — considered; disfavored because the gateway reaches a real Service name that exists in `shop` and the page describes hangs (no backends → connect timeout) rather than NXDOMAIN/immediate refusal.
- **Recent rollout still converging** — ruled out: `AGE 5s` for all `shop` objects, but the replicaset already reports `DESIRED 2 / CURRENT 2 / READY 2`; readiness is complete, and the selector text is static config, not a transient state.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no backends.
kubectl -n shop get endpointslices -l kubernetes.io/service-name=catalog -o wide
kubectl -n shop get endpoints catalog

# 2. Show the mismatch side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'
kubectl -n shop get pods --show-labels

# 3. Prove the selector is the cause: pods appear only under the correct label.
kubectl -n shop get pods -l app=catalog-api    # expect: No resources found
kubectl -n shop get pods -l app=catalog        # expect: the 2 Ready catalog pods
```

Expected confirmation: `endpoints catalog` shows `<none>`, `-l app=catalog-api` returns nothing, and `-l app=catalog` returns both Ready pods.

**Remediation:** patch the Service selector to match the shipped pod labels —
`kubectl -n shop patch svc catalog --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'` — then re-check that `kubectl -n shop get endpoints catalog` lists `10.244.0.119:8080,10.244.0.120:8080`. (Alternatively, relabel the deployment pod template to `app=catalog-api`, but changing the Service is the smaller, safer blast radius since the Deployment's own `selector` is immutable.) Follow-up: add a CI/admission check or an alert on `kube_endpoint_address_available == 0` so a Service with zero endpoints pages before shoppers see it.

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "Service shop/catalog selects pods with label app=catalog-api, but the catalog pods are labeled app=catalog, so no pod matches and the Service's EndpointSlice is empty. Traffic the storefront gateway sends to the catalog ClusterIP on 8080 has zero backends to load-balance to, so connections hang and time out instead of being served, producing storewide product-page timeouts while the catalog replicas themselves stay Ready.",
  "verdict": "confirmed"
}
```