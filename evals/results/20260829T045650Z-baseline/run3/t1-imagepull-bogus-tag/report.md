## Root cause

**Deployment `web/storefront` references a container image that does not exist in the registry.** Its pod template pins `registry.k8s.io/retail/storefront:2.4.1`, and the container runtime resolves that reference to a 404: `failed to resolve reference ... not found`. Because the image can never be pulled, neither replica's container starts, both pods stay `Pending`/`ImagePullBackOff`, and the Deployment reports `0/2` Ready with `Available=False / MinimumReplicasUnavailable`. This is a spec-level defect in the new release (revision 1 of this Deployment object), not a node, scheduling, or runtime-health problem. Verdict: **confirmed**.

## Evidence chain

- **The symptom matches the page.** `kubectl describe deployment.apps/storefront -n web`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, and `Conditions: Available False MinimumReplicasUnavailable`. `kubectl get all -A` shows `deployment.apps/storefront 0/2`.
- **The blocking mechanism is image resolution, stated explicitly by the kubelet.** From `describe of pod storefront-68b686c56f-c7tvt` (and identically in `describe of pod storefront-68b686c56f-d4pp7`):
  `Warning Failed ... Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`
  `code = NotFound` / `not found` is a registry-side "no such image or tag", not a transport or credential failure.
- **The container never ran.** Same describes: `Container ID:` (empty), `Image ID:` (empty), `State: Waiting / Reason: ImagePullBackOff`, `Restart Count: 0`. Nothing about the app itself has executed, so app-level bugs cannot be the cause.
- **The bad reference originates in the Deployment's spec, not somewhere downstream.** The identical image string appears in the Deployment pod template (`Image: registry.k8s.io/retail/storefront:2.4.1`), in `replicaset.apps/storefront-68b686c56f`, and in both pods. Fixing the pods is futile; the Deployment spec is what must change.
- **Both replicas fail the same way, so it is not one bad node or one unlucky pod.** Both pods are on `incident-lab-control-plane` and both show the same `NotFound` event.
- **Scheduling and networking are healthy — the failure is strictly after scheduling.** Pod conditions: `PodScheduled True`, `Initialized True`, `PodReadyToStartContainers True`, with only `Ready False` / `ContainersReady False`. Event `Normal Scheduled ... Successfully assigned web/storefront-68b686c56f-c7tvt to incident-lab-control-plane`.
- **The rest of the cluster is fine, which rules out a cluster-wide outage.** In `kubectl get all -A`, every `kube-system` and `local-path-storage` pod is `1/1 Running` with `0` restarts, and `daemonset.apps/kindnet` and `kube-proxy` are `1/1` ready. Other images from the same `registry.k8s.io` host (e.g. `registry.k8s.io/coredns/coredns:v1.14.6`, `registry.k8s.io/kube-proxy:v1.37.0`) are running, so the registry host itself is reachable — it is the `retail/storefront:2.4.1` repo/tag specifically that does not resolve.
- **Consistent with "right after the new release went out."** `deployment.kubernetes.io/revision: 1` with `NewReplicaSet: storefront-68b686c56f (2/2 replicas created)` and `OldReplicaSets: <none>`, i.e. the only ReplicaSet in play is the one carrying the bad image.
- **The degraded-but-serving edge behavior is consistent too:** there is no Service or Ingress for `storefront` anywhere in `kubectl get all -A` (only `default/kubernetes` and `kube-system/kube-dns`), so with zero Ready backends nothing origin-side can answer, and the edge cache is serving stale content — matching "stale prices and inventory, no personalization."

## Investigation ledger

