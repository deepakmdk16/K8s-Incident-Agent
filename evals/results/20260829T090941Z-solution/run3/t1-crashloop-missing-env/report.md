## Root cause

Deployment payments/checkout-worker ships a container whose command hard-requires the AMQP_URL environment variable, but its pod template defines no env at all. Every pod the ReplicaSet creates therefore starts, prints "FATAL: AMQP_URL not set", exits 1, and is restarted by the kubelet into CrashLoopBackOff, so the Deployment never reports a ready replica and checkout jobs are never consumed from the queue. Nothing in the namespace could have supplied the value either: the only ConfigMap present is kube-root-ca.crt and there are no Secrets, so the fix is to add the AMQP_URL env entry (backed by a real config or secret source) to the Deployment's container spec.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (pod shows "Environment:    <none>")` -> `env entry name: AMQP_URL with the queue connection string as its value (e.g. - name: AMQP_URL, value: amqp://<user>:<pass>@<rabbitmq-host>:5672/)`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replica and its only pod is crash-looping.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The worker container is in CrashLoopBackOff after repeated Error exits.
   source: namespace_overview(payments) — verified
   > worker(ready=False,restarts=5,waiting=CrashLoopBackOff,lastExit=Error)
3. [link] The container's previous instance died explicitly because AMQP_URL was unset.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj", "previous": true}) — verified
   > FATAL: AMQP_URL not set
4. [link] The container command in the Deployment pod template requires AMQP_URL and exits 1 when it is empty.
   source: get_object({"kind": "deployment", "name": "checkout-worker", "namespace": "payments"}) — verified
   > AMQP_URL not set
5. [defect] The Deployment pod template defines no environment variables for the worker container.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>
6. [defect] The container terminates with an error exit and the kubelet keeps restarting it.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Back-off restarting failed container worker in pod checkout-worker-66bfcdfc47-d9gdj_payments

## Investigation ledger

- The env var was meant to come from a ConfigMap key that is missing or misnamed (configMapKeyRef/envFrom pointing at an app config). — ruled out: The pod template contains no envFrom or valueFrom reference at all, and the namespace's only ConfigMap is the cluster CA bundle, so no config source was ever wired in.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "name": "kube-root-ca.crt",
- The value was supposed to come from a Secret that is absent or key-mismatched. — ruled out: There are no Secrets in the namespace at all, so no secretKeyRef could have resolved or failed.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/capacity pressure keeping the pod from running. — ruled out: The image was already present and the container was created and started repeatedly on a Ready node; the failure is at runtime, not at pull or schedule time.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n payments get deployment checkout-worker -o yaml` — expect to see: AMQP_URL not set  [PRESENT]
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
  "mechanism": "Deployment payments/checkout-worker's container \"worker\" runs a command that exits 1 unless AMQP_URL is set, yet .spec.template.spec.containers[worker].env is absent entirely (the pod reports \"Environment:    <none>\"), so the container terminates with Reason: Error, Exit Code: 1 after logging \"FATAL: AMQP_URL not set\". The kubelet keeps restarting it \u2014 \"Back-off restarting failed container worker\" \u2014 leaving the container in waiting=CrashLoopBackOff with restarts=5 and the Deployment at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
