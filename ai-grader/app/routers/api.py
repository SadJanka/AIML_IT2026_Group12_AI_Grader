from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n, models
from ..database import get_db
from ..grading import grade_submission
from ..grading.providers import LLMError, get_provider
from ..services.feedback import weak_topics
from ..services.files import FileError, extract_text
from ..services.results import course_results

router = APIRouter(prefix="/api", tags=["API"])


class SubmissionIn(BaseModel):
    student_code: str = Field(..., description="Student pseudonym code; created if it does not exist")
    text: str = Field(..., min_length=1)


def _get(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, "Nav atrasts")
    return obj


def _submission_json(sub: models.Submission) -> dict:
    a = sub.assignment
    return {
        "id": sub.id,
        "assignment_id": a.id,
        "student_code": sub.student.code,
        "status": sub.status,
        "ai_model": sub.ai_model,
        "ai_total": sub.ai_total,
        "final_total": sub.final_total if sub.status == models.APPROVED else None,
        "max_points": a.max_points,
        "injection_flag": sub.injection_flag,
        "uncertain": sub.uncertain,
        "copying_flag": sub.copying_flag,
        "injection_details": sub.injection_details.splitlines() if sub.injection_details else [],
        "warnings_or_error": sub.error or None,
        "summary": sub.final_summary,
        "criteria": [
            {
                "criterion_id": c.id,
                "name": c.name,
                "max_points": c.max_points,
                "ai_points": sc.ai_points if sc else None,
                "final_points": sc.final_points if sc else None,
                "justification": sc.ai_justification if sc else "",
                "mistakes": sc.ai_mistakes if sc else [],
                "improvement": sc.ai_improvement if sc else "",
                "teacher_comment": sc.teacher_comment if sc else "",
            }
            for c in a.criteria
            for sc in [sub.score_for(c.id)]
        ],
        "weak_topics": [
            {"topic": w.topic, "materials": [{"title": m.title, "url": m.url} for m in w.materials]}
            for w in weak_topics(sub)
        ] if sub.scores else [],
        "feedback_url": f"/feedback/{sub.feedback_token}",
    }


@router.get("/health")
def health():
    try:
        model = get_provider().model_label
    except LLMError as e:
        model = f"error: {e}"
    return {"status": "ok", "model": model}


@router.post("/extract-text")
async def extract_text_from_file(request: Request, file: UploadFile = File(...)):
    try:
        return {"text": extract_text(file.filename or "", await file.read())}
    except FileError as e:
        raise HTTPException(400, i18n.translate(str(e), i18n.normalize(request.cookies.get("lang")))) from e


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)):
    return [
        {"id": c.id, "name": c.name, "assignments": [{"id": a.id, "title": a.title} for a in c.assignments]}
        for c in db.scalars(select(models.Course).order_by(models.Course.id))
    ]


@router.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: int, db: Session = Depends(get_db)):
    a = _get(db, models.Assignment, assignment_id)
    return {
        "id": a.id, "course_id": a.course_id, "title": a.title, "instructions": a.instructions,
        "model_answer": a.model_answer, "language": a.language,
        "weight": a.weight, "max_points": a.max_points,
        "criteria": [{"id": c.id, "name": c.name, "description": c.description, "expected_answer": c.expected_answer,
                      "max_points": c.max_points, "topic": c.topic} for c in a.criteria],
    }


@router.post("/assignments/{assignment_id}/submissions", status_code=201)
def create_submission(assignment_id: int, body: SubmissionIn, db: Session = Depends(get_db)):
    a = _get(db, models.Assignment, assignment_id)
    code = body.student_code.strip()
    student = next((s for s in a.course.students if s.code.lower() == code.lower()), None)
    if student is None:
        student = models.Student(code=code[:50])
        a.course.students.append(student)
    sub = models.Submission(student=student, text=body.text.strip())
    a.submissions.append(sub)
    db.commit()
    return _submission_json(sub)


@router.post("/submissions/{submission_id}/grade")
def grade(submission_id: int, db: Session = Depends(get_db)):
    sub = _get(db, models.Submission, submission_id)
    if sub.status == models.APPROVED:
        raise HTTPException(409, "Submission is already approved")
    grade_submission(db, sub)
    if sub.status == models.ERROR:
        raise HTTPException(502, sub.error)
    return _submission_json(sub)


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    return _submission_json(_get(db, models.Submission, submission_id))


@router.post("/submissions/{submission_id}/approve")
def approve(submission_id: int, db: Session = Depends(get_db)):
    sub = _get(db, models.Submission, submission_id)
    if not sub.scores or any(s.final_points is None for s in sub.scores) \
            or len(sub.scores) < len(sub.assignment.criteria):
        raise HTTPException(409, "Not all criteria have points")
    sub.status = models.APPROVED
    sub.approved_at = datetime.now()
    db.commit()
    return _submission_json(sub)


@router.get("/courses/{course_id}/results")
def results(course_id: int, db: Session = Depends(get_db)):
    course = _get(db, models.Course, course_id)
    return [
        {
            "student_code": row.student.code,
            "assignments": [{"assignment_id": c.assignment.id, "points": c.points,
                             "max_points": c.max_points, "percent": c.percent} for c in row.cells],
            "final_percent": row.final_percent,
            "final_grade": row.final_grade or None,
        }
        for row in course_results(course)
    ]
