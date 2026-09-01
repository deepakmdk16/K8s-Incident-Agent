## Root cause

**Deployment `web/storefront` references a container image reference that does not exist in the registry.** The pod template pins `registry.k8s.io/retail/storefront:2.4.1`; containerd resolves that reference and gets a hard `NotFound` from the registry, so the kubelet never creates the container. Both replicas sit in `ErrImagePull`/`ImagePullBackOff`, no pod ever reaches Ready, and the Deployment reports `0/2` with `Available=False / MinimumReplicasUnavailable` — which is exactly the paged symptom. Product pages keep serving only because the edge cache is answering with stale content while no backend pod is Ready.

Verdict: **confirmed**.

## Evidence chain

- **The workload is down, not merely slow.** `kubectl get all -A`: `deployment.apps/storefront   0/2   2   0   ...   registry.k8s.io/retail/storefront:2.4.1`, and `describe deployment.apps/storefront -n web` → `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available   False   MinimumReplicasUnavailable`.
- **Both pods are stuck before container start.** `kubectl get all -A` shows `pod/storefront-68b686c56f-c7tvt  0/1  ErrImagePull` and `pod/storefront-68b686c56f-d4pp7  0/1  ErrImagePull`. In both `describe pod` outputs the container has `Container ID:` (empty), `Image ID:` (empty), `State: Waiting / Reason: ImagePullBackOff`, `Restart Count: 0` — the container image was never fetched, so the process never ran.
- **The exact failure is a registry-side "not found", not a transient network or auth error.** `describe of pod storefront-68b686c56f-c7tvt`, Events:
  > `Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`

  Identical message in `describe of pod storefront-68b686c56f-d4pp7`. `code = NotFound` at the *resolve reference* stage means the repository/tag does not exist — no 401/403 (auth), no i/o timeout / TLS error (network).
- **The bad reference originates in the Deployment spec, not in a mutated pod.** `describe deployment.apps/storefront -n web` Pod Template → `Image: registry.k8s.io/retail/storefront:2.4.1`; `describe replicaset.apps/storefront-68b686c56f -n web` Pod Template → same image; both pods → same image. The ReplicaSet is a faithful copy of the Deployment template, so the spec that must change is the Deployment's.
- **This is the new release, with no healthy predecessor to fall back to.** `describe deployment` shows `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, `NewReplicaSet: storefront-68b686c56f (2/2 replicas created)`. There is no prior ReplicaSet holding Ready pods, which is why availability went to zero rather than degrading partially — consistent with "0/2 Ready ... beginning right after the new storefront release went out".
- **Nothing else in the cluster is unhealthy.** `kubectl get all -A`: every `kube-system` and `local-path-storage` pod is `1/1 Running` with `0` restarts; `kindnet` and `kube-proxy` DaemonSets are `1/1` ready; `coredns` is `2/2`.

## Investigation ledger

- **Scheduling failure (insufficient resources, taints, node selector).** Ruled out: both pods show `Node: incident-lab-control-plane/172.18.0.2`, condition `PodScheduled: True`, and event `Successfully assigned web/storefront-...` with no `FailedScheduling`. `Node-Selectors: <none>`, `QoS Class: BestEffort` (no resource requests to satisfy).
- **Application crash / CrashLoopBackOff (bad command, bad config).** Ruled out: `Restart Count: 0` and empty `Container ID` on both pods — the container never started, so the `sh -c ... nc -l -p 8080` command was never executed. No `Started`/`Created`/`BackOff restarting failed container` events.
- **Failing readiness/liveness probe.** Ruled out: the pod spec in all three describes lists no probes (`Port: <none>`, no Liveness/Readiness lines), and readiness is moot since no container exists. `ContainersReady: False` is downstream of the pull failure.
- **Registry authentication / missing imagePullSecret (private repo).** Ruled out: the containerd error is `code = NotFound ... not found`, not `401 Unauthorized`/`403 Forbidden`/`pull access denied`. The pod also uses `Service Account: default` with no `imagePullSecrets` — but an auth problem would surface as an authorization error, not a resolve-time NotFound.
- **Network egress / DNS failure reaching the registry.** Ruled out: a network fault yields `dial tcp: i/o timeout`, `no such host`, or TLS errors; here the registry was reached and answered authoritatively that the reference does not exist. Cluster networking is otherwise healthy (`kindnet-88ckx 1/1 Running`, `coredns 2/2`).
- **Missing/mismatched Service or Endpoints breaking traffic.** Ruled out as *the paged cause*: the alert is `0/2 Ready` on the Deployment, a pod-readiness condition independent of Services. (Separately notable for follow-up: `kubectl get all -A` lists no Service in namespace `web` at all — but even a perfect Service would have zero Ready endpoints here, so it is not the mechanism behind the page.)
- **Image name correct but tag typo'd vs. repository nonexistent.** Not distinguishable from this output alone, and it does not change the remediation path. Both collapse to "the Deployment's image reference must be corrected to a tag/digest that exists". The `describe`-level evidence is sufficient to confirm the mechanism; `crane ls` / `skopeo list-tags` in the verification recipe pinpoints which.
- **Note on timing:** the alert says >15 minutes, while the Deployment and pods show `AGE 6s` / `revision: 1`. Most consistent reading: the workload was re-applied or recreated during the incident (or the collection is from a re-created object), resetting object ages. This does not weaken the root cause — the pull error is deterministic and reproduces on every attempt (`Pulling ... (x2 over 15s)` immediately followed by `Failed`), so it explains a sustained 15-minute outage as readily as a 6-second one.

**Remediation.** Correct the image reference in the Deployment's pod template to a tag that actually exists in `registry.k8s.io/retail/storefront` (ideally pinned by digest), e.g. `kubectl set image deployment/storefront storefront=registry.k8s.io/retail/storefront:<verified-tag> -n web`. If the previous good version cannot be identified quickly, `kubectl rollout undo deployment/storefront -n web` is **not** available here — `OldReplicaSets: <none>` means there is no prior revision to roll back to, so the fix must be a forward roll to a verified tag. Longer term: add an admission/CI check that the image exists before merge, and gate the rollout on `kubectl rollout status --timeout` so a bad release fails the pipeline instead of the cluster.

## Verification recipe

```bash
# 1. Confirm the Deployment's image reference and that zero replicas are available.
kubectl get deploy storefront -n web \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}{.status.availableReplicas}{"/"}{.status.replicas}{"\n"}'

# 2. Confirm the pull failure is a registry NotFound (not auth, not network).
kubectl get events -n web --field-selector reason=Failed \
  -o custom-columns=POD:.involvedObject.name,MSG:.message

# 3. Prove the tag does not exist in the registry (run from a host with registry access).
crane ls registry.k8s.io/retail/storefront   # or: skopeo list-tags docker://registry.k8s.io/retail/storefront
```

Expect: (1) `registry.k8s.io/retail/storefront:2.4.1` and `0/2`; (2) messages containing `code = NotFound ... not found`; (3) `2.4.1` absent from the tag list (or the repository itself erroring as nonexistent).

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The storefront Deployment's pod template pins the image registry.k8s.io/retail/storefront:2.4.1, a reference that does not exist in the registry. Every pull attempt fails at reference resolution with containerd 'code = NotFound ... not found', so the kubelet never creates the container and both replicas stay in ErrImagePull/ImagePullBackOff. With no prior ReplicaSet to fall back on (revision 1, OldReplicaSets: none), the Deployment reports 0/2 Ready and Available=False/MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```