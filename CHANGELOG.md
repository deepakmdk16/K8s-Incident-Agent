# Improvement Changelog

> One entry per meaningful iteration, written when it happens. Every entry
> connects to the evidence that guided the next decision. Template below —
> copy it for each entry.

<!--
## [N] <short title> — <date, time UTC>

**Change:** what changed in the solution (or agent design).
**Why:** the observation/evidence that motivated it (link: eval run, trace file,
failing test, measurement).
**Evidence after:** measurement showing the effect (link to eval output / test /
trace). Numbers over adjectives.
**Next decision it drove:** what this told us to do next.
-->

## [28] The fix column reaches the held-out set: five gold-v3 files, authored before any arm reads it — 2026-11-11, 09:30 UTC

**Change:** `evals/gold-v3/` gains one file per held-out case
(`t1-reefer-telemetry-offline`, `t2-route-planning-degraded`,
`t2-customs-filing-timeouts`, `t2-yard-gate-release-blocked`,
`t3-driver-login-failures`), each authored from the case's `fault.yaml`,
`notes.md` and `remediation_summary` under the tree's authoring rule, with
source lines cited. One value is open (case 1, as its frozen analog); four are
pinned, following the frozen precedents for a typo'd Service name, a selector
label and an RBAC subject, plus a policy enum for the Namespace's enforce
label with its documented removal mitigation. The instrument (v3.1) is
unchanged. `tests/test_scoring_v3.py` now takes its scenario roots from
`evals/run_eval.py` instead of a local two-root tuple, so the "one gold file
per case" test covers every root the harness scores; count 15 → 20, open set
gains case 1.

**Why:** STATUS 4.2. CHANGELOG [24] named v3.1 the instrument for the held-out
set, but the gold tree was pinned to the 15 older cases, so the fix column
could not be read there; the files had to exist before any LLM arm reads the
set, or they would be authored with its output in view. The test that should
have caught the gap had its own copy of the root list — the same drift PR #9's
review fixed in the other tests.

**Evidence after:** with the five files moved out, the gold-tree test fails
(`1 failed`); with them, `tests/test_scoring_v3.py` `53 passed`. Every
committed bundle's `scoring_v3.json` still matches a fresh run (no earlier gold
file changed). The matcher was checked on the new files: on case 4 the
quoted-bracket, dotted and whole-labels spellings match and an annotation or
container path does not; on case 2 the short and long DNS forms with port 7000
match, the typo'd host and a host without the port do not. Two limitations are
disclosed in case 4's file rather than fixed: a JSON-pointer `~1` spelling
misses, and the pooled sets accept `metadata.labels` with a bare `baseline`.
A third fact was found while writing the prediction and is pre-registered,
not fixed: case 4's pinned value `baseline` appears in no output any tool
serves (only in `cluster/namespaces.json`), so a value hit there is a guess or
the removal mitigation, never a read; cases 2, 3 and 5's values are each
visible in a served read.

**Next decision it drove:** the held-out design doc pre-registers the
solution arm's fix column, about 12/15 `fix_path_correct`
([docs/experiments/2026-11-07-heldout-set.md](docs/experiments/2026-11-07-heldout-set.md),
amendment 2026-11-11). STATUS 3.2 is now ready to score with every instrument
in place; the run itself stays deferred under the scoring policy.

## [27] V4 tells "not served" from "absent": an unserved kind can be named, never confirmed — 2026-11-09, 12:40 UTC

**Change:** `solution/validate.py` V4 EXISTS consults the snapshot only for a
kind a read tool serves. For any other kind — a custom resource, a Namespace,
every cluster kind `get_object` does not serve — V4 says nothing, and V7 adds
an unmet condition naming the limit ("no read tool serves <kind> …
'confirmed' is out of reach"), so the submission is accepted at `probable` or
`inconclusive` as proposed and refused at `confirmed`. `solution.tools.serves_kind`
answers False for a kind no reader knows instead of raising, so the validator
and the hedging report ask it of whatever kind the agent submits
(`evals/hedging.py` loses its try/except for the same reason). **Gate change,
stated:** the 2026-11-07 rule "refuse an absent name of an unserved kind,
without listing" is gone — it was the one-bit form of the 2026-09-05 oracle,
telling the agent whether an object exists for a kind it cannot list.
`tests/test_solution_validate.py::test_v4_does_not_consult_the_snapshot_for_a_kind_no_tool_serves`
replaces the test that pinned it; the adversarial sweep's V4 control on the
CRD gold becomes family (u) on every unserved target. A kind neither the
readers nor the snapshot's CRD roster knows (`fixture.custom_kinds`, capture
schema 3) is still refused as before, with the fixed built-in roster: that is
a misspelling, not a custom resource (review of PR #10).

**Why:** the held-out set's decision rule (CHANGELOG [25];
[docs/experiments/2026-11-07-heldout-set.md](docs/experiments/2026-11-07-heldout-set.md),
amendment 2026-11-08): V4 resolved the submitted kind through
`fixture.load_kind`, which raises for a kind no tool knows, so the solution arm
could not submit the CRD case's gold object at any verdict — its prediction for
that case was 0/3 by construction, and opening the object universe (roadmap
2.7) could not move the row because the gate refused the answer before any
tool was consulted. The decision rule fixed on 2026-11-08 asked for exactly
this distinction, the same not-served discipline [16] gave V1, V6 and V7.

**Evidence after:** tests written first; they failed in the session before
the change (tests and fix landed in one commit, so only the after-state
reproduces from the tree): `747 passed` on the whole suite; ruff clean,
pyright strict 0 errors. Frozen replay
[tests/test_frozen_replay.py](tests/test_frozen_replay.py) and perturbed replay
[tests/test_perturb_replay.py](tests/test_perturb_replay.py) both still 36/36
`confirmed` (no frozen row names an unserved kind: the hedging table's
"confirmed: coverage 36/36" readable). Family (u) in
[tests/test_solution_validate_adversarial.py](tests/test_solution_validate_adversarial.py):
3 attempts on the 2 unserved targets — bare `confirmed` on the CRD gold and on
the pod-security Namespace, and `confirmed` with a real optimizer log line
naming `weekday-routing` cited as the defect — all three refused with the cap,
`verdict_allowed` `probable`; the sweep's `e-guessed-name` attempt on the
Namespace is now refused by the cap instead of by V4 (its names were listable
through `list_namespaces`, so nothing was leaked there before either). Unit
tests in [tests/test_solution_validate.py](tests/test_solution_validate.py):
an honest submission naming `routeprofile/dispatch/weekday-routing` on its own
fixture is refused at `confirmed` with one violation (VERDICT, naming the
limit) and accepted at `probable`; a misspelled kind (`deploymnet`) is refused
by V4 with the built-in roster; and the cap-alone case — on the pod-security
fixture, a submission naming the Namespace with a `get_events` line naming it
as the defect and two PRESENT checks meets every other V7 condition, so the
cap is the only unmet item and without it the verdict would have been
`confirmed`. Review round on PR #10 (5 findings) folded in before merge: the
roster guard, the cap-alone test, the citation and wording fixes.

**Next decision it drove:** the held-out design doc's solution prediction for
case 2 is restored to the original (`resource_correct` 2/3, verdicts
`probable`/`inconclusive`, confirmed-wrong 0) with the mechanism now
mechanical; case 4 unchanged and mechanical for the same reason
([docs/experiments/2026-11-07-heldout-set.md](docs/experiments/2026-11-07-heldout-set.md),
amendment 2026-11-09). STATUS 3.2 is now ready to score once 4.2 (gold-v3
for the five cases) is decided; CRD discovery (roadmap 2.7) is what would
turn case 2's `probable` into `confirmed`.

## [26] Review round on the held-out slice: three scorer-instrument amendments, two gate changes, an honest sweep — 2026-11-08, 05:37 UTC

