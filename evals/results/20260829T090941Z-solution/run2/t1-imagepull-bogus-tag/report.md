## Root cause

Deployment web/storefront was rolled out with a container image reference that does not exist in the registry: .spec.template.spec.containers[storefront].image is registry.k8s.io/retail/storefront:2.4.1, and the kubelet's pull returns "registry.k8s.io/retail/storefront:2.4.1: not found". Both replicas were scheduled onto incident-lab-control-plane and stay Pending with the container waiting in ErrImagePull/ImagePullBackOff, so the Deployment reports 0/2 Ready and no storefront pod ever serves traffic — which is why product pages fall back to the stale edge cache. Fixing it means editing the Deployment's pod template image to a tag that exists in the registry.

Remediation: edit Deployment web/storefront, field `spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `an image reference that actually resolves in the registry (e.g. the last known-good storefront image, or a public shell image such as busybox:1.36 for this nc-based container)`.

## Evidence chain

1. [symptom] Deployment web/storefront reports 0/2 ready and both pods are Pending with image-pull waiting reasons.
   source: namespace_overview(web) — verified
   > deployment/storefront ready=0/2 podLabels={app=storefront}
2. [symptom] Both replicas are stuck in ImagePullBackOff / ErrImagePull.
   source: namespace_overview(web) — verified
   > storefront(ready=False,restarts=0,waiting=ImagePullBackOff)
3. [defect] The Deployment pod template names the non-existent image tag.
   source: get_object({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > "image": "registry.k8s.io/retail/storefront:2.4.1",
4. [link] The kubelet cannot resolve that image reference; the registry reports it as not found.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
5. [link] The kubelet keeps retrying the pull and backing off, leaving the container Waiting.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > Normal   BackOff    15s               kubelet            spec.containers{storefront}: Back-off pulling image "registry.k8s.io/retail/storefront:2.4.1"
6. [link] The same pull failure affects the second replica too, so it is the template and not one pod.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1"

## Investigation ledger

- Scheduling failure (no node capacity, taints, node selectors) keeping the pods Pending — ruled out: The pod was successfully scheduled and admitted onto the node; only the image pull failed.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Normal   Scheduled  16s               default-scheduler  Successfully assigned web/storefront-68b686c56f-d4pp7 to incident-lab-control-plane
- Missing or wrong registry credentials (imagePullSecrets) causing the pull to fail — ruled out: The pull error is a NotFound on reference resolution, not an authentication or authorization error, and the pod template declares no pull secrets at all.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > rpc error: code = NotFound desc = failed to pull and unpack image
- A broken referenced ConfigMap or mounted config in the namespace — ruled out: The only ConfigMap in web is the auto-injected CA bundle, and the container spec references no configuration at all (Environment: <none>).
  source: get_object({"kind": "configmaps", "namespace": "web"}) — verified
  > "name": "kube-root-ca.crt",

## Verification recipe

1. `kubectl -n web get deployment storefront -o jsonpath='{.spec.template.spec.containers[0].image}'` — expect to see: registry.k8s.io/retail/storefront:2.4.1  [PRESENT]
2. `kubectl -n web describe pod storefront-68b686c56f-d4pp7` — expect to see: not found  [PRESENT]
3. `kubectl -n web get events --field-selector type=Warning` — expect to see: Error: ImagePullBackOff  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t1-imagepull-bogus-tag",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "web",
    "name": "storefront"
  },
  "mechanism": "Deployment web/storefront sets .spec.template.spec.containers[storefront].image to \"registry.k8s.io/retail/storefront:2.4.1\", a reference the registry does not serve: the kubelet pull fails with `failed to resolve reference \"registry.k8s.io/retail/storefront:2.4.1\": registry.k8s.io/retail/storefront:2.4.1: not found`, so each replica's container is stuck Waiting with Reason ImagePullBackOff while the kubelet keeps re-Pulling and backing off, and the Deployment stays ready=0/2.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
