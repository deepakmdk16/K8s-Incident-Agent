#!/usr/bin/env bash
# Regression tests for the gate scripts themselves (offline, deterministic).
# Builds minimal fixtures by hand and asserts checkpoints.sh accepts/rejects
# them correctly. GATES_UNDER_TEST=1 stops checkpoints re-running these tests.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export GATES_UNDER_TEST=1
PASS=0; FAILED=0

check() { # name, expected exit, fixture dir
  local rc=0
  bash "$ROOT/scripts/checkpoints.sh" --package "$3" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq "$2" ]; then PASS=$((PASS+1)); printf 'ok   %s\n' "$1"
  else FAILED=$((FAILED+1)); printf 'FAIL %s (want exit %s, got %s)\n' "$1" "$2" "$rc"; fi
}

fixture() { # dir — a minimal package tree that should pass
  mkdir -p "$1/baseline" "$1/solution" "$1/tests" "$1/evals"
  { echo '# release'
    echo 'Approximate runtime: ~2 min - Approximate cost: ~$1'
  } > "$1/README.md"
  printf '## [0] scaffold\n## [1] first improvement\n' > "$1/CHANGELOG.md"
  echo 'x' > "$1/CLAUDE.md"
}

# 1. clean fixture passes
fixture "$TMP/clean";                                        check "clean fixture passes"            0 "$TMP/clean"
# 2. planted API key is caught (string concatenated so this file stays clean)
fixture "$TMP/key"; echo "key = sk-ant-""abcdefghijklmnopqrstuvwx" > "$TMP/key/notes.txt"
                                                             check "planted anthropic key caught"    1 "$TMP/key"
fixture "$TMP/ghp"; echo "tok = ghp_""a1B2c3D4e5F6g7H8i9J0a1B2c3D4e5F6g7H8" > "$TMP/ghp/notes.txt"
                                                             check "planted github token caught"     1 "$TMP/ghp"
# 3. bare TBD in the README is caught
fixture "$TMP/tbd"; echo 'Approximate cost: TBD' >> "$TMP/tbd/README.md"
                                                             check "bare TBD caught"                 1 "$TMP/tbd"
# 4. .env shipped inside a package is fatal
fixture "$TMP/env"; echo 'X=1' > "$TMP/env/.env";            check ".env in package caught"          1 "$TMP/env"
# 5. CHANGELOG with only the scaffold entry fails
fixture "$TMP/nolog"; printf '## [0] scaffold\n' > "$TMP/nolog/CHANGELOG.md"
                                                             check "scaffold-only CHANGELOG caught"  1 "$TMP/nolog"
# --- gaps found by an adversarial audit of the scans; must never silently return ---
# 6. DeepSeek-format key (sk- + 32 alnum) — missed by the old {40,} pattern
fixture "$TMP/dsk"; echo "k = \"sk-""abcdefghij0123456789abcdefghij01\"" > "$TMP/dsk/notes.txt"
                                                             check "deepseek-format key caught"      1 "$TMP/dsk"
# 7. Current OpenAI format (sk-proj-…, contains dashes) — the old run broke on '-'
fixture "$TMP/oai"; echo "k = \"sk-proj-""AbCdEf0123456789_ghijKLmnop\"" > "$TMP/oai/notes.txt"
                                                             check "sk-proj-format key caught"       1 "$TMP/oai"
# 8. Home path anywhere in the tree (a raw transcript carries hundreds of these)
fixture "$TMP/home"; echo '/Users/somebody/Documents/x' > "$TMP/home/notes.txt"
                                                             check "home path in tree caught"        1 "$TMP/home"
# 9. evals/run.sh present but NOT executable must FAIL, never skip as "not present"
fixture "$TMP/noexec"; printf '#!/bin/sh\nexit 1\n' > "$TMP/noexec/evals/run.sh"; chmod -x "$TMP/noexec/evals/run.sh"
                                                             check "non-executable evals/run.sh caught" 1 "$TMP/noexec"
# 10. .env nested below the package root is still fatal
fixture "$TMP/nested"; mkdir -p "$TMP/nested/solution"; echo 'K=v' > "$TMP/nested/solution/.env"
                                                             check "nested .env in package caught"   1 "$TMP/nested"

