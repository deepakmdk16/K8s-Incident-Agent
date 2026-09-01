## Root cause

Deployment web/storefront was rolled out with a container image reference that does not exist in the registry. Its pod template asks for registry.k8s.io/retail/storefront:2.4.1, and the kubelet's pull of that reference is rejected by the registry with a NotFound: "failed to resolve reference ... not found". Both replicas of the only ReplicaSet (revision 1) therefore stay Pending in ErrImagePull/ImagePullBackOff, so the Deployment reports 0/2 Ready and product pages are served only from stale edge cache. The fix is to correct the image tag in the Deployment pod template to a tag that is actually published (or roll back to the previously released tag); the pods themselves need no edit, they are recreated from the template.

Remediation: edit Deployment web/storefront, field `spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `an image reference that exists in the registry (roll back to the previously released storefront tag, or the corrected tag for this release), e.g. registry.k8s.io/retail/storefront:<published-tag>`.

## Evidence chain

1. [symptom] Deployment storefront reports 0/2 ready and both pods are Pending with image-pull waiting reasons.
   source: namespace_overview(web) — verified
   > deployment/storefront ready=0/2 podLabels={app=storefront}
2. [defect] The Deployment pod template names the image tag 2.4.1.
   source: get_object({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > "image": "registry.k8s.io/retail/storefront:2.4.1",
3. [link] The registry rejects that exact reference as not found, so the container cannot start.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
4. [link] Both replicas fail identically on the same image reference, matching the whole-Deployment outage.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1"
5. [link] The bad image comes from the single ReplicaSet created by this Deployment's template, so the human edit belongs on the Deployment.
   source: get_object({"kind": "replicasets", "namespace": "web"}) — verified
   > "name": "storefront-68b686c56f",

## Investigation ledger

- Pods cannot be scheduled (node capacity, taints, node selector) — ruled out: The pod is already assigned to a node and PodScheduled is True; the failure happens after scheduling, at image pull.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Successfully assigned web/storefront-68b686c56f-d4pp7 to incident-lab-control-plane
- Missing/incorrect registry credentials (imagePullSecrets) causing an auth failure — ruled out: The pull error is NotFound on reference resolution, not an authorization/unauthorized error, and the pod template declares no imagePullSecrets at all.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > rpc error: code = NotFound desc = failed to pull and unpack image
- A broken application config reference (ConfigMap key) preventing startup — ruled out: The only ConfigMap in the namespace is the injected CA bundle and the container spec references no configMap keys or env; the container never started at all.
  source: get_object({"kind": "configmaps", "namespace": "web"}) — verified
  > "name": "kube-root-ca.crt",

## Verification recipe

1. `kubectl -n web get deployment storefront -o jsonpath='{.spec.template.spec.containers[0].image}'` — expect to see: registry.k8s.io/retail/storefront:2.4.1  [PRESENT]
2. `kubectl -n web describe pod storefront-68b686c56f-d4pp7` — expect to see: not found  [PRESENT]
3. `kubectl -n web get events --field-selector type=Warning` — expect to see: ErrImagePull  [PRESENT]
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
  "mechanism": "Deployment web/storefront has .spec.template.spec.containers[storefront].image set to \"registry.k8s.io/retail/storefront:2.4.1\", a tag that does not exist in the registry, so the kubelet's image pull is rejected with \"NotFound ... failed to resolve reference ... not found\" and the container never starts, leaving both pods in ImagePullBackOff/ErrImagePull.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
