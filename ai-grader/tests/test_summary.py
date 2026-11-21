import io
import re

from openpyxl import load_workbook

from app.benchmark import build_dataset, file_bytes


def _graded_test_assignment(client):
    loc = client.post("/benchmark", follow_redirects=False).headers["location"]
    course_id = loc.split("/")[2].split("?")[0]
    assignment_id = re.findall(r'href="/assignments/(\d+)"', client.get(f"/courses/{course_id}").text)[0]
    task = build_dataset()[0]
    files = [("files", (w.filename, file_bytes(w, i))) for i, w in enumerate(task.works)]
    r = client.post(f"/assignments/{assignment_id}/submissions/bulk", files=files, data={"auto_grade": "1"})
    assert "Pievienoti darbi: 25" in r.text
    return course_id, assignment_id


def test_summary_approve_all_export_and_print(client):
    course_id, aid = _graded_test_assignment(client)

    page = client.get(f"/assignments/{aid}/summary").text
    assert "Ko klasei vajadzētu atkārtot" in page and "Rezultātu sadalījums" in page
    assert "Apstiprināt visus (23), izņemot atzīmētos" in page

    r = client.post(f"/assignments/{aid}/approve-all")
    assert "Apstiprināti darbi: 23. Vēl jāpārbauda pašam: 2." in r.text
    page = client.get(f"/assignments/{aid}/summary").text
    assert "Saites skolēniem (23)" in page and "/feedback/" in page

    x = client.get(f"/assignments/{aid}/export.xlsx")
    assert x.status_code == 200 and "spreadsheetml" in x.headers["content-type"]
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Kopsavilkums", "Vērtējumi", "Atgriezeniskā saite"]
    assert wb["Vērtējumi"].max_row == 26 and wb["Vērtējumi"]["A1"].value == "Skolēns"
    links = [c.value for c in wb["Vērtējumi"]["A"] if c.value]
    assert "Anna Bērziņa" in links

    printed = client.get(f"/assignments/{aid}/print").text
    assert printed.count('class="feedback print-page') == 23 and "window.print()" in printed

    cx = client.get(f"/courses/{course_id}/results.xlsx")
    wb = load_workbook(io.BytesIO(cx.content))
    assert wb.sheetnames == ["Rezultāti", "Vērtēšanas skala"] and wb["Rezultāti"].max_row == 26


def test_english_export_sheet_names(client):
    _, aid = _graded_test_assignment(client)
    client.cookies.set("lang", "en")
    try:
        wb = load_workbook(io.BytesIO(client.get(f"/assignments/{aid}/export.xlsx").content))
        assert wb.sheetnames == ["Summary", "Scores", "Feedback"]
    finally:
        client.cookies.clear()


def test_theme_toggle_is_on_every_page(client):
    for url in ["/", "/courses/1", "/assignments/1"]:
        html = client.get(url).text
        assert "data-theme-toggle" in html and 'localStorage.getItem("theme")' in html