# --- k8s fixture gates (evals/capture.sh contract; strings concatenated so
# --- this file never trips the scans itself) ---
k8sfix() { # dir — add a minimal schema-complete fixture to a package fixture
  local B="$1/evals/fixtures/t0-selftest"
  mkdir -p "$B/cluster" "$B/ns/payments/describe" "$B/ns/payments/logs"
  printf 'schema: 1\nid: t0-selftest\nmode: captured\nlog_channels:\n  - pod: payments/p\n    container: c\n    current: ok\n    previous: unavailable\n' > "$B/scenario.yaml"
  echo 'PAGE: checkout down' > "$B/page.txt"
  echo 'NAME READY' > "$B/cluster/get-all.txt"
  echo '{"items":[]}' > "$B/cluster/events.json"
  echo '{"items":[]}' > "$B/cluster/nodes.json"
  echo '{"serverVersion":{}}' > "$B/cluster/version.json"
  echo '{"items":[]}' > "$B/ns/payments/pods.json"
  echo 'Name: p' > "$B/ns/payments/describe/pod_p.txt"
  echo 'log line' > "$B/ns/payments/logs/p__c.log"
  # shaped like a REAL capture: cluster-info's kubeconfig embed redacted by
  # construction, raw PUBLIC cert PEM left as-is (only base64'd PEM is banned)
  mkdir -p "$B/ns/kube-public/describe"
  cat > "$B/ns/kube-public/configmaps.json" <<'EOF'
{"items":[{"kind":"ConfigMap","metadata":{"name":"cluster-info"},"data":{"kubeconfig":"apiVersion: v1\nclusters:\n- cluster:\n    certificate-authority-data: REDACTED-BY-CAPTURE\n    server: https://127.0.0.1:6443\n"}},{"kind":"ConfigMap","metadata":{"name":"kube-root-ca.crt"},"data":{"ca.crt":"-----BEGIN CERTIFICATE-----\nMIIBpublicfakecertbody\n-----END CERTIFICATE-----\n"}}]}
EOF
  echo 'Name: cluster-info' > "$B/ns/kube-public/describe/configmap_cluster-info.txt"
  # scoring-spec contract: every fixture has agent-invisible ground truth
  mkdir -p "$1/evals/scenarios/t0-selftest"
  echo '{"case_id":"t0-selftest"}' > "$1/evals/scenarios/t0-selftest/gold.json"
}
# 11. schema-complete fixture with redacted content passes
fixture "$TMP/kf"; k8sfix "$TMP/kf";                         check "valid k8s fixture passes"        0 "$TMP/kf"
# 12. service-account JWT inside a fixture log is caught
fixture "$TMP/kj"; k8sfix "$TMP/kj"
echo "Bearer eyJhbGciOi""JSUzI1NiIsImtpZCI6IngifQ.eyJpc3MiOi""JrdWJlcm5ldGVzIn0.sig" \
  > "$TMP/kj/evals/fixtures/t0-selftest/ns/payments/logs/p__c.log"
                                                             check "jwt in fixture caught"           1 "$TMP/kj"
# 13. base64'd PEM block (unredacted Secret/cert value) is caught
fixture "$TMP/kp"; k8sfix "$TMP/kp"
echo "ca.crt: LS0tLS1""CRUdJTiBDRVJUSUZJQ0FURS0tLS0t" \
  > "$TMP/kp/evals/fixtures/t0-selftest/ns/payments/logs/p__c.log"
                                                             check "base64 pem in fixture caught"    1 "$TMP/kp"
# 14. kubeconfig embed key with a live-looking value is caught
fixture "$TMP/kc"; k8sfix "$TMP/kc"
echo "client-key-data: ""TFMwdExTMUNSVWRKVGlCUWFiY2RlZmdoaWprbG1ub3A=" \
  > "$TMP/kc/evals/fixtures/t0-selftest/cluster/get-all.txt"
                                                             check "kubeconfig data in fixture caught" 1 "$TMP/kc"
# 15. fixture without its symptom-first page fails completeness
fixture "$TMP/kn"; k8sfix "$TMP/kn"; rm "$TMP/kn/evals/fixtures/t0-selftest/page.txt"
                                                             check "fixture missing page.txt caught" 1 "$TMP/kn"
# 16. fixture whose manifest lacks mode: captured|authored fails completeness
fixture "$TMP/km"; k8sfix "$TMP/km"
printf 'schema: 1\nid: t0-selftest\nlog_channels:\n  - pod: payments/p\n    container: c\n    current: ok\n    previous: ok\n' \
  > "$TMP/km/evals/fixtures/t0-selftest/scenario.yaml"
                                                             check "fixture without mode caught"     1 "$TMP/km"
# 17. ledger entries missing per-channel status fail (failure-modes 2026-08-28:
# the fixture schema must be honest about which evidence channels exist)
fixture "$TMP/kl"; k8sfix "$TMP/kl"
printf 'schema: 1\nid: t0-selftest\nmode: captured\nlog_channels:\n  - pod: payments/p\n    container: c\n' \
  > "$TMP/kl/evals/fixtures/t0-selftest/scenario.yaml"
                                                             check "status-less ledger caught"       1 "$TMP/kl"
