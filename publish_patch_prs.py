import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from pr_publisher import PRConfig, discover_repo_url, publish_prs_from_patch_md


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish one GitHub PR per patch section in PATCH.md")
    parser.add_argument("--repo-url", help="Git remote URL. Defaults to origin URL from local repo.")
    parser.add_argument("--local-repo-path", default=".", help="Local git repository path")
    parser.add_argument("--patch-md", default="PATCH.md", help="Markdown file containing ## Patch N sections")
    parser.add_argument("--base-branch", default="main", help="Base branch for each PR")
    parser.add_argument("--title-prefix", default="", help="Optional PR title prefix (e.g. 'security: ')")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    local_repo_path = Path(args.local_repo_path).resolve()
    patch_md_path = Path(args.patch_md).resolve()
    repo_url = args.repo_url or discover_repo_url(local_repo_path)
    config = PRConfig(
        repo_url=repo_url,
        local_repo_path=local_repo_path,
        base_branch=args.base_branch,
        title_prefix=args.title_prefix,
    )

    results = publish_prs_from_patch_md(config=config, patch_md_path=patch_md_path)
    for result in results:
        print(f"- {result.title}")
        print(f"  branch: {result.branch}")
        print(f"  url: {result.url}")
    if results:
        return -1
    return 0


if __name__ == "__main__":
    sys.exit(main())
