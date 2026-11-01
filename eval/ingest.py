"""Turn the Google Form uploads into an anonymous answer set.

Input, under eval/data/:
  "<question title> (File responses)/"  as downloaded from Google Drive. The folder title
      contains "Problem<N>"; files are named "<original name> - <uploader>.<ext>".
  author/P<N>/                          answers written by the study's author (optional),
      one file per answer. They are kept apart from participants' answers in the analysis.

Output: eval/data/answers/<answer_id>/page-<i>.<ext> and eval/data/manifest.csv. Several
files from one uploader for one problem become the pages of one answer. The uploader ->
ID mapping is written to eval/data/private/ so a participant's data can be deleted on
request; IDs are a salted hash, so they reveal nothing on their own.
"""

import csv
import hashlib
import re
import secrets
import shutil
from collections import defaultdict
from pathlib import Path

from common import ANSWERS_DIR, DATA_DIR, MIME_TYPES, write_manifest

PRIVATE_DIR = DATA_DIR / "private"
SALT_FILE = PRIVATE_DIR / "salt.txt"


def _salt() -> str:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not SALT_FILE.exists():
        SALT_FILE.write_text(secrets.token_hex(16), encoding="utf-8")
    return SALT_FILE.read_text(encoding="utf-8").strip()


def _anon_id(salt: str, who: str) -> str:
    return hashlib.sha256(f"{salt}:{who}".encode()).hexdigest()[:8]


def _collect() -> dict[tuple[str, str, str], list[Path]]:
    """(problem_id, source, uploader) -> files, in upload order."""
    groups: dict[tuple[str, str, str], list[Path]] = defaultdict(list)
    for folder in DATA_DIR.glob("*(File responses)"):
        match = re.search(r"Problem\s*(\d)", folder.name)
        if not match:
            raise SystemExit(f"Cannot tell the problem from folder name: {folder.name}")
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in MIME_TYPES:
                print(f"skipped unsupported file type: {path.suffix}")
                continue
            uploader = path.stem.rsplit(" - ", 1)[-1].strip()
            groups[(f"P{match.group(1)}", "participant", uploader)].append(path)

    for folder in sorted((DATA_DIR / "author").glob("P*")):
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in MIME_TYPES:
                # Each file is a separate answer (e.g. one correct, one with planted errors).
                groups[(folder.name, "author", path.stem)].append(path)
    return groups


def main() -> None:
    salt = _salt()
    if ANSWERS_DIR.exists():
        shutil.rmtree(ANSWERS_DIR)

    rows, mapping = [], []
    for (problem_id, source, who), paths in sorted(_collect().items()):
        answer_id = f"{problem_id}-{'A' if source == 'author' else ''}{_anon_id(salt, problem_id + who)}"
        target = ANSWERS_DIR / answer_id
        target.mkdir(parents=True)
        for i, path in enumerate(paths, 1):
            shutil.copy2(path, target / f"page-{i}{path.suffix.lower()}")
        rows.append({"answer_id": answer_id, "problem_id": problem_id, "source": source, "pages": len(paths)})
        mapping.append({"answer_id": answer_id, "uploader": who})

    write_manifest(rows)
    with open(PRIVATE_DIR / "respondents.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["answer_id", "uploader"])
        writer.writeheader()
        writer.writerows(mapping)

    counts = defaultdict(int)
    for row in rows:
        counts[(row["problem_id"], row["source"])] += 1
    print(f"{len(rows)} answers:", ", ".join(f"{p} {s}={n}" for (p, s), n in sorted(counts.items())))
    multi = [r["answer_id"] for r in rows if r["pages"] > 1]
    if multi:
        print("multi-page answers:", ", ".join(multi))


if __name__ == "__main__":
    main()
