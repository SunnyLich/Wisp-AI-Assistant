"""Dependency-free secret scanner for the tracked repository tree.

The scanner deliberately reports only rule names and short SHA-256 fingerprints;
it never echoes a possible credential into CI logs. Intentional synthetic test
fixtures must carry ``secret-scan: allow`` on the same source line.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "secret-scan: allow"
MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class SecretRule:
    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    fingerprint: str


RULES: tuple[SecretRule, ...] = (
    SecretRule("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    SecretRule("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretRule("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    SecretRule("groq-key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    SecretRule("github-token", re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    SecretRule("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    SecretRule("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    SecretRule("slack-token", re.compile(r"\bxox(?:[abprs]|o)-[A-Za-z0-9-]{20,}\b")),
    SecretRule("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    SecretRule("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b")),
    SecretRule(
        "credential-assignment",
        re.compile(
            r"(?i)\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"password|passwd|passphrase|secret)\b\s*[:=]\s*['\"]([^'\"\r\n]{12,})['\"]"
        ),
    ),
)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def scan_text(text: str, *, path: str) -> list[Finding]:
    """Return redacted findings for one text file."""
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line.casefold():
            continue
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                findings.append(
                    Finding(
                        path=path,
                        line=line_number,
                        rule=rule.name,
                        fingerprint=_fingerprint(match.group(0)),
                    )
                )
    return findings


def tracked_files(repo_root: Path) -> list[Path]:
    """Return Git-tracked files without consulting user-global ignore rules."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return [repo_root / item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item]


def scan_repository(repo_root: Path) -> list[Finding]:
    """Scan bounded, text-like tracked files under *repo_root*."""
    findings: list[Finding] = []
    for path in tracked_files(repo_root):
        try:
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        findings.extend(scan_text(text, path=path.relative_to(repo_root).as_posix()))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan tracked files for committed credentials.")
    parser.add_argument("repo", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()
    findings = scan_repository(repo_root)
    if not findings:
        print("Secret scan passed: no credential signatures found in tracked files.")
        return 0
    print(f"Secret scan failed: {len(findings)} potential credential(s) found.", file=sys.stderr)
    for finding in findings:
        print(
            f"{finding.path}:{finding.line}: [{finding.rule}] fingerprint={finding.fingerprint}",
            file=sys.stderr,
        )
    print(f"Use '{ALLOW_MARKER}' only for intentional synthetic fixtures.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
