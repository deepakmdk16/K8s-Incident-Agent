## Root cause

**Service `shop/catalog` has a selector that matches no pods.** The Service selects `app=catalog-api`, but the catalog Deployment's ReplicaSet stamps its pods with `app=catalog`. Because no pod carries `app=catalog-api`, the Service's EndpointSlice is empty, so every connection the storefront gateway opens to `catalog.shop.svc:8080` has no backend to be load-balanced to — it hangs until the client times out. The catalog pods themselves are healthy and Ready, which is exactly why the Deployment reports `2/2` while product pages still time out.

Verdict: **confirmed** — the selector/label mismatch is directly visible in the provided output and is a sufficient cause of "backend Ready but calls never complete."

## Evidence chain

1. **The Service selector** — from `kubectl get all -A`, Services table:
   `shop service/catalog ClusterIP 10.96.96.22 <none> 8080/TCP 5s app=catalog-api`
   The `SELECTOR` column reads `app=catalog-api`.

2. **The actual pod labels** — from the same output, ReplicaSet table:
   `shop replicaset.apps/catalog-65498fccb ... app=catalog,pod-template-hash=65498fccb`
   The ReplicaSet's selector (and therefore the labels it applies to its pods) is `app=catalog`, **not** `app=catalog-api`. Confirmed again by the Deployment table: `shop deployment.apps/catalog 2/2 ... app=catalog`.

3. **No other pod in the cluster could match** — the pod list in `kubectl get all -A` contains exactly three pods in `shop`: `catalog-65498fccb-g52gl`, `catalog-65498fccb-lbrwn` (owned by the `app=catalog` ReplicaSet) and `storefront-gateway-6785fd7b5d-cq9bt` (owned by the `app=storefront-gateway` ReplicaSet). Nothing anywhere carries `app=catalog-api`, so the Service's endpoint set is necessarily empty.

4. **Symptom shape matches an empty-endpoints Service** — the page says "storefront gateway reports its calls to the catalog backend are not completing" and "catalog deployment shop/catalog reports all replicas Ready." Both are simultaneously true here: `deployment.apps/catalog 2/2` and both pods `1/1 Running`, `RESTARTS 0`, yet no traffic can reach them through the ClusterIP. Requests to a ClusterIP with zero endpoints are not RST-ed by a listener; they stall, which is the "spinner hangs / intermittent timeouts" shoppers see.

5. **Nothing else in the path is broken** — `coredns` is `2/2` Running with `RESTARTS 0`, `kube-proxy-6ndq6` and `kindnet-88ckx` are `1/1` Running with `RESTARTS 0`, and `service/kube-dns` has a correctly matching selector `k8s-app=kube-dns` against `deployment.apps/coredns ... k8s-app=kube-dns`. So DNS resolution and dataplane programming are intact; the gateway resolves `catalog.shop.svc` to `10.96.96.22` and then finds nothing behind it.

## Investigation ledger

- **Catalog pods crashing / not actually serving** — ruled out: both `pod/catalog-65498fccb-g52gl` and `pod/catalog-65498fccb-lbrwn` show `READY 1/1`, `STATUS Running`, `RESTARTS 0`, and `deployment.apps/catalog` shows `READY 2/2` / `AVAILABLE 2`. Failing readiness probes would show `0/1` and a non-Available Deployment.

- **Insufficient replicas / capacity overload causing intermittent timeouts** — ruled out: the Deployment is at its desired count (`DESIRED 2 CURRENT 2 READY 2` on `replicaset.apps/catalog-65498fccb`), there are no Pending pods, and a capacity problem would not coexist with a Service selector that matches nothing.

- **DNS failure (gateway can't resolve the catalog name)** — ruled out: `coredns-559f6c778d-9sqc8` and `-t9nfq` are `1/1 Running` with `RESTARTS 0`, and `service/kube-dns` selector `k8s-app=kube-dns` correctly matches `deployment.apps/coredns` labels `k8s-app=kube-dns`. DNS plumbing is intact.

- **kube-proxy / CNI dataplane fault** — ruled out: `daemonset.apps/kube-proxy` is `1/1` desired/ready and `daemonset.apps/kindnet` is `1/1` desired/ready, with pod `kube-proxy-6ndq6` and `kindnet-88ckx` both `1/1 Running`, `RESTARTS 0`. A broken dataplane would also break coredns and apiserver traffic, which are healthy.

- **Wrong Service port / port-name mismatch** — considered and set aside: the Service exposes `8080/TCP`, which is plausible for the busybox `api` container, and a port mismatch would produce connection *refused/reset*, not the hang described. Regardless, it is moot — with zero selected pods there is no endpoint for any port. If the selector is fixed and calls still fail, re-check `targetPort` against the container's listening port.

- **Gateway misconfiguration (pointing at the wrong host)** — considered: the page states the gateway's calls to the catalog backend "are not completing," and the gateway pod is `1/1 Running`. The Service that exists to serve it is provably backend-less, which fully explains the symptom without invoking a second fault. No gateway config was provided to contradict this.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints at all.
kubectl -n shop get endpoints catalog -o wide
kubectl -n shop get endpointslice -l kubernetes.io/service-name=catalog -o wide
#    Expect: ENDPOINTS <none> / no addresses.

# 2. Show the selector vs. the real pod labels side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'
kubectl -n shop get pods --show-labels
#    Expect: selector {"app":"catalog-api"} but pods labeled app=catalog.

# 3. Prove the selector matches nothing, and that the pods are fine when addressed directly.
kubectl -n shop get pods -l app=catalog-api          # expect: "No resources found"
kubectl -n shop run probe --rm -it --image=busybox:1.36 --restart=Never -- \
  sh -c 'wget -T 3 -qO- http://10.244.0.119:8080/ ; echo "---" ; wget -T 3 -qO- http://catalog.shop.svc.cluster.local:8080/'
#    Expect: direct pod IP responds, ClusterIP call hangs/times out.
```

**Remediation:** change the Service spec to match the pods — `kubectl -n shop patch svc catalog -p '{"spec":{"selector":{"app":"catalog"}}}'` — then re-run step 1 and confirm two endpoints appear. (Fix it in the manifest/Helm values, not just live, or the next apply will re-break it.) Do *not* relabel the pods instead: mutating the Deployment's `spec.selector` is immutable and relabeling only the pod template would orphan the running ReplicaSet.

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "Service shop/catalog selects pods with label app=catalog-api, but the catalog Deployment's pods are labeled app=catalog, so no pod matches the selector and the Service's EndpointSlice is empty. Connections the storefront gateway makes to the catalog ClusterIP on 8080 have no backend to route to and stall until they time out, producing storewide product page hangs even though the catalog pods are Running and Ready.",
  "verdict": "confirmed"
}
```