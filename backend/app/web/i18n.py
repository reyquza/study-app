"""Japanese and English UI.

Templates wrap their Japanese text in _("..."). Japanese visitors see it as written; everyone
else gets the English in EN below. The language comes from the "lang" cookie (set by the
English / 日本語 switch) or, without it, from the browser's preferred language, so judges
and other English-language visitors get English without looking for a setting. Text with no
English entry falls back to Japanese; tests/test_i18n.py checks that none is missing.
"""

from fastapi import Request

LANGUAGES = ("ja", "en")
COOKIE_NAME = "lang"


def pick_language(request: Request) -> str:
    chosen = request.cookies.get(COOKIE_NAME)
    if chosen in LANGUAGES:
        return chosen
    # The first language the browser lists is the one the visitor prefers.
    first = request.headers.get("accept-language", "").split(",")[0].strip().lower()
    return "en" if first and not first.startswith("ja") else "ja"


def translate(text: str, language: str, **values) -> str:
    if language == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text


def template_context(request: Request) -> dict:
    """Jinja context processor: `lang` and the `_` translation function in every template."""
    language = pick_language(request)
    return {"lang": language, "_": lambda text, **values: translate(text, language, **values)}


EN: dict[str, str] = {
    # Shared
    "ホームに戻る": "Back to home",
    "ホームへ戻る": "Back to home",
    "とりあえずホームに戻る": "Back to home for now",
    "前のページに戻る": "Back to the previous page",
    "キャンセルして戻る": "Cancel and go back",
    "NextLearn ロゴ": "NextLearn logo",
    "ゲスト": "Guest",
    "アカウント登録": "Create an account",
    "ログアウト": "Log out",
    "問題": "Problem",
    "問題：": "Problem: ",
    "問題画像": "Problem image",
    "答案": "Answer",
    "答案画像": "Answer image",
    "出典:": "Source:",
    "出典なし": "No source",
    "問題ID:": "Problem ID:",
    "点": " pts",
    "{score}点": "{score} pts",
    "{n}回": "{n}",
    "アップロードするファイルのプレビュー": "Preview of the file to upload",
    "変換中...": "Converting...",
    "プレビューの表示に失敗しました。": "Could not show the preview.",
    "本当にこのタスクを削除しますか？": "Delete this task?",
    "タスクを削除": "Delete task",
    "タスクの詳細": "Task details",
    # Grading unavailable (model shut down)
    "AI採点は現在停止中です。": "AI grading is paused.",
    "採点に使っているAIモデル（{model}）の提供がGoogleによって終了したため、新しい答案を受け付けていません。":
        "Google has shut down the AI model this app grades with ({model}), so new answers are not accepted.",
    "新しいモデルへの移行と、採点精度の再検証が済みしだい再開します。":
        "Grading will resume once the app has moved to a new model and its accuracy has been re-checked.",
    "AI採点は現在停止中です（採点に使っているAIモデル {model} の提供終了のため）。":
        "AI grading is paused (Google shut down the model it uses, {model}).",
    "AI採点は停止中です（画面上部のお知らせ参照）": "AI grading is paused (see the notice at the top)",
    "実際の採点結果の見本を見る": "See a real graded sample",
    "代わりに、実際の採点結果の見本を見る": "See a real graded sample instead",
    "AIの採点結果の見本を見る": "See a sample AI grading",
    "AIの採点結果の見本を見る（登録不要）": "See a sample AI grading (no sign-up)",
    # Login
    "ログイン": "Log in",
    "新規登録": "Sign up",
    "日本の大学入試に向けた、手書きの数学答案をAIが採点するアプリです。":
        "AI grading of handwritten math answers, for Japanese university entrance exams.",
    "アカウントを作成すると、ゲストで登録した問題と採点結果がそのまま引き継がれます。":
        "Create an account to keep the problems and results from your guest session.",
    "メールアドレス": "Email",
    "パスワード": "Password",
    "8文字以上": "At least 8 characters",
    "または": "or",
    "登録せずに試す（ゲスト）": "Try it without an account (guest)",
    "ゲストのデータは、ログアウトするかCookieを消すと見られなくなります。":
        "Guest data is lost when you log out or clear cookies.",
    "メールアドレスかパスワードが違います。": "Incorrect email or password.",
    "このメールアドレスはすでに登録されています。ログインしてください。":
        "An account with this email already exists. Please log in.",
    "試行回数が多すぎます。しばらく時間をおいてからお試しください。":
        "Too many attempts. Please wait a while and try again.",
    "メールアドレスの形式と、パスワードが8文字以上かを確認してください。":
        "Check the email address, and that the password has at least 8 characters.",
    "メールアドレスの形式を確認してください。": "Check the email address.",
    "エラーが発生しました。もう一度お試しください。": "Something went wrong. Please try again.",
    "サーバーに接続できませんでした。": "Could not reach the server.",
    "登録してはじめる": "Sign up and start",
    # Home
    "何やってるんですか　勉強してください！": "What are you doing? Go study!",
    "今日の日付：": "Today:",
    "未完了のToDo": "To-dos left",
    "3日後までの未完了のToDoが表示されます": "Unfinished to-dos for the next 3 days",
    "{date}のToDo": "To-dos for {date}",
    "[未完了]": "[Not done]",
    "現在、未完了のTodoはありません。": "No unfinished to-dos.",
    "予定表を確認・編集する": "View and edit the schedule",
    "問題を登録する": "Add a problem",
    # Messages after a redirect (routes.ERROR_MESSAGES)
    "本日のAI採点の上限に達しました。また明日ご利用ください!": "You have reached today's AI grading limit. Please come back tomorrow!",
    "ゲストでの採点が混み合っています。少し時間をおくか、アカウント登録(無料)をするとご利用いただけます。":
        "Guest grading is busy. Please wait a little, or create a free account to keep grading.",
    "本日の問題登録の上限に達しました。また明日ご利用ください。": "You have reached today's limit for adding problems. Please come back tomorrow.",
    "AI採点は現在停止中です。理由は画面上部のお知らせをご覧ください。": "AI grading is paused. See the notice at the top of the page.",
    # Server-side validation
    "{label}は{limit}文字までです。": "{label} can be at most {limit} characters.",
    "問題文": "The problem text",
    "出典": "The source",
    "ミス": "The mistake",
    "メモ": "The note",
    "タグは{count}個まで、1つ{chars}文字までです。": "Up to {count} tags, each at most {chars} characters.",
    "許可されていないファイル形式です。({content_type} / {extension})": "This file type is not allowed. ({content_type} / {extension})",
    "ファイルサイズが大きすぎます(上限10MB)。": "The file is too large (10 MB maximum).",
    "PDFを読み込めませんでした。画像で送るか、別のPDFをお試しください。": "Could not read the PDF. Send a photo instead, or try another PDF.",
    "PDFは{pages}ページまでです。": "PDFs can have at most {pages} pages.",
    # Add a problem
    "問題登録": "Add a problem",
    "新しい問題を登録": "Add a new problem",
    "問題のファイル (画像 または PDF)": "Problem file (image or PDF)",
    "問題文の手動入力 (任意)": "Problem text (optional)",
    "画像が読みづらい場合の補助テキストや、OCR結果を入力します。": "Type the problem here if the image is hard to read.",
    "出典 (任意)": "Source (optional)",
    "例: 青チャート P.120": "e.g. textbook name, p. 120",
    "タグ (任意)": "Tags (optional)",
    "カンマ区切り（例: 数学, 微積分）": "Comma-separated (e.g. math, calculus)",
    "解答予定日 (任意)": "Planned date (optional)",
    # Solve
    "問題演習": "Solve",
    "答案を提出する": "Submit your answer",
    "解き終わったら、ノートの写真を撮ってアップロードしてください。": "When you finish, take a photo of your notebook and upload it.",
    "AIに採点してもらう": "Get it graded by AI",
    "アップロード中...": "Uploading...",
    # Grading
    "採点中": "Grading",
    "AIが採点しています": "The AI is grading your answer",
    "通常20〜40秒ほどかかります。このページは閉じても大丈夫です。結果はあとで問題の詳細から確認できます。":
        "This usually takes 20–40 seconds. You can close this page; the result will be in the problem's details.",
    "採点できませんでした": "Could not grade this answer",
    "時間をおいて、もう一度提出してください。今回の提出は1日の上限回数に数えません。":
        "Please submit again later. This attempt does not count toward your daily limit.",
    "AIモデルが使えなくなりました": "The AI model is no longer available",
    "採点に使っているAIモデル（{model}）の提供がGoogleによって終了したため、採点できませんでした。新しいモデルへの移行と、採点精度の再検証が済みしだい再開します。":
        "Google has shut down the AI model this app grades with ({model}), so this answer could not be graded. "
        "Grading will resume once the app has moved to a new model and its accuracy has been re-checked.",
    "AIが混み合っているため、自動で再試行しています…": "The AI is busy; retrying automatically…",
    "{seconds}秒経過": "{seconds} s elapsed",
    "もう一度提出する": "Submit again",
    # Result
    "採点結果": "Result",
    "あなたの答案": "Your answer",
    "AIによる採点": "AI grade",
    "AI講師からのアドバイス": "AI tutor's advice",
    "模範解答・解説": "Model answer and explanation",
    "自分の言葉で振り返ろう！": "Reflect in your own words!",
    "ここでミスを言語化することが成長への近道です。": "Putting your mistake into words is the fastest way to improve.",
    "今回の主なミス（一言で）": "Your main mistake (in a few words)",
    "例：場合分けの条件を見落とした": "e.g. missed a case in the case analysis",
    "次に活かすためのメモ": "A note for next time",
    "例：絶対値を外すときは必ず数直線を書く！": "e.g. draw a number line before removing absolute values!",
    "記録を保存してホームへ戻る": "Save and go home",
    # Sample
    "採点結果の見本": "Sample grading",
    "（採点日 {date}、モデル {model}）": "(graded {date} by {model})",
    "自分の答案で試す（登録不要のゲストも可）": "Try it with your own answer (guest mode needs no account)",
    # Problem details
    "問題の詳細と履歴": "Problem details and history",
    "問題の詳細": "Problem details",
    "点数と学習の履歴": "Scores and history",
    "提出日時": "Submitted",
    "点数": "Score",
    "主なミス / メモ": "Mistake / note",
    "詳細": "Details",
    "不明": "Unknown",
    "ミス:": "Mistake:",
    "メモ:": "Note:",
    "記録なし": "None",
    "確認": "View",
    "まだこの問題の提出履歴はありません。": "No submissions for this problem yet.",
    # Calendar
    "予定表": "Schedule",
    "{year}年 {month}月の予定": "Schedule for {year}-{month:02d}",
    "今月へ戻る": "This month",
    "日": "Sun",
    "月": "Mon",
    "火": "Tue",
    "水": "Wed",
    "木": "Thu",
    "金": "Fri",
    "土": "Sat",
    "+{n}件": "+{n} more",
    "前の月へ": "Previous month",
    "次の月へ": "Next month",
    "{date} のToDo": "To-dos for {date}",
    "{date} の予定": "Plan for {date}",
    "カレンダーに戻る": "Back to the calendar",
    "解いた回数:": "Times solved:",
    "前回の点数:": "Last score:",
    "今回の点数:": "Latest score:",
    "問題を解く": "Solve",
    "もう一度解く": "Solve again",
    "未完了のToDoはありません。素晴らしいです!": "No unfinished to-dos. Great job!",
    "完了したToDo": "Done",
    "完了したToDoはまだありません。": "Nothing done yet.",
    "新しいタスクを追加する": "Add a new task",
    "タスク追加": "Add tasks",
    "{date} にタスクを追加": "Add tasks for {date}",
    "追加する問題を選択してください（複数選択可）": "Choose the problems to add (you can pick several)",
    "タグや出典で検索...": "Search by tag or source...",
    "画像・テキストなし": "No image or text",
    "選択したタスクを追加する": "Add the selected tasks",
}
