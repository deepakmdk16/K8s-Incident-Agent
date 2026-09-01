## Root cause

**Deployment `web/storefront`** references a container image tag that does not exist in the registry. The pod template pins `registry.k8s.io/retail/storefront:2.4.1`; the container runtime resolves the reference and gets a hard `NotFound` from the registry, so no container image is ever unpacked. Both replicas sit in `ErrImagePull`/`ImagePullBackOff`, never start a container, and therefore never become Ready — which is exactly the paged symptom (`0/2 Ready`). Verdict: **confirmed**.

The mechanism is a bad reference in the Deployment spec, not a transient registry/network fault: the error is `code = NotFound ... not found` (a successful registry lookup that returned "no such tag"), not a timeout, TLS error, DNS failure, or `401/403 unauthorized`.

## Evidence chain

- **Symptom, workload level** — `kubectl get all -A`: `deployment.apps/storefront   0/2   2   0   6s   storefront   registry.k8s.io/retail/storefront:2.4.1`. Desired 2, available 0.
- **Symptom, deployment status** — describe of `deployment.apps/storefront`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable` and condition `Available   False   MinimumReplicasUnavailable`.
- **Pods never start** — `kubectl get all -A`: `pod/storefront-68b686c56f-c7tvt   0/1   ErrImagePull` and `pod/storefront-68b686c56f-d4pp7   0/1   ErrImagePull`. Both `0/1`, zero restarts.
- **Direct causal error** — describe of pod `storefront-68b686c56f-c7tvt`, event:
  `Warning Failed ... Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`
  The identical event appears in describe of pod `storefront-68b686c56f-d4pp7` — so it is not a single-node/single-pod fluke.
- **No container ever created** — describe of both pods: `Container ID:` (empty), `Image ID:` (empty), `State: Waiting / Reason: ImagePullBackOff`, `Ready: False`.
- **The bad reference lives in the workload spec, not just the pod** — describe of `deployment.apps/storefront` Pod Template: `Image: registry.k8s.io/retail/storefront:2.4.1`; identical in describe of `replicaset.apps/storefront-68b686c56f`. Changing the pods is futile; the Deployment spec is what must change.
- **Failure is at image resolution, not at app runtime** — the container `Command` (`sh -c echo "storefront starting on :8080"; while true; do ... nc -l -p 8080 ...`) never runs because there is no image; consistent with `Restart Count: 0` and no `Started`/`Created` events.
- **Fresh rollout, no prior good ReplicaSet to fall back to** — describe of deployment: `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, `NewReplicaSet: storefront-68b686c56f (2/2 replicas created)`, and the only event `Scaled up replica set storefront-68b686c56f from 0 to 2`. This matches the page's "right after the new storefront release went out" and explains why nothing is serving from the cluster (edge cache is serving stale content).

## Investigation ledger

- **Registry auth failure / missing imagePullSecret** — ruled out. The runtime error is `code = NotFound ... not found`, which is a resolved-but-absent tag. An auth problem surfaces as `unauthorized: authentication required` / `403 Forbidden` / `pull access denied`. Also, describe of both pods shows `Service Account: default` with no `ImagePullSecrets` needed by any other workload on this node — `coredns`, `kindnet`, `kube-proxy`, `local-path-provisioner` all pull fine (`1/1 Running`, 0 restarts).
- **Registry/network/DNS outage** — ruled out. Other images from the same host (`registry.k8s.io/kube-proxy:v1.37.0`, `registry.k8s.io/coredns/coredns:v1.14.6`) are running on the same node, and CoreDNS is `1/1 Running`. A connectivity fault would yield `dial tcp: i/o timeout` or `no such host`, not `NotFound`.
- **CrashLoopBackOff / bad app command / failing probe** — ruled out. Both pods show empty `Container ID`, `Restart Count: 0`, and `State: Waiting / ImagePullBackOff`. No container ever executed, and describe shows no liveness/readiness probes defined.
- **Scheduling failure (insufficient resources, taints, node selector, affinity)** — ruled out. Describe of both pods: `PodScheduled True`, `Successfully assigned web/storefront-... to incident-lab-control-plane`, `Node-Selectors: <none>`, `QoS Class: BestEffort` (no resource requests to satisfy). Pods even hold IPs (`10.244.0.10`, `10.244.0.11`) with `PodReadyToStartContainers True`.
- **Volume/ConfigMap/Secret mount failure** — ruled out. The only volume is the projected service-account token (`kube-api-access-*`), and `Initialized True` / `PodReadyToStartContainers True` show it mounted successfully.
- **Service/selector or networking misconfiguration causing "not Ready"** — ruled out as the *cause of the page*. The alert is `0/2 Ready` on the Deployment, which is decided by container readiness, not by any Service. (Worth noting separately for the follow-up: `kubectl get all -A` shows **no Service in namespace `web`**, and the container declares no `Port`. That will need attention before traffic can reach the pods, but it is not what makes the pods `0/2 Ready`.)
- **Image pull policy pinning a stale cached digest** — ruled out. There is no cached copy at all (`Image ID:` empty) and the failure is a registry-side tag resolution, not a local cache mismatch.
- **Age discrepancy (page says >15 min, objects show ~6–16s)** — noted, not a competing explanation. The Deployment is at `revision: 1` with `OldReplicaSets: <none>`, indicating this output was captured from a freshly (re)created object; the failure mode shown is deterministic and self-reproducing on every pull attempt (`Pulling ... (x2 over 15s)` then `BackOff`), so it persists indefinitely and is consistent with a 15-minute-old alert.

## Verification recipe

```bash
# 1. Confirm the exact image reference the Deployment spec is pinning.
kubectl get deployment storefront -n web \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
# expect: registry.k8s.io/retail/storefront:2.4.1

# 2. Confirm the registry itself says the tag does not exist (independent of k8s).
crane manifest registry.k8s.io/retail/storefront:2.4.1 ; echo "exit=$?"
crane ls registry.k8s.io/retail/storefront          # or: skopeo list-tags docker://registry.k8s.io/retail/storefront
# expect: MANIFEST_UNKNOWN / NAME_UNKNOWN, and 2.4.1 absent from the tag list

# 3. Confirm every replica fails for the same reason, with the NotFound signature.
kubectl get events -n web --field-selector reason=Failed \
  -o custom-columns=POD:.involvedObject.name,MSG:.message
# expect: both pods -> 'failed to resolve reference ...: not found'
```

**Remediation:** roll the Deployment forward to an image reference that actually exists — ideally pin by digest to make this class of failure impossible:

```bash
kubectl set image deployment/storefront -n web \
  storefront=registry.k8s.io/retail/storefront:<verified-tag-or-@sha256:...>
kubectl rollout status deployment/storefront -n web --timeout=120s
```

Since `revision: 1` with `OldReplicaSets: <none>` means there is **no** previous ReplicaSet to `kubectl rollout undo` to, forward-fixing the image is the only path; keep the edge cache serving until `rollout status` reports success. Follow-ups (not the paged cause): add a Service and a container `port` for `web/storefront`, add readiness/liveness probes, and add a CI gate that resolves the image manifest before the manifest is applied.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The Deployment's pod template pins the image registry.k8s.io/retail/storefront:2.4.1, a tag that does not exist in the registry. The kubelet's pull fails at reference resolution with 'code = NotFound ... not found', so no container image is ever unpacked and both replicas stay in ErrImagePull/ImagePullBackOff. With zero containers ever started, no replica can report Ready, producing the paged 0/2 Ready condition and MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```