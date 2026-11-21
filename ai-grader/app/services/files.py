import email
import email.policy
import email.utils
import html
import io
import os
import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree

from ..i18n import N_

MAX_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 300
MAX_DEPTH = 2

DOCUMENT_EXTENSIONS = (".docx", ".odt", ".rtf", ".pdf", ".txt", ".md", ".html", ".htm")
ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
EMAIL_EXTENSIONS = (".eml", ".msg")
UPLOAD_EXTENSIONS = DOCUMENT_EXTENSIONS + ARCHIVE_EXTENSIONS + EMAIL_EXTENSIONS
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS

_JUNK = re.compile(r"(^|/)(__MACOSX/|\.|~\$|thumbs\.db$|desktop\.ini$)", re.IGNORECASE)


class FileError(ValueError):
    pass


def is_junk(path: str) -> bool:
    return bool(_JUNK.search(path.replace("\\", "/")))


def is_document(path: str) -> bool:
    return path.lower().endswith(DOCUMENT_EXTENSIONS)


def is_archive(path: str) -> bool:
    return path.lower().endswith(ARCHIVE_EXTENSIONS)


def is_email(path: str) -> bool:
    return path.lower().endswith(EMAIL_EXTENSIONS)


def is_container(path: str) -> bool:
    return is_archive(path) or is_email(path)


def stem(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    for ext in sorted(UPLOAD_EXTENSIONS, key=len, reverse=True):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return PurePosixPath(name).stem


class _TextFromHTML(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts)
        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def html_to_text(markup: str) -> str:
    parser = _TextFromHTML()
    parser.feed(markup)
    return parser.text()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1257", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FileError(N_("Could not read the text file."))


def _odt_text(data: bytes) -> str:
    ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("content.xml"))
    paragraphs = []
    for el in root.iter():
        if el.tag in (ns + "p", ns + "h"):
            paragraphs.append("".join(el.itertext()))
    return "\n".join(p for p in paragraphs if p.strip())