# 18. zero-byte evidence file (pods.json) fails completeness
fixture "$TMP/kz"; k8sfix "$TMP/kz"; : > "$TMP/kz/evals/fixtures/t0-selftest/ns/payments/pods.json"
                                                             check "empty pods.json caught"          1 "$TMP/kz"
# 18b. fixture without its gold.json fails (scoring spec: unscoreable fixture)
fixture "$TMP/kg"; k8sfix "$TMP/kg"; rm "$TMP/kg/evals/scenarios/t0-selftest/gold.json"
                                                             check "fixture without gold.json caught" 1 "$TMP/kg"
# 18b2. the pipeline label inject.sh puts on scenario-owned cluster objects must
# never survive into a fixture (it names the planted object to every arm)
fixture "$TMP/kpl"; k8sfix "$TMP/kpl"
echo '{"items":[{"metadata":{"name":"x","labels":{"incident-lab.dev/scenario":"t9"}}}]}' \
  > "$TMP/kpl/evals/fixtures/t0-selftest/cluster/validatingwebhookconfigurations.json"
                                                             check "pipeline label in fixture caught" 1 "$TMP/kpl"
# 18c. capture schema 2 (2026-09-04) requires the admission webhook rosters: a
# schema-2 fixture without them was captured by a script that drifted from
# solution/fixture.py CLUSTER_KINDS — the lockstep pair, enforced mechanically
fixture "$TMP/kw"; k8sfix "$TMP/kw"
sed -i.bak 's/^schema: 1$/schema: 2/' "$TMP/kw/evals/fixtures/t0-selftest/scenario.yaml"; rm -f "$TMP/kw/evals/fixtures/t0-selftest/scenario.yaml.bak"
                                                             check "schema-2 fixture without webhook rosters caught" 1 "$TMP/kw"
# 18d. the same schema-2 fixture passes once both rosters are present
echo '{"items":[]}' > "$TMP/kw/evals/fixtures/t0-selftest/cluster/validatingwebhookconfigurations.json"
echo '{"items":[]}' > "$TMP/kw/evals/fixtures/t0-selftest/cluster/mutatingwebhookconfigurations.json"
                                                             check "schema-2 fixture with webhook rosters passes" 0 "$TMP/kw"
# --- inject.sh refusals (2026-09-04): every check below runs BEFORE the
# cluster is contacted, so a bogus --context proves which branch fired. A
# scenario that passes the checks dies later on "unreachable" instead.
inj() { # name, expected stderr substring, scratch repo root, scenario root, id
  local err; err="$(INJECT_REPO_ROOT="$3" bash "$ROOT/evals/inject.sh" --id "$5" --root "$4" --context no-such-ctx 2>&1 >/dev/null)"
  if printf '%s' "$err" | grep -q -- "$2"; then PASS=$((PASS+1)); printf 'ok   %s\n' "$1"
  else FAILED=$((FAILED+1)); printf 'FAIL %s (wanted %q in: %s)\n' "$1" "$2" "$err"; fi
}
scen() { # dir — a minimal scenario directory under $1 named $2, fault body from stdin
  mkdir -p "$1/$2"; cat > "$1/$2/fault.yaml"; echo '[PAGE] SEV1 X — ns' > "$1/$2/page.txt"
  printf '#!/bin/sh\nexit 0\n' > "$1/$2/wait.sh"; chmod +x "$1/$2/wait.sh"
}
WH='apiVersion: admissionregistration.k8s.io/v1\nkind: ValidatingWebhookConfiguration\nmetadata:\n  name: x\n  labels:\n    incident-lab.dev/scenario: %s\n'
# 18e. a ClusterRole is refused in any root
printf 'kind: ClusterRole\n' | scen "$TMP/inj-a/evals/scenarios-v2" t9-a
inj "inject: ClusterRole refused everywhere" "cluster-scoped objects" "$TMP/inj-a" evals/scenarios-v2 t9-a
# 18f. a webhook configuration is refused in the frozen root even when labelled
printf "$WH" t9-b | scen "$TMP/inj-b/evals/scenarios" t9-b
inj "inject: webhook refused in frozen root"  "frozen root stays namespaced" "$TMP/inj-b" evals/scenarios t9-b
# 18g. an UNLABELLED webhook configuration is refused in the additive root
printf 'kind: ValidatingWebhookConfiguration\nmetadata:\n  name: x\n' | scen "$TMP/inj-c/evals/scenarios-v2" t9-c
inj "inject: unlabelled webhook refused"      "carry the label" "$TMP/inj-c" evals/scenarios-v2 t9-c
# 18h. a labelled webhook configuration in the additive root passes the checks
printf "$WH" t9-d | scen "$TMP/inj-d/evals/scenarios-v2" t9-d
inj "inject: labelled webhook in v2 root passes" "unreachable" "$TMP/inj-d" evals/scenarios-v2 t9-d
# 18i. a webhook that intercepts anything but pod CREATE is refused (it could refuse the reset)
{ printf "$WH" t9-e; printf 'webhooks:\n  - rules:\n      - operations: ["CREATE", "DELETE"]\n        resources: ["namespaces"]\n'; } \
  | scen "$TMP/inj-e/evals/scenarios-v2" t9-e
