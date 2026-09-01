## Root cause

The new storefront release shipped a Deployment pod template pointing at an image tag that does not exist in the registry. Deployment web/storefront sets .spec.template.spec.containers[storefront].image to registry.k8s.io/retail/storefront:2.4.1, and the kubelet's pull of that reference is rejected by the registry with "not found" (NotFound on resolving the reference). Both pods of ReplicaSet storefront-68b686c56f therefore stay Pending with their container in ErrImagePull/ImagePullBackOff, never start, and the Deployment reports 0/2 Ready; user traffic is only being served stale from the edge cache. Scheduling, identity and configuration are not involved: the pods are scheduled and Initialized on the node, and the pull failure is a missing-tag error rather than an authentication or authorization error. Fixing the incident requires editing the Deployment's pod template image to a tag that exists.

Remediation: edit Deployment web/storefront, field `spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `An image reference that actually exists in the registry (e.g. the previously released, pullable storefront tag such as registry.k8s.io/retail/storefront:2.4.0 confirmed present with `crane manifest`), so the kubelet pull resolves instead of returning NotFound.`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/2 Ready and both its pods are Pending with image-pull waiting reasons.
   source: namespace_overview(web) — verified
   > deployment/storefront ready=0/2 podLabels={app=storefront}
2. [symptom] Both storefront pods are stuck in ImagePullBackOff / ErrImagePull.
   source: namespace_overview(web) — verified
   > storefront(ready=False,restarts=0,waiting=ImagePullBackOff)
3. [defect] The Deployment pod template names the non-existent image tag.
   source: get_object({"kind": "deployments", "name": "storefront", "namespace": "web"}) — verified
   > "image": "registry.k8s.io/retail/storefront:2.4.1",
4. [link] The kubelet cannot resolve that exact reference; the registry returns not found.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
5. [link] Both pods of the ReplicaSet produced by this Deployment hit the same pull failure, so the defect is in the shared template, not one pod.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1"
6. [link] The ReplicaSet created by this Deployment carries the same bad image, confirming it is inherited from the Deployment template.
   source: get_object({"kind": "replicasets", "namespace": "web"}) — verified
   > "name": "storefront-68b686c56f",

## Investigation ledger

- Pods could not be scheduled (node capacity, taints, node selector). — ruled out: The pod was successfully scheduled and bound to the node; the failure happens after scheduling, at image pull.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Normal   Scheduled  16s               default-scheduler  Successfully assigned web/storefront-68b686c56f-d4pp7 to incident-lab-control-plane
- Registry authentication failure / missing imagePullSecrets. — ruled out: The pull error is a NotFound on resolving the tag, not an unauthorized/denied response, and the pod uses the default service account with no pull-secret error.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1"
- A broken volume, ConfigMap or Secret reference kept the pods from starting. — ruled out: The only ConfigMap in the namespace is the automatic kube-root-ca.crt bundle, and the pod's only mount is the projected service-account volume, which mounted fine (Initialized True).
  source: get_object({"kind": "configmaps", "namespace": "web"}) — verified
  > "name": "kube-root-ca.crt",
- A rollback to the previous ReplicaSet would restore service. — ruled out: Only one ReplicaSet exists, at revision 1, so there is no prior good revision to roll back to; the Deployment template itself must be edited.
  source: get_object({"kind": "replicasets", "namespace": "web"}) — verified
  > "deployment.kubernetes.io/revision": "1"

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
  "mechanism": "Deployment web/storefront's pod template field .spec.template.spec.containers[storefront].image is set to \"registry.k8s.io/retail/storefront:2.4.1\", a tag the registry does not serve, so the kubelet's image pull fails with \"failed to resolve reference ... not found\" and the container is held in ErrImagePull/ImagePullBackOff instead of being created; the image must name an existing, pullable tag.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
