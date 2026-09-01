## Root cause

Deployment payments/checkout-worker ships a container whose startup command hard-requires the AMQP_URL environment variable, but the pod template defines no env at all. Every container start therefore hits the guard clause, prints "FATAL: AMQP_URL not set" and exits 1, so the kubelet keeps restarting it into CrashLoopBackOff (restart count 5) and the Deployment stays at 0/1 Ready, which is what the availability monitor paged on and why no checkout jobs are consumed. Nothing in namespace payments supplies the value either: the only ConfigMap present is kube-root-ca.crt and there are no Secrets, so the fix is to add the AMQP_URL env var (with the queue URL) to the Deployment's container spec.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[name=worker].env`: `absent (pod shows "Environment:    <none>")` -> `env entry name: AMQP_URL with the checkout queue URL, e.g. - name: AMQP_URL\n  value: amqp://rabbitmq.payments.svc.cluster.local:5672/`.

## Evidence chain

1. [symptom] The paged Deployment is 0/1 Ready and its only pod is crash-looping.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The worker container is in CrashLoopBackOff with repeated Error exits.
   source: namespace_overview(payments) — verified
   > worker(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
3. [defect] The Deployment pod template's container command requires AMQP_URL and exits 1 when it is unset, and the template declares no env block.
   source: get_object({"kind": "deployment", "name": "checkout-worker", "namespace": "payments"}) — verified
   > [ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }
4. [link] The previous container instance died on exactly that guard clause.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj", "previous": true}) — verified
   > FATAL: AMQP_URL not set
5. [link] The running pod has no environment variables at all, so AMQP_URL never reaches the container.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
6. [link] The container terminates with exit code 1 and the kubelet backs it off.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Reason:       CrashLoopBackOff
7. [defect] No ConfigMap in namespace payments carries a queue URL; only the injected CA bundle ConfigMap exists.
   source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
   > "name": "kube-root-ca.crt"
8. [defect] No Secret in namespace payments could supply AMQP_URL either.
   source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
   > 0 objects of kind secrets in namespace payments

## Investigation ledger

- Image pull failure or a bad image reference caused the crash loop — ruled out: The image is present on the node and the container is created and started successfully each time; it dies afterwards in its own command.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod
- Scheduling / node capacity or taints kept the pod from running — ruled out: The pod was scheduled and is Running on the node; the failure is inside the container.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Successfully assigned payments/checkout-worker-66bfcdfc47-d9gdj to incident-lab-control-plane
- A missing ConfigMap or Secret reference in the pod template failed to mount or inject — ruled out: The template references no ConfigMap or Secret for env at all — there is no envFrom or valueFrom to fail; the only volume is the default service-account projection.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Mounts:
  >       /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-shsf7 (ro)

## Verification recipe

1. `kubectl -n payments get deploy checkout-worker -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: AMQP_URL not set  [PRESENT]
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
  "mechanism": "Deployment payments/checkout-worker's container \"worker\" runs the command `[ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }`, but .spec.template.spec.containers[name=worker].env is absent \u2014 the running pod reports `Environment:    <none>` instead of an AMQP_URL entry. The container therefore exits with `Exit Code:    1` after logging `FATAL: AMQP_URL not set`, and the kubelet backs off restarting it (`Reason:       CrashLoopBackOff`, `Restart Count:  5`), leaving the Deployment at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
