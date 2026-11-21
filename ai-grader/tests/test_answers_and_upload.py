import io
import json

from docx import Document
from sqlalchemy import create_engine, inspect, text

from app.database import init_db
from app.grading.grader import grade_text
from app.grading.prompts import build_user_prompt, system_prompt
from app.grading.providers import Provider
from app.services.students import parse_student_lines

CRITERIA = [{"id": 1, "name": "1. Kur notiek fotosintēze?", "description": "", "max_points": 1,
             "topic": "", "expected_answer": "Hloroplastos."}]


class Capture(Provider):
    name = "capture"

    def __init__(self):
        self.system = self.user = None

    def generate(self, system, user, request):
        self.system, self.user = system, user
        return json.dumps({"criteria": [{"id": 1, "points": 1}], "summary": "ok"})


def test_expected_answer_and_model_answer_reach_the_prompt():
    prompt = build_user_prompt("U", CRITERIA, "hloroplastos", model_answer="Atbilžu lapa: 1) hloroplastos")
    assert "correct answer: Hloroplastos." in prompt
    assert "Atbilžu lapa: 1) hloroplastos" in prompt
    assert prompt.index("Atbilžu lapa") < prompt.index("<student_work>")


def test_feedback_language_in_system_prompt():
    assert "ENGLISH" in system_prompt("en")
    assert "LATVIAN" in system_prompt("lv")
    p = Capture()
    grade_text("U", CRITERIA, "chloroplasts", provider=p, language="en")
    assert "ENGLISH" in p.system


def test_rules_against_invented_mistakes():
    s = system_prompt("lv")
    assert "quote" in s and "maximum points" in s and "new facts" in s


def test_old_database_gets_new_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE courses (id INTEGER PRIMARY KEY, name VARCHAR(200), description TEXT, "
                          "grade_scale_json TEXT, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE assignments (id INTEGER PRIMARY KEY, course_id INTEGER, title VARCHAR(300), "
                          "instructions TEXT, weight FLOAT, created_at DATETIME)"))
        conn.execute(text("INSERT INTO courses (id, name) VALUES (1, 'Vecais kurss')"))
        conn.execute(text("INSERT INTO assignments (id, course_id, title) VALUES (1, 1, 'Vecais uzdevums')"))
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("assignments")}
    assert {"model_answer", "language"} <= cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT language FROM assignments WHERE id=1")).scalar() == "lv"


def test_parse_student_list_from_csv():
    rows = parse_student_lines("kods;vārds\nS-001;Anna\nS-002, Jānis\nS-003\t\n\n")
    assert rows == [("S-001", "Anna"), ("S-002", "Jānis"), ("S-003", "")]


def _docx_bytes(text_: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text_)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_endpoint(client):
    r = client.post("/api/extract-text", files={"file": ("atbildes.docx", _docx_bytes("1. Hloroplastos"))})
    assert r.status_code == 200 and "Hloroplastos" in r.json()["text"]
    r = client.post("/api/extract-text", files={"file": ("bilde.png", b"123")})
    assert r.status_code == 400


def test_bulk_upload_matches_and_creates_students(client):
    course_id = client.post("/courses", data={"name": "Ievilkšanas kurss"},
                            follow_redirects=False).headers["location"].split("/")[2].split("?")[0]
    client.post(f"/courses/{course_id}/students", data={"lines": "S-001; Anna Bērziņa"})
    r = client.post(f"/courses/{course_id}/assignments", data={
        "title": "Tests", "crit_name": ["1. jautājums"], "crit_max": ["1"], "crit_expected": ["Hloroplastos"],
        "crit_id": [""], "crit_desc": [""], "crit_topic": [""], "language": "en",
    }, follow_redirects=False)
    assignment_id = r.headers["location"].split("/")[2].split("?")[0]
    a = client.get(f"/api/assignments/{assignment_id}").json()
    assert a["language"] == "en" and a["criteria"][0]["expected_answer"] == "Hloroplastos"

    files = [
        ("files", ("S-001_tests.docx", _docx_bytes("hloroplastos"))),
        ("files", ("Pēteris Ozols.txt", "kodolā".encode("utf-8"))),
        ("files", ("bilde.png", b"123")),
    ]
    r = client.post(f"/assignments/{assignment_id}/submissions/bulk", files=files)
    assert r.status_code == 200
    assert "Pievienoti darbi: 2; jauni skolēni: 1." in r.text and "bilde.png" in r.text
    course_page = client.get(f"/courses/{course_id}").text
    assert "Pēteris Ozols" in course_page and "S-002" in course_page

    page = client.get(f"/assignments/{assignment_id}").text
    assert "S-001 (Anna Bērziņa)" in page and "S-001_tests.docx" in page
