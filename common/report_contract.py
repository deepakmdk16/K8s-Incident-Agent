"""The output contract BOTH arms put in their prompt, verbatim.

Sharing this text is a fairness invariant: baseline and solution differ in how
they investigate, never in what they are asked to produce. The four report
sections and the discrete-verdict rule mirror the report contract in
docs/decisions/problem-selection.md; the JSON schema mirrors the answer schema
in evals/scoring.md. Candidate fault types are deliberately NOT enumerated
anywhere in this text (anti-multiple-choice).
"""

from __future__ import annotations

import re

_FENCED_JSON = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def extract_answer(response_text: str) -> str:
    """The LAST fenced json block, per the contract below. Raises if absent.

    Lives next to the contract text so every arm parses answers identically.
    """
    blocks = _FENCED_JSON.findall(response_text)
    if not blocks:
        raise ValueError("response contains no fenced json answer block")
    return blocks[-1]


OUTPUT_CONTRACT = """\
Write your findings as a markdown report with EXACTLY these four sections:

## Root cause
The failing resource and the causal mechanism, stated plainly.

## Evidence chain
Every claim cites the exact piece of the provided output it came from (name the
source, e.g. "describe of pod X", "log line: ...", quote the relevant line).

## Investigation ledger
Each alternative explanation you considered and ruled out, with the evidence
that ruled it out. If you considered only one explanation, say so.

## Verification recipe
The 2-3 exact commands a human runs to independently confirm the root cause in
under 2 minutes.

Verdict rules (use the word, never a number — numeric self-confidence such as
"90% sure" is forbidden anywhere in the report):
- "confirmed": you have direct causal evidence linking mechanism to symptom.
- "probable": evidence is consistent but indirect.
- "inconclusive": name the additional evidence that would settle it.

After the report, output the machine-read answer as the LAST fenced code block,
exactly this shape:

```json
{
  "case_id": "<case id, echoed verbatim>",
  "failing_resource": {"kind": "...", "namespace": "...", "name": "..."},
  "mechanism": "1-3 sentences: the causal mechanism of the paged symptom only.",
  "verdict": "confirmed | probable | inconclusive",
  "missing_evidence": "required iff verdict is inconclusive: what would settle it"
}
```

`failing_resource` is the resource whose spec must CHANGE to fix the incident
(e.g. the owning workload, not the crashing pod it produced). In `mechanism`,
never mention any workload or mechanism other than the failing one — even to
rule it out; ruled-out alternatives belong in the Investigation ledger.
"""
