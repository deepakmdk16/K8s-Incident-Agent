## Root cause

**Verdict: confirmed.**

The Service `payments/payments-gateway` has the label selector `app=payments-gateway`, but the only pods backing it are produced by Deployment `payments/payments-gateway-api`, whose pods carry the label `app=payments-gateway-api`. The selector therefore matches **zero** pods, the Service's ClusterIP `10.96.215.202` has an empty endpoint set, and kube-proxy actively rejects connections to it ("Connection refused"). `checkout-api` pods in `storefront` health-check the payment gateway through the ExternalName alias `storefront/payments-gateway` → `payments-gateway.payments.svc.cluster.local` → `10.96.215.202`, get refused on every poll, never create their `/tmp/ready` marker, so their readiness probe fails forever, the Deployment reports 0/2 Ready, and the Service/ingress path in front of checkout has no endpoints → order submissions error out and completed-order volume sits at zero. No storefront release was needed for this: the break is a label/selector mismatch on the dependency's Service.

## Evidence chain

- **Symptom, pod side** — `describe pod/checkout-api-7db48f7c7b-6fb2v -n storefront`: `Ready: False`, `Readiness: exec [sh -c test -f /tmp/ready] ... failureThreshold=2` and event `Warning Unhealthy ... Readiness probe failed:` (x11 over 51s). Same for `checkout-api-7db48f7c7b-g7299`. Both pods are `Running`, not crashing — so this is a readiness gate, not a container failure.
- **Why the probe file is never created** — log line from both pods:
  `checkout-api starting; payment gateway endpoint http://payments-gateway:8080/health`
  then repeatedly:
  `wget: can't connect to remote host (10.96.215.202): Connection refused`
  `payment gateway UNREACHABLE at http://payments-gateway:8080/health - holding checkout submissions`
  The app deliberately withholds readiness ("holding checkout submissions") while the gateway is unreachable.
- **DNS is fine; the name resolved** — the log shows the resolved IP `10.96.215.202`, so the ExternalName hop worked. `kubectl get all -A` services: `storefront service/payments-gateway ExternalName ... payments-gateway.payments.svc.cluster.local`, and `payments service/payments-gateway ClusterIP 10.96.215.202 ... 8080/TCP SELECTOR app=payments-gateway`. The IP in the log is exactly that ClusterIP.
- **The selector mismatch** — same output, the workload behind it is `payments deployment.apps/payments-gateway-api 2/2 ... SELECTOR app=payments-gateway-api`, and `payments replicaset.apps/payments-gateway-api-9c78bc7b ... SELECTOR app=payments-gateway-api,pod-template-hash=9c78bc7b`. Pod labels are therefore `app=payments-gateway-api`, which does **not** match the Service selector `app=payments-gateway`. Empty endpoints on a ClusterIP is precisely what produces an immediate TCP `Connection refused` rather than a timeout.
- **The backend itself is healthy** — `pod/payments-gateway-api-9c78bc7b-qcwwf` and `-twfm4` are both `1/1 Running`, `0` restarts. So the gateway process is up; only the Service routing to it is broken.
- **Not a code change** — `describe deployment.apps/checkout-api -n storefront` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, one ScalingReplicaSet event. Consistent with "the storefront team shipped no release today."

## Investigation ledger

