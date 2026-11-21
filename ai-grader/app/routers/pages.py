import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import i18n, models
from ..config import settings
from ..database import SessionLocal, get_db
from ..grading import grade_submission
from ..grading.grader import corrected_submissions
from ..grading.providers import LLMError, get_provider
from ..i18n import N_
from ..seed import seed_demo
from .. import benchmark
from ..services.accuracy import evaluate
from ..services.export import assignment_workbook, course_workbook
from ..services.summary import summarize
from ..services.feedback import weak_topics
from ..services.files import (
    GENERIC_FOLDER, UPLOAD_EXTENSIONS, FileError, expand_upload, extract_text, is_archive, is_junk, stem,
    strip_common_root,
)
from ..services import similarity
from ..services.results import agreement_stats, course_results, results_csv
from ..services.students import match_or_create_student, parse_student_lines

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

MAX_BULK_FILES = 300


@pass_context
def fmt(ctx, value, digits: int = 1) -> str:
    if value is None:
        return "–"
    value = round(float(value), digits)
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return text if ctx.get("lang") == "en" else text.replace(".", ",")


def provider_label() -> str:
    try:
        return get_provider().model_label
    except LLMError as e:
        return f"! {e}"


templates.env.filters["fmt"] = fmt
templates.env.globals["provider_label"] = provider_label
templates.env.globals["ACCEPT"] = ",".join(UPLOAD_EXTENSIONS)
templates.env.globals["LANGUAGE_NAMES"] = i18n.LANGUAGE_NAMES

_grading_now: set[int] = set()
_progress: dict[int, dict] = {}


def lang_of(request: Request) -> str:
    return i18n.normalize(request.cookies.get("lang"))


def tr(request: Request, text: str, **params) -> str:
    return i18n.translate(text, lang_of(request), **params)


def render(request: Request, name: str, lang: str | None = None, **ctx) -> HTMLResponse:
    lang = i18n.normalize(lang) if lang else lang_of(request)
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx["lang"] = lang
    ctx["_"] = lambda text, **params: i18n.translate(text, lang, **params)
    return templates.TemplateResponse(request, name, ctx)


def go(request: Request, url: str, msg: str | None = None, **params) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote(tr(request, msg, **params))
    return RedirectResponse(url, status_code=303)


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404)
    return obj


