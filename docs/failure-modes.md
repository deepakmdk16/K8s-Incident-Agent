# Failure-mode log

Record agent failure modes AS THEY HAPPEN — date, what the agent did wrong, the
evidence, and the practical lesson. Concrete + non-obvious beats grand.

Each entry also records the prevention that now exists (CLAUDE.md's
self-improving loop): a solved issue must not be solvable twice.

<!--
## <date> — <one-line failure>
**What happened:** …
**Evidence:** …
**Prevention now in place:** test/gate/rule added, with link
**Lesson for reliable agents:** …
-->

## 2026-09-04 — RECURRENCE: an arm-score gap read as capability when it was vocabulary

**What happened:** the first scored run of the v2 cross-namespace case returned
solution 3/3 vs baseline 1/3, and the obvious reading — "the solution crosses a
namespace boundary, the baseline cannot" — was wrong. Both arms named the
correct object in **every** run. The gap was entirely in mechanism
classification: one baseline answer said the Service "selects" a label instead
of naming its "selector" and so missed the `\bselector\b` token the signature
requires; another described the root cause correctly but also mentioned the
downstream symptom and matched two classes where exactly one is required. Had
the pooled number been reported on its own, the repo would have carried a
capability claim its own evidence contradicts.

This is the second time the rubric's vocabulary has been mistaken for the thing
it measures — see 2026-08-29, "A mechanical rubric is a measurement instrument
with a vocabulary, and it can punish the better answer." That entry was about
authoring gold; this one is about *reading results*.

**Evidence:** `rows.jsonl` of
[evals/results/20260904T082737Z-baseline/](../evals/results/20260904T082737Z-baseline/summary.md)
— `resource_correct=3/3, class_correct=1/3`; the two arms' `answer.json`
failing_resource fields are identical across all six rows. Recorded in
CHANGELOG [13].

**Prevention now in place:** escalated a level — from a situational log entry
to a standing rule in [CLAUDE.md](../CLAUDE.md), because this governs the
project's central activity (comparing arms) rather than one case. The
instrument already exposed the truth: `verify_reported.py` prints a
"right object, sentence unmatched" row precisely for this, and `rows.jsonl`
carries `resource_correct` and `matched_classes` per row. The failure was in
reading the summary table instead of the rows.

**Lesson for reliable agents:** a metric gap names a *difference*, never its
cause. Before attributing one to capability, decompose it into the sub-scores
the harness already records — the most confident wrong conclusions come from a
true number read as if it answered a question it was never measuring.

## 2026-09-02 — `kind load docker-image` exits 0 having imported nothing

**What happened:** preparing the authoring cluster for the first v2 case, the
workload image was loaded with `kind load docker-image busybox:1.36 --name
incident-lab`. It printed an error line and **exited 0**. Taking the exit code
at face value would have left the node without the image, and the next capture
would have recorded `ErrImagePull` events instead of the injected fault — a
fixture that looks like a real incident but documents the wrong one.

**Evidence:** the command emitted `ctr: content digest sha256:b7f3d86d…: not
found` and returned success; `docker exec incident-lab-control-plane crictl
images | grep busybox` then printed nothing. The working path is
`docker save` → `docker cp` → `ctr -n k8s.io images import`, verified by the
same `crictl images` check.

**Prevention now in place:** [evals/cluster.sh](../evals/cluster.sh) does the
import through `ctr` and then **asserts the image is present in the node**,
failing loudly if it is not. The script also pins the kind node image by digest
so a fixture cannot be captured on a different Kubernetes version than the set
it joins. Its comment says why `kind load` is not used.

**Lesson for reliable agents:** a zero exit code is a claim, not evidence.
Where a command's whole purpose is to leave state behind, verify the state —
especially before an irreversible step downstream (here, a capture that would
be committed as ground truth). This is the same shape as the repo's
"error-as-green" failure mode: a gate that certifies a thing it never checked.

## 2026-08-28 — identity-linked API key 400s without a workspace id the Console won't show

