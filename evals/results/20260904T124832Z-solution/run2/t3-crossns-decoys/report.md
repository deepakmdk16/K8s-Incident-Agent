## Root cause

Service payments/payments-gateway selects pods with label app=payments-gateway, but the only pods backing that gateway are produced by Deployment payments/payments-gateway-api, whose pod template labels them app=payments-gateway-api. Nothing matches the selector, so Endpoints payments/payments-gateway has no addresses and kube-proxy rejects connections to its ClusterIP 10.96.215.202. Deployment storefront/checkout-api reaches the gateway through the ExternalName Service storefront/payments-gateway, which aliases payments-gateway.payments.svc.cluster.local; its health poll of http://payments-gateway:8080/health gets "Connection refused", the container never writes /tmp/ready, and the exec readiness probe "test -f /tmp/ready" keeps failing, so both checkout-api pods stay Ready=False, the Deployment reports 0/2 and orders cannot be submitted.

Remediation: edit Service payments/payments-gateway, field `.spec.selector.app`: `payments-gateway` -> `payments-gateway-api`.

## Evidence chain

1. [symptom] Deployment storefront/checkout-api has both pods Running but not Ready.
   source: namespace_overview(storefront) — verified
   > deployment/checkout-api ready=0/2 podLabels={app=checkout-api}
2. [symptom] The checkout-api container's exec readiness probe keeps failing.
   source: describe({"kind": "pod", "name": "checkout-api-7db48f7c7b-6fb2v", "namespace": "storefront"}) — verified
   > Readiness:      exec [sh -c test -f /tmp/ready] delay=5s timeout=1s period=5s successThreshold=1 failureThreshold=2
3. [link] checkout-api holds readiness because the payment gateway ClusterIP refuses connections.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6fb2v", "tail": 50}) — verified
   > wget: can't connect to remote host (10.96.215.202): Connection refused
4. [link] The pod logs tie the refused connection to the payment gateway health endpoint and to holding checkout submissions.
   source: get_logs({"namespace": "storefront", "pod": "checkout-api-7db48f7c7b-6fb2v", "tail": 50}) — verified
   > payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions
5. [link] The storefront-side name is an ExternalName alias to the payments Service, so it adds no backends of its own.
   source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "storefront"}) — verified
   > "externalName": "payments-gateway.payments.svc.cluster.local"
6. [link] The refused IP is the ClusterIP of Service payments/payments-gateway.
   source: get_object({"kind": "services", "namespace": "payments"}) — verified
   > "clusterIP": "10.96.215.202"
7. [defect] Service payments/payments-gateway selects app=payments-gateway and has zero endpoint addresses, while the gateway pods are labelled app=payments-gateway-api and are Ready.
   source: namespace_overview({"namespace": "payments"}) — verified
   > service/payments-gateway selector={app=payments-gateway} endpointAddresses=0
8. [defect] The gateway Deployment's pod template applies the label app=payments-gateway-api, which the Service selector does not match.
   source: get_object({"kind": "deployments", "name": "payments-gateway-api", "namespace": "payments"}) — verified
   > "labels": {
   >           "app": "payments-gateway-api"
   >         }
9. [defect] Endpoints payments/payments-gateway contains no subsets at all.
   source: get_object({"kind": "endpoints", "name": "payments-gateway", "namespace": "payments"}) — verified
   > "name": "payments-gateway",
   >   "namespace": "payments",

## Investigation ledger

- The gateway backend itself is down or crashing — ruled out: Both gateway pods are Running and Ready with no restarts, so the backend is healthy and only the Service routing is broken.
  source: namespace_overview({"namespace": "payments"}) — verified
  > pod/payments-gateway-api-9c78bc7b-qcwwf phase=Running labels={app=payments-gateway-api, pod-template-hash=9c78bc7b} node=incident-lab-control-plane gateway(ready=True,restarts=0)
- A NetworkPolicy in payments blocks traffic from storefront — ruled out: No NetworkPolicy objects exist in the payments namespace.
  source: get_object({"kind": "networkpolicies", "namespace": "payments"}) — verified
  > 0 objects of kind networkpolicies in namespace payments
- The storefront ExternalName Service points at the wrong hostname — ruled out: The ExternalName resolves to exactly the payments Service FQDN, so the alias is correct.
  source: get_object({"kind": "services", "name": "payments-gateway", "namespace": "storefront"}) — verified
  > "externalName": "payments-gateway.payments.svc.cluster.local"
- A bad checkout-api release or a broken script/ConfigMap reference — ruled out: The Deployment is still on its first revision and mounts ConfigMap checkout-scripts, which exists with the run.sh key it asks for.
  source: get_object({"kind": "configmaps", "namespace": "storefront"}) — verified
  > "dataKeys": [
  >       "run.sh"
  >     ],

## Verification recipe

1. `kubectl -n payments get endpoints payments-gateway -o wide` — expect to see: service/payments-gateway selector={app=payments-gateway} endpointAddresses=0  [PRESENT]
2. `kubectl -n payments get deploy payments-gateway-api -o jsonpath='{.spec.template.metadata.labels}'` — expect to see: "app": "payments-gateway-api"  [PRESENT]
3. `kubectl -n storefront logs deploy/checkout-api --tail=20` — expect to see: Connection refused  [PRESENT]
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
  "mechanism": "Service payments/payments-gateway has .spec.selector app=payments-gateway, while the pods of Deployment payments/payments-gateway-api carry the label app=payments-gateway-api, so the selector matches no pod and Endpoints payments/payments-gateway is written with no subsets (endpointAddresses=0). With no backend addresses, kube-proxy rejects TCP to the Service ClusterIP 10.96.215.202:8080, which clients observe as \"Connection refused\" instead of a gateway response.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
