import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import models
from ..i18n import translate
from .results import course_results
from .summary import summarize

HEADER_FILL = PatternFill("solid", fgColor="DCE6F8")
LOW_FILL = PatternFill("solid", fgColor="FCE4E1")
FLAG_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def _header(ws, row: int, values: list[str]) -> None:
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font, cell.fill, cell.alignment = BOLD, HEADER_FILL, Alignment(wrap_text=True, vertical="center")


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _sheet_title(text: str) -> str:
    return text[:31]


def assignment_workbook(assignment: models.Assignment, lang: str, base_url: str) -> bytes:
    t = lambda s, **kw: translate(s, lang, **kw)
    summary = summarize(assignment)
    wb = Workbook()

    ws = wb.active
    ws.title = _sheet_title(t("Summary"))
    ws["A1"] = assignment.title
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"{assignment.course.name} · {datetime.now():%d.%m.%Y %H:%M}"
    info = [
        (t("Graded submissions"), summary.n),
        (t("Approved submissions"), summary.counts.get(models.APPROVED, 0)),
        (t("Awaiting approval"), summary.to_review),
        (t("Class average %"), round(summary.mean_percent, 1) if summary.n else None),
        (t("Median %"), round(summary.median_percent, 1) if summary.n else None),
        (t("Max points"), assignment.max_points),
    ]
    for i, (k, v) in enumerate(info, start=4):
        ws.cell(row=i, column=1, value=k).font = BOLD
        ws.cell(row=i, column=2, value=v)
    row = 4 + len(info) + 1
    _header(ws, row, [t("Criterion"), t("Max"), t("Average points"), t("Average %"), t("Most common mistakes")])
    for c in summary.criteria:
        row += 1
        ws.cell(row=row, column=1, value=c.criterion.name).alignment = WRAP
        ws.cell(row=row, column=2, value=c.criterion.max_points)
        ws.cell(row=row, column=3, value=round(c.mean_points, 2))
        ws.cell(row=row, column=4, value=round(c.mean_percent, 1))
        ws.cell(row=row, column=5, value="\n".join(c.common_mistakes)).alignment = WRAP
        if c.mean_percent < 50:
            ws.cell(row=row, column=4).fill = LOW_FILL
    row += 2
    _header(ws, row, [t("Grade"), t("Students")])
    for grade, n in summary.grades:
        row += 1
        ws.cell(row=row, column=1, value=grade)
        ws.cell(row=row, column=2, value=n)
    _widths(ws, [42, 12, 16, 12, 70])

    ws = wb.create_sheet(_sheet_title(t("Scores")))
    crits = assignment.criteria
    headers = ([t("Student"), t("Code"), t("Status")] + [c.name for c in crits]
               + [t("Total"), t("Max"), "%", t("Grade"), t("AI suggested"), t("Flagged"), t("Link for the student")])
    _header(ws, 1, headers)
    for r_i, r in enumerate(summary.rows, start=2):
        s = r.submission
        values = ([s.student.display_name or s.student.code, s.student.code, t(s.status_label)]
                  + [(sc.final_points if (sc := s.score_for(c.id)) else None) for c in crits]
                  + [r.points, r.max_points, round(r.percent, 1), r.grade, s.ai_total, ", ".join(t(f) for f in s.flags),
                     f"{base_url}feedback/{s.feedback_token}" if r.approved else ""])
        for c_i, v in enumerate(values, start=1):
            ws.cell(row=r_i, column=c_i, value=v)
        pct_cell = ws.cell(row=r_i, column=3 + len(crits) + 3)
        if r.percent < 50:
            pct_cell.fill = LOW_FILL
        if s.flags:
            ws.cell(row=r_i, column=len(values) - 1).fill = FLAG_FILL
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, len(summary.rows) + 1)}"
    _widths(ws, [24, 9, 18] + [14] * len(crits) + [8, 7, 7, 9, 10, 18, 48])

    ws = wb.create_sheet(_sheet_title(t("Feedback")))
    _header(ws, 1, [t("Student"), t("Total"), t("Summary"), t("Mistakes"), t("Suggestions"),
                    t("Teacher comments"), t("Link for the student")])
    for r_i, r in enumerate(summary.rows, start=2):
        s = r.submission
        mistakes, tips, comments = [], [], []
        for c in crits:
            sc = s.score_for(c.id)
            if not sc:
                continue
            if sc.ai_mistakes and (sc.final_points or 0) < c.max_points:
                mistakes.append(f"{c.name}: " + "; ".join(sc.ai_mistakes))
            if sc.ai_improvement and (sc.final_points or 0) < c.max_points:
                tips.append(f"{c.name}: {sc.ai_improvement}")
            if sc.teacher_comment:
                comments.append(f"{c.name}: {sc.teacher_comment}")
        values = [s.student.display_name or s.student.code, f"{r.points:g}/{r.max_points:g}", s.final_summary,
                  "\n".join(mistakes), "\n".join(tips), "\n".join(comments),
                  f"{base_url}feedback/{s.feedback_token}" if r.approved else ""]
        for c_i, v in enumerate(values, start=1):
            ws.cell(row=r_i, column=c_i, value=v).alignment = WRAP
    ws.freeze_panes = "B2"
    _widths(ws, [24, 9, 60, 60, 60, 40, 48])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def course_workbook(course: models.Course, lang: str) -> bytes:
    t = lambda s, **kw: translate(s, lang, **kw)
    wb = Workbook()
    ws = wb.active
    ws.title = _sheet_title(t("Results"))
    headers = [t("Student")] + [f"{a.title} (%)" for a in course.assignments] + [t("Final %"), t("Final grade")]
    _header(ws, 1, headers)
    for r_i, row in enumerate(course_results(course), start=2):
        values = ([row.student.label] + [round(c.percent, 1) if c.percent is not None else None for c in row.cells]
                  + [round(row.final_percent, 1) if row.final_percent is not None else None, row.final_grade or None])
        for c_i, v in enumerate(values, start=1):
            ws.cell(row=r_i, column=c_i, value=v)
    ws.freeze_panes = "B2"
    _widths(ws, [28] + [18] * len(course.assignments) + [10, 14])

    ws = wb.create_sheet(_sheet_title(t("Grading scale")))
    _header(ws, 1, [t("Minimum percent"), t("Grade")])
    for r_i, (p, g) in enumerate(course.grade_scale, start=2):
        ws.cell(row=r_i, column=1, value=p)
        ws.cell(row=r_i, column=2, value=g)
    _widths(ws, [20, 20])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
