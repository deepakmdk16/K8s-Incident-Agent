## Root cause

**Verdict: confirmed.**

The Deployment `web/storefront` specifies a container image reference that does not exist in the registry: `registry.k8s.io/retail/storefront:2.4.1`. Every pod the ReplicaSet creates is scheduled successfully, but the kubelet's image pull fails at reference resolution with `NotFound` ("not found"), so the container never starts, the pod never becomes Ready, and the Deployment sits at `0/2 available` — which is exactly what the availability monitor paged on. Because the pod spec is the thing that carries the bad reference, no amount of restarting or rescheduling will help; the Deployment's `spec.template.spec.containers[0].image` must change.

## Evidence chain

- **The paged symptom is reproduced in the output.** `kubectl get all -A`: `web deployment.apps/storefront 0/2 2 0 6s`, and `describe deployment.apps/storefront`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, with condition `Available False MinimumReplicasUnavailable`.
- **Both pods are stuck pre-start, not crashing.** `kubectl get all -A`: `pod/storefront-68b686c56f-c7tvt 0/1 ErrImagePull 0 6s` and `pod/storefront-68b686c56f-d4pp7 0/1 ErrImagePull 0 6s`. `RESTARTS 0` and, in both describes, `Container ID:` and `Image ID:` are empty — the container was never created.
- **The failure mechanism is an unresolvable image reference.** `describe of pod storefront-68b686c56f-c7tvt`, event: `Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`. The identical event appears in `describe of pod storefront-68b686c56f-d4pp7`, so this is deterministic, not a one-off.
- **The bad reference originates in the workload spec, not the pod.** `describe deployment.apps/storefront` → `Pod Template: Containers: storefront: Image: registry.k8s.io/retail/storefront:2.4.1`; the same image appears in `describe replicaset.apps/storefront-68b686c56f` and in the `IMAGES` column of the Deployment/ReplicaSet rows of `kubectl get all -A`. The Deployment is the resource whose spec must change.
- **Scheduling, node, and admission are all healthy — only the pull is failing.** Both pod describes show `Normal Scheduled ... Successfully assigned web/storefront-68b686c56f-... to incident-lab-control-plane` and conditions `PodScheduled True`, `Initialized True`, `PodReadyToStartContainers True`; only `Ready`/`ContainersReady` are `False`.
- **The degraded-but-serving edge behaviour is consistent.** There is no `Service` in namespace `web` in the `kubectl get all -A` service list, and no in-cluster storefront pod is Ready — origin traffic has no healthy backend, matching "served from edge cache, stale prices, no personalization."
- **This is a fresh rollout with no healthy predecessor.** `describe deployment.apps/storefront`: `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, `NewReplicaSet: storefront-68b686c56f (2/2 replicas created)`, and only one storefront ReplicaSet exists in `kubectl get all -A`.

## Investigation ledger

- **Registry authentication failure (private repo, missing/expired imagePullSecret).** Ruled out: the containerd error is `code = NotFound ... not found` at the *resolve reference* step. An auth failure surfaces as `401 Unauthorized` / `pull access denied`, not `NotFound`. Additionally both pod specs show `Service Account: default` with no `ImagePullSecrets` and no auth-related events.
- **Registry unreachable / DNS or network egress broken.** Ruled out: the pull attempt reached the registry and got a definitive negative answer (`not found`) rather than a timeout, TLS error, or `no such host`. Cluster networking is otherwise healthy — `kindnet-88ckx`, `kube-proxy-6ndq6`, and both `coredns` pods are `1/1 Running` with `0` restarts.
- **Application crash / bad container command (CrashLoopBackOff).** Ruled out: `RESTARTS 0`, `Container ID:` empty, and the state is `Waiting / ImagePullBackOff` — the process never executed, so the `sh -c ... nc -l -p 8080` command is untested and cannot be the cause.
- **Failed readiness probe / missing port.** Ruled out: the pod template declares `Port: <none>` and no probes, and `ContainersReady False` is downstream of the container never being created. No probe-failure events appear.
- **Scheduling problem — insufficient resources, node taints, node selectors.** Ruled out: `PodScheduled True` with `Successfully assigned web/... to incident-lab-control-plane` for both pods; `Node-Selectors: <none>`, `QoS Class: BestEffort` (no resource requests to fail on).
- **Node/kubelet unhealthy or disk-pressure eviction.** Ruled out: every `kube-system` and `local-path-storage` pod on the same node `incident-lab-control-plane` is `1/1 Running` with `0` restarts and 9h age; the kubelet is actively emitting pull events for these pods.
- **Stuck rollout blocked by an unhealthy old ReplicaSet or surge settings.** Ruled out: `OldReplicaSets: <none>`, `revision: 1`, and the ReplicaSet reports `Replicas: 2 current / 2 desired` with `SuccessfulCreate` for both pods — the controller did its job; the kubelet could not.
- **`imagePullPolicy: Never` with an image absent from the node cache.** Ruled out: `Normal Pulling ... Pulling image` events show the kubelet is actively attempting a remote pull.

## Verification recipe

```bash
# 1. Confirm the Deployment spec carries the bad reference (this is the thing to change)
kubectl get deploy storefront -n web -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
# expect: registry.k8s.io/retail/storefront:2.4.1

# 2. Confirm the registry itself denies that reference (NotFound, not auth/network)
kubectl get events -n web --field-selector reason=Failed --sort-by=.lastTimestamp | tail -5
# expect: ... code = NotFound ... failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": not found

# 3. Independently confirm the tag does not exist, outside the cluster
crane manifest registry.k8s.io/retail/storefront:2.4.1 || echo "TAG DOES NOT EXIST"
# (or: skopeo inspect docker://registry.k8s.io/retail/storefront:2.4.1)
# and list what does exist:  crane ls registry.k8s.io/retail/storefront
```

**Remediation.** Repoint the Deployment at an image reference that actually exists — the last known-good storefront tag (ideally by digest), e.g.
`kubectl set image deploy/storefront -n web storefront=registry.k8s.io/retail/storefront:<known-good-tag>`.
Note that `kubectl rollout undo` is **not** available here: `describe deployment.apps/storefront` shows `revision: 1` and `OldReplicaSets: <none>`, so there is no prior revision to fall back to — the correct tag must be supplied explicitly. Longer term, verify the release tag was actually pushed before the manifest referencing it is applied (CI gate on image existence), and pin images by digest so a typo'd or never-pushed tag fails at the pipeline instead of at the kubelet.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The Deployment's pod template references the container image registry.k8s.io/retail/storefront:2.4.1, which does not exist in the registry. The kubelet fails to resolve that reference (containerd returns code = NotFound, 'not found'), so every replica stays in ErrImagePull/ImagePullBackOff and no container is ever created, leaving the Deployment at 0/2 available and the storefront origin with no healthy backend.",
  "verdict": "confirmed"
}
```