import enum
from datetime import date, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, Column, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import false, func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    # Random, so IDs in URLs and tokens reveal nothing about other accounts.
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=lambda: uuid4().hex)
    # Stored lower-cased. Guests have neither an email nor a password until they sign up.
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    problems: Mapped[list["Problem"]] = relationship(back_populates="user", cascade="all, delete-orphan")


problem_tag = Table(
    "problem_tag",
    Base.metadata,
    Column("problem_id", ForeignKey("problems.id")),
    Column("tag_id", ForeignKey("tags.id")),
)


class Tag(Base):
    """Tag master table, shared by all problems."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    problems: Mapped[list["Problem"]] = relationship(secondary=problem_tag, back_populates="tags")


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    user: Mapped["User"] = relationship(back_populates="problems")

    # URL of the uploaded image or PDF (Cloudinary).
    problem_image_path: Mapped[Optional[str]] = mapped_column(String(512))
    # Optional typed problem statement, used alongside or instead of the image.
    problem_text: Mapped[Optional[str]] = mapped_column(Text)
    # Where the problem comes from, e.g. a textbook page.
    source: Mapped[Optional[str]] = mapped_column(String(255))
    tags: Mapped[list["Tag"]] = relationship(secondary=problem_tag, back_populates="problems")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submissions: Mapped[list["Submission"]] = relationship(back_populates="problem", cascade="all, delete-orphan")
    schedules: Mapped[list["StudyPlan"]] = relationship(back_populates="problem", cascade="all, delete-orphan")
    grading_jobs: Mapped[list["GradingJob"]] = relationship(back_populates="problem", cascade="all, delete-orphan")

    @property
    def solve_count(self) -> int:
        return len(self.submissions)


class Submission(Base):
    """One graded attempt at a problem."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # URL of the photo of the handwritten answer.
    answer_image_path: Mapped[Optional[str]] = mapped_column(String(512))

    score: Mapped[Optional[int]] = mapped_column(Integer)
    ai_feedback: Mapped[Optional[str]] = mapped_column(Text)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    # The learner's own reflection, written after reading the feedback.
    mistake: Mapped[Optional[str]] = mapped_column(Text)
    user_memo: Mapped[Optional[str]] = mapped_column(Text)

    solved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    problem: Mapped["Problem"] = relationship(back_populates="submissions")


class StudyPlan(Base):
    """A problem scheduled to be solved on a given day (a to-do item)."""

    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    scheduled_date: Mapped[date] = mapped_column(Date)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    problem: Mapped["Problem"] = relationship(back_populates="schedules")


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GradingJob(Base):
    """A request to grade one answer, processed in the background by app.worker.

    The table is the queue: workers claim rows with SELECT ... FOR UPDATE SKIP LOCKED, and a
    claim is a time-limited lease, so a job whose worker died is picked up again.
    """

    __tablename__ = "grading_jobs"
    __table_args__ = (Index("ix_grading_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    problem: Mapped["Problem"] = relationship(back_populates="grading_jobs")
    answer_image_path: Mapped[str] = mapped_column(String(512))

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e]),
        default=JobStatus.QUEUED,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Not claimable before this time; pushed back after a failed attempt.
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    # Which model graded the answer, so results can be compared across grader versions.
    grader_model: Mapped[Optional[str]] = mapped_column(String(100))
    submission_id: Mapped[Optional[int]] = mapped_column(ForeignKey("submissions.id"))
    submission: Mapped[Optional["Submission"]] = relationship()

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
