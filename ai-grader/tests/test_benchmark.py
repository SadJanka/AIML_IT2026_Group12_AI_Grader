import re

from app.benchmark import STUDENTS, build_dataset, file_bytes, write_dataset
from app.services.files import expand_upload, extract_text


def test_dataset_is_deterministic_and_complete():
    a, b = build_dataset(), build_dataset()
    assert [w.text for t in a for w in t.works] == [w.text for t in b for w in t.works]
    assert [len(t.works) for t in a] == [25, 25, 25]
    for task in a:
        names = {c["name"] for c in task.criteria}
        for w in task.works:
            assert set(w.expected) == names
            for c in task.criteria:
                assert 0 <= w.expected[c["name"]] <= c["max_points"]
    tags = {tag for t in a for w in t.works for tag in w.tags}
    assert {"manipulation", "no diacritics", "off topic", "minutes not converted"} <= tags


def test_every_generated_file_can_be_read_back():
    for task in build_dataset():
        for i, w in enumerate(task.works):
            data = file_bytes(w, i)
            entries, _ = expand_upload(w.filename, data)
            text = "\n".join(extract_text(p, d) for p, d in entries)
            first_line = w.text.strip().splitlines()[0][:20]
            assert first_line.split()[0] in text, (w.filename, text[:80])


def test_full_benchmark_flow(client, tmp_path):
    tasks = write_dataset(tmp_path)
    assert (tmp_path / "expected-points.csv").exists() and (tmp_path / "1 - Tests (viegls).zip").exists()

    r = client.post("/benchmark", follow_redirects=False)
    course_id = r.headers["location"].split("/")[2].split("?")[0]
    page = client.get(f"/courses/{course_id}").text
    assignment_ids = re.findall(r'href="/assignments/(\d+)"', page)
    assert len(assignment_ids) == 3

    folder = tmp_path / tasks[0].folder
    files = [("files", (p.name, p.read_bytes())) for p in sorted(folder.iterdir())]
    paths = [f"{tasks[0].folder}/{p.name}" for p in sorted(folder.iterdir())]
    r = client.post(f"/assignments/{assignment_ids[0]}/submissions/bulk", files=files,
                    data={"paths": paths, "auto_grade": "1"})
    assert "Pievienoti darbi: 25; jauni skolēni: 25." in r.text
    zip_bytes = (tmp_path / f"{tasks[1].folder}.zip").read_bytes()
    r = client.post(f"/assignments/{assignment_ids[1]}/submissions/bulk",
                    files=[("files", (f"{tasks[1].folder}.zip", zip_bytes))], data={"auto_grade": "1"})
    assert "Pievienoti darbi: 25; jauni skolēni: 0." in r.text

    acc = client.get(f"/courses/{course_id}/accuracy")
    assert acc.status_code == 200
    assert "Lielākās atšķirības" in acc.text
    assert acc.text.count("<td><b>25</b>") == 2
    assert acc.text.count("Vēl nav novērtētu darbu") == 1
    course = client.get(f"/courses/{course_id}").text
    assert all(name in course for name in STUDENTS)
