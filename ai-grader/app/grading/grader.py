import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..i18n import translate
from .injection import detect_injection
from .prompts import PROMPT_VERSION, build_user_prompt, system_prompt
from .providers import GradingRequest, LLMBadRequest, LLMError, Provider, get_provider


EXAMPLE_CHARS = 1500


def _to_number(v):
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        m = re.search(r"-?\d+(\.\d+)?", v)
        return float(m.group()) if m else v
    return v


class _AICriterion(BaseModel):
    id: int
    points: float
    justification: str = ""
    mistakes: list[str] = Field(default_factory=list)
    improvement: str = ""

    @field_validator("id", "points", mode="before")
    @classmethod
    def _numbers(cls, v):
        return _to_number(v)

    @field_validator("mistakes", mode="before")
    @classmethod
    def _mistakes_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x) for x in v]


class _AIResponse(BaseModel):
    criteria: list[_AICriterion]
    summary: str = ""
    injection_suspected: bool = False


@dataclass
class CriterionResult:
    criterion_id: int
    points: float
    justification: str
    mistakes: list[str]
    improvement: str


@dataclass
class GradingResult:
    criteria: list[CriterionResult]
    summary: str
    injection_findings: list[str]
    model_label: str
    prompt_version: str
    raw: str
    warnings: list[str] = field(default_factory=list)
    check_points: dict[int, float] | None = None
    uncertain: bool = False

    @property
    def injection_flag(self) -> bool:
        return bool(self.injection_findings)


def extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The model response contains no JSON object.")
    return json.loads(text[start:end + 1])


def round_half(x: float) -> float:
    return round(x * 2) / 2


def validate_response(data: dict, criteria: list[dict],
                      language: str = "lv") -> tuple[list[CriterionResult], str, bool, list[str]]:
    try:
        parsed = _AIResponse.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"The model response does not match the format: {e.errors()[0]['msg']}") from e

    warnings: list[str] = []
    by_id = {c.id: c for c in parsed.criteria}
    results: list[CriterionResult] = []
    for c in criteria:
        ai = by_id.get(c["id"])
        if ai is None:
            warnings.append(translate('The AI did not grade the criterion "{name}": 0 points set, please review.',
                                      language, name=c["name"]))
            results.append(CriterionResult(
                c["id"], 0.0, translate("The AI did not grade this criterion; the teacher must assess it.", language),
                [], ""))
            continue
        points = round_half(ai.points)
        if points < 0 or points > c["max_points"]:
            warnings.append(translate(
                'The AI gave {points} points for the criterion "{name}" (allowed 0-{max}); the value was clamped.',
                language, points=_number(ai.points, language), name=c["name"], max=_number(c["max_points"], language)))
            points = min(max(points, 0.0), float(c["max_points"]))
        results.append(CriterionResult(c["id"], points, ai.justification.strip(),
                                       [m.strip() for m in ai.mistakes if m.strip()], ai.improvement.strip()))
    unknown = set(by_id) - {c["id"] for c in criteria}
    if unknown:
        warnings.append(translate("The AI returned unknown criteria {ids}; they were ignored.", language,
                                  ids=sorted(unknown)))
    return results, parsed.summary.strip(), parsed.injection_suspected, warnings


def _number(value: float, language: str) -> str:
    text = f"{value:g}"
    return text.replace(".", ",") if language == "lv" else text


def _run(provider: Provider, system: str, user_prompt: str, request: GradingRequest,
         criteria: list[dict], retries: int):
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            raw = provider.generate(system, user_prompt, request)
            return (raw, *validate_response(extract_json(raw), criteria, request.language))
        except (ValueError, LLMBadRequest) as e:
            last_error = e
    raise LLMError("Could not read the model response: {error}", error=last_error)


