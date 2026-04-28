import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from github import Github

from pr_artifacts import PatchSection, extract_patch_sections


TOKEN_ENV_CANDIDATES = ("GITHUB_TOKEN", "GITHUB_PAT", "AZURE_GITHUB_TOKEN")


@dataclass
class PRConfig:
    repo_url: str
    local_repo_path: Path
    base_branch: str
    title_prefix: str


@dataclass
class PRResult:
    branch: str
    title: str
    url: str


def resolve_token() -> str:
    for name in TOKEN_ENV_CANDIDATES:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError("Missing GitHub token. Set one of: GITHUB_TOKEN, GITHUB_PAT, AZURE_GITHUB_TOKEN")


def run_git(args: list[str], repo_path: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo_path), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout.strip()


def parse_owner_repo(repo_url: str) -> tuple[str, str]:
    normalized = repo_url.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]

    if normalized.startswith("git@"):
        _, right = normalized.split(":", 1)
        owner, repo = right.split("/", 1)
        return owner, repo

    parsed = urlparse(normalized)
    if not parsed.path:
        raise RuntimeError(f"Cannot parse repository from URL: {repo_url}")
    path = parsed.path.strip("/")
    if "/" not in path:
        raise RuntimeError(f"Cannot parse owner/repo from URL: {repo_url}")
    owner, repo = path.split("/", 1)
    return owner, repo


def build_patch_section_pr_body(section: PatchSection) -> str:
    return (
        "## Patch Section\n\n"
        f"{section.markdown}\n"
    )


def discover_repo_url(local_repo_path: Path) -> str:
    return run_git(["remote", "get-url", "origin"], local_repo_path)


def sanitize_branch_component(value: str) -> str:
    lower = value.lower()
    replaced = re.sub(r"[^a-z0-9._-]+", "-", lower)
    return replaced.strip("-.") or "security-fix"


def _publish_single_patch_pr(
    config: PRConfig,
    remote_repo,
    patch_path: Path,
    title: str,
    pr_body: str,
) -> PRResult:
    slug = sanitize_branch_component(title)[:48]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch = f"bugslayer/{slug}-{stamp}"

    run_git(["checkout", config.base_branch], config.local_repo_path)
    run_git(["pull", "--ff-only", "origin", config.base_branch], config.local_repo_path)
    base_status = run_git(["status", "--porcelain"], config.local_repo_path)
    if base_status:
        raise RuntimeError(
            "Local repository has uncommitted changes. Commit or stash them before running publish_patch_prs.py."
        )
    run_git(["checkout", "-b", branch], config.local_repo_path)

    try:
        try:
            run_git(["apply", "--index", "--recount", str(patch_path)], config.local_repo_path)
        except RuntimeError:
            # If index matching fails, attempt a 3-way apply and then stage everything.
            run_git(["apply", "--recount", str(patch_path)], config.local_repo_path)
            run_git(["add", "--all", "--", "."], config.local_repo_path)
        status = run_git(["status", "--porcelain"], config.local_repo_path)
        if not status:
            raise RuntimeError("Patch applied but produced no git changes.")
        run_git(["commit", "-m", title], config.local_repo_path)
        run_git(["push", "-u", "origin", branch], config.local_repo_path)
    except Exception:
        run_git(["checkout", config.base_branch], config.local_repo_path)
        run_git(["branch", "-D", branch], config.local_repo_path)
        raise

    pr = remote_repo.create_pull(
        title=title,
        body=pr_body,
        head=branch,
        base=config.base_branch,
    )
    return PRResult(branch=branch, title=title, url=pr.html_url)


def publish_prs_from_patch_md(config: PRConfig, patch_md_path: Path) -> list[PRResult]:
    token = resolve_token()
    owner, repo = parse_owner_repo(config.repo_url)
    github_client = Github(token)
    remote_repo = github_client.get_repo(f"{owner}/{repo}")
    sections = extract_patch_sections(patch_md_path)

    results: list[PRResult] = []
    with TemporaryDirectory(prefix="bugslayer-patches-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, section in enumerate(sections, start=1):
            title = f"{config.title_prefix}{section.heading}"
            body = build_patch_section_pr_body(section)
            print(section.diff_text)
            patch_path = temp_root / f"patch-{index:02d}-{section.slug}.diff"
            patch_path.write_text(section.diff_text, encoding="utf-8")
            results.append(
                _publish_single_patch_pr(
                    config=config,
                    remote_repo=remote_repo,
                    patch_path=patch_path,
                    title=title,
                    pr_body=body,
                )
            )
    return results
