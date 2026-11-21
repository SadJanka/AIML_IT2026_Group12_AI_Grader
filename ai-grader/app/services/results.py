import csv
import io
from dataclasses import dataclass, field

from .. import models
from ..i18n import translate


def grade_for_percent(percent: float, scale: list[list]) -> str:
    for min_percent, grade in sorted(scale, key=lambda x: -float(x[0])):
        if percent >= float(min_percent):
            return str(grade)
    return str(scale[-1][1]) if scale else ""


@dataclass
class Cell:
    assignment: models.Assignment
    submission: models.Submission | None = None
    points: float | None = None
    max_points: float = 0
    percent: float | None = None


@dataclass
class StudentRow:
    student: models.Student
    cells: list[Cell] = field(default_factory=list)
    final_percent: float | None = None
    final_grade: str = ""


def latest_approved(student: models.Student, assignment: models.Assignment) -> models.Submission | None:
    subs = [s for s in assignment.submissions if s.student_id == student.id and s.status == models.APPROVED]
    return max(subs, key=lambda s: (s.approved_at or s.submitted_at, s.id)) if subs else None


def course_results(course: models.Course) -> list[StudentRow]:
    rows = []
    for student in course.students:
        row = StudentRow(student)
        weighted, weights = 0.0, 0.0
        for a in course.assignments:
            cell = Cell(a, max_points=a.max_points)
            sub = latest_approved(student, a)
            if sub and a.max_points > 0:
                cell.submission = sub
                cell.points = sub.final_total or 0
                cell.percent = 100 * cell.points / a.max_points
                weighted += cell.percent * a.weight
                weights += a.weight
            row.cells.append(cell)
        if weights > 0:
            row.final_percent = weighted / weights
            row.final_grade = grade_for_percent(row.final_percent, course.grade_scale)
        rows.append(row)
    return rows


def results_csv(course: models.Course, lang: str = "lv") -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")

    def num(x):
        if x is None:
            return ""
        text = f"{x:.1f}"
        return text if lang == "en" else text.replace(".", ",")

    writer.writerow([translate("Student", lang)] + [f"{a.title} (%)" for a in course.assignments]
                    + [translate("Final %", lang), translate("Final grade", lang)])
    for row in course_results(course):
        writer.writerow([row.student.label] + [num(c.percent) for c in row.cells]
                        + [num(row.final_percent), row.final_grade])
    return chr(0xFEFF) + buf.getvalue()


@dataclass
class CriterionStat:
    criterion: models.Criterion
    n: int = 0
    ai_sum: float = 0
    final_sum: float = 0
    abs_diff_sum: float = 0
    changed: int = 0

    @property
    def mean_ai(self) -> float:
        return self.ai_sum / self.n if self.n else 0

    @property
    def mean_final(self) -> float:
        return self.final_sum / self.n if self.n else 0

    @property
    def mae(self) -> float:
        return self.abs_diff_sum / self.n if self.n else 0


@dataclass
class AgreementStats:
    submissions: int = 0
    scores: int = 0
    changed: int = 0
    abs_diff_sum: float = 0
    abs_diff_pct_sum: float = 0
    flagged: int = 0
    criteria: list[CriterionStat] = field(default_factory=list)

    @property
    def mae_points(self) -> float:
        return self.abs_diff_sum / self.scores if self.scores else 0

    @property
    def mae_percent(self) -> float:
        return self.abs_diff_pct_sum / self.scores if self.scores else 0

    @property
    def changed_share(self) -> float:
        return 100 * self.changed / self.scores if self.scores else 0


def agreement_stats(course: models.Course) -> AgreementStats:
    stats = AgreementStats()
    for a in course.assignments:
        per_crit = {c.id: CriterionStat(c) for c in a.criteria}
        for sub in a.submissions:
            if sub.status != models.APPROVED:
                continue
            stats.submissions += 1
            stats.flagged += int(sub.injection_flag)
            for s in sub.scores:
                if s.ai_points is None or s.final_points is None or s.criterion_id not in per_crit:
                    continue
                cs = per_crit[s.criterion_id]
                diff = abs(s.ai_points - s.final_points)
                cs.n += 1
                cs.ai_sum += s.ai_points
                cs.final_sum += s.final_points
                cs.abs_diff_sum += diff
                cs.changed += int(diff > 1e-9)
                stats.scores += 1
                stats.changed += int(diff > 1e-9)
                stats.abs_diff_sum += diff
                if cs.criterion.max_points:
                    stats.abs_diff_pct_sum += 100 * diff / cs.criterion.max_points
        stats.criteria.extend(cs for cs in per_crit.values() if cs.n)
    return stats