def parse_float(value: str | None, default: float | None = None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return default


@router.get("/lang/{code}")
def set_language(code: str, next: str = "/"):
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    response = RedirectResponse(next, status_code=303)
    response.set_cookie("lang", i18n.normalize(code), max_age=365 * 24 * 3600, samesite="lax")
    return response


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    courses = db.scalars(select(models.Course).order_by(models.Course.id)).all()
    return render(request, "index.html", courses=courses)


@router.post("/courses")
def create_course(request: Request, name: str = Form(...), description: str = Form(""),
                  db: Session = Depends(get_db)):
    course = models.Course(name=name.strip() or tr(request, "New course"), description=description.strip())
    db.add(course)
    db.commit()
    return go(request, f"/courses/{course.id}", N_("Course created."))


@router.post("/demo")
def add_demo(request: Request, db: Session = Depends(get_db)):
    course = seed_demo(db)
    return go(request, f"/courses/{course.id}", N_("Demo course added."))


@router.post("/benchmark")
def add_benchmark_course(request: Request, db: Session = Depends(get_db)):
    course = benchmark.create_course(db, lang_of(request))
    return go(request, f"/courses/{course.id}",
              N_("Test course created. Drop the folders from “test-data” into the matching assignments."))


@router.get("/courses/{course_id}/accuracy", response_class=HTMLResponse)
def accuracy_page(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    report = evaluate(course)
    if report is None:
        raise HTTPException(404)
    return render(request, "accuracy.html", course=course, report=report)


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_page(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    lang = lang_of(request)
    scale_text = "\n".join(f"{fmt({'lang': lang}, p, 1)}; {g}" for p, g in course.grade_scale)
    return render(request, "course.html", course=course, scale_text=scale_text)


@router.post("/courses/{course_id}/delete")
def delete_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Course, course_id))
    db.commit()
    return go(request, "/", N_("The course and all its data were deleted."))


@router.post("/courses/{course_id}/students")
def add_students(course_id: int, request: Request, lines: str = Form(...), db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    existing = {s.code.lower() for s in course.students}
    added = 0
    for code, name in parse_student_lines(lines):
        if code.lower() in existing:
            continue
        course.students.append(models.Student(code=code, display_name=name))
        existing.add(code.lower())
        added += 1
    db.commit()
    return go(request, f"/courses/{course_id}#skoleni", N_("Students added: {n}."), n=added)


@router.post("/students/{student_id}/delete")
def delete_student(student_id: int, request: Request, db: Session = Depends(get_db)):
    student = get_or_404(db, models.Student, student_id)
    course_id = student.course_id
    db.delete(student)
    db.commit()
    return go(request, f"/courses/{course_id}#skoleni", N_("The student and all their data were deleted."))


@router.post("/courses/{course_id}/materials")
def add_material(course_id: int, request: Request, topic: str = Form(...), title: str = Form(...),
                 url: str = Form(""), db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    url = url.strip()
    if url and not re.match(r"^https?://", url):
        return go(request, f"/courses/{course_id}#materiali", N_("The link must start with http:// or https://"))
    course.materials.append(models.Material(topic=topic.strip(), title=title.strip(), url=url))
    db.commit()
    return go(request, f"/courses/{course_id}#materiali", N_("Material added."))


@router.post("/materials/{material_id}/delete")
def delete_material(material_id: int, request: Request, db: Session = Depends(get_db)):
    material = get_or_404(db, models.Material, material_id)
    course_id = material.course_id
    db.delete(material)
    db.commit()
    return go(request, f"/courses/{course_id}#materiali", N_("Material deleted."))


@router.post("/courses/{course_id}/scale")
def update_scale(course_id: int, request: Request, scale: str = Form(...), db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    rows = []
    for line in scale.splitlines():
        if not line.strip():
            continue
        parts = re.split(r"\s*[;=:\t]\s*|\s+", line.strip(), maxsplit=1)
        percent = parse_float(parts[0])
        if percent is None or len(parts) < 2 or not parts[1].strip():
            return go(request, f"/courses/{course_id}#skala",
                      N_("Could not understand the line “{line}”. Format: percent; grade"), line=line)
        rows.append([percent, parts[1].strip()])
    if not rows:
        return go(request, f"/courses/{course_id}#skala", N_("The scale cannot be empty."))
    course.grade_scale_json = json.dumps(sorted(rows, key=lambda r: -r[0]), ensure_ascii=False)
    db.commit()
    return go(request, f"/courses/{course_id}#skala", N_("Grading scale saved."))


@router.get("/courses/{course_id}/assignments/new", response_class=HTMLResponse)
def new_assignment(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    return render(request, "assignment_form.html", course=course, assignment=None)


@router.get("/assignments/{assignment_id}/edit", response_class=HTMLResponse)
def edit_assignment(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    return render(request, "assignment_form.html", course=a.course, assignment=a)


def _apply_assignment_form(a: models.Assignment, form) -> tuple[str, dict] | None:
    a.title = form["title"].strip() or "Uzdevums"
    a.instructions = form["instructions"].strip()
    a.model_answer = form["model_answer"].strip()
    a.language = i18n.normalize(form["language"])
    a.weight = max(parse_float(form["weight"], 1.0), 0.0)

    def col(name, i):
        values = form[name]
        return values[i] if i < len(values) else ""

    rows = []
    for i, name in enumerate(form["crit_name"]):
        if not name.strip():
            continue
        max_points = parse_float(col("crit_max", i))
        if max_points is None or max_points <= 0:
            return N_("Criterion “{name}” needs maximum points (> 0)."), {"name": name}
        cid = col("crit_id", i).strip()
        rows.append({
            "id": int(cid) if cid.isdigit() else None,
            "name": name.strip(),
            "description": col("crit_desc", i).strip(),
            "expected_answer": col("crit_expected", i).strip(),
            "max_points": max_points,
            "topic": col("crit_topic", i).strip(),
        })
    if not rows:
        return N_("Add at least one grading criterion."), {}

    existing = {c.id: c for c in a.criteria}
    keep = set()
    for pos, r in enumerate(rows, start=1):
        crit = existing.get(r["id"]) if r["id"] else None
        if crit is None:
            crit = models.Criterion()
            a.criteria.append(crit)
        else:
            keep.add(crit.id)
        crit.position, crit.name, crit.description = pos, r["name"], r["description"]
        crit.expected_answer, crit.max_points, crit.topic = r["expected_answer"], r["max_points"], r["topic"]
    for cid, crit in existing.items():
        if cid not in keep:
            a.criteria.remove(crit)
    return None


async def _assignment_form(request: Request) -> dict:
    form = await request.form()
    single = lambda k, d="": str(form.get(k, d))
    lists = {k: [str(v) for v in form.getlist(k)]
             for k in ("crit_id", "crit_name", "crit_desc", "crit_expected", "crit_max", "crit_topic")}
    return {"title": single("title"), "instructions": single("instructions"),
            "model_answer": single("model_answer"), "language": single("language", "lv"),
            "weight": single("weight", "1"), **lists}


@router.post("/courses/{course_id}/assignments")
async def create_assignment(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    a = models.Assignment()
    error = _apply_assignment_form(a, await _assignment_form(request))
    if error:
        db.rollback()
        return go(request, f"/courses/{course_id}/assignments/new", error[0], **error[1])
    course.assignments.append(a)
    db.commit()
    return go(request, f"/assignments/{a.id}", N_("Assignment created."))


@router.post("/assignments/{assignment_id}/edit")
async def save_assignment(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    error = _apply_assignment_form(a, await _assignment_form(request))
    if error:
        db.rollback()
        return go(request, f"/assignments/{assignment_id}/edit", error[0], **error[1])
    db.commit()
    return go(request, f"/assignments/{assignment_id}",
              N_("Assignment saved. If you changed criteria or answers, consider grading the submissions again."))


@router.post("/assignments/{assignment_id}/delete")
def delete_assignment(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    course_id = a.course_id
    db.delete(a)
    db.commit()
    return go(request, f"/courses/{course_id}", N_("Assignment deleted."))


@router.get("/assignments/{assignment_id}", response_class=HTMLResponse)
def assignment_page(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    pending = sum(1 for s in a.submissions if s.status in (models.SUBMITTED, models.ERROR))
    return render(request, "assignment.html", assignment=a, course=a.course, pending=pending,
                  corrections=len(corrected_submissions(a)), grading_now=assignment_id in _grading_now)


@router.post("/assignments/{assignment_id}/ai-settings")
async def save_ai_settings(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    form = await request.form()
    a.use_examples = form.get("use_examples") == "1"
    a.double_check = form.get("double_check") == "1"
    db.commit()
    return go(request, f"/assignments/{assignment_id}", N_("AI settings saved."))


@router.get("/assignments/{assignment_id}/similarity", response_class=HTMLResponse)
def similarity_page(assignment_id: int, request: Request, threshold: int = similarity.DEFAULT_THRESHOLD,
                    db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    threshold = min(max(threshold, 30), 100)
    pairs = similarity.find_pairs(a, threshold, similarity.decisions(a, db))
    return render(request, "similarity.html", assignment=a, course=a.course, pairs=pairs, threshold=threshold,
                  checked=len(similarity.latest_submissions(a)))


def _pair_or_404(db: Session, assignment_id: int, a_id: int, b_id: int):
    a = get_or_404(db, models.Assignment, assignment_id)
    first, second = sorted((get_or_404(db, models.Submission, a_id), get_or_404(db, models.Submission, b_id)),
                           key=lambda s: s.id)
    if first.assignment_id != a.id or second.assignment_id != a.id or first.id == second.id:
        raise HTTPException(404)
    return a, first, second


@router.get("/assignments/{assignment_id}/similarity/{a_id}/{b_id}", response_class=HTMLResponse)
def similarity_compare(assignment_id: int, a_id: int, b_id: int, request: Request, db: Session = Depends(get_db)):
    a, first, second = _pair_or_404(db, assignment_id, a_id, b_id)
    score = similarity.pair_score(a, first, second)
    shared = similarity.shared_shingles(first.text, second.text)
    decision = similarity.decisions(a, db).get((first.id, second.id), "")
    return render(request, "similarity_compare.html", assignment=a, course=a.course, first=first, second=second,
                  percent=100 * score, decision=decision,
                  first_html=similarity.highlight(first.text, shared),
                  second_html=similarity.highlight(second.text, shared))


@router.post("/assignments/{assignment_id}/similarity/{a_id}/{b_id}")
async def similarity_decide(assignment_id: int, a_id: int, b_id: int, request: Request,
                            db: Session = Depends(get_db)):
    a, first, second = _pair_or_404(db, assignment_id, a_id, b_id)
    form = await request.form()
    decision = str(form.get("decision", ""))
    review = (db.query(models.SimilarityReview)
              .filter_by(submission_a_id=first.id, submission_b_id=second.id).one_or_none())
    if decision in ("copying", "not_copying"):
        if review is None:
            review = models.SimilarityReview(assignment_id=a.id, submission_a_id=first.id,
                                             submission_b_id=second.id, decision=decision)
            db.add(review)
        review.decision = decision
        review.decided_at = datetime.now()
    elif review is not None:
        db.delete(review)
    db.flush()
    similarity.refresh_copying_flags(a, db)
    db.commit()
    back = str(form.get("next", "")) or f"/assignments/{a.id}/similarity"
    if not back.startswith(f"/assignments/{a.id}/similarity"):
        back = f"/assignments/{a.id}/similarity"
    return go(request, back, N_("Decision saved."))


@router.post("/assignments/{assignment_id}/submissions")
async def add_submission(assignment_id: int, request: Request, student_id: int = Form(...), text: str = Form(""),
                         file: UploadFile | None = File(None), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    student = get_or_404(db, models.Student, student_id)
    if student.course_id != a.course_id:
        raise HTTPException(400)
    filename = ""
    if file is not None and file.filename:
        try:
            text = extract_text(file.filename, await file.read())
        except FileError as e:
            return go(request, f"/assignments/{assignment_id}", str(e))
        filename = file.filename
    if not text.strip():
        return go(request, f"/assignments/{assignment_id}", N_("Paste the text of the work or attach a file."))
    a.submissions.append(models.Submission(student=student, text=text.strip(), original_filename=filename))
    db.commit()
    return go(request, f"/assignments/{assignment_id}", N_("Submission added ({code})."), code=student.code)


def _split_path(path: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"[\\/]+", path) if p and p != "."]
    if not parts:
        return "", ""
    return (parts[0] if len(parts) >= 2 else ""), parts[-1]


def _looks_like_student(course: models.Course, name: str) -> bool:
    low = name.lower()
    return any(s.code.lower() in low or (s.display_name and s.display_name.lower() in low) for s in course.students)


def _archive_is_one_student(course: models.Course, archive_name: str, entries: list) -> bool:
    if not entries or any("/" in p for p, _ in entries):
        return False
    name = stem(archive_name)
    if _looks_like_student(course, name):
        return True
    return len(entries) == 1 and not GENERIC_FOLDER.search(name)


@router.post("/assignments/{assignment_id}/submissions/bulk")
async def add_submissions_bulk(assignment_id: int, request: Request, background: BackgroundTasks,
                               files: list[UploadFile] = File(...), paths: list[str] = Form([]),
                               auto_grade: str = Form(""), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    errors: list[str] = []
    collected: list[tuple[str, bytes]] = []

    for i, f in enumerate(files[:MAX_BULK_FILES]):
        path = (paths[i] if i < len(paths) and paths[i] else (f.filename or "")).replace("\\", "/").strip("/")
        if not path or is_junk(path):
            continue
        parent, _, name = path.rpartition("/")
        try:
            entries, skipped = expand_upload(name, await f.read())
        except FileError as e:
            errors.append(f"{name} ({tr(request, str(e))})")
            continue
        errors += [f"{n} ({tr(request, 'not supported or too large')})" for n in skipped]
        if is_archive(name) and not parent and _archive_is_one_student(a.course, name, entries):
            parent = stem(name)
        collected += [(f"{parent}/{p}" if parent else p, d) for p, d in entries]

    final_paths = strip_common_root([p for p, _ in collected])
    documents = [(*_split_path(p), d) for p, (_, d) in zip(final_paths, collected)]

    groups: dict[str, list[tuple[str, str]]] = {}
    for folder, name, data in documents[:MAX_BULK_FILES]:
        try:
            text = extract_text(name, data)
        except FileError as e:
            errors.append(f"{name} ({tr(request, str(e))})")
            continue
        if not text.strip():
            errors.append(f"{name} ({tr(request, 'empty')})")
            continue
        key = f"dir:{folder}" if folder else f"file:{name}:{len(groups)}"
        groups.setdefault(key, []).append((name, text.strip()))

    added, new_students = 0, 0
    for key, parts in groups.items():
        folder = key[4:] if key.startswith("dir:") else ""
        first_name = parts[0][0]
        student, created = match_or_create_student(a.course, first_name, folder)
        new_students += int(created)
        if len(parts) == 1:
            text = parts[0][1]
        else:
            text = "\n\n".join(f"=== {name} ===\n{body}" for name, body in parts)
        label = f"{folder}/" if folder else ""
        a.submissions.append(models.Submission(
            student=student, text=text,
            original_filename=(label + ", ".join(n for n, _ in parts))[:300]))
        added += 1
    db.commit()

    msg = tr(request, "Submissions added: {n}; new students: {m}.", n=added, m=new_students)
    if errors:
        shown = "; ".join(errors[:10]) + (" …" if len(errors) > 10 else "")
        msg += " " + tr(request, "Failed: {files}", files=shown)
    if auto_grade and added and _start_grading(assignment_id, background):
        msg += " " + tr(request, "AI grading started.")
    return RedirectResponse(f"/assignments/{assignment_id}?msg=" + quote(msg), status_code=303)


def _start_grading(assignment_id: int, background: BackgroundTasks) -> bool:
    if assignment_id in _grading_now:
        return False
    _grading_now.add(assignment_id)
    _progress[assignment_id] = {"total": 0, "done": 0, "errors": 0, "started": time.monotonic()}
    background.add_task(_grade_pending, assignment_id)
    return True


def _grade_pending(assignment_id: int) -> None:
    progress = _progress.setdefault(assignment_id, {"total": 0, "done": 0, "errors": 0,
                                                    "started": time.monotonic()})
    try:
        with SessionLocal() as db:
            a = db.get(models.Assignment, assignment_id)
            pending = [s for s in (a.submissions if a else []) if s.status in (models.SUBMITTED, models.ERROR)]
            progress.update(total=len(pending), done=0, errors=0, started=time.monotonic())
            for sub in pending:
                grade_submission(db, sub)
                progress["done"] += 1
                progress["errors"] += int(sub.status == models.ERROR)
    finally:
        _grading_now.discard(assignment_id)


@router.get("/assignments/{assignment_id}/progress")
def grading_progress(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    lang = lang_of(request)
    p = _progress.get(assignment_id, {})
    total, done = p.get("total", 0), p.get("done", 0)
    elapsed = time.monotonic() - p["started"] if p else 0
    eta = round(elapsed / done * (total - done)) if done and total > done else None
    return JSONResponse({
        "running": assignment_id in _grading_now,
        "total": total,
        "done": done,
        "errors": p.get("errors", 0),
        "percent": round(100 * done / total) if total else 0,
        "eta_seconds": eta,
        "rows": [{"id": s.id, "status": s.status, "label": i18n.translate(s.status_label, lang),
                  "ai": fmt({"lang": lang}, s.ai_total), "flag": s.injection_flag, "unsure": s.uncertain}
                 for s in a.submissions],
    })


@router.post("/assignments/{assignment_id}/grade-all")
def grade_all(assignment_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    get_or_404(db, models.Assignment, assignment_id)
    _start_grading(assignment_id, background)
    return go(request, f"/assignments/{assignment_id}", N_("AI grading started. The page will refresh by itself."))


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download_name(text: str, ext: str) -> str:
    safe = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")[:60] or "eksports"
    return f"{safe}.{ext}"


def _attachment(data: bytes, media_type: str, filename: str) -> Response:
    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"})


@router.get("/assignments/{assignment_id}/summary", response_class=HTMLResponse)
def assignment_summary(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    summary = summarize(a)
    links = [(r.submission, f"{request.base_url}feedback/{r.submission.feedback_token}")
             for r in summary.rows if r.approved]
    return render(request, "summary.html", assignment=a, course=a.course, s=summary, links=links,
                  grading_now=assignment_id in _grading_now)


@router.post("/assignments/{assignment_id}/approve-all")
def approve_all(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    to_approve = summarize(a).approvable
    now = datetime.now()
    for sub in to_approve:
        sub.status = models.APPROVED
        sub.approved_at = now
    db.commit()
    left = sum(1 for sub in a.submissions if sub.status == models.AI_GRADED)
    return go(request, f"/assignments/{assignment_id}/summary",
              N_("Approved submissions: {n}. Still to review yourself: {m}."), n=len(to_approve), m=left)


@router.get("/assignments/{assignment_id}/export.xlsx")
def export_assignment(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    data = assignment_workbook(a, lang_of(request), str(request.base_url))
    return _attachment(data, XLSX, _download_name(a.title, "xlsx"))


@router.get("/assignments/{assignment_id}/print", response_class=HTMLResponse)
def print_feedback(assignment_id: int, request: Request, db: Session = Depends(get_db)):
    a = get_or_404(db, models.Assignment, assignment_id)
    items = [(r.submission, weak_topics(r.submission)) for r in summarize(a).rows if r.approved]
    return render(request, "print.html", lang=a.language, assignment=a, items=items)


@router.get("/courses/{course_id}/results.xlsx")
def export_course(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    return _attachment(course_workbook(course, lang_of(request)), XLSX, _download_name(course.name, "xlsx"))


@router.get("/submissions/{submission_id}", response_class=HTMLResponse)
def submission_page(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = get_or_404(db, models.Submission, submission_id)
    a = sub.assignment
    others = [s.id for s in a.submissions]
    idx = others.index(sub.id)
    return render(request, "submission.html", sub=sub, assignment=a, course=a.course,
                  weak=weak_topics(sub) if sub.scores else [],
                  prev_id=others[idx - 1] if idx > 0 else None,
                  next_id=others[idx + 1] if idx < len(others) - 1 else None)


@router.post("/submissions/{submission_id}/grade")
def grade_one(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = get_or_404(db, models.Submission, submission_id)
    grade_submission(db, sub)
    if sub.status == models.ERROR:
        return go(request, f"/submissions/{submission_id}", N_("AI grading failed — see the error below."))
    return go(request, f"/submissions/{submission_id}", N_("The AI suggestion is ready. Review and approve it."))


@router.post("/submissions/{submission_id}/review")
async def review(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = get_or_404(db, models.Submission, submission_id)
    form = await request.form()
    for crit in sub.assignment.criteria:
        points = parse_float(form.get(f"points_{crit.id}"))
        score = sub.score_for(crit.id)
        if score is None:
            score = models.CriterionScore(criterion_id=crit.id)
            sub.scores.append(score)
        if points is not None:
            score.final_points = min(max(points, 0.0), float(crit.max_points))
        score.teacher_comment = str(form.get(f"comment_{crit.id}", "")).strip()
    sub.final_summary = str(form.get("summary", "")).strip()
    if sub.status in (models.SUBMITTED, models.ERROR):
        sub.status = models.AI_GRADED

    if form.get("action") == "approve":
        missing = [c.name for c in sub.assignment.criteria
                   if (s := sub.score_for(c.id)) is None or s.final_points is None]
        if missing:
            db.commit()
            return go(request, f"/submissions/{submission_id}", N_("Points missing for: {names}"),
                      names=", ".join(missing))
        sub.status = models.APPROVED
        sub.approved_at = datetime.now()
        db.commit()
        nxt = next((s for s in sub.assignment.submissions if s.status == models.AI_GRADED), None)
        if nxt:
            return go(request, f"/submissions/{nxt.id}", N_("Approved. Next submission waiting for review:"))
        return go(request, f"/assignments/{sub.assignment_id}",
                  N_("Approved. All AI-graded submissions have been reviewed."))
    db.commit()
    return go(request, f"/submissions/{submission_id}", N_("Saved."))


@router.post("/submissions/{submission_id}/reopen")
def reopen(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = get_or_404(db, models.Submission, submission_id)
    sub.status = models.AI_GRADED if sub.scores else models.SUBMITTED
    sub.approved_at = None
    db.commit()
    return go(request, f"/submissions/{submission_id}", N_("Approval cancelled — you can edit now."))


@router.post("/submissions/{submission_id}/delete")
def delete_submission(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = get_or_404(db, models.Submission, submission_id)
    assignment_id = sub.assignment_id
    db.delete(sub)
    db.commit()
    return go(request, f"/assignments/{assignment_id}", N_("Submission deleted."))


@router.get("/feedback/{token}", response_class=HTMLResponse)
def feedback_page(token: str, request: Request, db: Session = Depends(get_db)):
    sub = db.scalar(select(models.Submission).where(models.Submission.feedback_token == token))
    if sub is None:
        raise HTTPException(404)
    return render(request, "feedback.html", lang=sub.assignment.language, sub=sub, assignment=sub.assignment,
                  weak=weak_topics(sub) if sub.status == models.APPROVED else [])


@router.get("/courses/{course_id}/results", response_class=HTMLResponse)
def results_page(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    return render(request, "results.html", course=course, rows=course_results(course))


@router.get("/courses/{course_id}/results.csv")
def results_download(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    lang = lang_of(request)
    filename = f"{'results_course' if lang == 'en' else 'rezultati_kurss'}_{course.id}.csv"
    return Response(results_csv(course, lang), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/courses/{course_id}/stats", response_class=HTMLResponse)
def stats_page(course_id: int, request: Request, db: Session = Depends(get_db)):
    course = get_or_404(db, models.Course, course_id)
    return render(request, "stats.html", course=course, stats=agreement_stats(course),
                  provider=settings.llm_provider)
