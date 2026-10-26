from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    # Firebase Auth UID.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(255))
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
