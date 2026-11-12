from app.grading.rubric import Rubric, RubricStep, StepJudgment, build_prompt, score


RUBRIC = Rubric(
    problem_id="T1",
    statement="Find a.",
    answer_key="a > 2",
    steps=(
        RubricStep("cond_a", 3, "(1) condition A"),
        RubricStep("cond_b", 2, "(1) condition B"),
        RubricStep("combine", 5, "(2) combine", requires=("cond_a", "cond_b")),
    ),
)


def _judge(**awarded):
    return [StepJudgment(step_id=step_id, awarded=points, reason="r") for step_id, points in awarded.items()]


def test_total_is_computed_from_steps_and_clamped():
    awarded, _, missing = score(RUBRIC, _judge(cond_a=3, cond_b=9, combine=5))

    assert awarded == {"cond_a": 3, "cond_b": 2, "combine": 5}
    assert missing == []


def test_dependent_step_needs_its_prerequisites_in_full():
    awarded, _, _ = score(RUBRIC, _judge(cond_a=3, cond_b=1, combine=5))

    assert awarded["combine"] == 0


def test_missing_and_unknown_steps():
    awarded, _, missing = score(RUBRIC, _judge(cond_a=3, invented=4))

    assert awarded == {"cond_a": 3, "cond_b": 0, "combine": 0}
    assert missing == ["cond_b", "combine"]


def test_prompt_lists_every_step_with_points():
    prompt = build_prompt(RUBRIC, transcribe=True)

    assert "`cond_a` (3 pt)" in prompt and "`combine` (5 pt)" in prompt
    assert "Counts only if cond_a, cond_b earn full points." in prompt
    assert "transcribe" in prompt