def grade_text(instructions: str, criteria: list[dict], student_text: str,
               provider: Provider | None = None, retries: int = 1,
               model_answer: str = "", language: str = "lv",
               examples: list[dict] | None = None, double_check: bool = False) -> GradingResult:
    provider = provider or get_provider()
    examples = examples or []
    request = GradingRequest(instructions, criteria, student_text, model_answer, language)
    system = system_prompt(language)
    user_prompt = build_user_prompt(instructions, criteria, student_text, model_answer, examples)

    raw, results, summary, model_flag, warnings = _run(provider, system, user_prompt, request, criteria, retries)

    check_points: dict[int, float] | None = None
    uncertain = False
    if double_check:
        check_request = replace(request, temperature=settings.check_temperature)
        try:
            _, check, _, check_flag, _ = _run(provider, system, user_prompt, check_request, criteria, retries)
        except LLMError as e:
            warnings.append(translate("The second AI run failed: {error}", language, error=e.localized(language)))
        else:
            check_points = {r.criterion_id: r.points for r in check}
            model_flag = model_flag or check_flag
            first_total = sum(r.points for r in results)
            second_total = sum(check_points.values())
            biggest = max((abs(r.points - check_points[r.criterion_id]) for r in results), default=0.0)
            uncertain = biggest >= 1 or abs(first_total - second_total) >= 1
            if uncertain:
                warnings.append(translate(
                    "The second AI run gave {b} points instead of {a}; please review this submission.",
                    language, a=_number(first_total, language), b=_number(second_total, language)))

    findings = detect_injection(student_text, language)
    if model_flag:
        findings.insert(0, translate("the language model flagged a manipulation attempt", language))

    version = PROMPT_VERSION + (f"+ex{len(examples)}" if examples else "") + ("+check" if double_check else "")
    return GradingResult(results, summary, findings, provider.model_label, version, raw, warnings,
                         check_points, uncertain)


def criteria_payload(assignment: models.Assignment) -> list[dict]:
    return [
        {"id": c.id, "name": c.name, "description": c.description,
         "max_points": float(c.max_points), "topic": c.topic, "expected_answer": c.expected_answer or ""}
        for c in assignment.criteria
    ]


def corrected_submissions(assignment: models.Assignment,
                          exclude: models.Submission | None = None) -> list[models.Submission]:
    subs = [s for s in assignment.submissions
            if s.status == models.APPROVED and any(sc.changed_by_teacher for sc in s.scores)
            and (exclude is None or (s.id != exclude.id and s.student_id != exclude.student_id))]
    return sorted(subs, key=lambda s: s.approved_at or s.submitted_at, reverse=True)


def teacher_examples(assignment: models.Assignment, exclude: models.Submission | None = None,
                     limit: int | None = None) -> list[dict]:
    limit = settings.teacher_examples if limit is None else limit
    position = {c.id: c.position for c in assignment.criteria}
    examples = []
    for s in corrected_submissions(assignment, exclude)[:max(limit, 0)]:
        scores = sorted((sc for sc in s.scores if sc.final_points is not None),
                        key=lambda sc: position.get(sc.criterion_id, 0))
        examples.append({"text": s.text[:EXAMPLE_CHARS],
                         "scores": [{"id": sc.criterion_id, "points": sc.final_points,
                                     "comment": sc.teacher_comment} for sc in scores]})
    return examples


def grade_submission(db: Session, submission: models.Submission, provider: Provider | None = None) -> models.Submission:
    assignment = submission.assignment
    language = assignment.language or "lv"
    examples = teacher_examples(assignment, submission) if assignment.use_examples else []
    try:
        result = grade_text(assignment.instructions, criteria_payload(assignment), submission.text, provider,
                            model_answer=assignment.model_answer or "", language=language,
                            examples=examples, double_check=assignment.double_check)
    except LLMError as e:
        submission.status = models.ERROR
        submission.error = e.localized(language)
        db.commit()
        return submission

    existing = {s.criterion_id: s for s in submission.scores}
    for r in result.criteria:
        score = existing.get(r.criterion_id)
        if score is None:
            score = models.CriterionScore(criterion_id=r.criterion_id)
            submission.scores.append(score)
        score.ai_points = r.points
        score.ai_justification = r.justification
        score.ai_mistakes_json = json.dumps(r.mistakes, ensure_ascii=False)
        score.ai_improvement = r.improvement
        score.check_points = result.check_points.get(r.criterion_id) if result.check_points else None
        score.final_points = r.points
        score.teacher_comment = ""

    submission.ai_model = result.model_label
    submission.ai_prompt_version = result.prompt_version
    submission.ai_raw = result.raw
    submission.ai_summary = result.summary
    submission.final_summary = result.summary
    submission.ai_graded_at = datetime.now()
    submission.injection_flag = result.injection_flag
    submission.injection_details = "\n".join(result.injection_findings)
    submission.uncertain = result.uncertain
    submission.error = "\n".join(result.warnings)
    submission.status = models.AI_GRADED
    submission.approved_at = None
    db.commit()
    return submission
