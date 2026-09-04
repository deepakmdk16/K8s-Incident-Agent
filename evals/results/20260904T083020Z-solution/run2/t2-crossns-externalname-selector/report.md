## Root cause

Service payments/payments-gateway selects pods with label app=payments-gateway, but the only pods behind it — those produced by Deployment payments/payments-gateway-api — carry the label app=payments-gateway-api. Nothing matches the selector, so Endpoints payments/payments-gateway has no addresses and its ClusterIP 10.96.191.39 refuses connections. Service storefront/payments-gateway is an ExternalName alias to payments-gateway.payments.svc.cluster.local, so the checkout-api containers resolve that name to 10.96.191.39, get "Connection refused" on their health call to the gateway, never write /tmp/ready, and their exec readiness probe keeps failing, leaving Deployment storefront/checkout-api at 0/2 Ready and order submissions held.

Remediation: edit Service payments/payments-gateway, field `.spec.selector.app`: `payments-gateway` -> `payments-gateway-api`.

## Evidence chain

1. [symptom] The paged deployment has no ready replicas and both pods are not ready.
   source: namespace_overview(storefront) — verified
   > deployment/checkout-api ready=0/2 podLabels={app=checkout-api}
2. [symptom] The checkout-api container is Running but its exec readiness probe keeps failing.
   source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6jjnr", "namespace": "storefront"}) — verified
   > Warning  Unhealthy  0s (x6 over 110s)  kubelet            spec.containers{checkout-api}: Readiness probe failed:
3. [link] The storefront namespace reaches the payment gateway through an ExternalName alias into the payments namespace.
   source: namespace_overview(storefront) — verified
   > service/payments-gateway type=ExternalName externalName=payments-gateway.payments.svc.cluster.local
4. [link] checkout-api's health call through that alias is refused at 10.96.191.39, so it holds checkout submissions.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6jjnr", "tail": 40}) — verified
   > wget: can't connect to remote host (10.96.191.39): Connection refused
5. [link] 10.96.191.39 is the ClusterIP of Service payments/payments-gateway, the target of the ExternalName alias.
   source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "payments"}) — verified
   > "clusterIP": "10.96.191.39",
6. [defect] That Service's selector does not match the labels of the running gateway pods, so it has zero endpoint addresses.
   source: namespace_overview({"namespace": "payments"}) — verified
   > service/payments-gateway selector={app=payments-gateway} endpointAddresses=0
7. [defect] The gateway pods are labelled app=payments-gateway-api and are both ready.
   source: namespace_overview({"namespace": "payments"}) — verified
   > pod/payments-gateway-api-9c78bc7b-k65l8 phase=Running labels={app=payments-gateway-api, pod-template-hash=9c78bc7b} node=incident-lab-control-plane gateway(ready=True,restarts=0)
8. [defect] The owning Deployment's pod template stamps app=payments-gateway-api, so the Service selector is the value that disagrees.
   source: get_object({"kind": "deployments", "name": "payments-gateway-api", "namespace": "payments"}) — verified
   > "app": "payments-gateway-api"

## Investigation ledger

- The checkout-api workload itself is broken (bad image, missing config, crash loop). — ruled out: The container started normally from an image already on the node and has zero restarts; only the readiness probe fails.
  source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6jjnr", "namespace": "storefront"}) — verified
  > Normal   Pulled     116s               kubelet            spec.containers{checkout-api}: Container image "busybox:1.36" already present on machine and can be accessed by the pod
- The storefront ExternalName Service resolves to the wrong or a nonexistent name (DNS failure). — ruled out: The pod resolved the alias to a ClusterIP and got a TCP-level refusal, not a name-resolution error.
  source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6jjnr", "tail": 40}) — verified
  > checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health
- The gateway pods are themselves unhealthy or failing their own readiness probe. — ruled out: Both gateway pods are Running and ready with no restarts.
  source: namespace_overview({"namespace": "payments"}) — verified
  > deployment/payments-gateway-api ready=2/2 podLabels={app=payments-gateway-api}

## Verification recipe

1. `kubectl -n payments get endpoints payments-gateway -o yaml` — expect to see: payments-gateway  [PRESENT]
2. `kubectl -n payments get svc payments-gateway -o jsonpath='{.spec.selector}'` — expect to see: "app": "payments-gateway"  [PRESENT]
3. `kubectl -n storefront logs deploy/checkout-api --tail=20` — expect to see: Connection refused  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-crossns-externalname-selector",
  "failing_resource": {
    "kind": "Service",
    "namespace": "payments",
    "name": "payments-gateway"
  },
  "mechanism": "Service payments/payments-gateway has .spec.selector app=payments-gateway, while the pods of Deployment payments/payments-gateway-api are labelled app=payments-gateway-api, so the selector matches nothing and Endpoints payments/payments-gateway is created with no subsets; its ClusterIP 10.96.191.39 therefore answers every connection with \"can't connect to remote host (10.96.191.39): Connection refused\". The selector should read app=payments-gateway-api so the two ready gateway pods are enrolled as addresses.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