- **checkout-api image/command bug or bad ConfigMap** — ruled out: container `Started` cleanly, `Restart Count: 0`, and it logs a coherent startup line then a specific dependency error. The `checkout-scripts` ConfigMap volume mounted fine (`Optional: false`, no `FailedMount` event; pod would be stuck in `ContainerCreating` otherwise).
- **DNS / CoreDNS failure or a broken ExternalName** — ruled out: `wget` reports the numeric IP `10.96.215.202`, meaning the full `payments-gateway` → ExternalName → `payments-gateway.payments.svc.cluster.local` → ClusterIP resolution chain succeeded. Both `coredns` pods are `1/1 Running` with 0 restarts. A DNS failure would read "bad address", not "Connection refused".
- **Payments gateway pods down / crashlooping** — ruled out: both `payments-gateway-api` pods are `1/1 Running`, `RESTARTS 0`, and the Deployment reads `2/2 ... AVAILABLE 2`.
- **Node resource exhaustion starving checkout-api** — ruled out: `batch-compute/model-trainer` is Pending with `0/1 nodes are available: 1 Insufficient cpu` because it requests an absurd `cpu: 512` cores (`describe pod/model-trainer-...`). That pod never scheduled, so it consumes nothing; both checkout-api pods scheduled and started normally (`Successfully assigned storefront/checkout-api-... to incident-lab-control-plane`). Unrelated decoy — its own bug, but not this page.
- **Failing CronJob `report-exports/nightly-export`** — ruled out: it fails on its own EmptyDir precondition, `log line: nightly-export: /export/destination.conf is missing`, exit code 1. Different namespace, no network or dependency relationship to checkout, and no shared resource.
- **`release-canary/canary-runner` restarts (2)** — ruled out: it is `1/1 Running` and unrelated to the checkout call path; nothing in the checkout logs references it. Noise.
- **NetworkPolicy blocking storefront→payments** — ruled out as the primary mechanism: a policy drop yields a timeout, not an immediate `Connection refused`, and no NetworkPolicy objects appear anywhere in `kubectl get all -A`. (Note `get all` does not list NetworkPolicies; the refused-vs-timeout signature plus the visible selector mismatch is what settles it.)
- **Fix belongs on the Deployment's pod labels instead of the Service** — considered. Either edit closes the gap, but the Service `payments/payments-gateway` is the resource whose contract is wrong (its selector names a label no workload in the cluster carries, while the Deployment is internally consistent with its own ReplicaSet and pods). Relabelling live pods would also force a rollout of a healthy payments fleet; correcting the selector is the non-disruptive change.

## Verification recipe

```bash
# 1. The smoking gun: the Service has no endpoints.
kubectl get endpoints payments-gateway -n payments -o wide
kubectl get endpointslices -n payments -l kubernetes.io/service-name=payments-gateway

# 2. Prove the selector/label mismatch side by side.
kubectl get svc payments-gateway -n payments -o jsonpath='{.spec.selector}{"\n"}'
kubectl get pods -n payments --show-labels

# 3. Reproduce the refusal from inside the checkout namespace.
kubectl run -n storefront netcheck --rm -it --restart=Never --image=busybox:1.36 -- \
  wget -T 3 -qO- http://payments-gateway:8080/health
```

Expect: step 1 prints `<none>` / no endpoints; step 2 prints selector `{"app":"payments-gateway"}` against pods labelled `app=payments-gateway-api`; step 3 reproduces `Connection refused` to `10.96.215.202`.

**Remediation:** patch the Service selector to match the running pods, then confirm checkout recovers on its own (the pods poll every ~6s and will go Ready without a restart):

```bash
kubectl patch svc payments-gateway -n payments \
  -p '{"spec":{"selector":{"app":"payments-gateway-api"}}}'
kubectl get endpoints payments-gateway -n payments        # expect 2 addresses
kubectl rollout status deploy/checkout-api -n storefront  # expect 2/2 Ready
```

If the pods do not self-heal within a minute or two, `kubectl rollout restart deploy/checkout-api -n storefront`. Follow-ups (not incident-blocking): add an alert on `kube_endpoint_address_available == 0` for critical Services so a selector typo pages the owning team directly instead of surfacing as a downstream SEV1; and fix the two unrelated decoys — `batch-compute/model-trainer`'s `cpu: 512` request and `report-exports/nightly-export`'s missing `destination.conf`.

```json
{
  "case_id": "t3-crossns-decoys",
  "failing_resource": {"kind": "Service", "namespace": "payments", "name": "payments-gateway"},
  "mechanism": "The Service's label selector 'app=payments-gateway' matches no pods, because the backing workload's pods are labelled 'app=payments-gateway-api', leaving the ClusterIP 10.96.215.202 with an empty endpoint set so kube-proxy refuses every connection to it. checkout-api resolves this Service through the storefront ExternalName alias for its dependency health check, receives 'Connection refused' on each poll, and therefore never writes the /tmp/ready file its readiness probe tests. Both replicas stay NotReady, the checkout Deployment reports 0/2 available and receives no traffic, so order submissions fail and completed-order volume is zero.",
  "verdict": "confirmed"
}
```