**What happened:** the first live scored run failed 3/3 with
`anthropic-workspace-id is required when authenticating with an identity-linked
API key`. The key was an unscoped personal key ("All workspaces"); the org had
only the Default Workspace, whose ID the Console never displays and List
Workspaces never returns — and the sanctioned discovery path (response header
of a request that already runs there) is circular when this is your only key.
Unblocked by creating a fresh workspace, whose ID IS shown, and pinning it.
The harness itself behaved exactly as designed: 3 failed-case rows, $0 totals
with an explicit "3 case(s) without measured cost" warning, no silent zeros.
**Evidence:** dead-end bundle retained at
`evals/results/20260828T184246Z-baseline/`.
**Prevention now in place:** `common/llm.py` sends the header whenever
`ANTHROPIC_WORKSPACE_ID` is set; baseline/README.md names the requirement so a
cold session (or a fresh clone) hits instructions, not the 400.
**Lesson for reliable agents:** auth errors can be *structurally* unresolvable
with the credentials at hand — the fix was creating a new resource, not
retrying harder. An agent loop should classify a 4xx as "needs a different
credential/resource" fast instead of burning retries, and the harness's
count-don't-zero cost accounting is what made the dead run legible.

## 2026-08-28 — `kubectl logs --previous` is unreliable on kind/containerd

**What happened:** During an early smoke test (CrashLoopBackOff injected into a
kind cluster), `kubectl logs --previous` failed twice with "unable to retrieve
container logs for containerd://…" — containerd had already GC'd the exited
container. Plain `kubectl logs` returned the decisive evidence (`FATAL:
AMQP_URL not set`): during CrashLoopBackOff the *current* logs call already
serves the last terminated run's output.
**Evidence:** the lesson stands on the shipped prevention itself —
`evals/capture.sh` (both log channels, per-channel status) and
`tests/test_gates.sh` cases 16/21/22.
**Prevention now in place:** implemented — `evals/capture.sh` captures both
channels, tolerates failure of either, and records per-channel status in
`scenario.yaml`; the checkpoints.sh completeness gate rejects a ledger without
per-channel statuses (tests/test_gates.sh cases 16/21/22). The first real
capture confirmed the nondeterminism: `--previous` failed during the smoke
test but succeeded for the same fault an hour later.
**Lesson for reliable agents:** an evidence channel that *usually* works is a
trap for a verify-before-assert loop — the agent must treat "channel
unavailable" as observable data, not as an error to retry forever or a reason
to fabricate. Design the toolset so every channel can say "not available" in
band.

## 2026-08-28 — secret scan blocked the pipeline on PUBLIC cluster-CA material

**What happened:** the first real run of `capture.sh` (executed during
pre-merge adversarial review) died at its own self-check: every stock
kind/kubeadm cluster ships a `cluster-info` ConfigMap in `kube-public` whose
embedded kubeconfig contains `certificate-authority-data:` — the base64'd
PUBLIC cluster CA. The scrub redacted only `kind: Secret` data, and the leak
patterns (`LS0tLS1CRUdJTi`, `*-data:`) cannot tell public from private PEM, so
a fixture could never install.
**Evidence:** the lesson stands on the shipped prevention itself —
`capture.sh`'s redact-by-construction self-check and `tests/test_gates.sh`
cases 16-19.
**Prevention now in place:** `capture.sh` redacts kubeconfig-embed values by
construction wherever they appear (JSON and describe text); its self-check
uses the exact same four patterns as the checkpoints.sh fixture scan so the
two can never drift; the accept-side regression test now uses captured-shaped
content (cluster-info with redacted embed + raw public PEM) instead of a
sterile synthetic fixture (tests/test_gates.sh cases 16-19).
**Lesson for reliable agents:** a secret scanner that cannot distinguish
public from private key material will eventually block the pipeline on
legitimate bytes — and at 3am the tempting fix is weakening the gate, which is
exactly backwards. Redact the ambiguity at capture time so the gate never has
to decide; and test the ACCEPT side of a scanner on realistic bytes, not
hand-typed minimal fixtures, or the false-positive class ships.

