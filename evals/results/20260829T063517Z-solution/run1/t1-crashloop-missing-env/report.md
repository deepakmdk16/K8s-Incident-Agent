## Root cause

The checkout-worker Deployment's pod template declares no environment variables, but its container command requires AMQP_URL and exits immediately when that variable is empty. Every container start prints "FATAL: AMQP_URL not set" and terminates with exit code 1, so the kubelet backs the container off into CrashLoopBackOff, the pod never becomes Ready, the Deployment stays at 0/1 Ready, and nothing consumes checkout jobs from the queue. Nothing supplies the value either: the only ConfigMap present is the auto-injected kube-root-ca.crt, and the template has no configMapKeyRef, secretKeyRef or envFrom. The fix is to add an AMQP_URL env entry to the worker container in the Deployment pod template.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (no env entries; pod shows "Environment:    <none>")` -> `an env entry named AMQP_URL holding the queue connection string, e.g. value: amqp://user:pass@rabbitmq:5672/ (or valueFrom a ConfigMap/Secret key that actually exists in the namespace)`.

## Evidence chain

1. [symptom] The paged Deployment is 0/1 Ready and its only pod is in CrashLoopBackOff with restarts and lastExit=Error.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [defect] The Deployment pod template's container command exits 1 unless AMQP_URL is set, and the template defines no env at all.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > [ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }
3. [link] The previous container instance died printing exactly that fatal message, proving AMQP_URL was empty at runtime.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj", "previous": true}) — verified
   > FATAL: AMQP_URL not set
4. [link] The running pod has no environment variables injected and its last state terminated with exit code 1.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
5. [link] The kubelet is backing off restarting the failed worker container, which is what keeps the Deployment unavailable.
   source: get_events({"namespace": "payments", "warnings_only": false}) — verified
   > Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- A ConfigMap or Secret key reference that no longer resolves is the source of the empty variable. — ruled out: The namespace holds only the auto-injected kube-root-ca.crt ConfigMap with key ca.crt, and the pod template contains no configMapKeyRef/secretKeyRef/envFrom, so no reference was ever wired up to break.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "ca.crt"
- Image pull failure or a scheduling/capacity problem prevents the worker from running. — ruled out: The pod was scheduled successfully and the image was already present and started repeatedly; the container starts fine and then exits on its own.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n payments get deployment checkout-worker -o jsonpath='{.spec.template.spec.containers[0].env}{.spec.template.spec.containers[0].command}'` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
2. `kubectl -n payments logs checkout-worker-66bfcdfc47-d9gdj --previous` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
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
  "mechanism": "Deployment checkout-worker has no .spec.template.spec.containers[worker].env, so AMQP_URL is unset in the worker container; its command guard `[ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }` fires and the process exits with code 1 on every start, driving CrashLoopBackOff and holding the Deployment at 0/1 Ready. The template needs an env entry named AMQP_URL carrying the queue connection string instead of no env at all.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
