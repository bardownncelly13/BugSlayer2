# Vulnerability Triage Goals (for arbitrary open-source repos)

## Scope
- Use gitnexus analysis artifacts as primary structure/navigation aid.
- You may read repo files as needed, but prefer the graph for discovery.
- Do not modify repo files.

## Hard limits
- Time budget: stop after 20 minutes total.
- Token budget: be concise; max ~800 lines total output across all files.
- Vulnerabilities: find at most 1–2 high-confidence reachable issues.
- If uncertain, stop and report “needs manual review” rather than exploring its ok if there are no findings but find what is there.

## Required outputs (write these files under /opt/orchestrator)
1. ENTRYPOINTS.md
2. VULN_REPORT.md
3. POC.md
4. PATCH.diff

## 1) ENTRYPOINTS.md
- List primary runtime entrypoints (server, CLI, worker/job, tests).
- For each: file path, invocation method, purpose, confidence.
- Mark 3–10 “best starting entrypoints” for security review.

## 2) VULN_REPORT.md
- Identify up to 3 *reachable* vulnerability candidates from the chosen entrypoints.
- For each candidate:
  - Title + CWE (if applicable)
  - Attack surface and reachability chain from entrypoint (step-by-step)
  - Why it’s likely exploitable
  - Exact code locations (file:line) and the relevant snippet(s)
  - Preconditions/assumptions

## 3) POC.md (safe only)
- Provide a *non-destructive* proof of concept:
  - Demonstrates the issue without data loss, no persistence, no malware, no privilege escalation.
  - Prefer a local reproduction, test harness, or minimal request script.
  - If a GUI is appropriate, it must only display harmless output (e.g., "POC triggered") and must not execute arbitrary commands.

## 4) PATCH.diff
- Provide a minimal patch diff that mitigates the issue.
- Include reasoning for the chosen fix and any tradeoffs.
- Prefer adding tests if feasible (otherwise describe how to verify).

## Final summary format
At end of VULN_REPORT.md include a concise summary table:
- Entrypoint | Vulnerability | Reachability | PoC | Patch