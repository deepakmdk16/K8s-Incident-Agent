## Root cause

The Deployment web/storefront was rolled out with a container image reference that does not exist in the registry. Its pod template names registry.k8s.io/retail/storefront:2.4.1, and every pull attempt on the node returns a NotFound from the registry ("failed to resolve reference ... not found"). Both replicas of ReplicaSet storefront-68b686c56f therefore stay in Pending with the container waiting in ErrImagePull / ImagePullBackOff, so the Deployment reports 0/2 Ready and no storefront process ever starts — which is why product pages are only being served stale from the edge cache. The fix is to edit the Deployment's pod template image to a tag that is actually published (for example roll back to the previously deployed storefront image); deleting the pods will not help because the ReplicaSet recreates them from the same bad reference.

Remediation: edit Deployment web/storefront, field `spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `an image reference that actually exists in the registry (the tag published for this release, e.g. the last known-good storefront image); the tag 2.4.1 at registry.k8s.io/retail/storefront does not resolve`.

## Evidence chain

1. [symptom] The Deployment reports 0/2 Ready and both pods are Pending with image-pull waiting reasons.
   source: namespace_overview(web) — verified
   > deployment/storefront ready=0/2 podLabels={app=storefront}
2. [symptom] One pod waits in ImagePullBackOff, the other in ErrImagePull.
   source: namespace_overview(web) — verified
   > storefront(ready=False,restarts=0,waiting=ErrImagePull)
3. [defect] The Deployment pod template names the non-existent image tag.
   source: get_object({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > "image": "registry.k8s.io/retail/storefront:2.4.1",
4. [link] The kubelet cannot resolve that reference; the registry returns NotFound.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
5. [link] Both replicas fail identically on the same image reference.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound
6. [link] The pods are produced by ReplicaSet storefront-68b686c56f, whose template carries the same image, so the defect must be fixed in the owning Deployment.
   source: get_object({"kind": "replicasets", "namespace": "web"}) — verified
   > "name": "storefront-68b686c56f",

## Investigation ledger

- Scheduling failure / insufficient node capacity keeping the pods Pending — ruled out: The pod is already assigned to a node and PodScheduled is True; it is Pending only because the container image cannot be pulled.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > PodScheduled                True
- Missing or wrong imagePullSecret / registry authentication failure — ruled out: The pull error is a NotFound on the reference, not an authorization/unauthorized error, so credentials are not the blocker.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > rpc error: code = NotFound desc = failed to pull and unpack image
- A bad rollout that can be fixed by rolling back to a previous ReplicaSet — ruled out: Only one ReplicaSet at revision 1 exists in the namespace, so there is no prior template to roll back to; the Deployment's image field itself must be edited.
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
  "mechanism": "The Deployment web/storefront has .spec.template.spec.containers[storefront].image set to \"registry.k8s.io/retail/storefront:2.4.1\", a tag the registry does not have \u2014 the kubelet's pull is rejected with \"not found\" \u2014 instead of an image reference that resolves. Its two pods are admitted and scheduled but their container never starts, staying Waiting with ErrImagePull and then ImagePullBackOff while the kubelet keeps re-pulling on a back-off, so the Deployment stays at 0/2 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
