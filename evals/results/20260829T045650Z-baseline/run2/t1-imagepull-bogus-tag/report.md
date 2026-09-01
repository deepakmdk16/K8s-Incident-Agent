## Root cause

**Verdict: confirmed.**

The Deployment `web/storefront` specifies a container image reference that does not exist in the registry: `registry.k8s.io/retail/storefront:2.4.1`. The registry resolves the reference with a hard `NotFound`, so containerd never unpacks an image, the kubelet never starts the `storefront` container, and both replicas sit in `Pending`/`ImagePullBackOff` with `Ready: False`. Because no pod ever becomes Ready, the Deployment reports `0/2` and `Available=False (MinimumReplicasUnavailable)` — exactly the paged symptom. The fix must change the Deployment's pod template image (or push the missing tag); the pods themselves are disposable output of that spec.

## Evidence chain

1. **The symptom, at the workload level** — `kubectl get all -A`:
   `web deployment.apps/storefront   0/2   2   0   6s   storefront   registry.k8s.io/retail/storefront:2.4.1`
   and `describe deployment storefront`:
   `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`
   `Available   False   MinimumReplicasUnavailable`

2. **Both pods are stuck pre-start, not crashing** — `kubectl get all -A`:
   `pod/storefront-68b686c56f-c7tvt   0/1   ErrImagePull   0   6s`
   `pod/storefront-68b686c56f-d4pp7   0/1   ErrImagePull   0   6s`
   Both describes show `Status: Pending`, `State: Waiting / Reason: ImagePullBackOff`, `Container ID:` empty, `Image ID:` empty, `Restart Count: 0`. An empty Container ID with zero restarts proves the container was never created — this is a pull-stage failure, not a runtime failure.

3. **The exact causal mechanism** — `describe pod/storefront-68b686c56f-c7tvt`, Events:
   `Warning Failed ... Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image ...: failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found`
   The identical event appears in `describe pod/storefront-68b686c56f-d4pp7`. `code = NotFound` at the *resolve reference* step means the registry was reached and answered "that tag does not exist" — this is a bad image reference, not an auth or network fault.

4. **The bad reference originates in the Deployment spec, not the pods** — the same string appears at every level of ownership: `describe deployment.apps/storefront` → `Pod Template: Containers: storefront: Image: registry.k8s.io/retail/storefront:2.4.1`; `describe replicaset.apps/storefront-68b686c56f` → same image; both pod describes → same image. The ReplicaSet is `Controlled By: Deployment/storefront`, and the pods are `Controlled By: ReplicaSet/storefront-68b686c56f`. Deleting pods will regenerate them with the same unpullable image; only the Deployment's spec can break the loop.

5. **Why the page says "0/2 Ready" and edge cache is serving stale content** — the pods never pass `ContainersReady`/`Ready` (`Ready False`, `ContainersReady False` in both describes), so they are not endpoint-eligible for any Service. Nothing behind the storefront is serving live prices/inventory/personalization; only the CDN's stale copy remains.

6. **No prior good version is running to fall back to** — `describe deployment.apps/storefront`: `OldReplicaSets: <none>`, `NewReplicaSet: storefront-68b686c56f (2/2 replicas created)`, `deployment.kubernetes.io/revision: 1`. There is no previous ReplicaSet holding a working image, so a plain `kubectl rollout undo` has nothing to roll back to; remediation must supply a valid image.

## Investigation ledger

