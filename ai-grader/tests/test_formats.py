import io
import tarfile
import zipfile
from email.message import EmailMessage

import pytest
from docx import Document

from app.services import files
from app.services.files import FileError, expand_upload, extract_text


def docx(text: str) -> bytes:
    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_eml(sender: str, body: str, attachments: dict[str, bytes] | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "skolotajs@skola.lv", "Mājasdarbs"
    msg.set_content(body)
    for name, data in (attachments or {}).items():
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


@pytest.fixture
def assignment(client):
    loc = client.post("/courses", data={"name": "Formātu kurss"}, follow_redirects=False).headers["location"]
    course_id = loc.split("/")[2].split("?")[0]
    client.post(f"/courses/{course_id}/students", data={"lines": "S-001; Anna Bērziņa"})
    loc = client.post(f"/courses/{course_id}/assignments", data={
        "title": "Eseja", "crit_name": ["Saturs"], "crit_max": ["3"], "crit_id": [""], "crit_desc": ["x"],
        "crit_expected": [""], "crit_topic": [""],
    }, follow_redirects=False).headers["location"]
    return course_id, loc.split("/")[2].split("?")[0]


def upload(client, assignment_id, items, paths=None, auto=False):
    data = {}
    if paths:
        data["paths"] = paths
    if auto:
        data["auto_grade"] = "1"
    return client.post(f"/assignments/{assignment_id}/submissions/bulk",
                       files=[("files", (name, content)) for name, content in items], data=data)


def submissions(client, assignment_id):
    return client.get(f"/assignments/{assignment_id}").text


def test_odt_rtf_html_text():
    odt = make_zip({"content.xml": (
        '<?xml version="1.0"?><office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
        '<text:h>Virsraksts</text:h><text:p>Fotosintēze ir process.</text:p></office:text></office:body>'
        '</office:document-content>').encode()})
    assert extract_text("eseja.odt", odt) == "Virsraksts\nFotosintēze ir process."
    rtf = rb"{\rtf1\ansi\ansicpg1257 Fotosint\'e7ze ir process.\par Otra rinda}"
    assert extract_text("eseja.rtf", rtf) == "Fotosintēze ir process.\nOtra rinda"
    page = "<html><style>x{}</style><body><h1>Eseja</h1><p>Augi &amp; gaisma</p></body></html>".encode()
    assert extract_text("eseja.html", page) == "Eseja\nAugi & gaisma"


def test_old_doc_gives_helpful_message():
    with pytest.raises(FileError, match=".docx"):
        extract_text("eseja.doc", b"\xd0\xcf\x11\xe0")


def test_zip_name_with_baltic_characters_from_windows():
    raw = "Bērziņa.docx".encode("cp775")
    info = zipfile.ZipInfo(raw.decode("cp437"))
    info.flag_bits = 0
    assert files._zip_name(info) == "Bērziņa.docx"


def test_zip_root_folder_junk_and_unsupported():
    data = make_zip({
        "Darbi/Anna Bērziņa/eseja.docx": docx("a"),
        "Darbi/S-002.txt": b"b",
        "Darbi/.DS_Store": b"x",
        "__MACOSX/Darbi/._eseja.docx": b"x",
        "Darbi/bilde.png": b"x",
    })
    entries, skipped = expand_upload("Darbi.zip", data)
    assert sorted(p for p, _ in entries) == ["Anna Bērziņa/eseja.docx", "S-002.txt"]
    assert skipped == ["Darbi/bilde.png"]


def test_zip_inside_zip_uses_archive_name_as_student():
    inner = make_zip({"eseja.txt": b"Jana darbs"})
    entries, _ = expand_upload("visi.zip", make_zip({"Jānis Kalniņš.zip": inner, "Līga.txt": b"x"}))
    assert sorted(p for p, _ in entries) == ["Jānis Kalniņš/eseja.txt", "Līga.txt"]


def test_tar_gz_and_7z():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"darbs"
        info = tarfile.TarInfo("Anna/eseja.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    entries, _ = expand_upload("darbi.tar.gz", buf.getvalue())
    assert entries == [("Anna/eseja.txt", b"darbs")]

    py7zr = pytest.importorskip("py7zr")
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        z.writestr("Bērziņa darbs".encode(), "Anna Bērziņa/eseja.txt")
    entries, _ = expand_upload("darbi.7z", buf.getvalue())
    assert entries == [("Anna Bērziņa/eseja.txt", "Bērziņa darbs".encode())]


def test_broken_archive_message():
    with pytest.raises(FileError):
        expand_upload("bojats.zip", b"not a zip")


def test_eml_attachment_and_sender():
    eml = make_eml('"Pēteris Ozols" <p.ozols@skola.lv>', "Labdien, pielikumā mans darbs.",
                   {"eseja.docx": docx("Pētera eseja"), "paraksts.png": b"x"})
    entries, skipped = expand_upload("Mājasdarbs.eml", eml)
    assert [p for p, _ in entries] == ["Pēteris Ozols/eseja.docx"]
    assert skipped == ["paraksts.png"]


def test_eml_without_attachments_uses_body_and_address():
    entries, _ = expand_upload("x.eml", make_eml("marta.liepa@skola.lv", "Fotosintēze ir process."))
    assert entries == [("Marta Liepa/e-pasts.txt", "Fotosintēze ir process.".encode())]


def test_msg_outlook(monkeypatch):
    extract_msg = pytest.importorskip("extract_msg")

    class Att:
        longFilename, data = "eseja.txt", b"Outlook darbs"

    class Msg:
        sender, body, attachments = "Ilze Kļava <ilze@skola.lv>", "", [Att()]

        def close(self):
            pass

    monkeypatch.setattr(extract_msg, "openMsg", lambda data: Msg())
    entries, _ = expand_upload("darbs.msg", b"...")
    assert entries == [("Ilze Kļava/eseja.txt", b"Outlook darbs")]


def test_common_root_rules():
    strip = files.strip_common_root
    assert strip(["Darbi/Anna/e.docx", "Darbi/Juris/x.docx"]) == ["Anna/e.docx", "Juris/x.docx"]
    assert strip(["Mājasdarbs 3/a.docx", "Mājasdarbs 3/b.docx"]) == ["a.docx", "b.docx"]
    assert strip(["9b/a.docx", "9b/b.docx", "9b/c.docx"]) == ["a.docx", "b.docx", "c.docx"]
    assert strip(["Anna Bērziņa/eseja.docx", "Anna Bērziņa/pielikums.txt"]) == \
        ["Anna Bērziņa/eseja.docx", "Anna Bērziņa/pielikums.txt"]
    assert strip(["a.docx", "Anna/b.docx"]) == ["a.docx", "Anna/b.docx"]


def test_whole_class_folder_dropped(client, assignment):
    _, assignment_id = assignment
    r = upload(client, assignment_id, [("S-001.txt", b"a"), ("Dace Ābele.txt", b"b"), ("Edgars Ozols.txt", b"c")],
               paths=["7.b klase/S-001.txt", "7.b klase/Dace Ābele.txt", "7.b klase/Edgars Ozols.txt"])
    assert "Pievienoti darbi: 3; jauni skolēni: 2." in r.text


def test_folder_drop_groups_files_per_student(client, assignment):
    course_id, assignment_id = assignment
    r = upload(client, assignment_id,
               [("eseja.docx", docx("Annas eseja")), ("pielikums.txt", b"Annas pielikums"),
                ("darbs.txt", b"Jura darbs")],
               paths=["Anna Bērziņa/eseja.docx", "Anna Bērziņa/pielikums.txt", "Juris Ozols/darbs.txt"])
    assert "Pievienoti darbi: 2; jauni skolēni: 1." in r.text
    page = submissions(client, assignment_id)
    assert "S-001 (Anna Bērziņa)" in page and "Juris Ozols" in page


def test_moodle_export_zip(client, assignment):
    _, assignment_id = assignment
    data = make_zip({
        "Anna Bērziņa_481516_assignsubmission_file_/eseja.docx": docx("a"),
        "Kārlis Zariņš_234234_assignsubmission_file_/darbs.pdf.txt": b"b",
    })
    r = upload(client, assignment_id, [("Eseja-12345.zip", data)])
    assert "Pievienoti darbi: 2; jauni skolēni: 1." in r.text
    page = submissions(client, assignment_id)
    assert "S-001 (Anna Bērziņa)" in page and "(Kārlis Zariņš)" in page


def test_single_student_archive_vs_attachments_zip(client, assignment):
    course_id, assignment_id = assignment
    upload(client, assignment_id, [("Marta Liepa.zip", make_zip({"eseja.txt": b"m"}))])
    upload(client, assignment_id, [("attachments.zip", make_zip({"Anna Bērziņa.txt": b"a"}))])
    course = client.get(f"/courses/{course_id}").text
    assert "Marta Liepa" in course and "attachments" not in course


def test_emails_dropped_directly(client, assignment):
    course_id, assignment_id = assignment
    r = upload(client, assignment_id, [
        ("1.eml", make_eml("Anna Bērziņa <anna@skola.lv>", "sk. pielikumu", {"eseja.docx": docx("a")})),
        ("2.eml", make_eml("Roberts Lācis <r@skola.lv>", "Mans darbs ir šeit e-pastā.")),
    ])
    assert "Pievienoti darbi: 2; jauni skolēni: 1." in r.text
    assert "Roberts Lācis" in client.get(f"/courses/{course_id}").text


def test_auto_grade_after_upload(client, assignment):
    _, assignment_id = assignment
    r = upload(client, assignment_id, [("S-001_eseja.txt", b"Fotosinteze")], auto=True)
    assert r.status_code == 200
    page = submissions(client, assignment_id)
    assert 'badge ai_graded' in page and 'badge submitted' not in page
    progress = client.get(f"/assignments/{assignment_id}/progress").json()
    assert progress["running"] is False and progress["done"] == progress["total"] == 1
    assert progress["percent"] == 100 and progress["rows"][0]["status"] == "ai_graded"
