"""Git hook guardrail to block obvious real secrets from commits and pushes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
ZERO_SHA = "0" * 40

ASSIGNMENT_PATTERNS = (
    re.compile(
        r"(?P<name>\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b)\s*[:=]\s*(?P<value>.+)"
    ),
    re.compile(
        r"(?P<name>\b(?:api_key|apiKey|token|secret|password)\b)\s*[:=]\s*(?P<value>.+)"
    ),
)

KNOWN_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"),
)

PLACEHOLDER_MARKERS = (
    "...",
    "<your",
    "your-key",
    "your_key",
    "your-api-key",
    "your_api_key",
    "your-azure-key",
    "your token",
    "your-token",
    "example",
    "placeholder",
    "dummy",
    "test-key",
    "fake",
    "mock",
    "sample",
    "replace-me",
    "changeme",
    "not-needed",
)


@dataclass(frozen=True)
class Finding:
    """Represents one detected secret in a diff."""

    path: str
    line_number: int
    variable_name: str
    preview: str


def run_git(args: Sequence[str]) -> str:
    """Run a git command from the repository root."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def normalize_value(raw_value: str) -> str:
    """Trim quotes and punctuation around candidate secret values."""
    value = raw_value.strip()
    value = value.split("#", 1)[0].strip()
    value = value.rstrip(",;")

    while value and value[0] in "([{":
        value = value[1:].lstrip()
    while value and value[-1] in ")]}":
        value = value[:-1].rstrip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]

    return value.strip()


def is_placeholder(value: str) -> bool:
    """Allow common documentation and test placeholders."""
    lowered = value.lower()

    if not value or lowered in {"none", "null", "false", "true"}:
        return True

    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True

    if value.startswith("$") or value.startswith("${"):
        return True

    if "os.environ" in value or "getenv(" in value or "environ[" in value:
        return True

    if value.startswith("http://") or value.startswith("https://"):
        return True

    return False


def looks_like_secret(value: str) -> bool:
    """Best-effort check for real secret-looking values."""
    if is_placeholder(value):
        return False

    if any(pattern.search(value) for pattern in KNOWN_SECRET_PATTERNS):
        return True

    if len(value) < 20 or re.search(r"\s", value):
        return False

    if not re.fullmatch(r"[A-Za-z0-9._\-]+", value):
        return False

    has_letter = any(character.isalpha() for character in value)
    has_digit = any(character.isdigit() for character in value)
    return has_letter and has_digit


def build_preview(line: str, max_length: int = 120) -> str:
    """Build a readable preview without dumping the full secret."""
    sanitized = line.strip()
    if len(sanitized) <= max_length:
        return sanitized
    return sanitized[: max_length - 3] + "..."


def find_secret_in_line(path: str, line_number: int, line: str) -> Finding | None:
    """Return one finding when a line appears to add a real secret."""
    for pattern in ASSIGNMENT_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue

        value = normalize_value(match.group("value"))
        if looks_like_secret(value):
            return Finding(
                path=path,
                line_number=line_number,
                variable_name=match.group("name"),
                preview=build_preview(line),
            )

    for pattern in KNOWN_SECRET_PATTERNS:
        if pattern.search(line) and not is_placeholder(line):
            return Finding(
                path=path,
                line_number=line_number,
                variable_name="inline secret",
                preview=build_preview(line),
            )

    return None


def scan_unified_diff(diff_text: str) -> List[Finding]:
    """Scan added lines from a unified diff."""
    findings: List[Finding] = []
    current_path: str | None = None
    next_line_number: int | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            destination = raw_line[4:]
            current_path = None if destination == "/dev/null" else destination.removeprefix("b/")
            continue

        if raw_line.startswith("@@ "):
            match = re.search(r"\+(\d+)", raw_line)
            next_line_number = int(match.group(1)) if match else None
            continue

        if current_path is None or next_line_number is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            finding = find_secret_in_line(current_path, next_line_number, raw_line[1:])
            if finding:
                findings.append(finding)
            next_line_number += 1
            continue

        if raw_line.startswith(" "):
            next_line_number += 1

    return findings


def unique_commits(commits: Iterable[str]) -> List[str]:
    """Preserve commit order while removing duplicates."""
    seen: set[str] = set()
    ordered: List[str] = []
    for commit in commits:
        if commit and commit not in seen:
            seen.add(commit)
            ordered.append(commit)
    return ordered


def staged_diff() -> str:
    """Get the staged diff that would be committed."""
    return run_git(["diff", "--cached", "--no-color", "--unified=0", "--diff-filter=ACMR"])


def diff_for_commits(commits: Sequence[str]) -> str:
    """Get the combined patch for a sequence of commits."""
    patches = [
        run_git(["show", "--format=", "--no-color", "--unified=0", "--find-renames", commit, "--"])
        for commit in commits
    ]
    return "\n".join(patches)


def commits_from_pre_push(stdin_text: str) -> List[str]:
    """Resolve the commits being pushed from the pre-push hook payload."""
    commits: List[str] = []

    for line in stdin_text.splitlines():
        if not line.strip():
            continue

        local_ref, local_sha, _remote_ref, remote_sha = line.split()
        if local_sha == ZERO_SHA:
            continue

        if remote_sha == ZERO_SHA:
            rev_list = run_git(["rev-list", local_sha, "--not", "--remotes"])
        else:
            rev_list = run_git(["rev-list", f"{remote_sha}..{local_sha}"])

        commits.extend(rev_list.splitlines())

    return unique_commits(commits)


def report_findings(findings: Sequence[Finding]) -> int:
    """Print a useful error and return the exit status."""
    if not findings:
        return 0

    print("Secret guard blocked this git action.", file=sys.stderr)
    print("Possible real secret values were found in added lines:", file=sys.stderr)

    for finding in findings:
        print(
            f"  - {finding.path}:{finding.line_number} "
            f"({finding.variable_name}) -> {finding.preview}",
            file=sys.stderr,
        )

    print(file=sys.stderr)
    print("Move the value into environment variables or .env, then try again.", file=sys.stderr)
    print("If a key was already committed, rotate it before pushing.", file=sys.stderr)
    return 1


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="Scan staged changes.")
    mode.add_argument(
        "--pre-push",
        action="store_true",
        help="Scan commits read from pre-push hook stdin.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, stdin_text: str | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv or sys.argv[1:])

    if args.staged:
        diff_text = staged_diff()
    else:
        commits = commits_from_pre_push(stdin_text if stdin_text is not None else sys.stdin.read())
        if not commits:
            return 0
        diff_text = diff_for_commits(commits)

    findings = scan_unified_diff(diff_text)
    return report_findings(findings)


if __name__ == "__main__":
    raise SystemExit(main())