- **Registry authentication / missing imagePullSecret** — ruled out. An auth failure surfaces as `401 Unauthorized` / `pull access denied`; the event says `code = NotFound ... not found` at the resolve step. Additionally the pod spec shows `Service Account: default` and no `imagePullSecrets`, yet the registry still answered — the failure is the tag, not credentials.
- **Registry unreachable / DNS / egress blocked** — ruled out. Network failures produce dial/timeout/TLS errors, not a structured `NotFound` from the registry. Cluster DNS is healthy anyway: both `coredns-559f6c778d-*` pods are `1/1 Running`, and other images from `registry.k8s.io` (e.g. `registry.k8s.io/kube-proxy:v1.37.0`, `registry.k8s.io/coredns/coredns:v1.14.6`) are running on this node, proving the same registry host is reachable and pullable.
- **Application crash / bad command (CrashLoopBackOff)** — ruled out. `Restart Count: 0`, empty `Container ID`, `Status: Pending`. The `sh -c ... nc -l -p 8080` command never executed, so it cannot be the cause. No log evidence is even obtainable — `kubectl logs` would return "container is waiting to start".
- **Failed readiness/liveness probe** — ruled out. The pod template defines no probes at all (`describe deployment`, `describe pod` show no Liveness/Readiness lines), and containers never started, so probes are not in play.
- **Scheduling failure — insufficient resources, taints, node affinity, node pressure** — ruled out. Both pods show `PodScheduled True`, `Successfully assigned web/storefront-68b686c56f-* to incident-lab-control-plane`, `Node-Selectors: <none>`, `QoS Class: BestEffort` (no resource requests to fail against). The single node is healthy — every kube-system pod on it is `1/1 Running` with 0 restarts.
- **Storage / volume mount failure** — ruled out. `Volumes: <none>` in the pod template; the only mount is the standard projected service-account token, and `PodReadyToStartContainers True` plus `Initialized True` confirm the sandbox and volumes came up fine.
- **Service/Selector or networking misconfiguration hiding healthy pods** — ruled out as *root cause*. There is no Service for `storefront` in the output at all, but that is not what the alert measures: the workload availability monitor reads Deployment Ready replicas, which is `0/2` because the pods themselves are not Ready. Even a perfect Service would have zero endpoints here. (Worth filing separately that no `web/storefront` Service appears in `kubectl get all -A`.)
- **Nuance noted, not a competing cause** — the Deployment `AGE` is `6s` and `CreationTimestamp` is fresh, while the page says "over 15 minutes". This output is a snapshot taken after the object was (re)created/recollected; the pod-level events are internally consistent and independently prove the mechanism. It does not change the diagnosis.

## Verification recipe

```bash
# 1. Confirm the image reference the Deployment is asking for, and that the pods inherit it
kubectl get deploy storefront -n web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl get pods -n web -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].state.waiting.reason}{"\t"}{.status.containerStatuses[0].state.waiting.message}{"\n"}{end}'

# 2. Confirm the registry itself says the tag does not exist (independent of the cluster)
crane manifest registry.k8s.io/retail/storefront:2.4.1 || echo "TAG NOT FOUND -> root cause confirmed"
crane ls registry.k8s.io/retail/storefront    # lists tags that DO exist; pick the real one

# 3. Remediate by changing the Deployment spec to a tag that exists, then watch it recover
kubectl set image deploy/storefront storefront=registry.k8s.io/retail/storefront:<REAL_TAG> -n web
kubectl rollout status deploy/storefront -n web --timeout=120s
```

Expected before fix: step 1 prints `registry.k8s.io/retail/storefront:2.4.1` with `ImagePullBackOff` / `...not found` for both pods; step 2 fails to resolve the manifest. Expected after fix: `rollout status` reports `deployment "storefront" successfully rolled out` and `kubectl get deploy storefront -n web` shows `2/2`, which drops the edge cache back to live prices, inventory, and personalization.

Note for the follow-up ticket: since `OldReplicaSets: <none>`, there is no healthy prior revision to `kubectl rollout undo` into — the only path forward is a valid image. Longer term, gate releases with an admission policy or CI step that verifies the image manifest resolves before the Deployment is applied.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {"kind": "Deployment", "namespace": "web", "name": "storefront"},
  "mechanism": "The Deployment's pod template references registry.k8s.io/retail/storefront:2.4.1, a tag that does not exist in the registry, so containerd fails at reference resolution with 'code = NotFound ... not found'. The storefront container is therefore never created (empty Container ID, 0 restarts) and both replicas stay in ImagePullBackOff with Ready=False, leaving the Deployment at 0/2 Ready and Available=False.",
  "verdict": "confirmed"
}
```