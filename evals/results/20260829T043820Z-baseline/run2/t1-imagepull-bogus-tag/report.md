## Root cause

**Deployment `web/storefront` references a container image tag that does not exist in the registry.** The pod template pins `registry.k8s.io/retail/storefront:2.4.1`; containerd resolves that reference and gets a hard `NotFound` from the registry, so the image is never pulled, the container never starts, both replicas sit in `ErrImagePull`/`ImagePullBackOff`, and the Deployment reports `0/2` Ready. Because no pod ever becomes Ready, nothing backs the storefront workload and the edge cache is left serving stale content.

This is a **spec-level defect in the Deployment**, not a runtime failure of the pods: the pods are behaving correctly given an unresolvable image reference. Verdict: **confirmed**.

## Evidence chain

- **The symptom, as paged:** `kubectl get all -A` shows
  `web deployment.apps/storefront 0/2 2 0 ... registry.k8s.io/retail/storefront:2.4.1`
  — 2 desired, 2 up-to-date, **0 available**. Matches "0/2 Ready".
- **Both pods are stuck before container start, not after:** `kubectl get all -A` lists
  `pod/storefront-68b686c56f-c7tvt 0/1 ErrImagePull 0 6s` and
  `pod/storefront-68b686c56f-d4pp7 0/1 ErrImagePull 0 6s`.
  `RESTARTS 0` — nothing ever ran, so this is not a crash loop.
- **Direct causal event (the decisive line):** describe of pod `storefront-68b686c56f-c7tvt`, Events:
  > `Warning Failed ... Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`

  The identical event appears in describe of pod `storefront-68b686c56f-d4pp7`. `code = NotFound` at the **resolve reference** step means the registry answered and denied the existence of that tag — this is a nonexistent tag, not a network or auth failure.
- **Container never initialized:** describe of pod `...-c7tvt` shows `Container ID:` (empty), `Image ID:` (empty), `State: Waiting / Reason: ImagePullBackOff`, `Ready: False`.
- **The bad reference originates in the Deployment spec, and is propagated down:**
  - describe of `deployment.apps/storefront` → Pod Template → `Image: registry.k8s.io/retail/storefront:2.4.1`
  - describe of `replicaset.apps/storefront-68b686c56f` → Pod Template → same image
  - describe of both pods → same image.
  Fixing the pods or the ReplicaSet would be undone by the Deployment controller; the **Deployment** is the resource whose spec must change.