## 2026-08-28 — exported transcript tripped the home-path gate by quoting the gate's own tests

**What happened:** the first transcript-export dry run failed
`checkpoints.sh --secrets-only`: the session had displayed
`tests/test_gates.sh` while extending it, so the exported transcript quoted
its deliberately planted `$TMP/home` fixture tree, and the home-path
scan (which exempts the gate files themselves but not files that quote them)
flagged the quoted paths as a leak. The standard scrub only rewrote `/Users/`
paths (macOS), so the `/home/` form survived.
**Prevention adopted:** the scrub was extended to rewrite `/home/*` segments
exactly like `/Users/*`, so quoted gate-test content was neutralized before
the scan ran. (The transcript-export pipeline is no longer part of this tree;
the lesson below is what carries.)
**Lesson for reliable agents:** a scanner's exemption list protects the files
that legitimately contain the banned patterns, but transcripts REPRODUCE those
files — any pipeline that exports its own working history must scrub the
patterns its gates hunt, not just the ones its platform produces.

## 2026-08-29 — scored run half-poisoned by mid-run API credit exhaustion

**What happened:** the first full-set baseline run (12 cases x 3 replicates,
live claude-opus-5) exhausted the workspace's API credit balance after 22 of
36 case-runs; the remaining 14 failed with a billing 400. The harness
correctly recorded them as failed rows scored wrong — which means the
bundle's summary silently blends "model was wrong" with "billing refused the
request". Quoted as-is, it would have understated the baseline by ~14
percentage points of pure infrastructure noise.

**Prevention now in place:** (1) results bundles contaminated by
non-model failures get a README disclosure at the bundle root the moment the
contamination is found, before any number can circulate
(`evals/results/20260829T022557Z-baseline/README.md` is the template); the
clean/contaminated split is verifiable per-row via `rows.jsonl`'s `error`
field. (2) Pre-scored-run protocol: estimate the run's cost from the prior
bundle's per-case metrics (~$0.15/case-run → ~$5.50 for 12x3) and confirm
balance headroom of at least 3x the estimate before launching; a scored run
is never started on an unknown balance. (3) Distinguish error classes when
reading any bundle: a `BadRequestError ... credit balance` row is a rerun,
never a data point.

## 2026-08-29 — RECURRENCE: second poisoned bundle, this time a spend CAP, not balance

