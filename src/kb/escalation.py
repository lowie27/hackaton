"""When should a person answer instead of the documents?

Looks at the whole result list, not one hit: is there an answer at all, can
the best answer be trusted, do the sources disagree, are there near-identical
copies? Returns a level and plain-language reasons, so the UI can say *why*
it recommends a person.

- "ask":     no trustworthy answer in the documents; ask an expert.
- "confirm": there is a trustworthy answer, but conflicting copies exist;
             the owner can confirm it and clean up the others.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

MIN_ANSWER = 0.3  # cross-encoder relevance below this: the result does not answer the question
MIN_TRUST = 0.8  # the best result's context factor below this: it cannot be relied on
CLEAR_WIN = 0.3  # the most trustworthy claim must beat the next one by this much
NEAR_COPY = 0.7  # word overlap between two results that makes them near-identical copies


@dataclass
class Escalation:
    level: str | None = None  # None, "confirm" or "ask"
    reasons: list[str] = field(default_factory=list)

    def ask(self, reason: str) -> None:
        self.level = "ask"
        self.reasons.append(reason)

    def confirm(self, reason: str) -> None:
        self.level = self.level or "confirm"
        self.reasons.append(reason)


def _relevant(h) -> bool:
    return h.rerank_score is None or h.rerank_score >= MIN_ANSWER


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{3,}", text.lower())}


def assess_results(hits: Sequence, disputes: Sequence = ()) -> Escalation:
    e = Escalation()
    if not hits:
        e.ask("No document you can access answers this question.")
        return e

    top = hits[0]
    if top.rerank_score is not None and top.rerank_score < MIN_ANSWER:
        e.ask(f"No result really answers the question (best match is {top.rerank_score:.0%} relevant).")
    elif getattr(top, "trust", 1.0) < MIN_TRUST:
        careful = [w for w in top.warnings if not w.startswith("contradicts")]
        e.ask(f"The best result cannot be relied on: {', '.join(careful) or 'low trust'}.")

    for d in disputes:
        values = ", ".join(dict.fromkeys(c.text for c in d.claims))
        best, runner_up = d.claims[0], d.claims[1]
        if best.trust - runner_up.trust < CLEAR_WIN:
            e.ask(f"Sources disagree on the {d.label} ({values}) and none is clearly more trustworthy.")
        else:
            e.confirm(f"{len(d.claims)} versions give a different {d.label} ({values}). Result #{best.rank} is the "
                      "most trustworthy, but the others are still findable: its owner can confirm and clean up.")

    if not disputes:
        top3 = [h for h in hits[:3] if _relevant(h)]
        for i, a in enumerate(top3):
            for b in top3[i + 1:]:
                wa, wb = _words(a.text), _words(b.text)
                if a.doc_id != b.doc_id and wa and wb and len(wa & wb) / len(wa | wb) >= NEAR_COPY:
                    e.confirm(f"Results '{a.title}' and '{b.title}' are near-identical copies: "
                              "check which one is maintained.")
    return e
