import json
import math
from dataclasses import dataclass, field

from .. import models
from ..i18n import N_

MANIPULATION = "manipulation"


@dataclass
class Row:
    task: str
    student: str
    expected: dict[str, float]
    expected_total: float
    max_total: float
    tags: list[str]
    submission: models.Submission | None = None
    ai: dict[str, float] = field(default_factory=dict)

    @property
    def graded(self) -> bool:
        return self.submission is not None

    @property
    def ai_total(self) -> float:
        return sum(self.ai.values())

    @property
    def diff(self) -> float:
        return self.ai_total - self.expected_total

    @property
    def diff_pct(self) -> float:
        return 100 * self.diff / self.max_total if self.max_total else 0

    @property
    def flagged(self) -> bool:
        return bool(self.submission and self.submission.injection_flag)


@dataclass
class Summary:
    title: str
    rows: list[Row]
    max_total: float = 0

    @property
    def graded(self) -> list[Row]:
        return [r for r in self.rows if r.graded]

    @property
    def n(self) -> int:
        return len(self.graded)

    @property
    def pending(self) -> int:
        return len(self.rows) - self.n

    def _mean(self, values):
        values = list(values)
        return sum(values) / len(values) if values else None

    @property
    def mae(self):
        return self._mean(abs(r.diff) for r in self.graded)

    @property
    def mae_pct(self):
        return self._mean(abs(r.diff_pct) for r in self.graded)

    @property
    def bias(self):
        return self._mean(r.diff for r in self.graded)

    @property
    def exact_pct(self):
        return self._mean(100.0 * (abs(r.diff) < 1e-9) for r in self.graded)

    @property
    def within1_pct(self):
        return self._mean(100.0 * (abs(r.diff) <= 1 + 1e-9) for r in self.graded)

    @property
    def criterion_exact_pct(self):
        cells = [abs(r.ai.get(k, 0) - v) < 1e-9 for r in self.graded for k, v in r.expected.items()]
        return 100 * sum(cells) / len(cells) if cells else None

    @property
    def correlation(self):
        xs = [r.expected_total for r in self.graded]
        ys = [r.ai_total for r in self.graded]
        if len(xs) < 3:
            return None
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if not sx or not sy:
            return None
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


@dataclass
class TagStat:
    tag: str
    n: int
    mae_pct: float
    bias_pct: float


@dataclass
class Report:
    tasks: list[Summary]
    tags: list[TagStat]
    injection_total: int
    injection_flagged: int
    injection_inflated: int
    false_flags: int
    worst: list[Row]
    checked_n: int = 0
    unsure_n: int = 0
    unsure_mae_pct: float | None = None
    sure_mae_pct: float | None = None

    @property
    def any_graded(self) -> bool:
        return any(t.n for t in self.tasks)


def _norm(text: str) -> str:
    return " ".join((text or "").replace("_", " ").replace("-", " ").lower().split())


def _find_submission(assignment: models.Assignment, student: str) -> models.Submission | None:
    key = _norm(student)
    candidates = [s for s in assignment.submissions
                  if s.scores and any(x.ai_points is not None for x in s.scores)
                  and key in _norm(f"{s.student.display_name} {s.original_filename}")]
    return max(candidates, key=lambda s: s.id) if candidates else None


def evaluate(course: models.Course) -> Report | None:
    if not course.benchmark_json:
        return None
    truth = json.loads(course.benchmark_json)
    by_title = {a.title: a for a in course.assignments}
    tasks, all_rows = [], []
    for title, students in truth.items():
        assignment = by_title.get(title)
        if assignment is None:
            continue
        max_total = assignment.max_points
        rows = []
        for student, g in students.items():
            row = Row(title, student, g["criteria"], g["total"], max_total, g.get("tags", []))
            sub = _find_submission(assignment, student)
            if sub:
                row.submission = sub
                names = {c.id: c.name for c in assignment.criteria}
                row.ai = {names[s.criterion_id]: s.ai_points or 0 for s in sub.scores if s.criterion_id in names}
            rows.append(row)
        tasks.append(Summary(title, rows, max_total))
        all_rows += rows

    graded = [r for r in all_rows if r.graded]
    tag_rows: dict[str, list[Row]] = {}
    for r in graded:
        for t in r.tags or [N_("(no special features)")]:
            tag_rows.setdefault(t, []).append(r)
    tags = sorted((TagStat(t, len(rs), sum(abs(r.diff_pct) for r in rs) / len(rs),
                           sum(r.diff_pct for r in rs) / len(rs)) for t, rs in tag_rows.items()),
                  key=lambda s: -s.mae_pct)
    injections = [r for r in graded if MANIPULATION in r.tags]
    checked = [r for r in graded if any(sc.check_points is not None for sc in r.submission.scores)]
    unsure = [r for r in checked if r.submission.uncertain]
    sure = [r for r in checked if not r.submission.uncertain]

    def mean_abs_pct(rows):
        return sum(abs(r.diff_pct) for r in rows) / len(rows) if rows else None

    return Report(
        tasks=tasks,
        tags=tags,
        injection_total=len(injections),
        injection_flagged=sum(r.flagged for r in injections),
        injection_inflated=sum(r.diff > 1 for r in injections),
        false_flags=sum(r.flagged for r in graded if MANIPULATION not in r.tags),
        worst=sorted(graded, key=lambda r: -abs(r.diff_pct))[:10],
        checked_n=len(checked),
        unsure_n=len(unsure),
        unsure_mae_pct=mean_abs_pct(unsure),
        sure_mae_pct=mean_abs_pct(sure),
    )
