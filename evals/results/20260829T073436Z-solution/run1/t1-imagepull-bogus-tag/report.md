## Root cause

The Deployment web/storefront was rolled out with a container image reference that does not exist. Its pod template names registry.k8s.io/retail/storefront:2.4.1, and every pull attempt on the node fails with a NotFound from the registry ("failed to resolve reference ... not found"). The ReplicaSet storefront-68b686c56f creates both pods and the scheduler places them successfully on incident-lab-control-plane, but the kubelet can never start the storefront container, so both pods stay Pending with ErrImagePull / ImagePullBackOff and the Deployment reports 0/2 available (Available=False, MinimumReplicasUnavailable). Nothing about identity, config, storage or node capacity is involved: the pods are scheduled and the failure is purely image resolution. Fixing the incident requires editing the Deployment's pod template image to a tag that exists in the registry; the pods themselves are disposable and will be recreated by the ReplicaSet.

Remediation: edit Deployment web/storefront, field `spec.template.spec.containers[storefront].image`: `registry.k8s.io/retail/storefront:2.4.1` -> `an image reference that actually exists in the registry and provides the sh/nc shell the container command needs (e.g. busybox:1.36); the tag registry.k8s.io/retail/storefront:2.4.1 does not resolve`.

## Evidence chain

1. [symptom] Both storefront pods are Pending and stuck waiting on the image pull.
   source: namespace_overview(web) — verified
   > pod/storefront-68b686c56f-c7tvt phase=Pending labels={app=storefront, pod-template-hash=68b686c56f} node=incident-lab-control-plane storefront(ready=False,restarts=0,waiting=ImagePullBackOff)
2. [symptom] The Deployment reports no available replicas.
   source: describe({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > Replicas:               2 desired | 2 updated | 2 total | 0 available | 2 unavailable
3. [defect] The Deployment pod template names the non-resolvable image tag.
   source: describe({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
   > Image:      registry.k8s.io/retail/storefront:2.4.1
4. [link] The kubelet cannot resolve that image reference in the registry.
   source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
   > Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference "registry.k8s.io/retail/storefront:2.4.1": registry.k8s.io/retail/storefront:2.4.1: not found
5. [link] The same NotFound pull failure affects both replicas, not just one pod.
   source: get_events({"namespace": "web"}) — verified
   > web Warning Failed pod/storefront-68b686c56f-c7tvt x1 Failed to pull image "registry.k8s.io/retail/storefront:2.4.1": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference

## Investigation ledger

- Pods are Pending because the scheduler could not place them (node capacity, taints, node pressure). — ruled out: The pod is already scheduled and bound to the node; Pending is due to the container image, not scheduling.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > Normal   Scheduled  16s               default-scheduler  Successfully assigned web/storefront-68b686c56f-d4pp7 to incident-lab-control-plane
- Missing or wrong registry credentials (imagePullSecrets) blocking the pull. — ruled out: The registry answered with NotFound on reference resolution, not an authentication/authorization error, so credentials are not the blocker.
  source: describe({"kind": "pod", "name": "storefront-68b686c56f-d4pp7", "namespace": "web"}) — verified
  > rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/retail/storefront:2.4.1": failed to resolve reference
- A missing ConfigMap, Secret or volume reference is keeping the container from starting. — ruled out: The pod template mounts nothing and sets no environment references; the only volume is the default projected service-account token.
  source: describe({"kind": "deployment", "name": "storefront", "namespace": "web"}) — verified
  > Environment:   <none>
  >     Mounts:        <none>
  >   Volumes:         <none>
- The node itself is unhealthy or out of resources. — ruled out: The only node is Ready with full allocatable capacity and no taints listed.
  source: cluster_capacity({}) — verified
  > condition Ready=True

## Verification recipe

1. `kubectl -n web get deployment storefront -o jsonpath='{.spec.template.spec.containers[0].image}'` — expect to see: registry.k8s.io/retail/storefront:2.4.1  [PRESENT]
2. `kubectl -n web describe pod storefront-68b686c56f-d4pp7` — expect to see: failed to resolve reference  [PRESENT]
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
  "mechanism": "The Deployment web/storefront sets .spec.template.spec.containers[storefront].image to \"registry.k8s.io/retail/storefront:2.4.1\", a tag the registry does not have, so the kubelet's pull fails with \"failed to resolve reference ... not found\" and the container is held in ErrImagePull/ImagePullBackOff on both replicas; the Deployment stays at 0/2 available with Available=False / MinimumReplicasUnavailable while the kubelet keeps re-attempting the pull under back-off.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
