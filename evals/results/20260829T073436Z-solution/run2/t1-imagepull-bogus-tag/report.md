## Root cause

The new storefront release set the Deployment web/storefront pod template image to registry.k8s.io/retail/storefront:2.4.1, a reference that does not exist in the registry. Both pods of ReplicaSet storefront-68b686c56f schedule successfully onto incident-lab-control-plane but the kubelet's pull fails with "not found" (NotFound from the registry resolve step), so the containers stay in Waiting with ErrImagePull/ImagePullBackOff, never start, and the Deployment reports 0/2 Ready. Nothing else in the namespace is broken: the ServiceAccount, projected kube-root-ca.crt ConfigMap and node capacity all resolve fine. Fix by editing the Deployment pod template image to a tag that actually exists in the repository (or rolling back to the previous release image); deleting the pods will not help because the ReplicaSet recreates them from the same template.

Remediation: edit Deployment web/storefront, field `.spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `an image reference that resolves in the registry, e.g. the last known-good published storefront tag (registry.k8s.io/retail/storefront:<existing tag>)`.

## Evidence chain

1. [symptom] The Deployment reports 0/2 Ready and both of its pods are Pending with image-pull waiting reasons.
   source: namespace_overview(web) — verified
   > deployment/storefront ready=0/2 podLabels={app=storefront}
2. [symptom] Both pods are stuck in ImagePullBackOff / ErrImagePull.
   source: namespace_overview(web) — verified
   > storefront(ready=False,restarts=0,waiting=ImagePullBackOff)
3. [link] The kubelet cannot resolve the image reference; the registry returns not found.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
4. [link] The failure repeats for both replicas of the ReplicaSet, so it is template-driven, not pod-specific.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1"
5. [defect] The Deployment pod template carries the unresolvable image tag, which is the object a human must edit.
   source: get_object({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > "image": "registry.k8s.io/retail/storefront:2.4.1",
6. [defect] The only ReplicaSet (revision 1) reproduces the same image from the Deployment template, so recreating pods cannot fix it.
   source: get_object({"kind": "replicasets", "namespace": "web"}) — verified
   > "deployment.kubernetes.io/revision": "1"

## Investigation ledger

- Pods cannot be scheduled (insufficient capacity, taints, or node not ready) — ruled out: The pods were scheduled onto the node successfully and PodScheduled is True; the node is Ready with full allocatable capacity.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Normal   Scheduled  16s               default-scheduler  Successfully assigned web/storefront-68b686c56f-d4pp7 to incident-lab-control-plane
- Registry authentication failure / missing imagePullSecrets — ruled out: The pull error is a NotFound on reference resolution, not an unauthorized/authentication error, and the pod spec declares no imagePullSecrets.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > code = NotFound desc = failed to pull and unpack image
- A missing ConfigMap, Secret or ServiceAccount reference blocks the pod — ruled out: The only volume is the default projected service account token referencing kube-root-ca.crt, which exists in the namespace, and the pod is Initialized with containers only waiting on the image pull.
  source: get_object({"kind": "configmaps", "namespace": "web"}) — verified
  > "name": "kube-root-ca.crt",
- Container crashes after start (bad command, failing probe) — ruled out: The container never started: no container ID, zero restarts, state Waiting.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Container ID:

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
  "mechanism": "The Deployment web/storefront has .spec.template.spec.containers[storefront].image set to \"registry.k8s.io/retail/storefront:2.4.1\", a tag that does not exist in the registry instead of a published tag, so the kubelet's pull is rejected with \"not found\" and both replicas sit in Waiting with ErrImagePull/ImagePullBackOff while the kubelet keeps re-pulling on back-off, leaving the Deployment at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