def _rtf_text(data: bytes) -> str:
    from striprtf.striprtf import rtf_to_text
    raw = data.decode("latin-1")
    m = re.search(r"\\ansicpg(\d+)", raw)
    encoding = f"cp{m.group(1)}" if m else "cp1257"
    try:
        return rtf_to_text(raw, encoding=encoding, errors="replace")
    except LookupError:
        return rtf_to_text(raw, encoding="cp1257", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise FileError(N_("The file is too large (max 5 MB)."))
    name = (filename or "").lower()

    if name.endswith((".txt", ".md")):
        return _decode(data).strip()

    if name.endswith((".html", ".htm")):
        return html_to_text(_decode(data))

    if name.endswith(".docx"):
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
        except Exception as e:
            raise FileError(N_("Could not open the .docx file.")) from e
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(p for p in parts if p.strip()).strip()

    if name.endswith(".odt"):
        try:
            return _odt_text(data).strip()
        except Exception as e:
            raise FileError(N_("Could not open the .odt file.")) from e

    if name.endswith(".rtf"):
        try:
            return _rtf_text(data).strip()
        except ImportError as e:
            raise FileError(N_("RTF support needs the striprtf library (run run.bat again).")) from e

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as e:
            raise FileError(N_("Could not open the .pdf file.")) from e
        if not text:
            raise FileError(N_("The PDF contains no text (it may be a scanned image)."))
        return text

    if name.endswith(".doc"):
        raise FileError(N_("The old Word .doc format is not supported — open it in Word and save it as .docx or .pdf."))

    raise FileError(N_("Unsupported file type."))


@dataclass
class _Budget:
    files: int = 0
    bytes: int = 0

    def allows(self, size: int, container: bool = False) -> bool:
        per_file = MAX_TOTAL_BYTES if container else MAX_BYTES
        return size <= per_file and self.files < MAX_FILES and self.bytes + size <= MAX_TOTAL_BYTES

    def take(self, size: int) -> None:
        self.files += 1
        self.bytes += size


def _wanted(path: str) -> bool:
    return is_document(path) or is_container(path)


def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp775", "cp1257"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _read_zip(data, budget, skipped):
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                name = _zip_name(info).replace("\\", "/")
                if info.is_dir() or is_junk(name):
                    continue
                if not _wanted(name) or not budget.allows(info.file_size, is_container(name)):
                    skipped.append(name)
                    continue
                budget.take(info.file_size)
                out.append((name, z.read(info)))
    except (zipfile.BadZipFile, RuntimeError) as e:
        raise FileError(N_("Could not open the .zip file.")) from e
    return out


def _read_tar(data, budget, skipped):
    out = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            for m in t.getmembers():
                if not m.isfile() or is_junk(m.name):
                    continue
                if not _wanted(m.name) or not budget.allows(m.size, is_container(m.name)):
                    skipped.append(m.name)
                    continue
                budget.take(m.size)
                out.append((m.name, t.extractfile(m).read()))
    except tarfile.TarError as e:
        raise FileError(N_("Could not open the .tar archive.")) from e
    return out


def _read_7z(data, budget, skipped):
    try:
        import py7zr
        from py7zr.io import BytesIOFactory
    except ImportError as e:
        raise FileError(N_("7z support needs the py7zr library (run run.bat again).")) from e
    out = []
    try:
        with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as z:
            targets = []
            for info in z.list():
                name = info.filename.replace("\\", "/")
                if info.is_directory or is_junk(name):
                    continue
                size = info.uncompressed or 0
                if not _wanted(name) or not budget.allows(size, is_container(name)):
                    skipped.append(name)
                    continue
                budget.take(size)
                targets.append(info.filename)
            if targets:
                factory = BytesIOFactory(limit=MAX_BYTES)
                z.extract(targets=targets, factory=factory)
                for name in targets:
                    product = factory.products.get(name)
                    if product is not None:
                        product.seek(0)
                        out.append((name.replace("\\", "/"), product.read()))
    except FileError:
        raise
    except Exception as e:
        raise FileError(N_("Could not open the .7z file.")) from e
    return out


_RAR_READY = False


def _setup_rar_tools() -> None:
    global _RAR_READY
    if _RAR_READY:
        return
    import rarfile
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    unrar = shutil.which("unrar") or next((p for p in (os.path.join(d, "WinRAR", "UnRAR.exe") for d in pf)
                                           if os.path.exists(p)), None)
    sevenzip = shutil.which("7z") or next((p for p in (os.path.join(d, "7-Zip", "7z.exe") for d in pf)
                                           if os.path.exists(p)), None)
    bsdtar = shutil.which("bsdtar")
    if not bsdtar and os.name == "nt":
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
        bsdtar = candidate if os.path.exists(candidate) else None
    if unrar:
        rarfile.UNRAR_TOOL = unrar
    if sevenzip:
        rarfile.SEVENZIP_TOOL = sevenzip
    if bsdtar:
        rarfile.BSDTAR_TOOL = bsdtar
    try:
        rarfile.tool_setup(force=True)
    except rarfile.RarCannotExec:
        pass
    _RAR_READY = True


def _read_rar(data, budget, skipped):
    try:
        import rarfile
    except ImportError as e:
        raise FileError(N_("RAR support needs the rarfile library (run run.bat again).")) from e
    _setup_rar_tools()
    out = []
    try:
        with rarfile.RarFile(io.BytesIO(data)) as r:
            for info in r.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or info.is_symlink() or is_junk(name):
                    continue
                if not _wanted(name) or not budget.allows(info.file_size, is_container(name)):
                    skipped.append(name)
                    continue
                budget.take(info.file_size)
                out.append((name, r.read(info)))
    except rarfile.RarCannotExec as e:
        raise FileError(N_("Opening RAR needs WinRAR or 7-Zip on this computer. You can also unpack the RAR and drop the folder.")) from e
    except (rarfile.Error, OSError) as e:
        raise FileError(N_("Could not open the .rar file.")) from e
    return out


def _read_archive(filename, data, budget, skipped):
    name = filename.lower()
    if name.endswith(".zip"):
        return _read_zip(data, budget, skipped)
    if name.endswith(".rar"):
        return _read_rar(data, budget, skipped)
    if name.endswith(".7z"):
        return _read_7z(data, budget, skipped)
    return _read_tar(data, budget, skipped)


GENERIC_FOLDER = re.compile(
    r"attachment|pielikum|download|lejupiel|darb|files|faili|archive|arh[iī]v|submission|iesniegum|essay|esej|"
    r"mail|pasts|uzdevum|klase|class|homework|tests?\b|^\d+$", re.IGNORECASE)


def strip_common_root(paths: list[str]) -> list[str]:
    parts = [PurePosixPath(p).parts for p in paths]
    if not parts or not all(len(x) >= 2 for x in parts) or len({x[0] for x in parts}) != 1:
        return paths
    root = parts[0][0]
    deep = all(len(x) >= 3 for x in parts)
    direct_files = sum(1 for x in parts if len(x) == 2)
    if deep or GENERIC_FOLDER.search(root) or direct_files >= 3:
        return [str(PurePosixPath(*x[1:])) for x in parts]
    return paths


def _sender_name(raw_from: str) -> str:
    name, address = email.utils.parseaddr(raw_from or "")
    name = name.strip().strip('"')
    if not name and address:
        name = address.split("@")[0].replace(".", " ").replace("_", " ").title()
    return re.sub(r"[\\/]+", " ", name).strip() or "e-pasts"


def _read_eml(data):
    msg = email.message_from_bytes(data, policy=email.policy.default)
    sender = _sender_name(str(msg.get("From", "")))
    attachments = []
    for part in msg.iter_attachments():
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if filename and payload:
            attachments.append((filename, payload))
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        content = part.get_content()
        body = html_to_text(content) if part.get_content_type() == "text/html" else content
    return sender, attachments, body.strip()


def _read_msg(data):
    try:
        import extract_msg
    except ImportError as e:
        raise FileError(N_("Outlook .msg support needs the extract-msg library (run run.bat again).")) from e
    try:
        msg = extract_msg.openMsg(data)
        sender = _sender_name(msg.sender or "")
        attachments = []
        for att in getattr(msg, "attachments", []):
            filename = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None)
            payload = getattr(att, "data", None)
            if filename and isinstance(payload, (bytes, bytearray)):
                attachments.append((filename, bytes(payload)))
        body = (msg.body or "").strip()
        msg.close()
    except FileError:
        raise
    except Exception as e:
        raise FileError(N_("Could not open the Outlook .msg file.")) from e
    return sender, attachments, body


