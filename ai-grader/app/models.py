import json
import secrets
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .i18n import N_

SUBMITTED = "submitted"
AI_GRADED = "ai_graded"
APPROVED = "approved"
ERROR = "error"

STATUS_LABELS = {
    SUBMITTED: "Submitted",
    AI_GRADED: "Awaiting approval",
    APPROVED: "Approved",
    ERROR: "Error",
}

DEFAULT_GRADE_SCALE = [
    [95, "10"], [87, "9"], [77, "8"], [67, "7"], [58, "6"],
    [49, "5"], [39, "4"], [27, "3"], [15, "2"], [0, "1"],
]


def _now() -> datetime:
    return datetime.now()


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    grade_scale_json: Mapped[str] = mapped_column(Text, default=lambda: json.dumps(DEFAULT_GRADE_SCALE))
    benchmark_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    students: Mapped[list["Student"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Student.code")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Assignment.id")
    materials: Mapped[list["Material"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Material.topic")

    @property
    def grade_scale(self) -> list[list]:
        try:
            scale = json.loads(self.grade_scale_json)
            return sorted(([float(p), str(g)] for p, g in scale), key=lambda x: -x[0])
        except (ValueError, TypeError):
            return DEFAULT_GRADE_SCALE


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("course_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(50))
    display_name: Mapped[str] = mapped_column(String(200), default="")

    course: Mapped[Course] = relationship(back_populates="students")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="student", cascade="all, delete-orphan")

    @property
    def label(self) -> str:
        return f"{self.code} ({self.display_name})" if self.display_name else self.code


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    topic: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500), default="")

    course: Mapped[Course] = relationship(back_populates="materials")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    instructions: Mapped[str] = mapped_column(Text, default="")
    model_answer: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(5), default="lv")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    use_examples: Mapped[bool] = mapped_column(Boolean, default=True)
    double_check: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    course: Mapped[Course] = relationship(back_populates="assignments")
    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", order_by="Criterion.position")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", order_by="Submission.id")

    @property
    def max_points(self) -> float:
        return sum(c.max_points for c in self.criteria)


class Criterion(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    max_points: Mapped[float] = mapped_column(Float, default=1)
    topic: Mapped[str] = mapped_column(String(200), default="")
    expected_answer: Mapped[str] = mapped_column(Text, default="")

    assignment: Mapped[Assignment] = relationship(back_populates="criteria")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(20), default=SUBMITTED)
    feedback_token: Mapped[str] = mapped_column(String(64), default=lambda: secrets.token_urlsafe(16), unique=True)

    ai_model: Mapped[str] = mapped_column(String(200), default="")
    ai_prompt_version: Mapped[str] = mapped_column(String(20), default="")
    ai_raw: Mapped[str] = mapped_column(Text, default="")
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")

    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    injection_details: Mapped[str] = mapped_column(Text, default="")
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    copying_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    final_summary: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    student: Mapped[Student] = relationship(back_populates="submissions")
    scores: Mapped[list["CriterionScore"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def flags(self) -> list[str]:
        return [label for flag, label in ((self.injection_flag, N_("manipulation?")),
                                          (self.uncertain, N_("AI unsure")),
                                          (self.copying_flag, N_("copying"))) if flag]

    def score_for(self, criterion_id: int) -> "CriterionScore | None":
        return next((s for s in self.scores if s.criterion_id == criterion_id), None)

    @property
    def ai_total(self) -> float | None:
        if not self.scores:
            return None
        return sum(s.ai_points or 0 for s in self.scores)

    @property
    def final_total(self) -> float | None:
        if not self.scores:
            return None
        return sum(s.final_points or 0 for s in self.scores)


class CriterionScore(Base):
    __tablename__ = "criterion_scores"
    __table_args__ = (UniqueConstraint("submission_id", "criterion_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id", ondelete="CASCADE"))

    ai_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_justification: Mapped[str] = mapped_column(Text, default="")
    ai_mistakes_json: Mapped[str] = mapped_column(Text, default="[]")
    ai_improvement: Mapped[str] = mapped_column(Text, default="")

    check_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_comment: Mapped[str] = mapped_column(Text, default="")

    submission: Mapped[Submission] = relationship(back_populates="scores")
    criterion: Mapped[Criterion] = relationship()

    @property
    def ai_mistakes(self) -> list[str]:
        try:
            return [str(m) for m in json.loads(self.ai_mistakes_json or "[]")]
        except ValueError:
            return []

    @property
    def changed_by_teacher(self) -> bool:
        return (self.ai_points is not None and self.final_points is not None
                and abs(self.ai_points - self.final_points) > 1e-9)


class SimilarityReview(Base):
    __tablename__ = "similarity_reviews"
    __table_args__ = (UniqueConstraint("submission_a_id", "submission_b_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"))
    submission_a_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    submission_b_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(20))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