- **Deployment-level confirmation of the outage:** describe of `deployment.apps/storefront`:
  `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, and
  `Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`.
- **Scheduling and node health are fine (rules out an infrastructure story):** describe of pod `...-c7tvt`:
  `Normal Scheduled ... Successfully assigned web/storefront-68b686c56f-c7tvt to incident-lab-control-plane`, `PodScheduled True`, `PodReadyToStartContainers True`. Both pods even have IPs assigned (`10.244.0.10`, `10.244.0.11`).
- **Rest of the cluster is healthy:** `kubectl get all -A` shows every `kube-system` pod, `kindnet`, `kube-proxy`, `coredns` (2/2) and `local-path-provisioner` (1/1) `Running` with `0` restarts at 9h age. The only unhealthy objects in the entire cluster are the two storefront pods.
- **Timing note (does not change the diagnosis):** the alert claims ">15 minutes", but the Deployment `CreationTimestamp: Sat, 29 Aug 2026 06:49:37 +0530` and all objects show `AGE 6s`/`16s`. The snapshot was taken shortly after the object was (re)created; the failure mode is deterministic and identical on every pull attempt (`Pulling ... (x2 over 15s)` followed by `Failed`), so it reproduces indefinitely regardless of elapsed time.

## Investigation ledger

- **Crash loop / bad application command (`sh -c ... nc -l -p 8080`) —** ruled out. `RESTARTS 0` in `kubectl get all -A`, and describe of both pods shows `Container ID:` empty with `State: Waiting`. The container image was never obtained, so the command was never executed and cannot be implicated.
- **Failing readiness/liveness probe —** ruled out. Describe of the pods shows no `Liveness`/`Readiness` lines at all, and no `Unhealthy` events. `ContainersReady: False` is explained entirely by the container not existing.
- **Missing / misconfigured imagePullSecret (private registry, auth failure) —** ruled out. An auth failure surfaces as `401 Unauthorized`/`403 Forbidden` or `pull access denied`. The event says `code = NotFound ... not found` at the resolve step. Additionally, describe of the pods shows no `ImagePullSecrets` and `Service Account: default` — consistent with an intentionally public registry path.
- **Registry unreachable / DNS or egress failure —** ruled out. A network failure yields `dial tcp`, `i/o timeout`, or `no such host`. Here the registry responded authoritatively with `NotFound`. Corroborating: `coredns` is `2/2 Running` and `kindnet`/`kube-proxy` DaemonSets are `1/1` ready in `kubectl get all -A`.
- **Insufficient resources / unschedulable / node pressure / taints —** ruled out. Both pods show `Normal Scheduled ... Successfully assigned`, `PodScheduled True`, and were placed on `incident-lab-control-plane`. There are no `FailedScheduling`, `Evicted`, or `Insufficient cpu/memory` events, and the pods are `QoS Class: BestEffort` with no resource requests to violate.
- **Selector/label mismatch orphaning pods —** ruled out. Deployment `Selector: app=storefront`, ReplicaSet selector `app=storefront,pod-template-hash=68b686c56f`, and both pods carry `Labels: app=storefront, pod-template-hash=68b686c56f`. The ReplicaSet reports `2 current / 2 desired` and both pods are `Controlled By: ReplicaSet/storefront-68b686c56f`.
- **Missing Service / broken traffic routing to the storefront —** ruled out as the *cause*. There is indeed no Service for `web/storefront` in `kubectl get all -A`, but that would not make pods report `0/2 Ready`. The paged symptom is pod readiness, which is fully explained by the image pull. (Worth filing as a separate follow-up — even after the image is fixed, there is no Service object in the output to route traffic to these pods.)
- **Stuck rollout with old pods still serving —** ruled out. Describe of the Deployment shows `OldReplicaSets: <none>` and `deployment.kubernetes.io/revision: 1`; there is no previous ReplicaSet holding healthy pods, which is why availability went to zero rather than degrading partially.

## Verification recipe

```bash
# 1. Confirm the exact image the Deployment spec pins, and that it is the only image in play.
kubectl get deployment storefront -n web \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
# expect: registry.k8s.io/retail/storefront:2.4.1

# 2. Confirm the registry itself says the tag does not exist (NotFound, not auth/network).
kubectl get events -n web --field-selector reason=Failed \
  --sort-by=.lastTimestamp -o wide | tail -5
# expect: ... code = NotFound ... failed to resolve reference ... : not found

# 3. Prove it independently of the cluster: ask the registry for the tag list.
crane ls registry.k8s.io/retail/storefront    # or: skopeo list-tags docker://registry.k8s.io/retail/storefront
# expect: 2.4.1 absent (or the repository itself does not exist)
```

**Remediation:** patch the Deployment's image to a tag that actually exists in the registry — `kubectl set image deployment/storefront storefront=registry.k8s.io/retail/storefront:<valid-tag> -n web` — after confirming the tag with step 3. Note that `kubectl rollout undo` is **not** available here: describe of the Deployment shows `revision: 1` and `OldReplicaSets: <none>`, so there is no prior good revision to fall back to. Longer term, add an admission/CI check that the referenced image manifest resolves before the manifest is applied, and give the Deployment a `readinessProbe` plus a Service so the edge cache fallback is a deliberate, alarmed state rather than a silent one.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The storefront Deployment's pod template pins the image registry.k8s.io/retail/storefront:2.4.1, a tag the registry does not contain. Every pull attempt fails at reference resolution with 'code = NotFound ... not found', so the container is never created and both replicas remain in ErrImagePull/ImagePullBackOff. With zero pods ever reaching Ready, the Deployment reports 0/2 available and MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```