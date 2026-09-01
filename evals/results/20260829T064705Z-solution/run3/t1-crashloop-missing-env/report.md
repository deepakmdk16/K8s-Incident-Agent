## Root cause

The checkout-worker Deployment's pod template defines no environment variables at all for its only container, "worker". That container's start command begins with a hard precondition check: if AMQP_URL is empty it prints "FATAL: AMQP_URL not set" and exits 1. Because .spec.template.spec.containers[worker] carries neither an env entry nor an envFrom source for AMQP_URL, the container exits with code 1 on every start; the kubelet restarts it, it fails again, and the pod settles into CrashLoopBackOff. With the single replica never reaching Ready, the Deployment reports 0/1 Ready and no checkout jobs are consumed from the queue. The fix belongs in the Deployment's pod template, not in the pod, which is disposable. No ConfigMap or Secret in the payments namespace currently holds this value either, so the value (or an object to source it from) must be supplied along with the env entry.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (no env or envFrom entries; pod shows "Environment:    <none>")` -> `env with name AMQP_URL set to the checkout queue URL, e.g. - name: AMQP_URL / value: amqp://<user>:<pass>@<broker-host>:5672/ (or a valueFrom secretKeyRef/configMapKeyRef pointing at a queue-credentials object created in the payments namespace)`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas and its only pod is crash-looping.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The worker container is in CrashLoopBackOff after repeated Error exits.
   source: namespace_overview(payments) — verified
   > worker(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
3. [defect] The Deployment pod template's worker container requires AMQP_URL and exits 1 when it is unset.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > [ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }
4. [defect] The rendered pod receives no environment variables at all, so AMQP_URL is unset.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
5. [link] The container's previous instance died precisely on that precondition check.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj", "previous": true}) — verified
   > FATAL: AMQP_URL not set
6. [link] The container terminated with exit code 1 and the kubelet is backing off restarts.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- A ConfigMap key reference for AMQP_URL exists but names a missing/renamed key, so the env injection silently fails. — ruled out: The only ConfigMap in the payments namespace is the cluster CA bundle, and the Deployment template references no ConfigMap at all - there is no broken key reference, the env entry is simply absent.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "ca.crt"
- A Secret holding the queue credentials was deleted, breaking a secretKeyRef. — ruled out: There are no Secrets in the payments namespace and the pod template contains no secretKeyRef, so no secret reference is failing.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/capacity problem keeps the pod from running. — ruled out: The pod was scheduled and the image was already present and started six times; it fails after start, not before.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod
- A failing readiness/liveness probe or wrong probe port is keeping the pod unready. — ruled out: The container definition in the Deployment template declares no probes and no ports at all, so probes cannot be the cause of unreadiness.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Port:          <none>

## Verification recipe

1. `kubectl -n payments get deployment checkout-worker -o yaml | grep -A3 -e env -e command` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
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
  "mechanism": "Deployment payments/checkout-worker declares no AMQP_URL in .spec.template.spec.containers[worker].env (the field is absent entirely, and there is no envFrom), while that container's command requires it and exits 1 when it is empty. The container therefore terminates with exit code 1 immediately after start, logging \"FATAL: AMQP_URL not set\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