def _expand_email(filename, data, budget, skipped, depth):
    sender, attachments, body = _read_eml(data) if filename.lower().endswith(".eml") else _read_msg(data)
    out = []
    for att_name, att_data in attachments:
        if is_junk(att_name):
            continue
        if not _wanted(att_name) or not budget.allows(len(att_data), is_container(att_name)):
            skipped.append(att_name)
            continue
        if is_container(att_name):
            if depth >= MAX_DEPTH:
                skipped.append(att_name)
                continue
            inner, inner_skipped = _expand(att_name, att_data, budget, depth + 1)
            skipped += inner_skipped
            out += [(f"{sender}/{p}", d) for p, d in inner]
        else:
            budget.take(len(att_data))
            out.append((f"{sender}/{att_name}", att_data))
    if not out and body:
        out.append((f"{sender}/e-pasts.txt", body.encode("utf-8")))
    return out


def _expand(filename, data, budget, depth):
    skipped: list[str] = []
    if is_email(filename):
        return _expand_email(filename, data, budget, skipped, depth), skipped

    members = _read_archive(filename, data, budget, skipped)
    paths = strip_common_root([p for p, _ in members])
    out = []
    for path, (_, content) in zip(paths, members):
        if not is_container(path):
            out.append((path, content))
            continue
        if depth >= MAX_DEPTH:
            skipped.append(path)
            continue
        try:
            inner, inner_skipped = _expand(path, content, budget, depth + 1)
        except FileError:
            skipped.append(path)
            continue
        skipped += inner_skipped
        parent = str(PurePosixPath(path).parent)
        folder = parent if parent not in ("", ".") else stem(path)
        for p, d in inner:
            out.append((p if is_email(path) and parent in ("", ".") else f"{folder}/{p}", d))
    return out, skipped


def expand_upload(filename: str, data: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    if not is_container(filename):
        return [(filename, data)], []
    return _expand(filename, data, _Budget(), depth=0)
