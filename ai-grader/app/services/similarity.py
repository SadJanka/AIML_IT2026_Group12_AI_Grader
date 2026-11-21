import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from markupsafe import Markup, escape

from .. import models

WORD = re.compile(r"\w+", re.UNICODE)
SHINGLE = 5
MIN_FEATURES = 8
DEFAULT_THRESHOLD = 70


@dataclass
class Pair:
    a: models.Submission
    b: models.Submission
    score: float
    shared_words: int
    decision: str = ""

    @property
    def percent(self) -> float:
        return 100 * self.score


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return [_fold(m.group()) for m in WORD.finditer(text or "")]


def _ngrams(words: list[str]) -> list[str]:
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def source_terms(assignment: models.Assignment) -> set[str]:
    parts = [assignment.instructions or "", assignment.model_answer or ""]
    for c in assignment.criteria:
        parts += [c.name, c.description or "", c.expected_answer or ""]
    return set(_ngrams(tokens(" ".join(parts))))


def tfidf_vectors(texts: list[str], ignore: set[str] = frozenset()) -> list[dict[str, float]]:
    counts = [Counter(t for t in _ngrams(tokens(text)) if t not in ignore) for text in texts]
    df = Counter(term for c in counts for term in c)
    n = len(texts)
    vectors = []
    for c in counts:
        vec = {t: (1 + math.log(tf)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, tf in c.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({t: v / norm for t, v in vec.items()})
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def shared_shingles(text_a: str, text_b: str) -> set[tuple[str, ...]]:
    return _shingles(tokens(text_a)) & _shingles(tokens(text_b))


def highlight(text: str, shared: set[tuple[str, ...]]) -> Markup:
    matches = list(WORD.finditer(text or ""))
    words = [_fold(m.group()) for m in matches]
    marked = [False] * len(words)
    for i in range(len(words) - SHINGLE + 1):
        if tuple(words[i:i + SHINGLE]) in shared:
            for j in range(i, i + SHINGLE):
                marked[j] = True
    out, pos, i = [], 0, 0
    while i < len(matches):
        if not marked[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(matches) and marked[j + 1]:
            j += 1
        start, end = matches[i].start(), matches[j].end()
        out.append(escape(text[pos:start]))
        out.append(Markup("<mark>") + escape(text[start:end]) + Markup("</mark>"))
        pos, i = end, j + 1
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def latest_submissions(assignment: models.Assignment) -> list[models.Submission]:
    latest: dict[int, models.Submission] = {}
    for s in assignment.submissions:
        if s.student_id not in latest or s.id > latest[s.student_id].id:
            latest[s.student_id] = s
    return sorted(latest.values(), key=lambda s: s.student.code)


def decisions(assignment: models.Assignment, db) -> dict[tuple[int, int], str]:
    rows = db.query(models.SimilarityReview).filter_by(assignment_id=assignment.id).all()
    return {(r.submission_a_id, r.submission_b_id): r.decision for r in rows}


def find_pairs(assignment: models.Assignment, threshold: float = DEFAULT_THRESHOLD,
               reviewed: dict[tuple[int, int], str] | None = None) -> list[Pair]:
    subs = latest_submissions(assignment)
    ignore = source_terms(assignment)
    vectors = tfidf_vectors([s.text for s in subs], ignore)
    reviewed = reviewed or {}
    pairs = []
    for i in range(len(subs)):
        if len(vectors[i]) < MIN_FEATURES:
            continue
        for j in range(i + 1, len(subs)):
            if len(vectors[j]) < MIN_FEATURES:
                continue
            score = cosine(vectors[i], vectors[j])
            if 100 * score >= threshold:
                a, b = sorted((subs[i], subs[j]), key=lambda s: s.id)
                shared = shared_shingles(a.text, b.text)
                pairs.append(Pair(a, b, score, len(shared), reviewed.get((a.id, b.id), "")))
    return sorted(pairs, key=lambda p: -p.score)


def pair_score(assignment: models.Assignment, first: models.Submission, second: models.Submission) -> float:
    docs = {s.id: s for s in latest_submissions(assignment)}
    docs.update({first.id: first, second.id: second})
    ids = list(docs)
    vectors = tfidf_vectors([docs[i].text for i in ids], source_terms(assignment))
    return cosine(vectors[ids.index(first.id)], vectors[ids.index(second.id)])


def refresh_copying_flags(assignment: models.Assignment, db) -> None:
    copying = {i for (a, b), d in decisions(assignment, db).items() if d == "copying" for i in (a, b)}
    for s in assignment.submissions:
        s.copying_flag = s.id in copying
