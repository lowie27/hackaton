"""Where do the results disagree? Explainable contradiction detection.

Search results often say almost the same thing with a different number: 92%
in the policy, 93% in a Teams copy, 85% in an old FAQ. Flagging them as
"be careful" is not enough; the user needs to see *which fact* differs and
which source says what.

This module extracts checkable facts (percentages, euro amounts, numbers of
days/weeks/months/years) from each result, groups facts of the same kind
whose sentences talk about the same thing (word overlap), and reports groups
where the documents give different values. Documents for different countries
are never compared: 92% in Belgium and 8% in the Netherlands is a different
context, not a contradiction. No language model: every dispute can be traced
to two quoted sentences.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Two sentences are about the same thing when they share at least MIN_SHARED content
# words and that is at least SAME_TOPIC of the shorter sentence's words.
SAME_TOPIC = 0.3
MIN_SHARED = 3
# Only report disputes that involve one of the top LEAD results: a disagreement
# between two off-topic results further down is noise for this question.
LEAD = 3
# With a cross-encoder rerank, its score is an absolute relevance judgement: a dispute
# also needs a claim from a result it considers relevant to the question.
MIN_RERANK = 0.25
# Without rerank scores: the disputed sentences must contain this share of the question's words.
MIN_QUERY_COVERAGE = 0.5

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "twelve": 12}
KIND_LABEL = {"percent": "percentage", "amount": "amount", "day": "number of days", "week": "number of weeks",
              "month": "number of months", "year": "number of years"}
_NUM = r"\d+(?:[.,]\d+)?"
_PATTERNS = [
    ("percent", re.compile(rf"({_NUM})\s?%")),
    ("amount", re.compile(rf"(?:EUR|€)\s?({_NUM})|({_NUM})\s?(?:EUR|euro)", re.I)),
    ("unit", re.compile(rf"\b({_NUM}|{'|'.join(NUMBER_WORDS)})\s+(day|week|month|year)s?\b", re.I)),
]
STOPWORDS = set("""
the and for are but not you your with from that this than then they them their there have has had was were will
would can could should may might must its into onto per all any each only also such when what which who whom how
our out about over under after before during until unless more most less least very just been being does did
eur euro
""".split())


@dataclass
class Fact:
    kind: str
    value: float
    text: str  # as written, e.g. "92%", "EUR 150", "two days"
    sentence: str
    words: frozenset[str]


@dataclass
class Claim:
    rank: int
    doc_id: int
    title: str
    value: float
    text: str
    sentence: str
    trust: float = 1.0  # context factor of the hit (kb.context); 1.0 when context ranking is off


@dataclass
class Dispute:
    kind: str
    topic: str  # the words the disagreeing sentences share
    claims: list[Claim] = field(default_factory=list)  # most trustworthy first (trust, then rank)
    _words: frozenset[str] = frozenset()
    _country: str | None = None

    @property
    def label(self) -> str:
        return KIND_LABEL[self.kind]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "topic": self.topic,
                "claims": [c.__dict__ for c in self.claims]}


def _words(sentence: str) -> frozenset[str]:
    out = set()
    for w in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", sentence.lower()):
        if len(w) < 3 or w in STOPWORDS or w in NUMBER_WORDS:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)  # crude plural folding
    return frozenset(out)


def _number(raw: str) -> float:
    return float(NUMBER_WORDS.get(raw.lower(), None) or raw.replace(",", "."))


def extract_facts(text: str) -> list[Fact]:
    facts = []
    for sentence in (s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)):
        if not sentence:
            continue
        words = _words(sentence)
        for kind, pattern in _PATTERNS:
            for m in pattern.finditer(sentence):
                if kind == "unit":
                    facts.append(Fact(m.group(2).lower(), _number(m.group(1)), m.group(0), sentence, words))
                else:
                    raw = next(g for g in m.groups() if g)
                    facts.append(Fact(kind, _number(raw), m.group(0).strip(), sentence, words))
    return facts


def _same_topic(a: frozenset, b: frozenset) -> float:
    """Overlap relative to the shorter sentence (0 when below the thresholds)."""
    shared = len(a & b)
    if shared < MIN_SHARED:
        return 0.0
    score = shared / min(len(a), len(b))
    return score if score >= SAME_TOPIC else 0.0


def _compatible(a: str | None, b: str | None) -> bool:
    return not a or not b or a.strip().upper() == b.strip().upper()


def find_disputes(hits: Sequence, query: str | None = None, lead: int = LEAD) -> list[Dispute]:
    """hits: ranked search results with doc_id, title, text, country and (optionally) trust.

    Returns only real disagreements. Within a dispute, claims[0] is the most trustworthy
    source: all claims are about the same fact, so trust decides, not text relevance.
    """
    groups: list[Dispute] = []
    for rank, hit in enumerate(hits, start=1):
        for fact in extract_facts(hit.text):
            scored = [
                (_same_topic(g._words, fact.words), i)
                for i, g in enumerate(groups)
                if g.kind == fact.kind and _compatible(g._country, hit.country)
            ]
            best = max(scored, default=(0.0, -1))
            group = groups[best[1]] if best[0] > 0 else None
            if group is None:
                group = Dispute(fact.kind, "", _words=fact.words, _country=hit.country)
                groups.append(group)
            if any(c.doc_id == hit.doc_id for c in group.claims):
                continue  # one claim per document per topic
            group._country = group._country or hit.country
            trust = getattr(hit, "trust", 1.0)
            group.claims.append(Claim(rank, hit.doc_id, hit.title, fact.value, fact.text, fact.sentence, trust))

    relevant = {
        rank for rank, hit in enumerate(hits, start=1)
        if rank <= lead and (getattr(hit, "rerank_score", None) is None or hit.rerank_score >= MIN_RERANK)
    }
    reranked = any(getattr(h, "rerank_score", None) is not None for h in hits)
    query_words = _words(query or "")

    def about_the_question(g: Dispute) -> bool:
        if reranked or not query_words:
            return True
        said = frozenset().union(*(_words(c.sentence) for c in g.claims))
        return len(query_words & said) / len(query_words) >= MIN_QUERY_COVERAGE

    disputes = [
        g for g in groups
        if len({c.value for c in g.claims}) > 1 and any(c.rank in relevant for c in g.claims) and about_the_question(g)
    ]
    for d in disputes:
        d.claims.sort(key=lambda c: (-c.trust, c.rank))
        shared = [set(_words(c.sentence)) for c in d.claims]
        common = set.intersection(*shared) if shared else set()
        first = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", d.claims[0].sentence.lower())
        topic = []
        for w in first:
            key = w[:-1] if len(w) > 4 and w.endswith("s") else w
            if key in common and w not in topic:
                topic.append(w)
        d.topic = " ".join(topic)
    return disputes


def annotate(hits: Sequence, query: str | None = None) -> list[Dispute]:
    """Attach per-hit disagreements (hit.disagreements) and return the disputes.

    The most trustworthy claim lists everyone who disagrees with it; every other
    claim lists only the most trustworthy one, so the UI can say "#1 says 92%".
    """
    disputes = find_disputes(hits, query)
    by_rank = {rank: hit for rank, hit in enumerate(hits, start=1)}
    for d in disputes:
        best = d.claims[0]
        for claim in d.claims:
            others = [c for c in d.claims if c.value != claim.value] if claim is best else (
                [best] if best.value != claim.value else [])
            for other in others:
                by_rank[claim.rank].disagreements.append({
                    "label": d.label, "topic": d.topic, "value": claim.text, "sentence": claim.sentence,
                    "most_trusted": claim is best,
                    "other_rank": other.rank, "other_title": other.title, "other_value": other.text,
                    "other_sentence": other.sentence,
                })
    return disputes
