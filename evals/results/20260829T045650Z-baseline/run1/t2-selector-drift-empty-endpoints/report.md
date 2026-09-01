## Root cause

**Service `shop/catalog` has a selector that matches no pods.** The Service selects `app=catalog-api`, but the only pods backing the catalog workload carry the label `app=catalog` (set by `deployment.apps/catalog`, whose ReplicaSet selector is `app=catalog,pod-template-hash=65498fccb`). Because no pod satisfies `app=catalog-api`, the Service's EndpointSlice is empty, and every connection the storefront gateway opens to `catalog.shop.svc:8080` has no backend to be routed to — kube-proxy has nothing to DNAT the ClusterIP to, so calls hang until the client times out. The catalog pods themselves are healthy, which is exactly why the deployment reports all replicas Ready while product pages time out.

**Verdict: confirmed.**

## Evidence chain

- Service selector vs. pod labels (the mismatch itself), from `kubectl get all -A`, Services block:
  `shop  service/catalog  ClusterIP  10.96.96.22  <none>  8080/TCP  5s  app=catalog-api`
  and from the same output, Deployments block:
  `shop  deployment.apps/catalog  2/2 ... SELECTOR app=catalog`
  and ReplicaSets block:
  `shop  replicaset.apps/catalog-65498fccb  2 2 2 ... app=catalog,pod-template-hash=65498fccb`
  → The pods produced by the catalog deployment are labeled `app=catalog`; the Service asks for `app=catalog-api`. `app=catalog-api` ≠ `app=catalog`, so the label selector matches zero pods. Selector matching in Kubernetes is exact string equality on the full label; there is no prefix or substring matching.
- Catalog pods are healthy and running, so the failure is not in the workload:
  `shop  pod/catalog-65498fccb-g52gl  1/1  Running  0  5s  10.244.0.120`
  `shop  pod/catalog-65498fccb-lbrwn  1/1  Running  0  5s  10.244.0.119`
  → Matches the page's observation "Catalog deployment shop/catalog reports all replicas Ready", and confirms the breakage is in service discovery, not pod health.
- The client that is symptomatic exists and is itself healthy:
  `shop  pod/storefront-gateway-6785fd7b5d-cq9bt  1/1  Running  0  5s  10.244.0.121`
  and `shop  deployment.apps/storefront-gateway  1/1  1  1`
  → The gateway is up and issuing calls; the failure is on the path from gateway → `catalog` Service.
- Symptom shape corroborates "empty endpoints" rather than "refused/errored": the page says calls "are not completing" and shoppers see "spinner hangs". A ClusterIP with zero endpoints causes packets to be dropped/blackholed rather than actively refused, producing hangs and timeouts — precisely the reported behavior. Intermittency/partial grids is consistent with some gateway paths timing out on the catalog dependency while unrelated page elements render.
- Only one Service fronts catalog; there is no alternate path:
  Services block lists only `default/kubernetes`, `kube-system/kube-dns`, and `shop/catalog` — no second catalog Service, headless variant, or manually managed Endpoints object is visible.

## Investigation ledger

- **Catalog pods crashing / not ready** — ruled out. `pod/catalog-65498fccb-g52gl` and `-lbrwn` both show `1/1 Running` with `RESTARTS 0`, and `deployment.apps/catalog` shows `2/2  2  2` (ready/up-to-date/available).
- **Image pull or scheduling failure on catalog** — ruled out. Both catalog pods have assigned node `incident-lab-control-plane` and pod IPs (`10.244.0.120`, `10.244.0.119`), status `Running`; no `Pending`/`ImagePullBackOff`/`ErrImagePull` anywhere in the output.
- **Wrong Service port / port-name mismatch** — ruled out as *the* cause. The Service exposes `8080/TCP`, and the gateway targets the catalog backend. Even if the port were wrong, the observable state would still route to *some* endpoint; here the selector cannot match any pod at all, so the endpoint set is empty before port mapping is ever consulted. Port misconfiguration would also typically yield connection-refused, not the reported hangs.
- **CoreDNS / DNS resolution failure** — ruled out. `coredns-559f6c778d-9sqc8` and `-t9nfq` are both `1/1 Running  0 restarts`, `deployment.apps/coredns 2/2`, and `service/kube-dns` exists with selector `k8s-app=kube-dns` matching the coredns pods. Also, DNS for `catalog.shop.svc` would resolve fine — the Service object exists with ClusterIP `10.96.96.22`; the failure is behind the VIP, not in name resolution.
- **kube-proxy / CNI dataplane broken** — ruled out. `daemonset.apps/kube-proxy` is `1/1` desired/ready and `pod/kube-proxy-6ndq6` is `1/1 Running  0`; `daemonset.apps/kindnet` is `1/1` ready with `pod/kindnet-88ckx  1/1 Running  0`. A broken dataplane would also degrade DNS and other cluster traffic, which is not reported.
- **Control-plane degradation (apiserver/etcd/scheduler/controller-manager)** — ruled out. All four static pods are `1/1 Running` with `0` restarts and `10h` age; the new `shop` objects were created and reconciled `5s` ago, proving the control plane is actively scheduling and running workloads.
- **NetworkPolicy blocking gateway → catalog** — considered; no NetworkPolicy objects appear in the output, and `kubectl get all -A` would not list them, so this is not fully excluded by this output alone. It is nonetheless subordinate: even with all traffic permitted, a Service with a non-matching selector has no endpoints, so the selector drift is sufficient on its own to produce the symptom.
- **Storefront gateway misconfigured (pointing at a wrong hostname)** — considered and set aside. The page states the gateway "reports its calls to the catalog backend are not completing," i.e. it is reaching a resolvable destination that never answers, which is the empty-endpoint signature; a bad hostname would surface as NXDOMAIN/resolution errors instead.

## Verification recipe

```bash
# 1. The smoking gun: the Service has zero endpoints despite Ready pods.
kubectl -n shop get endpointslices -l kubernetes.io/service-name=catalog -o wide
kubectl -n shop get endpoints catalog -o wide     # expect <none> / no addresses

# 2. Show the selector vs. the actual pod labels side by side.
kubectl -n shop get svc catalog -o jsonpath='{.spec.selector}{"\n"}'   # {"app":"catalog-api"}
kubectl -n shop get pods --show-labels                                  # pods carry app=catalog

# 3. Prove the selector matches nothing, and that the correct label does.
kubectl -n shop get pods -l app=catalog-api    # expect "No resources found"
kubectl -n shop get pods -l app=catalog        # expect the 2 catalog pods
```

Remediation: patch the Service selector back to the label the workload actually publishes —
`kubectl -n shop patch svc catalog --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'` — then re-run step 1 and confirm two endpoint addresses (`10.244.0.119`, `10.244.0.120`) appear on port 8080. (Relabeling the pods to `app=catalog-api` would also work but requires changing the Deployment's immutable `spec.selector`, i.e. recreating the Deployment; patching the Service is the safe, non-disruptive fix.)

```json
{
  "case_id": "t2-selector-drift-empty-endpoints",
  "failing_resource": {"kind": "Service", "namespace": "shop", "name": "catalog"},
  "mechanism": "The catalog Service selects pods with label app=catalog-api, but the catalog pods are labeled app=catalog, so the selector matches zero pods and the Service's EndpointSlice is empty. With no backends behind ClusterIP 10.96.96.22:8080, kube-proxy has nothing to DNAT to and gateway connections to the catalog backend blackhole until they time out, producing storewide spinner hangs and empty product grids while the pods themselves stay Ready.",
  "verdict": "confirmed"
}
```