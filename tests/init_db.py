import os
from datetime import date, timedelta
import models
from main import engine, SessionLocal


def init_database():
    print("データベースの初期化を開始します...")

    # 1. 既存のテーブルを削除して再作成
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    print("テーブルを再作成しました。")

    # 画像保存用のフォルダがなければ作っておく
    os.makedirs("static/images/problems", exist_ok=True)
    os.makedirs("static/images/answers", exist_ok=True)

    db = SessionLocal()
    try:
        print("サンプルデータを投入します...")

        # 1. タグの作成
        tag_math = models.Tag(name="数学")
        db.add(tag_math)
        db.flush() # IDを確定させる

        # 2. 問題の作成（AIが採点しやすいシンプルな2次方程式）
        problem1 = models.Problem(
            problem_text="""
                座標平面上の放物線
                $$
                y = x^2 - 4x + 3
                $$
                と直線
                $$
                y = mx + 1
                $$
                が異なる2点 $A,B$ で交わっているとする。
                (1) 交点 $A,B$ の $x$ 座標を $m$ を用いて表せ。
                (2) 線分 $AB$ の長さを $m$ を用いて表せ。
            """,
            source="テスト用ダミー問題集 P.10",
            # problem_image_path は空（None）にしておきます
        )
        problem1.tags.append(tag_math)
        db.add(problem1)
        db.flush()

        # 3. 予定の作成（今日の日付で未完了のToDoとして登録）
        tomorrow = date.today() - timedelta(1)
        plan1 = models.StudyPlan(
            problem_id=problem1.id,
            scheduled_date=tomorrow,
            is_completed=False
        )
        db.add(plan1)

        db.commit()
        print("✅ データベースの初期化とサンプルデータの投入が完了しました！")
        print("   -> サーバーを起動して、トップページから「問題演習」を開いてください。")

    except Exception as e:
        db.rollback()
        print(f"❌ エラーが発生しました: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    init_database()