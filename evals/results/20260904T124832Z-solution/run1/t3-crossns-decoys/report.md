## Root cause

Service payments/payments-gateway selects pods with label app=payments-gateway, but the only pods backing it, produced by Deployment payments/payments-gateway-api, carry the label app=payments-gateway-api. Nothing matches the selector, so Endpoints payments/payments-gateway has no addresses and its ClusterIP 10.96.215.202 refuses connections. Deployment storefront/checkout-api reaches the gateway through Service storefront/payments-gateway, an ExternalName alias for payments-gateway.payments.svc.cluster.local, so every health call to http://payments-gateway:8080/health returns "Connection refused"; the container never writes /tmp/ready, its exec readiness probe "test -f /tmp/ready" keeps failing, and both checkout-api pods stay Ready=False, leaving the deployment at 0/2 and order submissions failing.

Remediation: edit Service payments/payments-gateway, field `.spec.selector.app`: `payments-gateway` -> `payments-gateway-api`.

## Evidence chain

1. [symptom] Both checkout-api pods are Running but not Ready, so the deployment is 0/2.
   source: namespace_overview(storefront) — verified
   > deployment/checkout-api ready=0/2 podLabels={app=checkout-api}
2. [symptom] The readiness probe on the checkout-api pod keeps failing.
   source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6fb2v", "namespace": "storefront"}) — verified
   > Readiness probe failed:
3. [link] checkout-api holds checkout submissions because its payment gateway health check is refused.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6fb2v", "tail": 50}) — verified
   > wget: can't connect to remote host (10.96.215.202): Connection refused
4. [link] The name payments-gateway that checkout-api calls is a Service in storefront that is an ExternalName alias for the payments namespace service, which is how storefront depends on payments.
   source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "storefront"}) — verified
   > "externalName": "payments-gateway.payments.svc.cluster.local"
5. [link] The refused IP 10.96.215.202 is the ClusterIP of the aliased Service payments/payments-gateway.
   source: get_object({"kind": "services", "namespace": "payments"}) — verified
   > "clusterIP": "10.96.215.202",
6. [defect] Service payments/payments-gateway selects a label no pod carries and has zero endpoint addresses.
   source: namespace_overview({"namespace": "payments"}) — verified
   > service/payments-gateway selector={app=payments-gateway} endpointAddresses=0
7. [defect] The gateway pods are labelled app=payments-gateway-api, not app=payments-gateway.
   source: namespace_overview({"namespace": "payments"}) — verified
   > pod/payments-gateway-api-9c78bc7b-qcwwf phase=Running labels={app=payments-gateway-api, pod-template-hash=9c78bc7b}
8. [defect] The Deployment pod template labels those pods app=payments-gateway-api.
   source: get_object({"kind": "deployments", "name": "payments-gateway-api", "namespace": "payments"}) — verified
   > "app": "payments-gateway-api"

## Investigation ledger

- The gateway pods themselves are down or unhealthy — ruled out: Both gateway pods are Running and Ready, so the backend workload is healthy.
  source: namespace_overview({"namespace": "payments"}) — verified
  > deployment/payments-gateway-api ready=2/2 podLabels={app=payments-gateway-api}
- The storefront ExternalName service points at the wrong DNS name — ruled out: The ExternalName correctly names payments-gateway.payments.svc.cluster.local, which resolves to the cluster IP seen in the checkout-api logs.
  source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "storefront"}) — verified
  > "externalName": "payments-gateway.payments.svc.cluster.local"
- A NetworkPolicy in payments blocks traffic from storefront — ruled out: No NetworkPolicy objects exist in the payments namespace.
  source: get_object({"kind": "networkpolicies", "namespace": "payments"}) — verified
  > 0 objects of kind networkpolicies in namespace payments
- The checkout-api pod spec is broken (missing ConfigMap or bad mount) — ruled out: ConfigMap storefront/checkout-scripts exists with the run.sh key the pod mounts, and the container started normally.
  source: get_object({"kind": "configmaps", "namespace": "storefront"}) — verified
  > "run.sh"

## Verification recipe

1. `kubectl -n payments get svc payments-gateway -o jsonpath='{.spec.selector}' ; kubectl -n payments get pods --show-labels` — expect to see: selector={app=payments-gateway} endpointAddresses=0  [PRESENT]
2. `kubectl -n payments get endpoints payments-gateway -o yaml` — expect to see: payments-gateway  [PRESENT]
3. `kubectl -n storefront logs checkout-api-7db48f7c7b-6fb2v --tail=20` — expect to see: Connection refused  [PRESENT]
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
  "mechanism": "Service payments/payments-gateway has .spec.selector.app = \"payments-gateway\" while the pods of Deployment payments/payments-gateway-api are labelled app=payments-gateway-api, so no pod matches and Endpoints payments/payments-gateway holds no addresses; its ClusterIP 10.96.215.202 therefore has no backend and TCP connects to port 8080 are answered \"Connection refused\" instead of reaching the gateway. The selector should read app=payments-gateway-api to match the pod template labels.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
