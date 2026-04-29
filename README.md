# BugSlayer2
[![Setup Guide](https://img.shields.io/badge/Setup%20Guide-BugSlayer%202-00ff88?style=for-the-badge&logo=github&logoColor=black)](https://morganmcl.github.io/bug-slayer-setup/#bs2)
An AI-powered vulnerability triage and patch publishing pipeline for arbitrary open-source repositories. BugSlayer2 uses Claude Code (via the `gitnexus` MCP server for code graph navigation) to autonomously identify high-confidence security vulnerabilities, generate proof-of-concept exploits, produce minimal patches, and automatically open GitHub pull requests — one PR per patch section.

---

## Overview

BugSlayer2 operates as a structured agentic workflow. Given a target repository, it:

1. **Triages the codebase** — identifies primary runtime entrypoints and attack surfaces using `gitnexus` graph-based analysis.
2. **Produces security artifacts** — writes `ENTRYPOINTS.md`, `VULN_REPORT.md`, `POC.md`, and `PATCH.md` under `/opt/orchestrator`.
3. **Publishes patch PRs** — parses `PATCH.md` into individual sections and opens a scoped GitHub pull request for each fix.

The entire pipeline is designed to operate within strict time and token budgets (20-minute cap, ~800 lines of output), keeping findings focused and actionable rather than exhaustive.

---

## Repository Structure

```
BugSlayer2/
├── claude.json.template      # Claude Code project config template (MCP server setup, feature flags)
├── GOALS.md                  # Agentic task specification: scope, hard limits, required outputs
├── pr_artifacts.py           # Parses PATCH.md into structured PatchSection objects
├── pr_publisher.py           # Core PR publishing logic: branch creation, patch apply, GitHub API
├── publish_patch_prs.py      # CLI entrypoint: reads args, orchestrates PR publishing
├── requirements.txt          # Python dependencies
└── runGCP.py                 # GCP execution runner (cloud-based pipeline entrypoint)
```

---

## How It Works

### Phase 1 — Vulnerability Triage (Claude Code + gitnexus)

Driven by the agent specification in `GOALS.md`, a Claude Code session:

- Uses the `gitnexus` MCP server to navigate the target repo's code graph without modifying any files.
- Identifies up to 3 reachable vulnerability candidates from primary entrypoints.
- Writes four structured output files to `/opt/orchestrator`:

| File | Contents |
|------|----------|
| `ENTRYPOINTS.md` | Runtime entrypoints with file paths, invocation methods, and confidence ratings |
| `VULN_REPORT.md` | Vulnerability candidates with CWE, reachability chains, code locations, and a summary table |
| `POC.md` | Non-destructive proof-of-concept reproduction steps |
| `PATCH.md` | Minimal patch diffs per vulnerability, with fix rationale |

The agent stops and reports "needs manual review" rather than producing low-confidence findings.

### Phase 2 — Patch PR Publishing

Once `PATCH.md` is produced, the Python tooling automates GitHub PR creation:

```
publish_patch_prs.py
    └── pr_publisher.py        # Branches, applies patch, commits, pushes, opens PR
        └── pr_artifacts.py    # Parses ## Patch N sections from PATCH.md into diffs
```

Each `## Patch N` section in `PATCH.md` becomes its own `bugslayer/<slug>-<timestamp>` branch and pull request against the configured base branch.

---

## Setup

### Prerequisites

- Python 3.11+
- `git` available on `PATH`
- A GitHub token with `repo` scope
- Claude Code with the `gitnexus` MCP server configured (see `claude.json.template`)

### Installation

```bash
git clone https://github.com/bardownncelly13/BugSlayer2.git
cd BugSlayer2
pip install -r requirements.txt
```

### Configuration

Copy and adapt the Claude Code project config:

```bash
cp claude.json.template claude.json
```

The template pre-configures the `gitnexus` MCP server at `/usr/bin/gitnexus mcp` and sets the relevant Claude Code feature flags for the agentic workflow.

Set your GitHub token in the environment (any of the following):

```bash
export GITHUB_TOKEN=ghp_...
# or
export GITHUB_PAT=ghp_...
# or
export AZURE_GITHUB_TOKEN=...
```

A `.env` file is also supported via `python-dotenv`.

---

## Usage

### Running the Triage Agent

Open the target repository in Claude Code with BugSlayer2's `claude.json` config active. The agent follows the `GOALS.md` specification and writes its findings to `/opt/orchestrator`.

### Publishing Patch PRs

After the triage run produces a `PATCH.md`:

```bash
python publish_patch_prs.py \
  --patch-md /opt/orchestrator/PATCH.md \
  --local-repo-path /path/to/target/repo \
  --base-branch main \
  --title-prefix "security: "
```

**Arguments:**

| Flag | Default | Description |
|------|---------|-------------|
| `--patch-md` | `PATCH.md` | Path to the patch markdown file |
| `--local-repo-path` | `.` | Local clone of the target repository |
| `--base-branch` | `main` | Branch PRs are opened against |
| `--repo-url` | *(auto-detected from origin)* | Override the remote URL |
| `--title-prefix` | `""` | Optional prefix for PR titles (e.g. `"security: "`) |

The script prints each created PR's branch name and URL.

---

## Output Artifacts

After a successful run, `/opt/orchestrator` will contain:

```
/opt/orchestrator/
├── ENTRYPOINTS.md   # Ranked entrypoints for security review
├── VULN_REPORT.md   # Findings with reachability chains + summary table
├── POC.md           # Safe reproduction steps
└── PATCH.md         # Diffs ready for PR publishing
```

---

## Dependencies

Key packages from `requirements.txt`:

- `PyGithub` — GitHub REST API client for PR creation
- `python-dotenv` — `.env` file support for token loading

---

## Future Work

### Integrate Deterministic Patch Verification from BugSlayer1

BugSlayer1 included a deterministic patch verification stage that confirmed applied patches actually resolved the identified vulnerability (e.g., via test harness execution, static re-analysis, or AST diffing). BugSlayer2 currently applies and commits patches without this verification step. Future work should:

- Port the deterministic verification logic from BugSlayer1 into the pipeline, running it between patch application and PR creation.
- Fail or flag PRs where verification cannot confirm the fix, rather than publishing unverified patches.
- Optionally surface verification results as a CI check comment on the opened PR.

This would close the loop from "patch proposed" to "patch confirmed effective" before human review is requested.

---

## Security Notes

- The triage agent operates **read-only** on target repositories — it does not modify repo files during analysis.
- POCs are intentionally non-destructive: no data loss, no persistence, no privilege escalation.
- GitHub tokens are never hardcoded; they are resolved from environment variables at runtime.
- The `claude.json.template` should not be committed with real tokens or sensitive paths.
