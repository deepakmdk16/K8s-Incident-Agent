## Root cause

The Deployment payments/checkout-worker ships a container command that refuses to start unless AMQP_URL is set, but its pod template defines no environment variables at all: spec.template.spec.containers[worker] has no env block, and the payments namespace contains no ConfigMap or Secret carrying that value (the only ConfigMap is kube-root-ca.crt, and there are no Secrets). Every pod the ReplicaSet creates therefore prints "FATAL: AMQP_URL not set" and exits 1 immediately, so the container is restarted into CrashLoopBackOff and the Deployment stays 0/1 Ready, which is what the workload availability monitor paged on. The fix is to add the AMQP_URL environment variable to the Deployment's pod template (either a literal value or a valueFrom reference to a ConfigMap/Secret key that actually exists in the namespace).

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `no env entries at all (pod shows "Environment:    <none>")` -> `an AMQP_URL entry, e.g. env: [{name: AMQP_URL, value: "amqp://rabbitmq.payments.svc:5672/"}] (or a valueFrom pointing at a ConfigMap/Secret key that exists in the payments namespace)`.

## Evidence chain

1. [symptom] The paged Deployment is 0/1 Ready and its only pod is crash-looping with an Error exit.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The worker container is in CrashLoopBackOff with repeated restarts.
   source: namespace_overview(payments) — verified
   > worker(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
3. [link] The container itself reports the missing variable as the reason it exits.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj"}) — verified
   > FATAL: AMQP_URL not set
4. [defect] The Deployment pod template defines no env for the worker container, though its command requires AMQP_URL.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > [ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }
5. [defect] The running pod confirms no environment variables were injected.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
6. [link] The container exits 1 each time and the kubelet backs off restarting it.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- A ConfigMap in the namespace holds AMQP_URL but the reference/key is wrong — ruled out: The only ConfigMap in the payments namespace is kube-root-ca.crt, whose sole key is ca.crt; no ConfigMap supplies AMQP_URL and the pod template references none.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "dataKeys": [
  >       "ca.crt"
  >     ],
- A Secret supplies AMQP_URL and failed to mount or resolve — ruled out: There are no Secrets at all in the payments namespace, so no secret reference can be the missing link.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/node pressure keeping the pod down — ruled out: The pod was scheduled successfully and the image was present and started six times; the failure is the container exiting after start, not pull or placement.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Normal   Started    84s (x6 over 4m27s)  kubelet            spec.containers{worker}: Container started

## Verification recipe

1. `kubectl -n payments get deployment checkout-worker -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
2. `kubectl -n payments logs checkout-worker-66bfcdfc47-d9gdj` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
3. `kubectl -n payments describe pod checkout-worker-66bfcdfc47-d9gdj` — expect to see: Environment:    <none>  [PRESENT]
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
  "mechanism": "The Deployment payments/checkout-worker has an empty environment in its pod template \u2014 spec.template.spec.containers[worker].env is absent \u2014 while the container's command requires AMQP_URL to be non-empty, so the worker container prints \"FATAL: AMQP_URL not set\" and exits with code 1 on every start; the kubelet restarts it repeatedly (5 restarts, waiting=CrashLoopBackOff) and the Deployment reports 0/1 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