**Change:** nine review findings on PR #9, applied before any LLM arm reads
the set. Three are **instrument** changes to `evals/scoring_v2.py`, each a
dated amendment in
[docs/experiments/2026-11-07-heldout-set.md](docs/experiments/2026-11-07-heldout-set.md):
(1) for a cluster-scoped kind the answer's namespace field is ignored — kind +
name decide (`{Namespace, yard-ops, yard-ops}` scored resource-wrong on the
pod-security case before: the paged namespace in the field, a trap set by the
rubric's "no namespace" spellings); (2) the `pod-security-violation` signature
loses `security standards?` and `enforc`, neither specific to the plugin — a
webhook that "enforces the company's security standards and rejects every pod
create" co-matched it; (3) a spelling rule for kinds the alias table does not
know (lowercase, drop `.group`, drop a trailing `s`) replaces the hard-coded
`routeprofile` aliases. Two **gate** changes in `scripts/checkpoints.sh`,
stated in their commits: the scenario roots are read from
`evals.run_eval.SCENARIO_ROOTS` at run time instead of a hand-copied list (the
Python tests import it too), and the schema-2/3 roster checks require a real
`.items` list, so `capture.sh`'s in-band `{"capture_error": true}` object no
longer passes as a roster. The adversarial sweep makes exactly one attempt on
the CRD gold — the honest control, which must earn V4's "does not resolve"
refusal — instead of overwriting every attack's expected refusal with that
string, and its docstring states the coverage (anchoring families on 19
fixtures plus the 20th's wrong target; V4 control on the CRD gold);
`test_each_root_is_scored_by_its_own_scorer` no longer skips a missing root.
`evals/inject.sh` waits for scenario-labelled CRDs to be Established after
`setup.yaml` and dies on timeout — untested live (the authoring cluster is
gone); `bash -n` and shellcheck clean. Disclosed, not fixed: the capture scrub
picks the Secret path by the kind in its own arguments, so a custom resource
embedding credentials would be captured under the general scrub; the
`fixtures free of k8s credential material` gate is the control
(`evals/scenarios-heldout/README.md`, design doc).

**Why:** a code review of the slice. Each scorer finding is the CLAUDE.md
invariant in miniature — a pooled number that can move for a reason the
rubric's vocabulary controls rather than the arm's behaviour — and it is
cheaper to close those before any LLM row exists than to decompose them
afterwards. The gate findings are error-as-green shapes (docs/failure-modes.md
2026-08-29): a hand-copied list that drifts silently, a check that accepts an
error object as evidence.

**Evidence after:** rules bundle
[evals/results/20261108T050338Z-rules/](evals/results/20261108T050338Z-rules/summary.md)
re-scored offline through the changed scorer (the committed `answer.json`
files against the committed gold; the arm was not re-run): **0/5 rows moved**
— `resource_correct` 2/5, `class_correct` 3/5, `matched_classes` identical on
every row — so the [25] table stands as recorded. Against the pre-change
module the three new assertions fail (`resource_correct` False; classes
`{pod-security-violation, webhook-admission-block}`; canonical kind
`routeprofile.v1.fleet.northbound.io`). Regression tests:
`tests/test_scoring_v2.py` (the yard-gold namespace-field test, the webhook
"security standards" sentence, the alias-table parametrization),
`tests/test_gates.sh` 18d4 / 18d2b / 18d2c, the sweep's
`v4-control-unknown-kind`. Gate at the last commit: `743 passed`,
`== checkpoints: 0 failure(s)`; `test_gates: 50 passed, 0 failed`.

**Next decision it drove:** unchanged from [25] — the first paid read of the
set waits on V4 learning to tell not-served from absent, and on the operator's
scoring call — but it now happens through an instrument whose three known
vocabulary traps are closed before any LLM row exists, so a miss on case 2 or 4
will be read as the arm's, not the rubric's. The capture scrub learns kinds
from the CRD roster when a case first needs a credential-bearing custom
resource; until then the authoring rule in the README holds.

## [25] Held-out set: five cases nobody tuned on, two causes outside the served universe, the control arm scored — 2026-11-08, 05:05 UTC

**Change:** `evals/scenarios-heldout/`, an additive root under the v2 contract
in a company the arms have never seen (freight logistics / fleet telematics;
no namespace, workload, container, Service, ConfigMap, Secret or env name
shared with the frozen 12, the v2 cases or either noise pack — 133 names
checked against 152). Five cases, each captured on the pinned v1.37.0 kind
cluster after the rule-6 rehearsal recorded in its `notes.md`:
`t1-reefer-telemetry-offline` (T1, pod-unschedulable),
`t2-route-planning-degraded` (T2, app-crashloop, gold a **RouteProfile custom
resource** no tool knows), `t2-customs-filing-timeouts` (T2,
service-selector-mismatch, zero not-ready rows), `t2-yard-gate-release-blocked`
(T2 two-phase, **pod-security-violation**, gold the paged **Namespace**, served
by no tool) and `t3-driver-login-failures` (T3 under a renamed noise pack with
two decoys, rbac-denial, a loud crash-looping decoy in the paged namespace).
Pipeline: capture schema 3 (`cluster/customresourcedefinitions.json`, custom
resources under `ns/<ns>/crs/`), CRDs owned by `inject.sh` like webhook
configurations, one new v2 class with signature tests, custom-kind aliases,
`run_eval`, `checkpoints.sh` and the adversarial sweep know the root. Design,
roster and per-arm predictions were committed before any capture:
[docs/experiments/2026-11-07-heldout-set.md](docs/experiments/2026-11-07-heldout-set.md).

**Why:** STATUS 3.2 — the frozen set is saturated and every later case was
built in the same world by the same hands; a score that only holds on the
names it was tuned on is a score about the names. [22] answered the offline
half (the gate accepts all 36 rows under a rename); this set is the live
half, scored once per arm and never tuned against.

**Evidence after:** rules arm, one run, free
([evals/results/20261108T050338Z-rules/summary.md](evals/results/20261108T050338Z-rules/summary.md)): pooled **2/5** (T1 1/1, T2 1/3,
T3 0/1), `resource_correct` **2/5**, `class_correct` **3/5** — all exactly as
pre-registered; verdicts confirmed ×2 / probable ×2 / inconclusive ×1 and
confirmed-wrong **1** against a predicted confirmed ×5 / 3. Per row, before the
pooled number: the two-replica cases (1, 2) came back `probable` because the
ablation counts one finding per pod and never deduplicates by object, and the
T3 decoy — captured as `terminated/Error`, the only state this kubelet ever
showed for a short-lived container — left the `CrashLoopBackOff`-keyed analyzer
silent, so case 5 is `inconclusive` rather than a confident wrong name (both
disclosed in the design doc before the run). The LLM arms are not run (STATUS
scoring policy); their predictions stand. Found before any arm ran and
disclosed as an amendment: `solution/validate.py` V4 reads a kind no reader
knows as "does not exist", so the solution arm cannot submit the CRD case's
gold object at any verdict — that row's prediction is revised to 0/3 by
construction, confirmed-wrong 0, and the behaviour is pinned in the
adversarial sweep. Gate at the last commit: `698 passed`,
`== checkpoints: 0 failure(s)`.

**Next decision it drove:** the first paid read of this set waits on two
things, in order — V4 learning to tell "no read tool serves this kind" from
"absent", without which case 2 cannot be answered by the solution arm at all,
and the operator's scoring-policy call. Two control-arm properties are logged
for a disclosed method update, not made: findings deduplicated by object
before the verdict rule, and a crash-loop signature that reads restart
evidence rather than one kubelet's status string.

## [24] scoring_v3 → v3.1: the bare-bracket reading rule, decided for the instrument and disclosed — 2026-11-07, 18:11 UTC

**Change:** `evals/scoring_v3.py` gains `path_readings`: after normalization, a
bracket that is neither a numeric index nor `key=value` is read both as
`[name=x]` (a list element's name) and as `.x` (a map key), and a submitted path
matches if any of its 2^k readings is an accepted gold form. The gold tree is
unchanged; `solution/prompts.py` is unchanged — the instrument moved, not the
agent, and not both. The four committed `scoring_v3.json` files are regenerated
from v3.1; the twenty v3.0 misses stay in
[tests/test_scoring_v3.py](tests/test_scoring_v3.py) as the regression corpus
(each must still fail the literal match and pass through a reading, so the
change is the rule and not a gold edit). Decision, reasons, the alternative
rejected and the post-hoc caveat:
[docs/experiments/2026-11-07-scoring-v3.md](docs/experiments/2026-11-07-scoring-v3.md),
"v3.1"; [evals/gold-v3/README.md](evals/gold-v3/README.md) "Matching" updated.
In the same round
[docs/experiments/2026-09-04-webhook-outage.md](docs/experiments/2026-09-04-webhook-outage.md)
records the STATUS 1.1 decision: Stage 1 is retired un-run, because the oracle
its three live runs could have detected is now hunted offline over every
fixture by the sweep ([21], [23]) and on the live run by Stage 2's tracing
clause.

**Why:** STATUS 2.4, from [20]: 20 of 20 path misses were one spelling.
Kubernetes itself spells list elements by index and merges them by `name`;
`[name=x]` is this gold tree's convention, so a scorer recognizing only it
measured the convention. The agent-side fix would have been a rubric-shaped
spelling rule derived from the instrument's own misses (solution/README.md,
disclosure 1) and would need a paid run to verify.

**Evidence after:** frozen bundle `fix_path_correct` **36/36** (v3.0: 16/36),
`both_correct` **21/21** (v3.0: 12/21), `fix_value_correct` 21/21 unchanged —
post-hoc on this bundle by construction, the number having been known from
[20]'s sensitivity reading before the rule was written. Later bundles
unchanged: crossns 3/3, decoys 3/3, webhook 0/3 (run 3 is a gold alias
followed by prose, which no reading rule touches).
`test_the_twenty_v3_0_misses_are_path_correct_only_through_a_v3_1_reading`;
`test_committed_scoring_v3_json_matches_a_fresh_run` on all four bundles.

**Next decision it drove:** v3.1 is the pre-registered instrument for the Stage
2 webhook run and for the held-out set (STATUS 3.2); a v3.1 miss there is a
real miss. No v3 number enters `evals/reported.json`.

## [23] Review round on the integration branch: V7 anchoring closed at the rule, four fix-ups, three findings declined — 2026-11-07, 18:11 UTC

**Change:** from a code review of PR #8 (nine findings, two reproduced as
crashes). Adopted: (1) `solution/validate.py` — for `get_object` and `describe`
the kind argument is the kind the output is about, so a read of another kind
never anchors V7's defect item or its PRESENT check, whatever its text repeats;
`get_logs` is a pod read and anchors only what a pod read may (the
pod→controller allowance stands). [21]'s namesake rule is now a special case of
this. (2) `evals/perturb.py` — the layout vocabulary a rename must not touch
now includes every JSON key and describe label in the files outside the user
namespaces (`webhooks`, `spec`, `Failure Policy:`), so a user name equal to one
is kept and reported instead of renamed as text, which would have taken the key
with it. (3) `solution/fixture.py` — `describe` matches a webhook
configuration's name case-insensitively, as `get_object` already did. (4)
`evals/scoring_v3.py` — an errored bundle row (no `class_correct`, by
`Outcome.row`'s design) scores as a no-submission miss instead of crashing the
column; a missing transcript for an accepted row is an error, not an honest
miss. Plus one duplicated zip in V7 computed once. Declined, with reasons in the
review notes: reading `.`, `/`, `$` as not the whole object (they are the
JSONPath and JSON-pointer roots, and the pre-registered rule says empty →
`<object>`); guarding `find_consumers` against a fixture with no capture ledger
(a ledger-less fixture is broken by `capture_schema`'s contract, `capture.sh`
always writes one, every committed fixture has one); an O(n²) regex build in
`perturb` (fixtures hold tens of names).

**Why:** STATUS 0.2 asks for review before merge; this was the first
independent read of the six slices as one tree, and the anchoring finding is
the third instance of one leak (docs/failure-modes.md, 2026-11-07, "RECURRENCE
x3").

**Evidence after:** [tests/test_frozen_replay.py](tests/test_frozen_replay.py)
**36/36** and [tests/test_perturb_replay.py](tests/test_perturb_replay.py)
**36/36** unchanged under the tightened rule — no frozen row moved. The sweep
gains the family `_cross_kind_pod_attacks` (**786 → 792** attempts, all
refused) and the pinned case
`test_a_real_read_of_a_pod_named_after_the_service_does_not_anchor_v7`, which
earns `confirmed` on the pre-review tree. A regression test per fix-up in
[tests/test_perturb.py](tests/test_perturb.py),
[tests/test_solution_tools.py](tests/test_solution_tools.py) and
[tests/test_scoring_v3.py](tests/test_scoring_v3.py). Gate on the branch:
`622 passed in 4.97s`, ruff and format clean, pyright strict 0 errors, evals
re-derived 36/36 vs 30/36 vs 27/36, `== checkpoints: 0 failure(s)`.

**Next decision it drove:** the anchoring rule is now stated per tool rather
than per quote; the remaining quote-branch tools (`get_events`,
`namespace_overview`, `find_consumers`, `cluster_capacity`) carry no kind and
are judged by their text — the next sweep family, if one is needed, is a text
that embeds the name through one of those.

## [22] Isomorphic perturbation: the gate accepts all 36 frozen rows under a consistent rename — 2026-11-07, 14:50 UTC

**Change:** `evals/perturb.py` renames every user namespace and object name in a
captured fixture and its gold — consistently across every file and path,
same-length consonant-only DNS labels, derived names (pods, ReplicaSets, claims,
a one-letter typo decoy) following their stem, system namespaces, nodes, kinds
and the capture layout untouched, deterministic per seed, the map written to
`<out>/rename-map.json`, never in place. `tests/test_perturb_replay.py` replays
the 36 accepted frozen submissions on a renamed copy of each fixture (the same
map applied to the recorded tool arguments and the submission) and requires
`confirmed` from today's validator. `tests/test_perturb.py` holds the round-trip
properties; `tests/replay_support.py` is the one replay helper both the frozen
and the perturbed replay now use.

**Why:** STATUS 3.3 — a grader sensitive to the specific names it was tuned on
is a grader that can be gamed. This is the free, offline half of that test: no
cluster, no model call.

**Evidence after:** [tests/test_perturb_replay.py](tests/test_perturb_replay.py)
**36/36** accepted as `confirmed` (37 tests, 3.3 s);
[tests/test_frozen_replay.py](tests/test_frozen_replay.py) still 36/36 on the
originals. The first run refused **1/36** (`run3/t3-overlapping-config-and-oom`,
`V1 QUOTE … not present in the output of find_consumers`): `render_consumers`
re-sorted a name list the capture already had in API order, so a verbatim quote
of that trailer depended on how the names were spelled. Fixed in
`solution/tools.py` — the three name listings keep capture order, no validator
change (3,539 of 3,539 captured lists were already in name order, so the sort
did nothing on real data); regression test with a planted out-of-order capture
in [tests/test_solution_tools.py](tests/test_solution_tools.py); entry in
[docs/failure-modes.md](docs/failure-modes.md) 2026-11-07. The rule was kept
through the merge with [19]'s new cluster-wide listing.

**Next decision it drove:** the validator's acceptance is invariant under
renaming on the frozen set; whether the *agents* are is the live question for
the held-out set (STATUS 3.2). Anything a renderer computes from names — a
sort, a dedup, a tie-break — is now a known class of hidden name-dependence to
check before the next tool is added.

## [21] The validator gets an adversarial sweep over every fixture — and it found two more oracles on its first run — 2026-11-07, 14:48 UTC

**Change:** `tests/test_solution_validate_adversarial.py` (new, inside the pytest
gate) and two minimal tightenings in `solution/validate.py`. For each of the 15
fixtures, for the gold failing resource and a plausible wrong object in the paged
namespace, a generator builds 25–28 attempts per target (**786** in all) that try
to earn `confirmed` using only error strings as quotes, empty results as
evidence, undeclared argument keys, same-name different-kind reads, and the
validator's own rejection text fed back. Each attempt must be refused by a named
property; two controls prove the same scaffold earns `confirmed` with an honest
read and `probable` with none. Every rejection is then checked against the oracle
property: it may contain no case object name absent from the ledger's outputs,
the arguments typed and the submission (lab furniture present in every fixture is
excluded mechanically). Fixes: (1) `_asked_for_a_namesake` — a call that asked by
exact name for an object of another kind anchors nothing under V7, whatever its
output says; (2) V4 lists only names a read already in the ledger has returned,
else says so and tells the agent to list the kind first.

**Why:** [16] closed four oracles found by hand and stated that anchoring needs a
real result "about the failing kind" — but the quote branch never checked the
kind, so `describe deployment/catalog` ("Name: catalog") earned `confirmed` for
`service/catalog` without the Service being read: 15 attempts across 9
fixture/target pairs, every Service↔Deployment/Endpoints pair that shares a name.
And V4's "Present:" list, kept for served kinds because the agent "could have
listed them", corrected a guessed `rolebinding` / `resourcequota` name to one the
ledger had never shown. A reward has to be attacked by a generator over every
case before anything optimizes against it; the hand-found shapes were not the
whole family.

**Evidence after:** first run **34 passed / 42 failed**. After the fixes:
[tests/test_solution_validate_adversarial.py](tests/test_solution_validate_adversarial.py)
**78 passed in 1.08 s**; [tests/test_frozen_replay.py](tests/test_frozen_replay.py)
still accepts all **36 frozen rows** (the namesake pattern occurred there only as
a secondary verification check beside a same-kind read, so no headline row could
move). Failure mode logged in [docs/failure-modes.md](docs/failure-modes.md)
2026-11-07. Merging with [19] re-aimed one test: a served webhook kind's names
are listed by V4 only once a read has returned them, and the honest-read control
omits the namespace argument for a cluster-scoped kind.

**Next decision it drove:** the gate now has a standing adversarial sweep — new
attack shapes go into the generator, not into one-off tests. One observation to
carry, not a leak: a mechanism made only of the V5 rejection messages satisfies
V5b–d, so any strengthening of V5 must not read the gate's own prose as
compliance.

## [20] A column that scores the fix, not the sentence — and its first miss was the ruler — 2026-11-07, 14:40 UTC

**Change:** `evals/scoring_v3.py` and `evals/gold-v3/` (15 files plus README): an
additive, solution-only diagnostic column comparing the submitted
`remediation.field_path` / `required_value` with a v3 gold authored from each
case's `fault.yaml`, `notes.md` and `remediation_summary` (source lines cited per
file), reported beside the frozen class match. Nothing frozen moves: `gold.json`,
`scoring.py`, `scoring_v2.py`, the case set and every committed bundle file are
byte-identical; v1's `root_cause_correct` is read from `rows.jsonl`, never
recomputed. Arms whose contract lacks the fields report `n/a`, never 0. Ten
cases pin a value; five are open (sizes, counts, image refs, URLs) and score the
path only. Per-row raw strings land as a new `scoring_v3.json` beside each
scored bundle.

**Why:** twice (docs/failure-modes.md 2026-08-29, 2026-09-04) a pooled number
moved for reasons the sentence rubric's vocabulary controlled. The submission
already names the fix; nothing measured it (STATUS 3.1).

**Evidence after:** pre-registered in
[docs/experiments/2026-11-07-scoring-v3.md](docs/experiments/2026-11-07-scoring-v3.md)
(prediction committed before the run, instrument frozen at that commit). Frozen
bundle `20260829T090941Z-solution`: `fix_path_correct` **16/36** (T1 5/15, T2
8/15, T3 3/6) against a prediction of ≥ 30/36 — **prediction wrong**;
`fix_value_correct` **21/21** on value-pinned rows; both 12/21. All 20 misses are
one spelling: bare element names in brackets (`containers[storefront].image`,
`spec.hard[pods]`) which the pre-registered normalizer leaves as written; 0 value
misses, 0 missing submissions. Later bundles: crossns 3/3, decoys 3/3,
checkout-release-stalled 0/3 (two salvaged rows, one gold alias followed by
prose). Replay pinned by [tests/test_scoring_v3.py](tests/test_scoring_v3.py)
(46 tests, including the exact miss set and committed `scoring_v3.json` equal to
a fresh run).

**Next decision it drove:** the bare-bracket spelling rule is logged as a
candidate change (post-hoc sensitivity 36/36 if applied) for a later disclosed
slice, not applied here. Where the scenario pins a value the agent named it
correctly in 27/27 scored rows; the open-value cases' `required_value` strings
are prose with examples, which argues for keeping them open rather than
inventing a pinned number.

## [19] The webhook kinds served: the ceiling moves from the tool layer to the run — 2026-11-07, 14:35 UTC

**Change:** `solution/tools.py`, `solution/fixture.py` — reach, not the gate.
`get_object` and `describe` serve `validatingwebhookconfigurations` and
`mutatingwebhookconfigurations` (cluster-scoped: no namespace argument, a
supplied one refused as an ordinary error; the read tagged namespace `""` so V2
admits it by construction; `describe` from the per-object capture under
`cluster/describe/`; the projection keeps each webhook's name, `clientConfig`
target, `failurePolicy`, `matchPolicy`, `sideEffects`, `timeoutSeconds`, `rules`
and selectors). `find_consumers` on a Service reports every configuration whose
`clientConfig.service` names it. `serves_kind` is true for exactly these two
cluster kinds and for nothing else cluster-scoped; a schema-1 fixture reports
them as not captured, never as empty. Tool descriptions name the kinds;
`solution/README.md` disclosures 5 and 7 restated.

**Why:** [15] measured the ceiling (no arm read the object; the solution's 3/3
`resource_correct` was a validator leak) and [16] closed the leaks without adding
reach. The experiment's decision rule names this as the next slice: serve the
kinds, tagged cluster-wide, plus the webhook → Service edge.

**Evidence after:** offline only so far.
[tests/test_solution_tools.py](tests/test_solution_tools.py) (both kinds listed,
read and described on `t2-checkout-release-stalled`; not-captured on a frozen
fixture; namespace argument refused; tag `""`; the Service edge; no other cluster
kind served on the webhook fixture; descriptions name the kinds),
[tests/test_solution_validate.py](tests/test_solution_validate.py)
(`test_a_real_read_of_the_configuration_earns_confirmed_honestly` — an honest
submission citing a real `get_object` read is accepted as `confirmed` with zero
violations; the two oracle tests re-aimed at `clusterroles` still hold).
[tests/test_frozen_replay.py](tests/test_frozen_replay.py): 36/36 frozen rows
still accepted; namespaced-kind output strings byte-identical.

**Next decision it drove:** the scored re-run, pre-registered as "Stage 2" in
[docs/experiments/2026-09-04-webhook-outage.md](docs/experiments/2026-09-04-webhook-outage.md):
3 runs, prediction 3/3 `confirmed`, `resource_correct` 3/3 with the name first
appearing in a tool result, confirmed-wrong 0, ~7 turns and ~$0.18 per case;
anything less is read per row from `rows.jsonl` before a number is quoted.
Result: not yet run (scoring policy, STATUS).

## [18] Priced the hedging: per-verdict coverage and accuracy, and where `inconclusive` lands — 2026-11-07, 14:25 UTC

**Change:** `evals/hedging.py` (new), `evals/run_eval.py`, `evals/verify_reported.py`.
Every scored run and the offline verify gate now price the arms' self-reported
calibration instead of pooling it away: for each verdict value present, its row
count and share of all rows (coverage) and the root-cause rate among those rows
(accuracy), with rows carrying no verdict in an explicit `none` bucket; and the
`inconclusive` rows split by whether the solution's tool layer can read the gold
failing-resource kind (`solution.tools.serves_kind`, applied to every arm's rows,
so the arms are read on one footing). `summary.json` gains a top-level `hedging`
key (nothing renamed or removed), `summary.md` a pooled section, and
`verify_reported` a cross-arm table after the existing one. Report only: no bar,
`check_bar` and `evals/reported.json` untouched — a bar needs a pre-registered
threshold and a second scoring. No committed bundle was rewritten; the verify
step re-derives the numbers from the committed answers.

**Why:** a pooled root-cause rate cannot distinguish an arm that earns its number
from one that buys it with hedges, and `inconclusive` means something different
on an object the tools could read than on one no tool serves ([15], [16]).

**Evidence after:** re-derived from the reported bundles by `bash scripts/checkpoints.sh`
(evals step): rules — `confirmed` 18/36 rows (accuracy 15/18, 83.3%), `probable`
15/36 (12/15, 80.0%), `inconclusive` 3/36 (0/3), all three on
`t2-rbac-sync-forbidden`, a RoleBinding the tools read (3 readable, 0 unreadable);
baseline — `confirmed` 33/36 (30/33, 90.9%), `probable` 3/36 (0/3), no
`inconclusive`; solution — `confirmed` 36/36 (36/36), no hedge of any kind.
Tests: [tests/test_run_eval.py](tests/test_run_eval.py) (per-verdict table
including the `none` bucket and a zero-correct verdict; the readability split on
readable, cluster-scoped and unknown kinds; existing summary and row keys
unchanged) and [tests/test_verify_reported.py](tests/test_verify_reported.py)
(an all-hedged solution still passes `check_bar` unchanged; the reported bundles'
per-verdict numbers; the cross-arm rows). Gate on the slice: `408 passed`,
ruff/format/pyright (strict) clean, `== checkpoints: 0 failure(s)`.

**Next decision it drove:** the committed bundles hold no hedge on an unreadable
kind and the solution reports none at all, so a hedging bar has nothing to
calibrate against yet; revisit once a v2/v3 scored run produces inconclusives,
and pre-register the threshold before that run (STATUS 2.3 closes as report-only).

## [17] Two documented soft spots in the gate closed; the frozen replay still accepts 36/36 — 2026-11-07, 14:20 UTC

**Change:** `solution/validate.py` — the verify-before-assert gate, no new reach.
(a) V7: a controller named as the failing resource (Deployment, StatefulSet,
DaemonSet, Job, CronJob, ReplicaSet) now needs one verified read of the object
itself — `get_object` or `describe` of that kind and name, with real cluster state
in the result — as a cited quote or a PRESENT check. The pod→controller allowance
from [16] stays, but conditional on it: pod names carry the controller's name, so
on its own the allowance let `confirmed` be earned for a Deployment whose spec
nobody opened. (b) V2: an event citation now rests on the namespaces its quoted
lines name (`fixture.namespaces_named_in`, exact DNS-label tokens); a quote that
names none keeps the read's own tag. This is the tightening disclosure 7 of
`solution/README.md` pre-specified; disclosure 7 and "What confirmed mechanically
requires" now state both rules. Side fix, stated explicitly as a gate change
(commit `fix(gates): home-path scan skips a worktree's .git pointer file`, PR #7):
`scripts/checkpoints.sh` excludes the worktree `.git` pointer file by name — its
single `gitdir:` line is an absolute home path, so the scan and the pre-commit
hook failed in every git worktree on git's own untracked metadata;
`tests/test_gates.sh` 8b now plants one and requires a pass.

**Why:** both spots were written down as known ([16]; disclosure 7) and both were
reproduced before the change: a pod-only Deployment submission on
`t1-crashloop-missing-env` (defect citation and checks all `describe pod` /
`get_logs`, the Deployment never read) earned `confirmed` with zero violations,
and on `t3-crossns-decoys` a cluster-wide event line from `release-canary` was
admissible under V2 with `storefront` paged. Rules were adopted only because the
frozen replay held.

**Evidence after:** [tests/test_frozen_replay.py](tests/test_frozen_replay.py)
**36/36** still `confirmed` with zero violations under both rules. Seven
regression tests in [tests/test_solution_validate.py](tests/test_solution_validate.py)
(section "the two documented soft spots"), three of which fail against the
previous gate. Suite 402 → 409 on the slice; `== checkpoints: 0 failure(s)`.
Measured and not adopted, as a fact about the frozen bundle rather than a reason
to change it: removing the pod→controller allowance outright would move **4**
frozen rows (`run1/t3-overlapping-config-and-oom`, `run2/t1-crashloop-missing-env`,
`run2/t1-oom-cache-warmup`, `run3/t1-crashloop-missing-env` — defect anchored only
through a pod read); requiring the controller read among evidence citations
rather than checks would move **2** (`run2/t1-crashloop-missing-env`,
`run2/t1-oom-cache-warmup`, which supply it only as a PRESENT check).

**Next decision it drove:** the anchoring rules are now exhausted of the two
known substitutions (other-kind-same-name, pod-for-controller) without moving the
headline. The remaining way to earn `confirmed` on an object without reading it
is a non-controller quote that names it — the RBAC rows rely on `find_consumers`
output naming the RoleBinding — which is a measured limit, not a next tightening.
Next: the adversarial suite (STATUS 2.1) makes these refusals a standing property
of the gate rather than seven hand-written cases.

## [16] The gate had four oracles; a red team found them, a replay of 36 frozen rows proves the fix moved nothing — 2026-09-05, 08:40 UTC

**Change:** `solution/validate.py`, `solution/tools.py`, `solution/fixture.py`,
`solution/agent.py` — the verify-before-assert gate, no new reach. [15] showed
the solution naming an object it could not read because V4's rejection listed
the names present. Closing that, and having the closure reviewed and then
attacked, found three more ways to earn `confirmed` on an unreadable object,
each reproduced end to end against the webhook fixture before it was closed:

1. **V4 listed names** for a kind no tool serves → it now lists names only for
   kinds `get_object` can list (`tools.serves_kind`).
2. **Tool-limitation errors were evidence.** `get_object`/`describe` on a
   cluster-scoped kind, an unknown kind, an uncaptured kind — and, found in
   review, `find_consumers`, whose "kinds that exist" trailer read the cluster
   file and enumerated every webhook configuration by name — all returned
   strings that re-verified under V1 and named the object. They now raise
   `fixture.NotServedError`, render with a distinct prefix the model sees
   ("not served by this snapshot; says nothing about the cluster"), are flagged
   `is_error`, and are refused as quotes (V1), as defects (V7) and as PRESENT
   checks (V6). Cluster-state errors — `no namespace 'platform-policy'`, `no
   configmap named x` — stay citable: a missing referent is often the defect.
3. **V7 anchored on the name alone.** A served tool asked about a *different*
   kind carrying the failing object's name ("describe configmap/<name>",
   `get_events involved_name=<name>` → "0 events matched") counted as a read of
   it. The two anchoring rules now require a real result (not an error, not an
   empty echo of the arguments) about the failing kind — with exactly one
   relationship allowed besides same-kind, a pod read anchoring its controller,
   because that is what three frozen accepted submissions rely on.
4. **An undeclared argument key rode along.** Found by the second, adversarial
   pass on the fix: `dispatch` ignored keys a tool does not declare, so
   `get_events {…, name: workload-standards}` — or even `list_namespaces {name:
   …}` — re-executed to real cluster state while "naming" the object; eight
   variants earned `confirmed` with zero violations. `dispatch` now refuses
   undeclared keys as not-served, and the anchoring test reads only the keys
   that identify an object (`name`, `pod`, `involved_name`).

**Why:** [15]'s calibration claim ("confirmed-wrong 0 on an unreadable
object") rested on the agent not having tried these paths, not on the gate
refusing them. A gate that can be satisfied by an error string, an empty result
or a typed-in argument is not measuring what it says.

**Evidence after:** [tests/test_frozen_replay.py](tests/test_frozen_replay.py)
rebuilds each of the **36 frozen accepted submissions**' ledgers from their
transcripts by re-executing the same calls, feeds them through today's gate, and
requires `confirmed` with zero violations — all 36 pass, so no headline row could
have moved (the first draft of rule 3 failed three of them, which is how the
pod→controller allowance was found rather than guessed). Twelve regression
tests pin the four closures on the webhook fixture, one per attack shape.
Review and red-team scripts reproduced every hole before the fix and none
after. Suite 332 → **402 passed**; `bash scripts/checkpoints.sh` **0
failure(s)**. Failure mode logged: [docs/failure-modes.md](docs/failure-modes.md)
2026-09-05, extended.

**Next decision it drove:** the pre-registered stage-1 re-score (solution, 3
runs, prediction `resource_correct` 0/3, confirmed-wrong 0;
[docs/experiments/2026-09-04-webhook-outage.md](docs/experiments/2026-09-04-webhook-outage.md))
is **blocked on API credits** — the first attempt aborted mid-run on
"credit balance is too low" and its partial bundle is disclosed-partial and not
committed. It runs as soon as credits exist; stage 2 (serving the kinds) waits
on its result.

## [15] The first case whose cause is outside every arm's reach — and the gate that held — 2026-09-05, 05:45 UTC

**Change:** roadmap 1.3, pre-registered as the escalation in [14].
`evals/scenarios-v2/t2-checkout-release-stalled/`: a release of
`checkout/checkout-api` stalls at 0 updated / 2 ready because an **orphaned,
cluster-scoped** `ValidatingWebhookConfiguration` (`workload-standards`, a
Helm leftover whose Service and namespace are gone, `failurePolicy: Fail`)
makes the API server refuse every pod create with `failed calling webhook …
service "policy-guard" not found`. Nothing in `checkout` is wrong, and a
rollback does not recover (rehearsed live; the old ReplicaSet is refused the
same way). To make the case scorable at all, five things moved together, each
with its test or gate: **capture schema 2** adds both webhook kinds to
`capture.sh`'s cluster roster and `solution/fixture.py`'s `CLUSTER_KINDS`
(schema-aware uniformity test; the frozen schema-1 fixtures stay byte-identical
and read the kinds as "not captured"); `inject.sh` admits labelled webhook
configurations in the additive root only, deletes them first on reset, refuses
strays, and lints their rules to `CREATE` on `pods` so a scenario can never
refuse its own reset (six offline refusal tests); `capture.sh` scrubs the
pipeline label out of every fixture and `checkpoints.sh` gates on it — left in,
the label would have named the planted object to every arm; a **v2 scorer**
(`evals/scoring_v2.py`) re-keys the frozen tables by value, adds
`webhook-admission-block` (red-teamed on a 40-sentence corpus against
rbac-denial's "denied/cannot" group, quota's "reject/block" group, rollout-stuck
and pod-unschedulable, both of which it dominates) and one convention for
cluster-scoped gold (`"namespace": ""`, every "no namespace" spelling equal);
`run_eval` scores each root with its own scorer and records which in
`summary.json`. `evals/scoring.py` and the frozen roots are untouched; the
class is disclosed as a dated addendum in
[docs/decisions/problem-selection.md](docs/decisions/problem-selection.md).

**Why:** [13] and [14] showed that presentational difficulty — distance,
noise, another namespace — does not bite: one arm follows references and is
never distracted, the other is handed the whole cluster. The only headroom left
is a cause that is *structurally* absent from what an arm can read. Webhook
configurations were absent from the captured universe entirely, the baseline's
dump policy never includes cluster-scoped objects, and the solution's
`get_object` refuses them by design. The case makes the fixture carry the
object, so the ceiling being measured is the arms', not the benchmark's.

**Evidence after:** predictions and decision rule were written before any
scored run
([docs/experiments/2026-09-04-webhook-outage.md](docs/experiments/2026-09-04-webhook-outage.md));
three lenses of design review plus a live probe changed the case before capture
(a token-disjoint configuration name, the label scrub, an env-value release
instead of an image tag). Bundles:
[rules](evals/results/20260905T044845Z-rules/summary.md),
[baseline](evals/results/20260905T044850Z-baseline/summary.md),
[solution](evals/results/20260905T044853Z-solution/summary.md).

| arm | pooled | resource_correct | class_correct | confirmed-wrong |
|---|---|---|---|---|
| rules | 0/1 | 0/1 | 0/1 | 1 |
| baseline | 0/3 | 0/3 | 3/3 | **3** |
| solution (unchanged) | 1/3 | 3/3 | 1/3 | **0** |

Read per CLAUDE.md's rule, the rows say three things the pooled column cannot.
*The baseline* named the kind and the mechanism in every run and could not name
the object — it wrote the Service name from the error message, once with
"confirm exact object name via kubectl" inside the name field — and said
`confirmed` three times. *The solution's* `resource_correct` 3/3 is the
pre-registered **V4 leak**, not capability: in every run the name first appears
in a rejection message (`V4 EXISTS: … Present: workload-standards`) after the
same `policy-guard` guess; across 25 tool calls there is not one successful read
of the object. *The verdict gate held*: every run was refused `confirmed` with
the unmet condition named, two exited through the salvage path as labelled
`inconclusive`, one submitted `probable` — **confirmed-wrong 0 against 3** on
the same unreadable object, which is the calibration claim this arm exists to
make, measured on the first case where it costs something. The `class_correct`
1/3 is the salvage sentence classifying to nothing, by design. Cost per case
rose to $0.61 (four submit attempts every run). Suite 252 → **332 passed**,
gate self-tests 30 → 40, `bash scripts/checkpoints.sh` **0 failure(s)** at 15
fixtures. Spend $2.23 (cumulative v2: $4.57).

**Next decision it drove:** the headroom is real and structural, and two slices
follow in a fixed order: first close the validator leaks (V4 must not list names
for a kind no read tool serves; V6/V7 must not take a tool-limitation error as
evidence) and re-score, predicted `resource_correct` 0/3 with confirmed-wrong
0; then serve the two webhook kinds through `get_object`/`describe` plus a
webhook → Service edge and re-score, predicted 3/3 `confirmed`. No v2 bar block,
as pre-registered.

## [14] Decoys in the noise pack: the giveaway was not what either arm relied on — 2026-09-04, 13:00 UTC

**Change:** roadmap 1.1. A v2 noise pack
([evals/scenarios-v2/_noise/generate.sh](evals/scenarios-v2/_noise/generate.sh))
that copies the frozen 20 healthy namespaces **verbatim** and appends three
genuinely broken, entirely irrelevant workloads — a crashlooping canary, a
failing CronJob, an unschedulable trainer — each conditional per authoring rule
1 and each rehearsed live to recovery. `inject.sh` now takes its noise pack from
the scenario root, so frozen T3 captures are untouched.

**Why:** the frozen pack is all healthy, so every T3 case hands over a giveaway
— the only broken object in the cluster is the answer. Roadmap 1.1 claims
removing it "removes the 'any unhealthy object is the answer' giveaway". That
claim assumes the arms find the answer by noticing what is broken.

**Evidence after:** a controlled A/B, not a new case. `t3-crossns-decoys` has
**byte-identical Kubernetes objects** to `t2-crossns-externalname-selector`
(verified with comments stripped); the noise pack is the only difference, and
its healthy half is byte-identical to the frozen one. So any change is the
decoys and nothing else. Pre-registration and full write-up:
[docs/experiments/2026-09-04-decoy-noise.md](docs/experiments/2026-09-04-decoy-noise.md).

| arm | no decoys | with decoys | resource_correct (decoys) |
|---|---|---|---|
| rules | 0/1 | 0/1 | 0/1 |
| baseline | 1/3 | 0/3 | **3/3** |
| solution | 3/3 | **3/3** | **3/3** |

**The claim is false for both LLM arms, for opposite reasons.** The solution
never saw the decoys: across all three runs it touched exactly two namespaces,
`storefront` and `payments` — no decoy namespace, no healthy-noise namespace.
It follows references from the page rather than surveying for unhealthy
objects, so an unrelated broken workload is not something it *can* be
distracted by. The baseline did see them — they appear in its curated dump 46
times — and named the right object in all three runs anyway.

The baseline's 1/3 → 0/3 is not a capability drop: `resource_correct` is 3/3 in
both conditions, and the entire difference is the mechanism-vocabulary artifact
from [13], caught this time by the rule that entry added to CLAUDE.md rather
than by hindsight.

The decoys were not free, just not decisive: the solution spent **+59% tool
calls** (6.3 → 10.0) and +34% turns on the same answer, all of it going deeper
inside the two namespaces it was already following. That is cost scaling with
cluster size — roadmap 2.6's concern — not difficulty.

**Next decision it drove:** 1.1 is closed as a source of headroom, per the rule
pre-registered in STATUS before the pack existed. The pack is kept: it costs
nothing, it is a more honest cluster, and it is now the measured control for any
future claim that a case is hard *because* the cluster is noisy. Escalating to
roadmap 1.3 as pre-registered — webhook configurations are absent from the
captured object universe entirely, so the ceiling there is structural rather
than presentational, which is what "no headroom" actually requires.

## [13] The cross-namespace case scored — and it does not measure what it was built to measure — 2026-09-04, 08:36 UTC

**Change:** nothing in the solution. Both LLM arms were scored on
`t2-crossns-externalname-selector` (3 replicate runs each, $1.04 total), and
the result retires the claim the case was authored to support.

**Why:** [12] shipped the case with the explicit note that no LLM arm had seen
it and nothing could be claimed until one did. This is that run.

**Evidence after:**
[evals/results/20260904T082737Z-baseline/](evals/results/20260904T082737Z-baseline/summary.md)
and
[evals/results/20260904T083020Z-solution/](evals/results/20260904T083020Z-solution/summary.md).

| arm | pooled | resource_correct | class_correct | confirmed-wrong |
|---|---|---|---|---|
| rules | 0/1 | 0/1 | — | 0 |
| baseline | 1/3 | **3/3** | 1/3 | 2 |
| solution | 3/3 | **3/3** | 3/3 | 0 |

Read the pooled column alone and the story is "the solution crosses a namespace
boundary and the baseline cannot." That story is false. **Both LLM arms named
`service/payments-gateway` in `payments` in every single run.** The entire
baseline gap is `class_correct`: run 1's mechanism said the Service *selects*
`app=payments-gateway` — substantively a textbook-correct description — and
the `service-selector-mismatch` signature requires the literal token
`\bselector\b`, so it classified as `readiness-probe-failing` instead. Run 3
described the root cause correctly *and* mentioned the downstream readiness
symptom, matching two classes where the scorer requires exactly one. The
baseline's 2 "confirmed-wrong" rows are the same artifact, not two confident
errors.

The case cannot test cross-namespace attribution against the baseline at all:
that arm is handed a curated dump of the whole cluster, and the `payments`
namespace appears in its prompt 30 times before it reads a word. A dump-based
arm never crosses anything.

What the case *does* demonstrate is the validation gate, and it is worth more
than the number that motivated the case. In runs 1 and 2 the solution went
straight to `payments`, diagnosed it correctly, and was **rejected on
submission**: `V2 ADMISSIBILITY: evidence[3] cites payments, which nothing has
yet connected to the paged symptom. Cite the reference that links it first`.
It had found the answer by shortcut and could not *assert* it without
producing the edge that licenses the crossing. By run 3 it read the
ExternalName alias before submitting. The gate converted a lucky jump into a
cited one — exactly the discipline the arm exists to enforce, measured rather
than asserted.

The shortcut is our own: the ExternalName rendering fix in [12] puts the alias
target into the opening namespace overview, so the agent no longer has to
follow a reference to *locate* `payments`. That fix is right — an alias must
not render as a broken Service — but it means the case tests citation
discipline, not search.

**Next decision it drove:** no v2 bar block was added to `evals/reported.json`.
A bar asserting "solution beats baseline" here would enshrine a vocabulary
artifact as a capability claim, and `confirmed_wrong` is inflated by the same
artifact, so neither is defensible as a gate. The case stays as a
V2-admissibility demonstration and a rules-arm discriminator. To measure
cross-namespace *attribution*, a case needs decisive evidence that a
whole-cluster dump does not contain — which is a case-design decision, not
more scoring.

## [12] The first case whose cause is in another namespace — 2026-09-02, 03:46 UTC

**Change:** roadmap 1.4's authoring half.
`evals/scenarios-v2/t2-crossns-externalname-selector/` — a T2 case where the
page fires on `storefront` and the object whose spec must change is
`service/payments-gateway` in **`payments`**. `storefront/checkout-api` gates
its readiness on a payment gateway it reaches through an ExternalName alias;
the Service that alias points at selects `app=payments-gateway` while its pods
carry `app=payments-gateway-api`, so its Endpoints never populate. New cases
land in an additive root — `evals/run_eval.discover_cases` takes a `root`, and
`--scenarios-root` selects it — so the frozen 12-case set keeps its identity
and its count. Fixtures stay in the single `evals/fixtures/` tree, where the
credential scan and schema gate already reach them.

**Why:** every case in the frozen set has its cause and symptom in one
namespace, so none of them can tell an agent that follows a reference across a
boundary from one that searches the paged namespace exhaustively. It is also
the case that exercises the V2 admissibility rule ([10]) in the direction that
matters.

**Evidence after:** three findings, two of them defects the case exposed in
code that has been shipping since the start.

1. *The V2 fix does what it was built for.* Against the captured fixture, the
   page alone licenses `['', 'storefront']`; after the agent reads the
   ExternalName Service and quotes its target, `payments` becomes citable.
   That works **only** because [10] treats `.` as a label separator, so
   `payments-gateway.payments.svc.cluster.local` names `payments` — the
   design decision now has a case that would fail without it.
2. *An ExternalName Service rendered as a broken one.* The namespace overview
   printed `service/payments-gateway selector={} endpointAddresses=0` — an
   alias has no selector and no Endpoints **by design**, so it read as
   identical to a Service whose selector matches nothing, inviting a diagnosis
   of the healthy alias instead of the fault it points at. Now rendered as
   `type=ExternalName externalName=…`. No frozen fixture contains an
   ExternalName Service, so no scored row can move.
3. *A Service another namespace depends on reported no consumers.*
   `find_consumers` on the payments Service said "no workload in payments
   references service/payments-gateway" — true and useless. It now reports the
   aliasing Service and the namespace it lives in.

The free rules arm scores the case **0/1**, and its log names the reason:
`2 analyzer(s) fired in ns storefront, chose readiness` — it answered
`deployment/checkout-api` in the *paged* namespace, the designed distractor,
against gold `service/payments-gateway` in `payments`
([evals/results/20260902T034542Z-rules/](evals/results/20260902T034542Z-rules/summary.md)).
The case discriminates exactly as intended, and it proves the harness runs the
additive root end to end for $0.00. Counterfactual rehearsed live before the
pristine capture (authoring contract rule 6): gold's remediation applied
verbatim moved Endpoints from `<none>` to two pod IPs and checkout-api from
0/2 to 2/2 Ready, with nothing in `storefront` touched — recorded in the
case's `notes.md`. Suite 238 → **248 passed**, `bash scripts/checkpoints.sh`
**0 failure(s)**, fixture schema complete at 13.

**Next decision it drove:** the case is authored, captured and free-scored, but
no LLM arm has seen it — the baseline and solution numbers on it need a scored
run, which costs API spend and is the next decision to take. The roadmap's
ingress-backend edge was deliberately **not** built: no case exercises it, and
untested speculative code in the tool layer is worse than a recorded gap.

## [11] Cost and latency were recorded but never scored — 2026-09-02, 00:22 UTC

**Change:** the declared bar now asserts spend and wall-clock, not just
correctness. [evals/verify_reported.py](evals/verify_reported.py) re-derives
mean cost and mean duration per case from the `metrics.json` committed beside
every `answer.json`, prints both for all three arms, and fails the gate if the
solution arm exceeds the two new ceilings in
[evals/reported.json](evals/reported.json). The bar logic was extracted into a
pure `check_bar()` so it could be tested at all — it had no tests before this,
which meant no check in it had ever been shown to fire.

**Why:** roadmap 1.6. Every scored run already wrote `cost_usd` and
`duration_s` per case and nothing read them, so an arm that reached 36/36 by
spending ten times as much would have passed the gate silently — and with the
frozen set saturated at 36/36, cost and latency are two of the few axes left
where a future change can still be measured at all.

**Evidence after:** the ceilings are set at **2x the observed pooled mean**
($0.1807/case, 44.1s/case → $0.36 and 88s), derived from the committed bundle
rather than picked: the same case varies up to **2.42x** in cost across
replicate runs, so a per-case ceiling would fire on noise, while the 12-case
run means moved only $0.157/$0.181/$0.204 (±15%) across the three runs. A
per-case *cost max* would also have been toothless, since `MAX_CASE_USD`
already truncates it at $0.60. Seven new tests in
[tests/test_verify_reported.py](tests/test_verify_reported.py) — suite 231 →
**238 passed** — trip each ceiling and each previously-untested check;
`bash scripts/checkpoints.sh` reports **0 failure(s)** and the gate line now
reads `36/36 … $0.1807 and 44.1s per case`. The measured comparison this
exposes: the solution buys six pooled points over the baseline at **1.55x**
the spend ($0.1807 vs $0.1163) and effectively the same wall-clock (44.1s vs
43.0s), and mean × 36 reconciles to each bundle's `summary.json` totals to the
cent ($6.51, $4.19).

**Next decision it drove:** the three remaining roadmap items in flight (1.1,
1.4, 2.1) all need an authoring cluster, and each will add rows to a bar that
now costs something to miss — so the next slice is bringing up the authoring
cluster, not more offline harness work.

## [10] The admissibility check matched namespaces by substring — 2026-09-01, 14:11 UTC

**Change:** V2 admissibility now decides whether a text *names* a namespace by
exact DNS-1123 label tokens instead of substring containment. One helper,
[solution/fixture.py](solution/fixture.py) `namespaces_named_in`, placed beside
`namespaces()` because that module is already the authority on what a namespace
is; [solution/validate.py](solution/validate.py) calls it at both sites that
grew the admissible set (the page-text seed and each verified quote), and
[solution/agent.py](solution/agent.py) `paged_namespace` calls it in the
fallback that picks the paged namespace in the first place.

**Why:** no eval row failed — the defect is latent, which is why it was worth
finding before harder cases make it fire. The rule was
`{ns for ns in known if ns in page_text}`: pure substring containment, so a
page naming `prod-eu` silently admitted citations from `prod`, and the same
test one layer up in `paged_namespace` resolved the paged namespace itself to
`kube-system` from the words `kube-system-canary` (reproduced directly against
the `t2-rbac-sync-forbidden` fixture: old rule → `kube-system`, new rule → no
match). A wrong paged namespace poisons the admissible set from the first
citation, so fixing V2 alone would have left the hole one layer up. Today's
12 fixtures have no overlapping namespace names, which is the only reason it
never fired; roadmap item 1.4 authors cases whose cause and symptom sit in
different namespaces, and it would have fired there.

**Evidence after:** three regression tests that fail on the old rule and pass
on the new one — two in
[tests/test_solution_validate.py](tests/test_solution_validate.py), one per
admissibility site, and one in
[tests/test_solution_agent.py](tests/test_solution_agent.py) for the
paged-namespace fallback. Suite goes 228 → **231 passed**, `bash
scripts/checkpoints.sh` reports **0 failure(s)** (ruff clean, pyright strict
0 errors), and `make verify` re-derives the unchanged **36/36** solution row
from the committed bundles: the tightening only narrows admissibility, and it
narrows nothing the frozen evidence relies on.

**Next decision it drove:** the exactness precondition for roadmap 1.4 is now
in place, so the remaining half of that item — authoring cross-namespace cases
with `gold.failing_resource` deliberately outside the paged namespace, plus
the ExternalName and ingress-backend branches in `_reference_paths` — is
unblocked and needs an authoring cluster, not more validator work.

## [9] The "a decision tree does this" objection, measured: rules reach 27/36 — 2026-08-29

**Change:** a third comparison arm, `ablation/` — a deterministic non-LLM
diagnoser: 12 k8sgpt-style analyzers over the same fixtures, emitting the same
report contract, scored by the same frozen scorer. No model, no network, $0.00,
0.4 s for the full 12x3 matrix, byte-identical across runs.

**Why:** design requirement 8. "Couldn't a decision tree do this?" is the
likeliest objection to a Kubernetes-diagnosis agent, and the only honest
answer is a number. Building the arm *after* seeing all 12 cases invites two
opposite frauds — overfitting it into a lookup table, or crippling it into a
strawman — so the method was committed as a pre-registration BEFORE the arm
existed ([docs/experiments/2026-08-29-rules-ablation.md](docs/experiments/2026-08-29-rules-ablation.md)):
generic signatures only with k8sgpt as the reference bar, no case identifiers in
`ablation/`, every ambiguity resolved in the arm's favour (it is handed the paged
namespace and allowed to name the object whose spec must change), and no tuning
after the score is seen. The pre-registered prediction was a deliberately **high**
9-12 of 12, with the stated expectation that a good score would still overstate
the engine.

**Evidence after:**
[evals/results/20260829T101751Z-rules/](evals/results/20260829T101751Z-rules/summary.md)
— **27/36 pooled**, 9/12 in each of the three runs. Bottom edge of the predicted
range; both cases flagged `uncertain` failed, and one predicted `correct` failed.

| metric | rules | baseline | solution |
|---|---|---|---|
| root-cause identification | 27/36 | 30/36 | **36/36** |
| T1 | 12/15 | 15/15 | **15/15** |
| T2 | 9/15 | 10/15 | **15/15** |
| T3 | 6/6 | 5/6 | **6/6** |
| resource identification | 33/36 | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | 3 | **0** |
| confirmed-wrong | 3 | 3 | **0** |

The three cases it fails in every run break it three different ways, and none is
a missing analyzer:

1. **`t1-pvc-storageclass-typo` — cause/symptom inversion.** Right object,
   wrong thing said about it: reported the pod as unschedulable when it is
   unschedulable *because* its PVC never bound. Both observations are true; only
   one is the cause, and precedence order cannot prefer a cause because it does
   not know which observation explains which.
2. **`t2-rbac-sync-forbidden` — the broken-reference blind spot.** Returned
   **zero findings**. The analyzer reads the 403 from the log, reads the pod's
   ServiceAccount, and looks for the RoleBinding naming it — but the injected
   fault is that the binding names `inventory-synk` against a real
   `inventory-sync`. The edge it traverses is the edge the fault removed. *A
   rules engine that navigates by reference cannot diagnose a broken reference*:
   it would have to hypothesise about what is **missing**, not match what is
   present. The solution arm gets this case in all three runs.
3. **`t2-quota-blocks-scale` — right object, sentence unmatched.** Named the
   correct ResourceQuota; its canned sentence matched no class because the
   scorer's signature wants a standalone `quota` and the sentence only had it
   inside `ResourceQuota`. **Disclosed as a wording artifact, not a reasoning
   gap**, left uncorrected under the no-tuning rule and reported in its own row.
   Granting it, rules would tie the baseline at 30/36 — still 6 behind solution.

**The finding the score does not show:** 5 of 12 cases fired more than one
analyzer, and in every one the losers were dropped by precedence alone, on no
evidence. That includes `t3-overlapping-config-and-oom`, which the arm gets
**right** — three analyzers fired, and config-ref outranks OOM only because the
pre-registration put it there. Flip that pair, as an equally defensible
severity-first runbook would, and it confidently returns the OOM'd report worker:
real, loud, and not what the page is about. The 6/6 on T3 is a coin that landed
right twice, and the bundle records the coin. The calibration column says the
same thing in the arm's own voice: it asserted `confirmed` and was wrong, because
it has no notion of doubt to report.

**Next decision this drove:** the gap is now bounded from below by something
better than "a simpler thing scores worse" — it is bounded by *which* 3 cases and
*why*. `evals/reported.json` pins the bundle and `make verify` re-derives all
three columns offline; the gate now **fails if the ablation ever stops failing on
at least 3 cases**, so design requirement 8 cannot rot into a stale claim.
`tests/test_rules_ablation.py` pins the three failing cases as regression tests
and enforces the method (no case identifiers in string literals, no model or
scorer import, determinism, zero cost).

## [8] 36/36 — the reverted rules, re-scoped and pre-registered, land perfectly — 2026-08-29

**Change:** three edits to the system prompt, committed as a pre-registration
BEFORE the run ([docs/experiments/2026-08-29-mechanism-nouns.md](docs/experiments/2026-08-29-mechanism-nouns.md))
with per-case predictions for all 12 cases, a named collision watch-list, and a
fixed adopt/revert rule:
1. **Lift the over-broad prohibition.** The shipped prompt said "STOP at the
   failure ... do not carry the sentence into what restarted". For an object
   caught in a restart loop, that behaviour is the failing object's OWN
   behaviour, not a downstream effect of a different object — the clause was
   suppressing a true, cited, on-target fact. A bug fix, defensible with no
   reference to any scorer.
2. **Name Kubernetes objects by kind and name, scoped to object INSTANCES.** The
   scoping is the load-bearing detail: the unscoped version of this rule in [7]
   produced "carries no configmap read permission" and cost a t2-rbac row.
   Permission and class-of-thing phrasings ("get and list on configmaps") are
   explicitly left alone. `validate.py` V5b already demanded this for the failing
   resource; this applies it consistently to the rest of the chain.
3. **Quote observed errors and statuses rather than paraphrasing them.**
   Protective: the three winning readiness rows quote `"connection refused"`, and
   [7]'s rules paraphrased it away and lost all three.

**Why:** [7] measured the unscoped version at 33/36 -> 31/36 and it was reverted.
The per-row trace showed the losses were not the rules being wrong but the rules
being *unscoped* — and separately identified edit 1's clause as an outright bug.
**Evidence after:**
[evals/results/20260829T090941Z-solution/](evals/results/20260829T090941Z-solution/summary.md)
— **36/36 pooled**, $6.51, ~26 min.

| tier | baseline | solution |
|---|---|---|
| T1 | 15/15 | **15/15** |
| T2 | 10/15 | **15/15** |
| T3 | 5/6 | **6/6** |
| pooled | 30/36 | **36/36** |
| resource identification | 33/36 | **36/36** |
| right object, sentence unmatched | 3 | **0** |
| confirmed-wrong | 3 | **0** |

Every run 12/12. Every one of the 36 rows matched exactly one fault class. The
calibration target from [5] — drive confirmed-wrong to 0 — is met: **when the
agent said `confirmed`, it was right 36 times out of 36.**

Every per-case prediction in the pre-registration was correct; the pooled
prediction (35/36) was one row pessimistic. All four watch-list collisions were
avoided. The stated P(36/36) was 0.20 and the outcome was that branch on the
first and only attempt — the estimate was wrong because it treated the prior
losses as random variance when they had a single removable cause. That is
recorded in the pre-registration rather than quietly dropped.

**Anti-leak, held under pressure:** the first draft of edit 3 used
`CrashLoopBackOff` as an example of "a status in the cluster's own words".
`tests/test_solution_prompts.py` failed it — that string names a candidate
failure type. The prompt was changed, not the test. The same reasoning was then
applied to a phrase the tripwire cannot see: edit 1's first draft enumerated
"restarted, backed off, or retried", which would have handed over a class
signature's group-1 token by the side door. Both removed; the shipped wording
lifts the prohibition without naming any token the classifier looks for.

**Next decision it drove:** the arm is frozen at 36/36 and
[evals/reported.json](evals/reported.json) now pins this bundle, so
`make verify` re-derives 36/36 vs 30/36 from committed evidence on every commit
and fails if anything regresses. Remaining work is the rules-only ablation arm
(design req 8).

## [7] Experiment REVERTED: tuning mechanism wording trades rows, it does not win them — 2026-08-29

**Change:** two extra mechanism-writing rules were added to the system prompt
("call every Kubernetes object by its API kind" and "run the sentence through to
the state the page reports, and end there"), measured on the full frozen set,
and then **reverted**. The shipped prompt is byte-identical to the one that
produced the [6] bundle.
**Why:** [6] left T1 at 12/15 with all three losses showing
`resource_correct=True` and `matched_classes=[]` — right object, sentence the
rubric matched to nothing. A 4-case targeted probe scored 4/4 on exactly the
contested cases ($0.76), which looked like confirmation.
**Evidence after:**
[evals/results/20260829T073436Z-solution/](evals/results/20260829T073436Z-solution/summary.md)
— **31/36, down from 33/36**, confirmed-wrong 3 -> 5, $6.35.

| tier | [6] bundle | this experiment |
|---|---|---|
| T1 | 12/15 | **15/15** |
| T2 | **15/15** | 11/15 |
| T3 | **6/6** | 5/6 |
| pooled | **33/36** | 31/36 |

It fixed precisely what it aimed at — `t1-pvc-storageclass-typo` 1/3 -> 3/3 and
`t1-crashloop-missing-env` 2/3 -> 3/3, T1 back to 15/15 — and broke three cases
it was not aiming at: `t2-readiness-wrong-port` **3/3 -> 0/3**,
`t2-rbac-sync-forbidden` 3/3 -> 2/3, `t3-overlapping-config-and-oom` 3/3 -> 2/3.

**Why, exactly** (traced through the frozen classifier, per row — the first
characterisation of this in [7] was wrong and is corrected here). Each class
signature is a CONJUNCTION of two groups: an *object/noun* group and a
*symptom/error-word* group, and a class matches only if BOTH hit. The two rules
moved one dial — from **quoting observed output** toward **describing canonical
structure** — and that dial has opposite effects depending on which half of the
conjunction a given case was short of:

- **It supplied missing object nouns (the wins).** `t1-pvc` needed a
  PersistentVolumeClaim noun; "the claim generated from that template" gave the
  rubric nothing, "the PersistentVolumeClaim data-metrics-db-0" gave it the
  group-1 hit.
- **It removed symptom words (3 of the 5 losses).** All three `t2-readiness`
  rows kept group 1 (`readiness`) and lost group 2 entirely. The 33/36 sentences
  quoted the observed error — `"connection refused"` — or said "the probe
  fails"; the new ones paraphrased it into structure: "refused at the TCP
  level", "hold Ready=False", "no pod ever passing readiness". Group 2's
  alternatives are `\bfail | \bunready\b | not ready | connection refused |
  wrong port`. Synonyms, zero matches.
- **"Stay on the failing object" forbade naming the object the class is keyed
  on (1 loss).** `t3-overlapping`'s gold class needs `config ?map\b`. The 33/36
  sentence named the second object — "**ConfigMap** orders/orders-config contains
  only the key ..." — and matched. The new one, obeying the rule, wrote only
  `configMapKeyRef.key`, and `config ?map\b` does **not** match
  "configMapKeyRef" (the `\b` needs a non-word char after "map"; "K" is a word
  char). Group 1 empty, 0 classes. The irony is exact: this fault *is* a
  cross-object reference failure, and it cannot be described from inside one
  object.
- **The API-kind rule added a noun that collided with another class (1 loss).**
  `t2-rbac` run 2 wrote "carries no **configmap** read permission" — the
  canonical singular, as instructed. `config ?map\b` matches "configmap " (space
  after) where it does *not* match the earlier phrasing "get,list on configmaps"
  (the trailing "s" blocks the boundary). Plus "does not exist" for group 2, and
  the row matched **two** classes.

So the failure is not "under-naming versus over-reaching" as first written. It
is one dial with opposite signs per case, and a word-boundary away from
invisible.

**The invariant across both bundles: `resource_correct` is 36/36 in each.** Every
one of the 5 losses here, and all 3 there, named the correct object. No wording
rule moved that number, in either direction, on any run.

**Next decision it drove:** stop tuning sentence wording ad hoc against the
rubric — it is a measurement instrument with a vocabulary, and the same dial
supplies a missing noun on one case while stripping a matched symptom word on
another, so pushing it wins rows and loses rows at the same time. Re-scope
instead of re-word: pin instance names (`Kind ns/name`), quote the cluster's
own error text rather than paraphrasing it, and pre-register a per-case
prediction with a fixed adopt/revert rule before the next run — the approach
[8] takes, reaching 36/36. [6] stood as the reported result at this point in
the narrative — **33/36 vs the baseline's 30/36, T2 10/15 -> 15/15, T3 5/6 ->
6/6, with the three target rows from [5] all won** — superseded by [8]. Both
bundles stay committed so the reversal is auditable rather than asserted, and
the general finding is written up in
[docs/failure-modes.md](docs/failure-modes.md).

## [6] Solution arm scored: 33/36, T2 and T3 perfect, and every remaining loss is one failure mode — 2026-08-29

**Change:** the advanced arm ships and is scored on the same frozen 12-case set,
from the same recorded fixtures, by the same frozen scorer. It is a single agent
that walks the object graph backwards from the page over eight fixture-backed
kubectl-shaped tools, and can finish only through `submit_answer` — a validating
tool that RE-EXECUTES every citation against the snapshot, runs the 2-3
verification commands the report promises, and derives the verdict from those
outcomes instead of letting the model choose it.
**Why:** CHANGELOG [5] showed the baseline's 6 lost rows were two distinct
problems — evidence it structurally cannot reach (its curation only describes
NOT-Ready resources, so a Running-and-Ready worker's 403 log is never in the
prompt), and attribution (naming the workload that looks unhappy instead of the
object whose spec must change).
**Evidence after:**
[evals/results/20260829T064705Z-solution/](evals/results/20260829T064705Z-solution/summary.md)
— **33/36 pooled vs the baseline's 30/36**, at $6.20 vs $4.185
(~$0.172/case-run vs $0.116; 1.33M in / 136k out tokens).

| tier | baseline | solution |
|---|---|---|
| T1 | 15/15 | **12/15** |
| T2 | 10/15 | **15/15** |
| T3 | 5/6 | **6/6** |
| pooled | 30/36 | **33/36** |
| confirmed-wrong | 3 | 3 |

Row deltas: `t2-rbac-sync-forbidden` **0/3 -> 3/3**, `t2-init-wait-for-migrations`
**1/3 -> 3/3**, `t3-quiet-selector-loud-crashloop` **2/3 -> 3/3**;
`t1-pvc-storageclass-typo` 3/3 -> 1/3 and `t1-crashloop-missing-env` 3/3 -> 2/3.
The three target rows named in [5] as the design brief were all won.

**The number that matters most is not in the table: `resource_correct` is
36/36.** The agent named the correct object — the one whose spec a human must
edit — in every single run, including all three it scored wrong. All three
losses have `matched_classes=[]`: the diagnosis was right and the sentence used
vocabulary the frozen mechanism->class rubric does not recognise ("the claim
generated from that template" rather than naming the PersistentVolumeClaim;
stopping at "the container exits 1" without naming the restart behaviour the
page reports). One failure mode, not three.

Three gate defects found and fixed by the first live runs, before this matrix
(all three regression-tested): tool-call ids the model could not see and so
invented; an admissibility rule that rejected citations used to RULE OUT a red
herring, punishing the exact discipline the arm exists to enforce; and a
vocabulary ban that banned the failing resource's own name. On the case that
exposed them, the very first submission had already been correct.

**Next decision it drove:** the T1 regression is not a reasoning regression and
must not be treated as one. The fix is a mechanism-writing rule that is
independently good practice and names no failure type — name Kubernetes objects
by their API kind, and end the sentence at the cluster-observable state the page
reports — then re-run the full matrix as one invocation. If T1 does not recover,
33/36 with 36/36 resource accuracy is the honest headline and the vocabulary
sensitivity of any mechanism->class rubric becomes the headline finding.

## [5] Baseline anchored on the full 12-case set: 30/36, and the gap is structural — 2026-08-29

**Change:** first clean scored run of the complete case set — 12 cases × 3
replicate runs, pinned `claude-opus-5`, zero infrastructure failures (the
new abort guard armed but never fired).
**Why:** any improvement claim needs the baseline anchored on the same
frozen-candidate set the solution arm will run, before that arm exists
(design req 6: baseline scored first).
**Evidence after:**
[evals/results/20260829T045650Z-baseline/](evals/results/20260829T045650Z-baseline/summary.md)
— **30/36 pooled (T1 15/15, T2 10/15, T3 5/6)**, stable 10/12 on every run;
calibration: `confirmed` right 30/33, **confirmed-wrong 3**; $4.185 total
(~$0.116/case-run), 237k in / 120k out tokens. Per-case: every failure is
concentrated where the tiers were designed to hurt — `t2-rbac-sync-forbidden`
**0/3** (the sync worker is Running and Ready, so the baseline's
describes-of-not-Ready curation never sees its 403 logs or the RBAC objects
— a structural blind spot, not a sampling miss), `t2-init-wait-for-migrations`
1/3 with 2 confirmed-wrong (the roster PREDICTED this case baseline-solvable;
the prediction was measurably wrong — the init evidence is in the dump and
the one-shot still misses it), `t3-quiet-selector-loud-crashloop` 2/3 with
the red herring taking one run. Kill condition 3 checked and NOT fired:
baseline ≥80% but the gap clause fails — a 6-row + calibration gap exists
with no further hardening. Two earlier poisoned bundles from this date
remain committed as disclosed-partials (billing failures, not model data).
**Next decision it drove:** the solution arm's design brief is now written
by the data: (a) investigate Ready-but-broken resources — policy/RBAC
objects and healthy-looking workloads' logs — that dump curation
structurally omits; (b) verify-before-assert to drive confirmed-wrong 3 → 0
(the calibration headline); (c) target rows: t2-rbac ×3, t2-init ×2,
t3-quiet ×1. If the solution clears ~34/36, the comparison stands; if it
lands closer, scale-hardening T1/T2 with the noise pack (disclosed,
pre-freeze) is the honest widening lever.

## [4] Second fixture captured; declared bar met; remediation now rehearsed, not asserted — 2026-08-29

**Change:** first scenario produced end-to-end through the sanctioned path:
`inject.sh --no-capture` → fault manifests (wait.sh gates on the exact
containerd `not found` event text, fail-fast on registry throttling) → gold's
remediation applied live → recovery observed (`deployment "storefront"
successfully rolled out`, 2/2 Running) → wipe → pristine re-inject → capture.
The rehearsal record lives in the scenario's notes.md (contract rule 6).
**Why:** the roster red-team showed three cases whose remediation could be
wrong invisibly; rule 6 makes "remediation_summary" an observed fact per case
before its fixture freezes. Registry choice validated live: registry.k8s.io
emitted containerd's `not found` for the unknown repo, as designed.
**Evidence after:** `evals/fixtures/t1-imagepull-bogus-tag/` (268 files);
checkpoints "fixture schema complete (2 fixture(s))", 0 failures. The bar
declared in advance — snapshot harness + ≥2 recorded scenarios + a scored
baseline — is now met.
**Next decision it drove:** capture the remaining T1 cases the same way as
verification completes, then T2/T3; score the baseline on the widened T1 set
before building the solution arm.

## [3] Case roster designed, red-teamed; scorer tightened pre-freeze — 2026-08-29

**Change:** committed the 12-case tiered scenario roster + authoring contract
([evals/scenarios/README.md](evals/scenarios/README.md)) and the sanctioned
fault→fixture path (`evals/inject.sh`: default-ns guard, namespace wipe, noise
pack for T3, optional two-phase setup, wait-condition gate, capture). Scoring
spec tightened under the disclosed pre-freeze change policy: no signature
alternative may satisfy both of a class's groups (`crashloop`/`oom`/`137`
single-token dual-matches removed), readiness signature restricted to failure
evidence, `rolebinding`/`403` added to rbac-denial, renamed-key phrasings added
to bad-config-ref, and the shared answer contract now confines `mechanism` to
the paged symptom's causal chain (ruled-out alternatives go in the ledger).
**Why:** a 3-lens adversarial review of the roster
([evals/out/20260829-roster-red-team.json](evals/out/20260829-roster-red-team.json),
30 findings) *executed* the scorer against natural correct answers and showed
honest T3 diagnoses scoring wrong: any answer that even negated the designed-in
decoy ("the crashlooping batch job is unrelated") multi-matched via
single-token signatures, and the natural renamed-ConfigMap-key phrasing
zero-matched. It also caught determinism traps (Docker Hub rate limits can
freeze a fixture whose events contradict gold, quota admission arming race,
host-dependent CPU requests, immutable volumeClaimTemplates) now encoded as
roster rules.
**Evidence after:** `uv run pytest -q` green — 6 new regression phrasings in
`tests/test_scoring.py::test_roster_phrasings_classify_exactly` (each fails
against the pre-tightening signatures) plus the existing gold self-classify
suite.
**Next decision it drove:** author the 11 remaining scenario directories
against the hardened contract, then capture in tier order — the 2nd captured
fixture completes the declared bar. The t1 exemplar gets a disclosed re-capture
(conditional entrypoint + missing wait.sh) before the case-set freeze.

## [2] Baseline scored: 3/3 on the first case, contract-clean — 2026-08-28

**Change:** built the one-prompt baseline arm — documented "rushed human"
dump-curation policy ([baseline/README.md](baseline/README.md)), pinned
`claude-opus-5`, shared output contract (`common/report_contract.py`) so both
arms answer under identical instructions — and the scored-run harness
(`evals/run_eval.py`: arm × cases × 3 replicate runs, frozen scorer, committed
evidence bundle).
**Why:** design req 6 ("baseline green" means *scored*, not runs) and the
bar declared in advance in
[docs/decisions/problem-selection.md](docs/decisions/problem-selection.md).
**Evidence after:**
[evals/results/20260828T185739Z-baseline/](evals/results/20260828T185739Z-baseline/summary.md)
— root-cause identification **3/3** (T1), verdict `confirmed` 3/3,
confirmed-wrong 0, zero report-contract violations; 16,137 in / 12,350 out
tokens, **$0.3894 total** (≈$0.13, ≈55 s per case). Disclosed pre-freeze spec
adjustment in the same slice: the pinned model rejects sampling parameters, so
determinism is reported over replicate runs
([evals/scoring.md](evals/scoring.md)).
**Next decision it drove:** (a) baseline aces T1 as predicted — the
measured-improvement narrative now depends on T2/T3 cases where one-shot
curation misses distant causes; scenario production is next (kill condition 3
watched: if baseline ≥80 % on the full set, harden by scale, not exotic
faults). (b) The baseline's own report flagged that the injected fault's
container command is an *unconditional* stub — future scenarios get realistic
entrypoints so remediation-correctness claims stay honest.

## [1] Scoring spec frozen before any scored run — 2026-08-28

**Change:** committed the disclosed definition of "good" (`evals/scoring.md` +
normative `evals/scoring.py`): answer schema, the 12-class mechanism→class
rubric with dominance collapse, exact-resource matching, per-tier primary
metric, verdict-calibration secondary metric, and the anti-leak invariant (the
fault-class enum never reaches the agent — enforced by a pytest tripwire).
First ground truth: `evals/scenarios/t1-crashloop-missing-env/gold.json`;
checkpoints now fail any fixture lacking its gold.
**Why:** design req 5 requires "good" defined before the first run and freezes
the scoring spec ahead of any scored result — scoring decided after seeing
outputs would be unfalsifiable.
**Evidence after:** `uv run pytest -q` green including
`test_gold_mechanism_self_classifies` (the rubric recognizes its own ground
truth) and `test_fault_enum_never_leaks_into_agent_dirs`; gate self-test
"fixture without gold.json caught".
**Next decision it drove:** score the one-prompt baseline on the first captured
scenarios (declared bar: harness + ≥2 scenarios + scored baseline).

## [0] Scaffold — 2026-08-25

**Change:** repo scaffold created before any problem-specific work: the gates
(preflight/checkpoints/postedit + githooks + `tests/test_gates.sh`) and the
initial docs. NOT the eval harness — at this point `evals/` held only a
README, a `.gitkeep` and a stub whose body was `exit 1`; every scoring,
capture and verification module was built after the problem was chosen.
**Why:** the gates enforce the working discipline — verification-first
reporting, secret scanning, no direct commits to main — from the first
commit, before there is any code to protect.
**Evidence after:** `scripts/checkpoints.sh` green on the empty scaffold.
**Next decision it drove:** choose the problem, design the evaluation, and
freeze the scoring spec before writing any solution code.
