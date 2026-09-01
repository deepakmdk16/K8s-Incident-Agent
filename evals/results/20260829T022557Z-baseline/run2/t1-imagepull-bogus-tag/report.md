## Root cause

**Verdict: confirmed.**

The `Deployment web/storefront` pod template references an image tag that does not exist in the registry: `registry.k8s.io/retail/storefront:2.4.1`. The container runtime cannot resolve the reference (`NotFound ... not found`), so both replicas never get a container image, never start, and never become Ready. With 0/2 Ready pods, the Deployment reports `Available=False (MinimumReplicasUnavailable)`, which is exactly the paged symptom. Because no container ever runs, there is no backend serving live prices/inventory/personalization and traffic falls back to the edge cache's stale content.

The fix must change the Deployment's spec (the image reference), not the pods it produced.

## Evidence chain

- **Symptom, from `kubectl get all -A`:** `web deployment.apps/storefront 0/2 2 0 6s ... registry.k8s.io/retail/storefront:2.4.1` — 2 desired/updated, 0 available.
- **Both pods are stuck pre-start, from `kubectl get all -A`:**
  `pod/storefront-68b686c56f-c7tvt 0/1 ErrImagePull` and `pod/storefront-68b686c56f-d4pp7 0/1 ErrImagePull`, `RESTARTS 0`.
- **Direct causal event, from `describe pod/storefront-68b686c56f-c7tvt -n web`:**
  `Warning Failed ... Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`
  The identical message appears in `describe pod/storefront-68b686c56f-d4pp7 -n web` — not a single-node/single-pod fluke.
- **Container never ran, from both pod describes:** `Container ID:` (empty), `Image ID:` (empty), `State: Waiting / Reason: ImagePullBackOff`, `Ready: False`, `Restart Count: 0`. Nothing was ever executed, so the app command (`nc -l -p 8080`) is irrelevant to the failure.
- **The bad reference originates in the workload spec, from `describe deployment.apps/storefront -n web`:** Pod Template → `Image: registry.k8s.io/retail/storefront:2.4.1`, and the same image in `describe replicaset.apps/storefront-68b686c56f -n web`. The ReplicaSet faithfully propagated the Deployment's template.
- **Deployment-level symptom mapping, from `describe deployment.apps/storefront -n web`:**
  `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`
  `Available False MinimumReplicasUnavailable`
  `Progressing True ReplicaSetUpdated`
- **Scheduling and node health are fine, from both pod describes:** `PodScheduled True`, `Initialized True`, `PodReadyToStartContainers True`, `Node: incident-lab-control-plane/172.18.0.2`, and `Normal Scheduled ... Successfully assigned`.
- **Nothing else in the cluster is broken, from `kubectl get all -A`:** every `kube-system` and `local-path-storage` pod is `1/1 Running` with `0` restarts; `coredns 2/2`, `kindnet 1/1`, `kube-proxy 1/1`.

## Investigation ledger

- **Bad registry credentials / private-registry auth (`imagePullSecrets` missing)** — Ruled out. Auth failures surface as `401 Unauthorized` / `pull access denied`. The observed error is `code = NotFound ... failed to resolve reference ... not found`, i.e. the registry answered and the reference does not exist. Also `describe pod` shows no `imagePullSecrets` requirement error and the pod's only volume is the standard `kube-api-access-*` projected token.
- **Registry unreachable / DNS or network egress failure** — Ruled out. Network-level failures produce `dial tcp`, `i/o timeout`, or `no such host`. Here containerd got a definitive `NotFound` resolve answer. Additionally `coredns-559f6c778d-9sqc8` and `-t9nfq` are `1/1 Running` with 0 restarts, and `kindnet-88ckx` / `kube-proxy-6ndq6` are healthy.
- **Application crash / bad startup command (CrashLoopBackOff)** — Ruled out. `Restart Count: 0` and `Container ID:` is empty in both pod describes; the container was never created, so the `sh -c ... nc -l -p 8080` command never executed.
- **Failing readiness/liveness probe** — Ruled out. No probes are defined in the Deployment or ReplicaSet pod template, and the pod never reached `Running` (`Status: Pending`, `State: Waiting`).
- **Scheduling failure: insufficient resources, taints, node selectors** — Ruled out. Both pods show `PodScheduled True` and `Normal Scheduled ... Successfully assigned web/... to incident-lab-control-plane`. `Node-Selectors: <none>`, `QoS Class: BestEffort` (no resource requests to fail against), and only the default not-ready/unreachable tolerations.
- **Stuck volume mount / PVC pending** — Ruled out. `Volumes: <none>` in the Deployment template; the only pod volume is the projected service-account token, and `Initialized True` / `PodReadyToStartContainers True`.
- **Service/Selector mismatch causing "no endpoints"** — Ruled out as the *cause*. There is no Service for `storefront` in the output at all, but the paged symptom is explicitly `0/2 Ready` on the Deployment, which is fully explained by the image pull failure. Even a perfect Service would have zero Ready endpoints here. (Worth noting as a separate hygiene item, not the root cause.)
- **A prior good ReplicaSet was scaled down by a bad rollout (rollback candidate)** — Considered. `describe deployment` shows `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, and only one ReplicaSet `storefront-68b686c56f` exists cluster-wide. So there is no previous-revision ReplicaSet to roll back to in this snapshot; the fix is to correct the image tag forward.
- **Timeline discrepancy (alert says ">15 minutes", objects show `AGE 6s`)** — Noted, does not change the diagnosis. The Deployment, ReplicaSet, and pods all share `AGE 6s` / `CreationTimestamp: Sat, 29 Aug 2026 06:49:37 +0530`, consistent with the object having been recreated (or the snapshot taken after a re-apply) shortly before capture. The failure mode is deterministic and independent of elapsed time: the tag does not exist, so it will fail identically at any age.

## Verification recipe

```bash
# 1. Confirm the exact image reference baked into the Deployment spec.
kubectl get deployment storefront -n web \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
# expect: registry.k8s.io/retail/storefront:2.4.1

# 2. Confirm the registry says that reference does not exist (not auth, not network).
kubectl get events -n web --field-selector reason=Failed \
  --sort-by=.lastTimestamp -o wide | tail -5
# expect: 'code = NotFound ... failed to resolve reference ... : not found'

# 3. Confirm the tag is genuinely absent upstream (independent of the cluster).
crane ls registry.k8s.io/retail/storefront   # or: skopeo list-tags docker://registry.k8s.io/retail/storefront
# expect: 2.4.1 absent; note which tag actually exists
```

**Remediation:** patch the Deployment to a tag that actually exists in the registry (verified via step 3), e.g.
`kubectl set image deployment/storefront storefront=registry.k8s.io/retail/storefront:<verified-tag> -n web`, then watch `kubectl rollout status deployment/storefront -n web`. Follow-ups: add an admission/CI check that resolves image references before merge, and set `minReadySeconds` plus a real `Service` + readiness probe so a bad tag cannot silently take the whole workload to zero.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The storefront Deployment's pod template pins image registry.k8s.io/retail/storefront:2.4.1, a tag that does not exist in the registry. Every replica's image pull fails with 'code = NotFound ... failed to resolve reference ... not found', so the container is never created and both pods sit in ErrImagePull/ImagePullBackOff. With 0 of 2 replicas ever reaching Ready, the Deployment reports Available=False (MinimumReplicasUnavailable), which is the paged 0/2 Ready condition.",
  "verdict": "confirmed"
}
```