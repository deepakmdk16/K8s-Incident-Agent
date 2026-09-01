## Root cause

The Deployment payments/checkout-worker defines its worker container with a startup command that exits immediately unless the AMQP_URL environment variable is set, but the pod template supplies no environment at all: .spec.template.spec.containers[worker] has no env and no envFrom. Every pod the ReplicaSet creates therefore prints "FATAL: AMQP_URL not set" and terminates with exit code 1, so the kubelet restarts it and it lands in CrashLoopBackOff, leaving the Deployment at 0/1 Ready and no consumer draining the checkout queue. Nothing in the namespace supplies the value either: the only ConfigMap present is kube-root-ca.crt and there are no Secrets, so the fix is to add the AMQP_URL environment variable (with a real value, or a valueFrom reference to an object created alongside it) to the Deployment's pod template.

Remediation: edit Deployment payments/checkout-worker, field `spec.template.spec.containers[worker].env`: `absent (no env entries; pod shows "Environment: <none>")` -> `env entry named AMQP_URL carrying the queue connection URL, e.g. - name: AMQP_URL / value: amqp://<user>:<pass>@<rabbitmq-host>:5672/ (or a valueFrom secretKeyRef/configMapKeyRef pointing at an object that actually exists in the payments namespace)`.

## Evidence chain

1. [symptom] The paged Deployment is at 0/1 Ready and its only pod is crash-looping with exit Error.
   source: namespace_overview(payments) — verified
   > deployment/checkout-worker ready=0/1 podLabels={app=checkout-worker}
2. [symptom] The container repeatedly fails and is restarted.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Reason:       CrashLoopBackOff
3. [link] The container itself states why it exits: the AMQP_URL variable is not set.
   source: get_logs({"namespace": "payments", "pod": "checkout-worker-66bfcdfc47-d9gdj"}) — verified
   > FATAL: AMQP_URL not set
4. [link] The container command requires AMQP_URL and exits 1 when it is empty.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > [ -n \"${AMQP_URL:-}\" ] || { echo \"FATAL: AMQP_URL not set\"; exit 1; }; echo \"connected to queue at ${AMQP_URL}\"; while :; do echo \"consuming checkout jobs\"; sleep 10; done
5. [defect] The Deployment pod template's worker container defines no env or envFrom, so AMQP_URL is never injected.
   source: get_object({"kind": "deployments", "name": "checkout-worker", "namespace": "payments"}) — verified
   > "image": "busybox:1.36",
   >             "imagePullPolicy": "IfNotPresent",
   >             "name": "worker",
   >             "resources": {},
6. [defect] The running pod confirms an empty environment.
   source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
   > Environment:    <none>

## Investigation ledger

- A ConfigMap key reference that no longer resolves (missing key or renamed ConfigMap) is what strips the variable. — ruled out: The pod template contains no configMapKeyRef or envFrom at all, and the only ConfigMap in the payments namespace is kube-root-ca.crt, which holds just ca.crt.
  source: get_object({"kind": "configmaps", "namespace": "payments"}) — verified
  > "dataKeys": [
  >       "ca.crt"
  >     ],
- A Secret holding the queue credentials was deleted, so the injection failed. — ruled out: There are no Secrets in the payments namespace to reference, and the template references none.
  source: get_object({"kind": "secrets", "namespace": "payments"}) — verified
  > 0 objects of kind secrets in namespace payments
- Image pull failure or scheduling/capacity problem keeps the pod from running. — ruled out: The pod was scheduled and the image was already present; the container started repeatedly and exited on its own.
  source: describe({"kind": "pod", "name": "checkout-worker-66bfcdfc47-d9gdj", "namespace": "payments"}) — verified
  > Normal   Started    84s (x6 over 4m27s)  kubelet            spec.containers{worker}: Container started

## Verification recipe

1. `kubectl -n payments get deployment checkout-worker -o jsonpath='{.spec.template.spec.containers[0].env}'` — expect to see: "name": "worker"  [PRESENT]
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
  "mechanism": "The Deployment payments/checkout-worker declares a worker container whose command aborts unless AMQP_URL is set, yet .spec.template.spec.containers[worker].env is absent entirely (no env, no envFrom), so the variable resolves to empty instead of the queue URL. Each pod from this template logs \"FATAL: AMQP_URL not set\" and exits with code 1 immediately after start, and the kubelet keeps restarting it into CrashLoopBackOff, holding the Deployment at ready 0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
