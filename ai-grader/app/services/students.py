import re
from pathlib import Path

from .. import models

_HEADER_WORDS = {"kods", "code", "id", "skolēns", "skolens", "student", "nr", "nr."}


def parse_student_lines(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\s*[;,\t]\s*", line, maxsplit=1)
        code = parts[0].strip().strip('"')
        name = parts[1].strip().strip('"') if len(parts) > 1 else ""
        if not code or code.lower() in _HEADER_WORDS:
            continue
        rows.append((code[:50], name[:200]))
    return rows


def next_code(course: models.Course) -> str:
    numbers = [int(m.group(1)) for s in course.students if (m := re.fullmatch(r"S-(\d+)", s.code))]
    return f"S-{(max(numbers) + 1) if numbers else 1:03d}"


def clean_name(text: str) -> str:
    text = re.sub(r"_\d+_assign(submission|feedback)_.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\d+\)$", "", text)
    return re.sub(r"[_\-\s]+", " ", text).strip()


def match_or_create_student(course: models.Course, filename: str, folder: str = "") -> tuple[models.Student, bool]:
    haystack = f"{folder} {Path(filename).stem}".lower()
    normalized = re.sub(r"[_\-]+", " ", haystack)
    for s in course.students:
        if re.search(rf"(?<![^\W_]){re.escape(s.code.lower())}(?![^\W_])", haystack):
            return s, False
    for s in course.students:
        name = s.display_name.strip().lower()
        if name and (name in haystack or name in normalized):
            return s, False
    display = clean_name(folder) if folder else clean_name(Path(filename).stem)
    student = models.Student(code=next_code(course), display_name=display[:200])
    course.students.append(student)
    return student, True
