import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PatchSection:
    heading: str
    slug: str
    markdown: str
    diff_text: str


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def is_valid_unified_diff(text: str) -> bool:
    lines = text.splitlines()
    has_old = any(line.startswith("--- ") or line.startswith("---a/") or line.startswith("---b/") for line in lines)
    has_new = any(line.startswith("+++ ") or line.startswith("+++a/") or line.startswith("+++b/") for line in lines)
    has_hunk = any(line.startswith("@@") for line in lines)
    has_change_lines = any(
        (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
        for line in lines
    )
    return has_old and has_new and has_hunk and has_change_lines


def normalize_unified_diff(text: str) -> str:
    normalized_lines: list[str] = []
    for line in strip_ansi(text).splitlines():
        if line.startswith("---a/") or line.startswith("---b/"):
            line = line.replace("---", "--- ", 1)
        elif line.startswith("+++a/") or line.startswith("+++b/"):
            line = line.replace("+++", "+++ ", 1)

        if line.startswith("@@") and not line.startswith("@@ "):
            # Normalize compact hunk header forms like "@@-1,20 +1,26 @@"
            line = re.sub(r"^@@\s*", "@@ ", line)

        normalized_lines.append(line)
    return "\n".join(normalized_lines).strip() + "\n"


def _slugify(value: str) -> str:
    lowered = value.lower()
    sanitized = re.sub(r"[^a-z0-9]+", "-", lowered)
    return sanitized.strip("-") or "patch"


def extract_patch_sections(patch_md_path: Path) -> list[PatchSection]:
    if not patch_md_path.exists():
        raise FileNotFoundError(f"PATCH.md not found: {patch_md_path}")
    raw = patch_md_path.read_text(encoding="utf-8", errors="replace")
    section_pattern = r"^##\s+(Patch\s+\d+:\s+.+?)\s*$"
    matches = list(re.finditer(section_pattern, raw, re.M))
    sections: list[PatchSection] = []
    if not matches:
        raise RuntimeError(f"No patch sections found in {patch_md_path}")

    for idx, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        section_md = raw[start:end].strip() + "\n"

        diff_match = re.search(r"```diff\s*\n(.*?)\n```", section_md, re.S)
        if not diff_match:
            raise RuntimeError(f"Missing ```diff block in section '{heading}'")

        diff_text = normalize_unified_diff(diff_match.group(1))
        if not is_valid_unified_diff(diff_text):
            raise RuntimeError(f"Invalid unified diff in section '{heading}'")

        slug_seed = heading.split(":", 1)[1].strip() if ":" in heading else heading
        sections.append(
            PatchSection(
                heading=heading,
                slug=_slugify(slug_seed),
                markdown=section_md,
                diff_text=diff_text,
            )
        )
    return sections
