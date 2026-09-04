## Root cause

Service payments/payments-gateway selects pods with app=payments-gateway, but the only pods serving the gateway are produced by Deployment payments/payments-gateway-api and carry the label app=payments-gateway-api. Nothing matches the selector, so Endpoints payments/payments-gateway has no addresses and every TCP connection to its clusterIP 10.96.215.202:8080 is refused. Deployment storefront/checkout-api reaches the gateway through the ExternalName Service storefront/payments-gateway (externalName=payments-gateway.payments.svc.cluster.local), so its health check to http://payments-gateway:8080/health fails with "wget: can't connect to remote host (10.96.215.202): Connection refused"; the container therefore never writes /tmp/ready, its exec readiness probe "test -f /tmp/ready" keeps failing, both replicas stay Ready=False, and checkout takes no traffic. The fix is to change the selector on the payments Service to app=payments-gateway-api so the two healthy gateway pods become endpoints.

Remediation: edit Service payments/payments-gateway, field `.spec.selector.app`: `payments-gateway` -> `payments-gateway-api`.

## Evidence chain

1. [symptom] Both checkout-api replicas are Running but not Ready, so the Deployment reports 0/2.
   source: namespace_overview(storefront) — verified
   > deployment/checkout-api ready=0/2 podLabels={app=checkout-api}
2. [symptom] The checkout container's readiness probe keeps failing.
   source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6fb2v", "namespace": "storefront"}) — verified
   > Warning  Unhealthy  1s (x11 over 51s)  kubelet            spec.containers{checkout-api}: Readiness probe failed:
3. [link] The checkout container marks itself unready because the payment gateway address refuses connections at 10.96.215.202.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6fb2v", "tail": 50}) — verified
   > wget: can't connect to remote host (10.96.215.202): Connection refused
4. [link] That address is reached via the storefront ExternalName alias to the payments namespace Service.
   source: namespace_overview(storefront) — verified
   > service/payments-gateway type=ExternalName externalName=payments-gateway.payments.svc.cluster.local
5. [link] 10.96.215.202 is the clusterIP of Service payments/payments-gateway, listening on port 8080.
   source: get_object({"kind": "services", "namespace": "payments"}) — verified
   > "clusterIP": "10.96.215.202",
6. [defect] Service payments/payments-gateway selects app=payments-gateway and has zero endpoint addresses, while the running gateway pods are labelled app=payments-gateway-api and are Ready.
   source: namespace_overview({"namespace": "payments"}) — verified
   > service/payments-gateway selector={app=payments-gateway} endpointAddresses=0
7. [defect] The Deployment that owns the healthy gateway pods stamps them with app=payments-gateway-api, which the Service selector does not match.
   source: get_object({"kind": "deployment", "name": "payments-gateway-api", "namespace": "payments"}) — verified
   > "labels": {
   >           "app": "payments-gateway-api"
   >         }
8. [defect] Endpoints payments/payments-gateway exists but carries no subsets at all.
   source: get_object({"kind": "endpoints", "name": "payments-gateway", "namespace": "payments"}) — verified
   > "name": "payments-gateway",
   >   "namespace": "payments",

## Investigation ledger

- The gateway backend itself is down or crashing — ruled out: Both gateway pods are Running and Ready with no restarts, so the backend is serving; only the Service selector fails to reach them.
  source: namespace_overview({"namespace": "payments"}) — verified
  > pod/payments-gateway-api-9c78bc7b-qcwwf phase=Running labels={app=payments-gateway-api, pod-template-hash=9c78bc7b} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- The storefront ExternalName alias points at the wrong DNS name — ruled out: The alias resolves to payments-gateway.payments.svc.cluster.local, which is exactly the Service that exists in the payments namespace with clusterIP 10.96.215.202 — the address the checkout log shows it connecting to.
  source: get_object({"kind": "services", "namespace": "payments"}) — verified
  > "name": "payments-gateway",
  >       "namespace": "payments",
- A NetworkPolicy in the payments namespace is blocking cross-namespace traffic — ruled out: No NetworkPolicy objects exist in the payments namespace, and a policy drop would time out rather than return 'Connection refused'.
  source: get_object({"kind": "networkpolicies", "namespace": "payments"}) — verified
  > 0 objects of kind networkpolicies in namespace payments
- The checkout-api pod spec is broken (bad ConfigMap volume or missing script key) — ruled out: ConfigMap storefront/checkout-scripts exists with the run.sh key the container mounts and executes, and the container started and logged normally.
  source: get_object({"kind": "configmaps", "namespace": "storefront"}) — verified
  > "dataKeys": [
  >       "run.sh"
  >     ],
- The unrelated not-ready pods elsewhere (report-exports, release-canary, batch-compute) are involved — ruled out: The checkout container's only failing dependency named in its own logs is the payment gateway endpoint; no reference from checkout-api to those namespaces exists.
  source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6fb2v", "tail": 50}) — verified
  > checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health

## Verification recipe

1. `kubectl get svc payments-gateway -n payments -o jsonpath='{.spec.selector}' && kubectl get endpoints payments-gateway -n payments` — expect to see: service/payments-gateway selector={app=payments-gateway} endpointAddresses=0  [PRESENT]
2. `kubectl get deploy payments-gateway-api -n payments -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "payments-gateway-api"  [PRESENT]
3. `kubectl logs -n storefront checkout-api-7db48f7c7b-6fb2v --tail=20` — expect to see: Connection refused  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t3-crossns-decoys",
  "failing_resource": {
    "kind": "Service",
    "namespace": "payments",
    "name": "payments-gateway"
  },
  "mechanism": "Service payments/payments-gateway has .spec.selector set to {app: payments-gateway}, while the pods that serve the gateway are labelled app=payments-gateway-api by Deployment payments/payments-gateway-api, so no pod matches; Endpoints payments/payments-gateway is created with no subsets (\"endpointAddresses=0\") and kube-proxy has nothing to forward to, so TCP connections to its clusterIP 10.96.215.202 on port 8080 are answered with \"Connection refused\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
