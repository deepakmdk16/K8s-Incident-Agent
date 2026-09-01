# t3-overlapping-config-and-oom — scenario notes

**Tier:** T3 (overlapping genuine faults + cluster noise pack).
**Status:** authored, verified (adversarial pass 2026-08-29); noise pack
landed; captured and scored in the frozen case set (`inject.sh` applies the
noise pack for `t3-*` ids before the fault; fixtures:
`evals/fixtures/t3-overlapping-config-and-oom/`).

**Namespaces owned:** `orders` only. The noise pack owns its own filler
namespaces; this scenario creates none of them and must not collide (the
`_noise` author must not use `orders`).

**Provenance:** authored as fault-as-code (`fault.yaml`), never hand-injected.
inject.sh run date: pending first capture.

## Ground truth (informal — the scored version is gold.json)

- Failing resource: `orders/deployment/orders-api`
- Fault class: broken ConfigMap key reference after a rename
- Mechanism: ConfigMap `orders-config` carries `database_url` (renamed from
  `db_url`). orders-api's env `DATABASE_URL` still references key `db_url`
  → kubelet fails container create on both replicas with
  CreateContainerConfigError `couldn't find key db_url in ConfigMap
  orders/orders-config`. Containers are never created, 0/2 Ready, Service
  `orders-api` has no endpoints → gateway 5xx on order submission.

## Gold-side asymmetry (why the consumer, not the ConfigMap, is gold)

A renamed key is two-sided: either the producer (ConfigMap) restores the old
key or the consumer updates its ref. The fixture contains the evidence that
picks a side: deployment `orders-audit` consumes the NEW key `database_url`
from the same ConfigMap via env valueFrom, is Ready, and logs
`audit: config ok - database_url resolved from orders-config (host=...)` at
startup. A working consumer of the new name evidences the rename as
intentional; restoring `db_url` would re-introduce a retired name against
that evidenced intent, so the canonical fix — and gold — is orders-api's
`configMapKeyRef`. gold.json's `decisive_evidence` states the same asymmetry.

## Overlapping genuine decoy (verifiably off the paged path)

`orders-report-worker` (nightly order export) genuinely OOM-crashloops: its
ConfigMap-mounted script builds a ~150MiB in-memory export buffer (command
substitution over `dd bs=1M count=150`) under a 48Mi memory limit; the cgroup
OOM-kills the shell mid-allocation (exit 137, OOMKilled), and restarts
accumulate. It is off the order-submission path: no Service targets it,
neither orders-api nor orders-audit references it, and its script and logs
describe a nightly export window, not request serving. Rule-1 counterfactual
for the decoy: raise its memory limit to 512Mi and the buffer build completes
(peak transient memory during the substitution is ~2x150MiB, under 512Mi)
and the serve loop runs — the fault lives in the limits, not the script.

## Wait condition (wait.sh)

All three simultaneously, polled every 5s, 300s budget:

1. Both orders-api pods waiting `CreateContainerConfigError` with status
   message containing `couldn't find key db_url` (the exact renamed-key
   evidence, not a coarse waiting reason);
2. orders-audit pod Ready=True AND its log contains `database_url resolved`
   (the asymmetry is captured in the fixture, not just implied);
3. orders-report-worker `restartCount >= 2` with
   `lastState.terminated.reason=OOMKilled` (decoy fully manifested).

## Why deterministic

- Renamed-key fault: kubelet env resolution is a pure lookup against the
  ConfigMap; the key is absent on every sync and the message text is fixed
  kubelet wording. No image pull (`busybox:1.36` is node-cached), no timing
  dependence, no network.
- Audit consumer: the key is present at container create; the proving log
  line is the script's first statement after the env assert.
- Decoy OOM: a ~150MiB anonymous allocation under a 48Mi hard limit crosses
  the cgroup boundary on every start; only kill timing varies, never the
  direction. Single node, cached image, no external dependencies.
- Noise pack: coexistence only — this scenario creates and reads nothing
  outside namespace `orders`.

## Counterfactual verification record (rule 6)

Recorded **2026-08-29 ~02:30 IST**, all items observed live:

- `inject.sh --no-capture` manifested all three wait.sh gates: "orders-api
  CreateContainerConfigError x2; orders-audit Ready with database_url log;
  report-worker restarts=2 OOMKilled" (noise pack applied first, all 20
  namespaces Available).
- Gold remediation applied (patched orders-api's DATABASE_URL
  configMapKeyRef `db_url` → `database_url`): `deployment "orders-api"
  successfully rolled out`, new ReplicaSet 2/2 ready, Service endpoints
  populated with both pod IPs — while the decoy kept OOMing, proving the
  primary fix alone recovers the paged path.
- Decoy counterfactual: raised orders-report-worker memory limit to 512Mi →
  `export buffer ready (157286400 bytes)`, pod Running ready=true
  0 restarts.
- Wiped and re-injected cleanly for the pristine capture (the wipe reverts
  both rehearsal patches; the capture runs from fault.yaml alone).
