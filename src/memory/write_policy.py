"""Write-acceptance policy for managed session memory."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.memory.types import MemorySettings, MemoryWriteCandidate


_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b.{0,32}[:=]\s*\S+"),
]
_CHAIN_OF_THOUGHT_PATTERNS = [
    re.compile(r"(?i)\bchain[- ]of[- ]thought\b"),
    re.compile(r"(?i)\bhidden reasoning\b"),
    re.compile(r"(?i)\binternal reasoning\b"),
    re.compile(r"(?i)\bstep-by-step reasoning\b"),
]
_LOW_VALUE_TITLE_PATTERNS = [
    re.compile(r"(?i)^(note|todo|memory|fact|decision|task)$"),
]


@dataclass(frozen=True)
class MemoryPolicyDecision:
    """Decision returned by the manual or autonomous write policy."""

    accepted: bool
    reason: str


def contains_secret_like_text(text: str) -> bool:
    """Return whether the text looks like a secret that should not persist."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def contains_reasoning_trace(text: str) -> bool:
    """Return whether the text looks like chain-of-thought or internal reasoning."""
    return any(pattern.search(text) for pattern in _CHAIN_OF_THOUGHT_PATTERNS)


def evaluate_manual_write(candidate: MemoryWriteCandidate, settings: MemorySettings) -> MemoryPolicyDecision:
    """Validate explicit human/tool/API writes."""
    if settings.mode == "off":
        return MemoryPolicyDecision(False, "session memory mode is off")
    text = f"{candidate.title}\n{candidate.content}"
    if contains_secret_like_text(text):
        return MemoryPolicyDecision(False, "memory content looks secret-like")
    if contains_reasoning_trace(text):
        return MemoryPolicyDecision(False, "chain-of-thought style content cannot be persisted")
    if candidate.source in {"tool", "web"} and (candidate.confidence is None or candidate.confidence < 0.75):
        return MemoryPolicyDecision(False, "tool- or web-derived memory requires confidence >= 0.75")
    return MemoryPolicyDecision(True, "accepted")


def evaluate_autonomous_write(candidate: MemoryWriteCandidate, settings: MemorySettings) -> MemoryPolicyDecision:
    """Validate conservative autonomous writeback candidates."""
    if settings.mode != "auto":
        return MemoryPolicyDecision(False, "session memory mode does not allow autonomous writeback")
    text = f"{candidate.title}\n{candidate.content}"
    if contains_secret_like_text(text):
        return MemoryPolicyDecision(False, "memory content looks secret-like")
    if contains_reasoning_trace(text):
        return MemoryPolicyDecision(False, "chain-of-thought style content cannot be persisted")
    if len(candidate.content.strip()) < 16:
        return MemoryPolicyDecision(False, "memory candidate is too short to be durable")
    if any(pattern.match(candidate.title.strip()) for pattern in _LOW_VALUE_TITLE_PATTERNS):
        return MemoryPolicyDecision(False, "memory title is too generic")
    if candidate.confidence is not None and candidate.confidence < 0.65:
        return MemoryPolicyDecision(False, "memory candidate confidence is too low")
    return MemoryPolicyDecision(True, "accepted")


__all__ = [
    "MemoryPolicyDecision",
    "contains_reasoning_trace",
    "contains_secret_like_text",
    "evaluate_autonomous_write",
    "evaluate_manual_write",
]