inj "inject: webhook on namespace DELETE refused" "must be exactly operations" "$TMP/inj-e" evals/scenarios-v2 t9-e
# 18j. the lint is scoped to webhook documents: a Deployment's container
# `resources:` block in the same file is not a webhook rule (regression,
# 2026-09-05 — the first form of the lint refused the real scenario)
{ printf "$WH" t9-f; printf 'webhooks:\n  - rules:\n      - operations: ["CREATE"]\n        resources: ["pods"]\n---\nkind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n        - name: api\n          resources:\n            requests:\n              cpu: 10m\n'; } \
  | scen "$TMP/inj-f/evals/scenarios-v2" t9-f
inj "inject: container resources block is not a webhook rule" "unreachable" "$TMP/inj-f" evals/scenarios-v2 t9-f
# 19. STATUS.md with completed [x] items fails (open-items-only; history is git log)
fixture "$TMP/sx"; printf '# STATUS\n- [x] something already done\n- [ ] open item\n' > "$TMP/sx/STATUS.md"
                                                             check "stale [x] in STATUS.md caught"   1 "$TMP/sx"
# 20. STATUS.md with only open items passes
fixture "$TMP/so"; printf '# STATUS\n- [ ] open item\n' > "$TMP/so/STATUS.md"
                                                             check "open-items-only STATUS.md passes" 0 "$TMP/so"

# --- error-as-green regressions (2026-08-29): a scan that did not run must
# never certify. A nonexistent --package dir is exit 2 before any scan; an
# unreadable subdir makes grep rc>=2, which must count as a FAILURE, not a
# clean pass (it printed all-green against nothing before the fix).
check "nonexistent package dir exits 2"       2 "$TMP/does-not-exist-xyz"
if [ "$(id -u)" -ne 0 ]; then  # root reads anything; the plant only works unprivileged
  fixture "$TMP/unreadable"; mkdir -p "$TMP/unreadable/locked"; chmod 000 "$TMP/unreadable/locked"
                                               check "unreadable subdir fails, not green"  1 "$TMP/unreadable"
  chmod 755 "$TMP/unreadable/locked"
fi

# --- postedit_gate.sh: must fail SAFE. A gate that fires for environmental
# reasons (missing linter, unparsable payload) is noise, and noise gets ignored.
pe() { # name, expected exit, stdin payload
  local rc=0
  printf '%s' "$3" | bash "$ROOT/scripts/postedit_gate.sh" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq "$2" ]; then PASS=$((PASS+1)); printf 'ok   %s\n' "$1"
  else FAILED=$((FAILED+1)); printf 'FAIL %s (want exit %s, got %s)\n' "$1" "$2" "$rc"; fi
}
pe "postedit: unparsable payload is a no-op"  0 'not json'
pe "postedit: empty payload is a no-op"       0 '{}'
pe "postedit: out-of-scope file is a no-op"   0 '{"tool_input":{"file_path":"/tmp/README.md"}}'
pe "postedit: missing in-scope file is a no-op" 0 '{"tool_input":{"file_path":"/x/solution/a.py"}}'
# non-python file in scope must be a no-op: ruff parses .sh as python = 370 fake errors
pe "postedit: non-python in-scope file no-op" 0 "{\"tool_input\":{\"file_path\":\"$ROOT/tests/test_gates.sh\"}}"
# true positive: with the toolchain present (post-stack), a real finding in an
# existing in-scope file must block — the fail-safe exits above must not eat it
mkdir -p "$TMP/solution"; printf 'import os\n' > "$TMP/solution/unused_import.py"
pe "postedit: real lint finding blocks"       2 "{\"tool_input\":{\"file_path\":\"$TMP/solution/unused_import.py\"}}"
# common/ holds the shared kernel — the hook must cover it too (review finding 2026-08-28)
mkdir -p "$TMP/common"; printf 'import os\n' > "$TMP/common/unused_import.py"
pe "postedit: common/ is in hook scope"       2 "{\"tool_input\":{\"file_path\":\"$TMP/common/unused_import.py\"}}"

echo "== test_gates: $PASS passed, $FAILED failed"
exit "$([ "$FAILED" -eq 0 ] && echo 0 || echo 1)"
