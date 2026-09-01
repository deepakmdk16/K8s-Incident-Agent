## Root cause

The checkout-worker Deployment's pod template defines a container whose startup command aborts unless the environment variable AMQP_URL is set, but the template declares no env entries at all. The container therefore exits with code 1 on every start ("FATAL: AMQP_URL not set"), the kubelet backs it off into CrashLoopBackOff, and the Deployment stays at 0/1 Ready so no checkout jobs are consumed from the queue. Nothing in the payments namespace supplies the value either — the only ConfigMap present is kube-root-ca.crt and there are no Secrets — so the fix is to add the AMQP_URL environment variable (a literal value, or a valueFrom reference to a config object created for it) to the Deployment's container spec.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (no env entries; pod shows "Environment:    <none>")` -> `env entry named AMQP_URL carrying the broker URL, e.g. - name: AMQP_URL
  value: amqp://<user>:<pass>@<broker-host>:5672/`.

## Evidence chain

1. [symptom] The paged Deployment is 0/1 Ready and its only pod is crash-looping.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The worker container is in CrashLoopBackOff after exiting with Error.
   source: namespace_overview(payments) — verified
   > worker(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
3. [link] The container itself reports the missing variable as the reason it exits.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj"}) — verified
   > FATAL: AMQP_URL not set
4. [defect] The Deployment pod template's container command requires AMQP_URL and exits 1 when it is empty.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > FATAL: AMQP_URL not set\"; exit 1; }
5. [defect] The running pod produced by that template has an empty environment, confirming no env was injected from any source.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
6. [link] The container terminates with exit code 1 each time and the kubelet backs it off.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- A ConfigMap key referenced by the workload was renamed or deleted, breaking an envFrom/configMapKeyRef — ruled out: The namespace's only ConfigMap is the CA bundle, and the pod template contains no configMapKeyRef or envFrom at all, so no broken reference exists — the env entry is simply absent.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "name": "kube-root-ca.crt",
- A Secret holding the broker credentials failed to mount or resolve — ruled out: There are no Secrets in the payments namespace, so no secret reference is involved in the failure.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/capacity problem keeping the pod from running — ruled out: The pod was scheduled and the image was already present and started successfully six times; it is the process that exits, not the pull or the scheduler.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n payments logs deploy/checkout-worker` — expect to see: FATAL: AMQP_URL not set  [PRESENT]
2. `kubectl -n payments get deploy checkout-worker -o yaml` — expect to see: AMQP_URL not set  [PRESENT]
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
  "mechanism": "Deployment payments/checkout-worker declares no .spec.template.spec.containers[worker].env, so AMQP_URL is unset in the container; the command's guard on ${AMQP_URL:-} prints \"FATAL: AMQP_URL not set\" and exits 1 at every start, so the worker container terminates with Exit Code 1 and is held in CrashLoopBackOff instead of having an env entry named AMQP_URL carrying the broker URL.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