**What happened:** hours after the credit-exhaustion entry above, the clean
re-run was poisoned the same way by a *different* billing mechanism: a
configured monthly usage limit ("You have reached your specified API usage
limits... regain access 2026-09-01") tripped after 22/36 case-runs. The
prior prevention (≥3x credit headroom) checked the balance — it cannot see
a cap, and the cap's natural reset was days away.

**Escalated prevention (per the standing rule: a solved issue recurring
escalates the prevention a level):** now machine-checked — `run_eval`
raises `InfrastructureError` on any billing/limit 400 (`_INFRA_MARKERS`),
aborts the run without spending further, writes the partial bundle with an
auto-generated DISCLOSED-PARTIAL README, and exits non-zero
(`test_run_case_aborts_on_billing_or_limit_failure` pins both marker
strings). Procedural residue: before a scored run, confirm BOTH credit
balance AND the monthly usage limit leave ≥3x the run's estimate; a
"limit" error message names a Console setting, not a top-up — read the
error class before reaching for the wallet.

## 2026-08-29 — A mechanical rubric is a measurement instrument with a vocabulary, and it can punish the better answer

**What happened:** the solution arm's first full scored matrix came back 33/36
against the baseline's 30/36, but with T1 *down* 15/15 → 12/15. The regression
looked like a capability loss and was not one. Every lost row had
`resource_correct=True` and `matched_classes=[]`: the agent named the correct
object — the one whose spec a human must edit — in **36 of 36 runs**, and lost
three rows purely on the wording of the mechanism sentence. It wrote "the claim
generated from that template" where the rubric's signature for that class needs
a PersistentVolumeClaim noun, and it stopped at "the container exits 1" without
naming the restart behaviour the page was actually about.

Then the correction over-shot in the opposite direction. Told to carry the
sentence through to the paged state, the agent stopped quoting the cluster's
own words and started paraphrasing them into structure. The three
`t2-readiness` rows had quoted the observed error — `"connection refused"` —
before; after the correction they wrote "refused at the TCP level", "hold
Ready=False", "no pod ever passing readiness". All three kept the object half
of the signature (`readiness`) and lost the symptom half entirely — synonyms,
zero matches — and scored **worse** for it, on the exact same fault. A
more thorough-sounding answer that abandons the cluster's own vocabulary can
score lower than a narrower one that quotes it.

**The general shape (traced per row through the frozen classifier, after a
first, wrong characterisation).** Each class signature is a CONJUNCTION of an
*object/noun* group and a *symptom/error-word* group; both must hit. The
correction moved a single dial — from **quoting observed output** toward
**describing canonical structure** — and that dial has opposite signs depending
on which half of the conjunction a case was short of:

- it SUPPLIED object nouns where those were missing ("the claim" -> "the
  PersistentVolumeClaim data-metrics-db-0"), winning 3 rows;
- it REMOVED symptom words where those were what matched: the readiness rows
  quoted `"connection refused"` before and paraphrased it to "refused at the TCP
  level" / "Ready=False" / "no pod ever passing readiness" after — all synonyms,
  none in the signature — losing 3 rows;
- the "stay on the failing object" half of the rule FORBADE naming the object
  the class is keyed on: the config-reference class needs `config ?map\b`, the
  old sentence named "ConfigMap orders/orders-config", the new one wrote only
  `configMapKeyRef.key`, and the regex's word boundary does not match
  "configMapKeyRef" because "K" is a word character. That fault *is* a
  cross-object reference failure and cannot be described from inside one object;
- and the API-kind half ADDED a noun that collided with a different class:
  "carries no configmap read permission" matches `config ?map\b` where the
  earlier "get,list on configmaps" did not (the trailing "s" blocks the
  boundary), producing a two-class match.

Three of those four outcomes turn on a word boundary or a synonym. That is the
real lesson: the instrument is not measuring "is this diagnosis good", it is
measuring "does this sentence contain one of these strings", and an agent tuned
against it is being tuned toward the strings.

**Prevention now in place:** (1) the fix was grounded in the FROZEN, shared
`OUTPUT_CONTRACT` — "never mention any workload or mechanism other than the
failing one" — rather than in observed classifier keywords, so the rule is a
restatement of a pre-registered contract and not a fitted one; the two other
mechanism rules (name the field by its API path, say what fails in failure
words) are likewise defensible as incident-writing practice independent of any
scorer. (2) The failure-verb list in `solution/validate.py` deliberately
EXCLUDES fault-shaped nouns, because listing them would be a hint list in a
different spelling — an anti-leak violation the repo's string tripwire cannot
see. (3) `resource_correct` is reported beside every headline number in
README/CHANGELOG, so a reader can separate "found the right object" from
"described it in words the rubric knows". (4) All of it is disclosed in
`solution/README.md` rather than left for a reader to reverse-engineer: no gate
was weakened and no threshold moved — the code bent, not the gate.

## 2026-08-29 — a `sed` scrub silently corrupted the transcript it was scrubbing

**What happened:** the transcript-export procedure scrubbed home paths and
emails with `sed -i ''`. Applied to a 1,262-line JSONL transcript it left **3
lines unparseable** that were valid in the original — `sed` edits bytes and knows
nothing about JSON string escaping, so a substitution landing next to an escape
sequence produced `Invalid \escape`. The damage was invisible to the secret scan,
which greps text and does not parse JSON: the gate would have gone green on a
corrupted artifact.

**Second finding, same export:** the transcript email rule
(`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`) fired on
`\n@pytest.mark.parametrize`. In the raw bytes an escaped newline before a Python
decorator reads as `n@pytest.mark` — a false positive that will recur in any
exported transcript containing decorated test code, and one that invites muting
the rule, which is how a real address eventually ships.

**Prevention adopted:** (1) scrub JSON structurally, not textually — parse
each line, rewrite only string leaves, re-serialise; the export is then verified
by re-parsing every line and asserting zero malformed before the secret scan is
consulted (the export pipeline has since left this tree; the rule generalizes
to any exported structured artifact). (2) The gate's email pattern now requires the local part not to be
preceded by a backslash or another local-part character, which removes the
escape-sequence class without narrowing what counts as an address; both
directions were proven on a copy — a planted `someone@example.com` still FAILs,
the decorator alone PASSes. (3) General rule for any future scrub: a gate that
greps text cannot certify a structured artifact, so structural validity gets its
own explicit check rather than riding on the text scan being quiet.

## 2026-08-29 — A diagnoser that navigates by reference is blind to a broken reference

**What happened:** the rules-only ablation arm (`ablation/`, built to measure
the "couldn't a decision tree do this?" objection rather than argue about it)
returned **zero findings** on `t2-rbac-sync-forbidden` — not a wrong answer, no
answer at all. Its RBAC analyzer is three steps: grep the pod log for a 403,
read the pod's `serviceAccountName`, find the RoleBinding whose subjects name
that ServiceAccount. Steps one and two succeed. Step three finds nothing,
because the injected fault is that `inventory-reader-binding` binds
`inventory-synk` while the pod runs as `inventory-sync` — one character apart.

**The general shape.** The analyzer's only path from the symptom to the resource
whose spec must change is the ServiceAccount -> RoleBinding reference, and the
fault *is* that reference. **The edge it must traverse is the edge the fault
removed.** Solving the case requires a claim about something absent — "this
binding points at a ServiceAccount that does not exist", or "this
ServiceAccount is bound by nothing" — and pattern matching can only ask
questions about objects that are present. Absence has no signature to match.

This generalises past RBAC and past this repo. Dangling references are a large
share of real Kubernetes incidents (a Service selecting labels no pod carries, a
volume naming a missing ConfigMap, a PVC naming a StorageClass that was
renamed), and they are exactly the class where a static analyzer's traversal
runs out of graph. The tool is quietest precisely where the graph is broken —
and silence reads as health.

**Why it is not a bug to fix.** The obvious repair is a reverse rule: flag any
RoleBinding whose subject ServiceAccount does not exist. It was deliberately NOT
added. The pre-registration
([docs/experiments/2026-08-29-rules-ablation.md](experiments/2026-08-29-rules-ablation.md))
bars tuning after the score is seen, and — the stronger reason — that rule would
only have been written *because the case had already been read*. A rules engine
that grows one analyzer per case it has already failed is a lookup table wearing
a decision tree's clothes, and it measures nothing.

**Evidence:** [evals/results/20260829T101751Z-rules/](../evals/results/20260829T101751Z-rules/summary.md)
— rules 27/36 vs baseline 30/36 vs solution 36/36; this case scores 0 in all
three runs with `verdict=inconclusive` and `analyzers_fired=0`. The solution arm
identifies it in all three runs, by hypothesising about the binding and then
checking, rather than by matching.

**Prevention now in place:** the finding cannot rot into a stale README claim.
`evals/reported.json` pins the ablation bundle, `make verify` re-derives all
three columns offline, and the gate **fails if the ablation ever stops failing
on at least 3 cases**; `tests/test_rules_ablation.py` pins this case by name as
a regression test and blocks the lookup-table failure mode mechanically (no case
id, scenario namespace or fixture workload name may appear in any string literal
under `ablation/`).

## 2026-08-29 — RECURRENCE x2: the privacy gate was blind to half the leak, and describing it trips it

**What happened:** a privacy audit of the exported session transcripts found
the operator's machine username in every one of them, plus private operator
context verbatim in most, while `scripts/checkpoints.sh` reported
`PASS no home paths in target` throughout. Its rule was slash-delimited, and an
agent-session transcript also carries the home path DASH-delimited, as a
sanitized-cwd project key. A slash-form grep over the exports returned 0 on
every file, so nothing was ever flagged. The export procedure's
own `sed` step scrubbed only the slash form — it **manufactured** the exact
spelling its gate could not see.

**Why it is a recurrence, twice over.** (1) The 2026-08-28 entry on `/home/`
segments and the 2026-08-29 entry on sed/JSON corruption both touched this same
scrub and neither caught the dashed form — a partial fix that left the class
open. (2) The same pass re-confirmed the older corruption was never repaired:
10 lines across two transcripts were still unparseable, `jq` aborting partway
through a shipped artifact, while this log already claimed the
"re-parse and assert zero malformed" prevention existed. It existed in neither
`checkpoints.sh` nor the export procedure. **A prevention that is written down
but not mechanised is not a prevention**, and this log said otherwise for a day.

**A third instance, live, while fixing it.** Writing the working-notes item
that *describes* the scrubber failed the commit, because quoting a literal home
prefix is itself a home path; so did a code comment in the new scrubber. That is
the 2026-08-28 "exported transcript tripped the home-path gate by quoting the
gate's own tests" entry recurring in two new files. The gate is right and the
text bends: describe the pattern, never spell it.

**Prevention adopted (mechanised at the time; the export pipeline and its
gates have since left this tree — the lessons are what carry):**
1. The `sed` pipeline was replaced by a structural scrubber: parse each line,
   rewrite string leaves **and dict keys** (session snapshots key file backups
   by absolute path — scrubbing values alone left the leak in the key, caught by
   the scrubber's own post-write check), re-serialise, then re-parse and assert
   zero malformed lines before writing. The username came from `$USER` at run
   time, so no private string was committed into the tooling that removes
   private strings.
2. The gate grew rules proven to FAIL on planted defects before being trusted:
   exports free of dash-form home paths and every transcript line valid JSON.
   A text scan cannot certify a structured artifact, so structural validity got
   its own check rather than riding on the text scan being quiet.
3. The nested-escape trap is real: transcripts embed JSON inside JSON, so one
   `json.loads` leaves a leaf still holding literal escape sequences. The first
   version of the redaction regex cleaned the outer copy and left the nested
   one — found only because the leak check was re-run after the scrub reported
   success. **Verify after the fix, not after the intent.**

## 2026-08-29 — error-as-green: the gate certified a directory that does not exist

**What happened:** `bash scripts/checkpoints.sh --secrets-only --package
/nonexistent-dir-xyz` printed every scan PASS and exited 0. Two composing
defects: `--package` resolved a nonexistent dir to an empty `TARGET` (the
`$(cd ... && pwd)` substitution fails silently under `set -u` without `-e`),
and every scan block read grep's exit status as binary — `if grep; then fail;
else ok` — so rc=2 (*the scan itself errored*) took the same branch as rc=1
(*scanned clean*). A typo'd path at packaging time would have
produced a fully green verification report describing nothing.

**Prevention now in place (both halves proven to fire before being trusted):**
`--package` validates the directory exists and exits 2 loudly; the four
grep-based scan blocks now case on the exit status — 0 fail, 1 ok, >=2 **fail**
with "scan ERRORED — cannot certify". Planted checks: a secret in a package
still FAILs (detection intact through the restructure), a chmod-000 subdir now
FAILs with the ERRORED message where it previously passed green, and both are
regression cases in `tests/test_gates.sh` (36/36). General rule, third
occurrence of the class in this log (sed scrub, dash-form paths, now this): **a
check that did not run is a failure, never a pass** — absence of a finding is
only meaningful when the finder is proven to have looked.
