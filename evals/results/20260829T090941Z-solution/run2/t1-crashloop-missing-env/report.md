## Root cause

Deployment payments/checkout-worker starts its container with a guard that requires AMQP_URL, but the pod template's container "worker" declares no environment variables at all: the running pod reports `Environment:    <none>`. With AMQP_URL unset, the guard fires on every start, the container prints `FATAL: AMQP_URL not set` and exits with Exit Code 1, and the kubelet keeps restarting it (Restart Count: 5, Reason: CrashLoopBackOff, `Back-off restarting failed container worker`). Because the only pod of the Deployment never reaches Ready, the Deployment stays at ready=0/1 and no checkout jobs are consumed from the queue. There is no ConfigMap or Secret in namespace payments carrying this value either — the sole ConfigMap is kube-root-ca.crt and there are no Secrets — so the fix is to add the AMQP_URL environment variable to the Deployment's pod template.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (container spec has no env; pod shows "Environment:    <none>")` -> `env entry name: AMQP_URL with the queue connection string as its value (e.g. env: [{name: AMQP_URL, value: "amqp://<queue-host>:5672"}]), supplied literally or via a valueFrom reference to an object that exists in namespace payments`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replica and its pod is crash-looping.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The pod's worker container is in CrashLoopBackOff after repeated Error exits.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Reason:       CrashLoopBackOff
3. [link] The container exits because AMQP_URL is unset, as its own log says.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj", "previous": true}) — verified
   > FATAL: AMQP_URL not set
4. [link] The container command exits 1 when AMQP_URL is empty or unset.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > [ -n "${AMQP_URL:-}" ] || { echo "FATAL: AMQP_URL not set"; exit 1; }; echo "connected to queue at ${AMQP_URL}"; while :; do echo "consuming checkout jobs"; sleep 10; done
5. [defect] The pod template's worker container declares no environment variables at all, so AMQP_URL can never be set.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
6. [defect] The container terminated with Error/exit 1 and the kubelet keeps backing off restarting it.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Warning  BackOff    12s (x7 over 4m25s)  kubelet            spec.containers{worker}: Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- A ConfigMap key reference for AMQP_URL that no longer resolves (wrong key or missing ConfigMap), which would normally show as CreateContainerConfigError. — ruled out: The only ConfigMap in namespace payments is kube-root-ca.crt with key ca.crt, and the container references no ConfigMap for env at all, so no broken key reference exists.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "dataKeys": [
  >       "ca.crt"
  >     ],
- A Secret holding the queue URL was deleted, so the env reference broke. — ruled out: There are no Secrets in namespace payments and the container spec references none.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/capacity problem keeping the pod from running. — ruled out: The pod was scheduled and the image was already present and started six times; the failure is after start, inside the container.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Normal   Pulled     84s (x6 over 4m27s)  kubelet            spec.containers{worker}: Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n payments logs checkout-worker-66bfcdfc47-d9gdj --previous` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
2. `kubectl -n payments describe pod checkout-worker-66bfcdfc47-d9gdj` — expect to see: Environment:    <none>  [PRESENT]
3. `kubectl -n payments get deployment checkout-worker -o yaml` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t1-crashloop-missing-env",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "payments",
    "name": "checkout-worker"
  },
  "mechanism": "The container \"worker\" in Deployment payments/checkout-worker has no `.spec.template.spec.containers[worker].env` entry for AMQP_URL \u2014 the pod's container shows `Environment:    <none>` instead of an AMQP_URL value \u2014 so its startup guard on `${AMQP_URL:-}` fails, the container prints `FATAL: AMQP_URL not set` and terminates with `Reason: Error` / `Exit Code: 1` on every start, and the kubelet holds it in `CrashLoopBackOff` (`Back-off restarting failed container worker`) at Restart Count: 5, leaving the Deployment at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
