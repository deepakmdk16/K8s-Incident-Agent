# t1-imagepull-bogus-tag — scenario notes

**Tier:** T1 (textbook single fault). **Status:** authored 2026-08-29;
captured and scored in the frozen case set (fixtures:
`evals/fixtures/t1-imagepull-bogus-tag/`).

**Provenance:** authored offline from the roster row (README #2) and the
scoring contract; no cluster access during authoring. Applied only via
`evals/inject.sh`.

**Namespaces owned:** `web` (only namespace this scenario creates).

**Ground truth (informal — the formal, scored version is `gold.json`):**

- Failing resource: `web/deployment/storefront`
- Fault class: image pull failure — the pod template names
  `registry.k8s.io/retail/storefront:2.4.1`, a repo/tag that does not exist.
- Mechanism: containerd cannot resolve the reference; every pull attempt
  fails with its `... registry.k8s.io/retail/storefront:2.4.1: not found`
  message; kubelet backs off (ErrImagePull → ImagePullBackOff); the
  containers are never created, so both replicas stay 0/2 Ready.
- Decisive evidence: the Failed/BackOff pod events whose message names the
  exact image reference and contains `not found`.
- Remediation: point the deployment at an image reference that exists
  (`kubectl -n web set image deployment/storefront storefront=<ref>`); the
  template image field is mutable, so no recreate is needed.

**Wait condition:** a pod event in `web` whose message contains BOTH the
exact image reference and `not found` (containerd phrasing) — never merely
the ErrImagePull/ImagePullBackOff reason, which auth and network failures
also reach. `wait.sh` additionally fail-fasts (immediate exit 1, distinct
diagnostic) on any event containing `toomanyrequests` or `rate limit`.

**Why deterministic:**

- **Registry choice is the load-bearing decision** (README rule 3, roster
  red-team): registry.k8s.io imposes no anonymous pull rate limits, so the
  only reachable outcome for a nonexistent repo/tag is `not found`. On
  Docker Hub the same fixture could freeze with `toomanyrequests` events
  contradicting gold. The wait.sh fail-fast turns any residual throttling
  surprise into a loud abort instead of a bad capture.
- The failure happens at manifest resolution, before any platform/arch
  selection or layer download — host architecture (arm64 node) is
  irrelevant, and no bytes are pulled.
- No scheduling or timing sensitivity: single node, no resource requests,
  both replicas schedule immediately; the first kubelet sync attempts the
  pull and emits the decisive event within seconds.
- Requires egress to registry.k8s.io at capture time. If the registry were
  unreachable, the event text would differ (DNS/timeout, not `not found`)
  and wait.sh would time out loudly rather than capture a wrong fixture.

**Gold-side asymmetry:** none needed — only one object can defensibly
change (the deployment's image reference); no producer/consumer ambiguity.

**Rule 1 (counterfactual entrypoint):** the fault lives entirely in the
image reference; the inline serve loop (`nc -l -p 8080` responder) is
deliberately busybox-sh compatible so the identical command succeeds once
the image reference is corrected. The container never starts while the
fault is live, so the command is moot in the captured fixture — it exists
to make the counterfactual real, not to shape the evidence.

**Counterfactual-verification record (rule 6) — to be completed at
rehearsal, before the pristine capture:**

- Recipe: `inject.sh --id t1-imagepull-bogus-tag --no-capture`; confirm the
  wait.sh gate passed (not-found event present); apply the remediation with
  the node-cached stand-in for a valid reference:
  `kubectl -n web set image deployment/storefront storefront=busybox:1.36`;
  confirm recovery: `kubectl -n web rollout status deployment/storefront`
  reaches 2/2 Available (serve loop runs as-is under busybox); then wipe
  and re-inject cleanly for the real capture.
- Rehearsal date / fix applied / recovery observed: **2026-08-29 ~01:45 IST.**
  `inject.sh --no-capture` manifested the fault (wait.sh: "pull-failure event
  contains 'not found' and names registry.k8s.io/retail/storefront:2.4.1" —
  confirming registry.k8s.io emits containerd's `not found` for the unknown
  repo). Fix applied: `kubectl -n web set image deployment/storefront
  storefront=busybox:1.36`. Recovery observed: `deployment "storefront"
  successfully rolled out`, both replicas 1/1 Running — the identical serve
  loop runs under the corrected reference. Wiped and re-injected cleanly for
  the pristine capture after this record.
