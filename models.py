from datetime import datetime, date
from typing import List, Optional
from sqlalchemy import String, Text, Integer, DateTime, Date, Boolean, ForeignKey, Table, Column
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase): # classがデータベースのテーブルであることを示す親クラス
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    problems: Mapped[List["Problem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    
    
# 中間テーブル    
problem_tag = Table(
    "problem_tag",
    Base.metadata,
    Column("problem_id", ForeignKey("problems.id")),
    Column("tag_id", ForeignKey("tags.id")),
)
# マスターテーブル方式
class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    problems: Mapped[List["Problem"]] = relationship(
        secondary=problem_tag,
        back_populates="tags"
    )



class Problem(Base):
    """問題データを管理するテーブル
       Mapped[pythonのデータ型] mapped_column(SQLのデータ型)
       Optional = null OK
    """
    __tablename__ = 'problems'

    id: Mapped[int] = mapped_column(primary_key=True)
    
    """ User System """
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey('users.id'))
    user: Mapped["User"] = relationship(back_populates="problems")
    
    # 問題データ
    """  format must be like 'static/images/problems/uuid' """
    problem_image_path: Mapped[Optional[str]] = mapped_column(String(512))
    problem_text: Mapped[Optional[str]] = mapped_column(Text)  # OCRテキストや手入力用（任意）
    source: Mapped[Optional[str]] = mapped_column(String(255))  # 出典（例: "青チャート P.120"）
    tags: Mapped[List["Tag"]] = relationship(
        secondary=problem_tag,
        back_populates="problems"
        )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # cascade="all, delete-orphan" により、問題が削除されたら関連する解答履歴や計画も削除する
    submissions: Mapped[List["Submission"]] = relationship(back_populates="problem", cascade="all, delete-orphan")
    schedules: Mapped[List["StudyPlan"]] = relationship(back_populates="problem", cascade="all, delete-orphan")

    @property
    def solve_count(self) -> int:
        """解いた回数（解答履歴の数から動的に算出）"""
        return len(self.submissions)


class Submission(Base):
    """解答（答案）の履歴を管理するテーブル"""
    __tablename__ = 'submissions'

    id: Mapped[int] = mapped_column(primary_key=True)
     
    # 答案（ノート）の画像パス
    """  format must be like 'static/images/answers/uuid' """
    answer_image_path: Mapped[Optional[str]] = mapped_column(String(512))
    
    # 成績・フィードバック
    score: Mapped[Optional[int]] = mapped_column(Integer)          
    ai_feedback: Mapped[Optional[str]] = mapped_column(Text)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    mistake: Mapped[Optional[str]] = mapped_column(Text)           
    user_memo: Mapped[Optional[str]] = mapped_column(Text)       
    
    # 解いた日時（提出日時）
    solved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relation
    problem_id: Mapped[int] = mapped_column(ForeignKey('problems.id'))
    problem: Mapped["Problem"] = relationship(back_populates="submissions")


class StudyPlan(Base):
    """ToDo(学習スケジュール)を管理するテーブル"""
    __tablename__ = 'study_plans'

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey('problems.id'))
    
    # いつ解く予定か
    scheduled_date: Mapped[date] = mapped_column(Date)
    
    # 完了したかどうか
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # Relation
    problem: Mapped["Problem"] = relationship(back_populates="schedules")