- **Private registry / missing or wrong imagePullSecret** — ruled out. An auth failure surfaces as `401 Unauthorized`, `authentication required`, or `pull access denied`. The event says `code = NotFound ... not found`. Also `Service Account: default` with no `imagePullSecrets` and no pull-secret related events.
- **Registry unreachable / DNS / network egress failure** — ruled out. That produces `dial tcp`, `i/o timeout`, or `no such host`. Instead the pull reached the registry and got a definitive negative resolution. Corroborated by other pods running images pulled from the same `registry.k8s.io` host (`coredns:v1.14.6`, `kube-proxy:v1.37.0`).
- **Rate limiting / throttling (e.g. `toomanyrequests`)** — ruled out. No such string; the error is `NotFound`, and it is deterministic across both pods rather than intermittent.
- **Insufficient resources / unschedulable / node pressure** — ruled out. Both pods have `PodScheduled True` and were `Successfully assigned` to the node; there are no `FailedScheduling`, `Insufficient cpu/memory`, or eviction events. `QoS Class: BestEffort` with no resource requests means there is nothing to be short of.
- **Node not ready or kubelet broken** — ruled out. The kubelet on `incident-lab-control-plane` is actively emitting `Pulling`/`Failed`/`BackOff` events, and every control-plane and CNI pod on that node is `1/1 Running` with `0` restarts.
- **Application crash / bad command / CrashLoopBackOff** — ruled out. `Container ID` and `Image ID` are empty and `Restart Count: 0`, so the `sh -c ... nc -l -p 8080` command never executed. The command itself is also syntactically fine.
- **Failing readiness/liveness probe** — ruled out. No probes are defined in the pod template, and no `Unhealthy` events appear. The container isn't running, so no probe could be evaluated.
- **Service/selector mismatch sending traffic nowhere** — ruled out as *root cause*. Deployment selector `app=storefront` matches the pod labels `app=storefront`, and the RS selector matches its pods. There is no `storefront` Service at all, but even a perfect Service would have zero Ready endpoints while the image cannot be pulled. (Worth a separate follow-up ticket, not this SEV2's cause.)
- **Stuck rollout blocked by an old ReplicaSet / bad rollout strategy** — ruled out. `OldReplicaSets: <none>`, `revision: 1`, and the RS reports `2 current / 2 desired`; the replica machinery did its job, `SuccessfulCreate` fired for both pods. Nothing is stuck at the controller level.
- **`imagePullPolicy: Never` with an image absent from the node cache** — ruled out. The kubelet is actively attempting remote pulls (`Normal Pulling ... (x2 over 15s)`), which would not happen under `Never`.
- **Digest/architecture mismatch (image exists but no matching platform manifest)** — ruled out. That yields `no match for platform in manifest`, not `failed to resolve reference ... not found`.
- **Noted discrepancy, not a competing explanation:** the alert says "over 15 minutes," but the Deployment, RS, and pods all show `AGE 6s`/`16s`. The most likely reading is that the object was recently recreated or the capture was taken after a re-apply, so the timestamps reflect the current generation rather than the incident start. This does not change the mechanism — the same image reference is present in the current spec and is failing right now.

## Verification recipe

```bash
# 1. Confirm the exact image string the Deployment is asking for.
kubectl get deployment storefront -n web \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
# expect: registry.k8s.io/retail/storefront:2.4.1

# 2. Confirm the registry says that reference does not exist (independent of the cluster).
crane manifest registry.k8s.io/retail/storefront:2.4.1 ; echo "exit=$?"
crane ls registry.k8s.io/retail/storefront          # what tags DO exist?
# (no crane? use: skopeo inspect docker://registry.k8s.io/retail/storefront:2.4.1)
# expect: MANIFEST_UNKNOWN / NOT_FOUND, non-zero exit

# 3. Confirm every replica is blocked on that pull and nothing else.
kubectl get events -n web --sort-by=.lastTimestamp | grep -Ei 'pull|backoff'
# expect: repeated Failed ... code = NotFound ... not found, for both pods
```

**Remediation.** Roll the Deployment's image forward to a tag that actually exists in `registry.k8s.io/retail/storefront` (confirm with step 2 above), or roll back if a prior good revision exists:

```bash
# Preferred: pin to a verified-existing tag (or, better, an immutable digest)
kubectl set image deployment/storefront storefront=registry.k8s.io/retail/storefront:<verified-tag> -n web
kubectl rollout status deployment/storefront -n web --timeout=120s

# If a previous good revision exists in this Deployment's history:
kubectl rollout history deployment/storefront -n web
kubectl rollout undo deployment/storefront -n web
```

Note that `kubectl rollout undo` will not help here as captured — `revision: 1` with `OldReplicaSets: <none>` means there is no prior revision to fall back to, so the forward fix is the path.

Follow-ups (not the root cause, but exposed by this incident): (1) there is no `Service` fronting `storefront`, so verify how edge traffic is meant to reach it; (2) add a CI/admission check that the image reference resolves before the manifest is applied, and prefer immutable digests over mutable tags; (3) the pods define no readiness probe and no resource requests (`QoS Class: BestEffort`), which weakens rollout safety and makes `maxUnavailable: 25%` unable to halt a bad release.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The storefront Deployment's pod template pins image registry.k8s.io/retail/storefront:2.4.1, a reference that does not exist in the registry; the kubelet's pull fails with 'code = NotFound ... failed to resolve reference ... not found' and backs off. Because no container image can be obtained, neither replica's container ever starts, so both pods sit in ImagePullBackOff with empty Container IDs and the Deployment reports 0/2 Ready with Available=False (MinimumReplicasUnavailable).",
  "verdict": "confirmed"
}
```