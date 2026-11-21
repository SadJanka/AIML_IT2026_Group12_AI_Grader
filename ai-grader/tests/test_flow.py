import re


def _ids(html: str, prefix: str) -> list[int]:
    return sorted({int(x) for x in re.findall(rf'href="/{prefix}/(\d+)"', html)})


def test_full_flow(client):
    home = client.get("/")
    assert home.status_code == 200 and "Dabaszinības" in home.text
    course_id = _ids(home.text, "courses")[0]
    course = client.get(f"/courses/{course_id}").text
    assignment_id = _ids(course, "assignments")[0]

    r = client.post(f"/assignments/{assignment_id}/grade-all", follow_redirects=True)
    assert r.status_code == 200
    page = client.get(f"/assignments/{assignment_id}").text
    assert page.count("Gaida apstiprinājumu") == 4
    assert "manipulācija?" in page

    sub_ids = _ids(page, "submissions")
    sub = client.get(f"/api/submissions/{sub_ids[0]}").json()
    assert sub["status"] == "ai_graded" and sub["final_total"] is None

    form = {"summary": "Labs darbs!", "action": "approve"}
    for c in sub["criteria"]:
        form[f"points_{c['criterion_id']}"] = str(c["ai_points"])
    first = sub["criteria"][0]
    form[f"points_{first['criterion_id']}"] = str(first["max_points"])
    form[f"comment_{first['criterion_id']}"] = "Ļoti precīzi!"
    assert client.post(f"/submissions/{sub_ids[0]}/review", data=form).status_code == 200

    approved = client.get(f"/api/submissions/{sub_ids[0]}").json()
    assert approved["status"] == "approved"
    assert approved["criteria"][0]["final_points"] == first["max_points"]

    fb = client.get(approved["feedback_url"])
    assert fb.status_code == 200 and "Ļoti precīzi!" in fb.text and "Labs darbs!" in fb.text
    assert "Modelis:" not in fb.text

    results = client.get(f"/api/courses/{course_id}/results").json()
    graded = [r for r in results if r["final_percent"] is not None]
    assert len(graded) == 1 and graded[0]["final_grade"]
    csv = client.get(f"/courses/{course_id}/results.csv")
    assert csv.status_code == 200 and "Gala vērtējums" in csv.text
    stats = client.get(f"/courses/{course_id}/stats").text
    assert "apstiprināti darbi" in stats


def test_unapproved_feedback_is_hidden(client):
    r = client.post("/api/assignments/1/submissions", json={"student_code": "S-099", "text": "Fotosintēze ir process."})
    assert r.status_code == 201
    sub = r.json()
    fb = client.get(sub["feedback_url"]).text
    assert "vēl tiek vērtēts" in fb


def test_api_grade_and_approve(client):
    sub = client.post("/api/assignments/1/submissions",
                      json={"student_code": "S-100", "text": "Augi uzņem ogļskābo gāzi un ūdeni."}).json()
    graded = client.post(f"/api/submissions/{sub['id']}/grade").json()
    assert graded["status"] == "ai_graded" and graded["ai_total"] is not None
    approved = client.post(f"/api/submissions/{sub['id']}/approve").json()
    assert approved["status"] == "approved"
    assert client.post(f"/api/submissions/{sub['id']}/grade").status_code == 409


def test_create_assignment_and_delete_student(client):
    r = client.post("/courses", data={"name": "Testa kurss"}, follow_redirects=False)
    course_id = int(r.headers["location"].split("/")[2].split("?")[0])
    client.post(f"/courses/{course_id}/students", data={"lines": "T-1; Testa Skolēns\nT-2"})
    r = client.post(f"/courses/{course_id}/assignments", data={
        "title": "Eseja", "instructions": "Uzraksti", "weight": "2",
        "crit_id": ["", ""], "crit_name": ["Saturs", "Valoda"], "crit_desc": ["a", "b"],
        "crit_max": ["4", "1,5"], "crit_topic": ["T1", "T2"],
    }, follow_redirects=False)
    assignment_id = int(r.headers["location"].split("/")[2].split("?")[0])
    a = client.get(f"/api/assignments/{assignment_id}").json()
    assert a["max_points"] == 5.5 and a["weight"] == 2

    page = client.get(f"/courses/{course_id}").text
    student_id = re.search(r'action="/students/(\d+)/delete"', page).group(1)
    client.post(f"/students/{student_id}/delete")
    assert "T-1" not in client.get(f"/courses/{course_id}").text
