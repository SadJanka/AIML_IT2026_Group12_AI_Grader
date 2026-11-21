from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .. import models
from .results import grade_for_percent

ATTENTION_PERCENT = 50


@dataclass
class SubRow:
    submission: models.Submission
    points: float
    max_points: float
    grade: str

    @property
    def percent(self) -> float:
        return 100 * self.points / self.max_points if self.max_points else 0

    @property
    def approved(self) -> bool:
        return self.submission.status == models.APPROVED


@dataclass
class CriterionSummary:
    criterion: models.Criterion
    mean_points: float
    n: int
    common_mistakes: list[str] = field(default_factory=list)
    materials: list[models.Material] = field(default_factory=list)

    @property
    def mean_percent(self) -> float:
        return 100 * self.mean_points / self.criterion.max_points if self.criterion.max_points else 0


@dataclass
class AssignmentSummary:
    assignment: models.Assignment
    rows: list[SubRow]
    counts: dict[str, int]
    criteria: list[CriterionSummary]
    histogram: list[tuple[str, int]]
    grades: list[tuple[str, int]]

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def mean_percent(self):
        return sum(r.percent for r in self.rows) / self.n if self.n else None

    @property
    def median_percent(self):
        return median(r.percent for r in self.rows) if self.n else None

    @property
    def min_row(self):
        return min(self.rows, key=lambda r: r.percent) if self.rows else None

    @property
    def max_row(self):
        return max(self.rows, key=lambda r: r.percent) if self.rows else None

    @property
    def weakest(self) -> list[CriterionSummary]:
        return [c for c in self.criteria if c.mean_percent < 70][:3]

    @property
    def attention(self) -> list[SubRow]:
        return sorted((r for r in self.rows if r.percent < ATTENTION_PERCENT), key=lambda r: r.percent)

    @property
    def flagged(self) -> list[models.Submission]:
        return [s for s in self.assignment.submissions if s.flags]

    @property
    def to_review(self) -> int:
        return self.counts.get(models.AI_GRADED, 0)

    @property
    def approvable(self) -> list[models.Submission]:
        return [s for s in self.assignment.submissions
                if s.status == models.AI_GRADED and not s.flags and s.scores
                and all(sc.final_points is not None for sc in s.scores)]


def latest_scored(assignment: models.Assignment) -> list[models.Submission]:
    latest: dict[int, models.Submission] = {}
    for s in assignment.submissions:
        if s.scores and s.final_total is not None:
            if s.student_id not in latest or s.id > latest[s.student_id].id:
                latest[s.student_id] = s
    return sorted(latest.values(), key=lambda s: s.student.code)


def summarize(assignment: models.Assignment) -> AssignmentSummary:
    course = assignment.course
    max_points = assignment.max_points
    subs = latest_scored(assignment)
    rows = [SubRow(s, s.final_total or 0, max_points,
                   grade_for_percent(100 * (s.final_total or 0) / max_points, course.grade_scale) if max_points else "")
            for s in subs]

    counts = Counter(s.status for s in assignment.submissions)

    materials: dict[str, list[models.Material]] = {}
    for m in course.materials:
        materials.setdefault(m.topic.strip().lower(), []).append(m)

    criteria = []
    for c in assignment.criteria:
        scores = [sc for s in subs for sc in s.scores if sc.criterion_id == c.id and sc.final_points is not None]
        if not scores:
            continue
        mistakes = Counter(m.strip() for sc in scores if (sc.final_points or 0) < c.max_points
                           for m in sc.ai_mistakes if m.strip())
        criteria.append(CriterionSummary(
            criterion=c,
            mean_points=sum(sc.final_points for sc in scores) / len(scores),
            n=len(scores),
            common_mistakes=[m for m, _ in mistakes.most_common(3)],
            materials=materials.get((c.topic or "").strip().lower(), []),
        ))
    criteria.sort(key=lambda c: c.mean_percent)

    bins = [0] * 10
    for r in rows:
        bins[min(int(r.percent // 10), 9)] += 1
    histogram = [(f"{i * 10}–{i * 10 + 9 if i < 9 else 100}%", n) for i, n in enumerate(bins)]

    grade_counts = Counter(r.grade for r in rows)
    grades = [(g, grade_counts.get(g, 0)) for _, g in course.grade_scale]

    return AssignmentSummary(assignment, rows, dict(counts), criteria, histogram, grades)
