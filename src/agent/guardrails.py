"""Guardrails: enforce the chatbot's safety restrictions.

Implemented checks:
  * Forbidden subject  - detect questions about how the bot was built and
    short-circuit with the configured refusal message.
  * Redirect rules     - detect intents that should push the client to a
    website or to another WhatsApp vendor (configured in config.yaml).
  * Grounding check    - after generation, verify the answer is actually
    supported by the retrieved knowledge (lexical overlap heuristic).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import RedirectRule, Settings

# Questions about the internal construction of the bot -> refusal.
_FORBIDDEN_PATTERNS = [
    r"how\s+(were|are|was)\s+you\s+(built|made|created|developed|programmed|trained)",
    r"who\s+(built|made|created|developed|programmed|trained|designed)\s+you",
    r"your\s+(source\s*code|code|prompt|system\s*prompt|instructions|developer|creators|architecture|training|model)",
    r"what\s+(are|were)\s+you\s+(built|made|programmed)\s+(with|in|using)",
    r"what\s+(is|are)\s+your\s+(system\s*prompt|instructions|prompt)",
    r"show\s+(me\s+)?your\s+(prompt|instructions|code|system\s*message)",
    r"what\s+model\s+are\s+you",
    r"how\s+do\s+you\s+work\s+internally",
    r"leak\s+(your|the)\s+(prompt|instructions|code)",
]
_FORBIDDEN_REGEX = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_PATTERNS]

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "is", "are", "was", "were", "be", "do", "does", "did", "i", "you",
    "it", "we", "they", "this", "that", "your", "our", "can", "could", "will",
    "would", "please", "from", "as", "about", "not", "what", "how", "my", "me",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


class Guardrails:
    def __init__(self, settings: "Settings"):
        self._settings = settings

    # -- 1. Never talk about how the bot was built --------------------------
    def forbidden_subject(self, text: str) -> str | None:
        """Return the refusal message if the message targets the bot's internals."""
        for pattern in _FORBIDDEN_REGEX:
            if pattern.search(text):
                return self._settings.agent.refusal_message
        return None

    # -- 2. Redirect to a website or another WhatsApp vendor ----------------
    def match_redirect(self, text: str) -> "RedirectRule | None":
        if not self._settings.redirects.enabled:
            return None
        lowered = text.lower()
        for rule in self._settings.redirects.rules:
            for keyword in rule.keywords:
                if keyword.lower() in lowered:
                    return rule
        return None

    # -- 3. Answer must be grounded in the retrieved knowledge --------------
    def is_grounded(self, answer: str, context_texts: list[str]) -> bool:
        if not self._settings.knowledge.enable_grounding_check:
            return True
        if not answer:
            return False

        answer_tokens = _tokenize(answer)
        if not answer_tokens:
            return True  # nothing to verify (e.g. pure numbers/emojis)

        context_blob = " ".join(context_texts).lower()
        hits = sum(1 for token in answer_tokens if token in context_blob)
        overlap = hits / len(answer_tokens)
        return overlap >= self._settings.knowledge.grounding_min_overlap

    def retrieval_ok(self, hits: list, threshold: float | None = None) -> bool:
        """The best retrieved chunk must clear the similarity threshold."""
        threshold = (
            threshold
            if threshold is not None
            else self._settings.knowledge.score_threshold
        )
        if not hits:
            return False
        return hits[0].similarity >= threshold
