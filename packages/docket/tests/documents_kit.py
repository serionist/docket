"""Documents the tests build from scratch: OOXML packages written from XML strings, workbooks through openpyxl, pdfs
by hand and encrypted through pypdf. Every word in them is invented; nothing binary is committed."""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = f"{R}/officeDocument"
CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def package(members: dict[str, str | bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A zip of these members, in this order."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as z:
        for name, data in members.items():
            z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return out.getvalue()


def replaced(data: bytes, name: str, content: str | bytes) -> bytes:
    """A package with one member's content replaced, the rest as they were."""
    source = zipfile.ZipFile(io.BytesIO(data))
    members: dict[str, str | bytes] = {n: source.read(n) for n in source.namelist()}
    members[name] = content
    return package(members)


def streamed(members: dict[str, str | bytes], big: str, chunks: int, chunk: bytes) -> bytes:
    """A package whose member `big` is `chunk` written `chunks` times, deflated as it is written, so a member of tens of
    MiB never sits whole in memory; the others as `package` writes them."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
        info = zipfile.ZipInfo(big)
        info.compress_type = zipfile.ZIP_DEFLATED
        with z.open(info, "w") as f:
            for _ in range(chunks):
                f.write(chunk)
    return out.getvalue()


def patched(data: bytes, name: str, *, declared: int | None = None, compressed: int | None = None) -> bytes:
    """A package whose member `name` lies about its sizes, in its local header and in the central directory alike."""
    out = bytearray(data)
    # (signature, offset of the name length, offset of the name, offset of compress_size, offset of file_size)
    for sig, name_len_at, name_at, compressed_at, declared_at in ((b"PK\x03\x04", 26, 30, 18, 22),
                                                                  (b"PK\x01\x02", 28, 46, 20, 24)):  # fmt: skip
        at = out.find(sig)
        while at >= 0:
            length = struct.unpack_from("<H", out, at + name_len_at)[0]
            if bytes(out[at + name_at : at + name_at + length]) == name.encode():
                if declared is not None:
                    struct.pack_into("<I", out, at + declared_at, declared)
                if compressed is not None:
                    struct.pack_into("<I", out, at + compressed_at, compressed)
            at = out.find(sig, at + 4)
    return bytes(out)


def rels(*entries: tuple[str, str, str]) -> str:
    """A relationships part: (id, type, target) each; a type without a scheme is one of officeDocument's."""
    body = "".join(
        f'<Relationship Id="{i}" Type="{t if "://" in t else f"{R}/{t}"}" Target="{escape(target)}"/>'
        for i, t, target in entries
    )
    return f'{DECL}<Relationships xmlns="{PKG}">{body}</Relationships>'


def core(creator: str | None = None, modified_by: str | None = None) -> str:
    who = f"<dc:creator>{escape(creator)}</dc:creator>" if creator else ""
    last = f"<cp:lastModifiedBy>{escape(modified_by)}</cp:lastModifiedBy>" if modified_by else ""
    return (
        f'{DECL}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/">{who}{last}</cp:coreProperties>'
    )


# -- docx -----------------------------------------------------------------------------------------------------------


def wp(*runs: str) -> str:
    """A paragraph of plain runs."""
    return "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{escape(r)}</w:t></w:r>' for r in runs) + "</w:p>"


def wrow(*cells: str) -> str:
    return "<w:tr>" + "".join(f"<w:tc>{wp(c)}</w:tc>" for c in cells) + "</w:tr>"


def wroot(name: str, inner: str) -> str:
    return f'{DECL}<w:{name} xmlns:w="{W}" xmlns:r="{R}" xmlns:mc="{MC}">{inner}</w:{name}>'


def docx(
    body: str,
    *,
    headers: Sequence[str] = (),
    footnotes: str | None = None,
    endnotes: str | None = None,
    comments: str | None = None,
    creator: str | None = None,
    modified_by: str | None = None,
) -> bytes:
    """A docx whose body is `body` (w: markup); headers are whole header parts' inner markup."""
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": f'{DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml"), ("rId2", CORE_REL, "docProps/core.xml")),
        "docProps/core.xml": core(creator, modified_by),
        "word/document.xml": wroot("document", f"<w:body>{body}</w:body>"),
    }
    doc_rels: list[tuple[str, str, str]] = []
    for n, header in enumerate(headers, start=1):
        parts[f"word/header{n}.xml"] = wroot("hdr", header)
        doc_rels.append((f"rIdH{n}", "header", f"header{n}.xml"))
    for kind, inner in (("footnotes", footnotes), ("endnotes", endnotes), ("comments", comments)):
        if inner is not None:
            parts[f"word/{kind}.xml"] = wroot(kind, inner)
            doc_rels.append((f"rId{kind}", kind, f"{kind}.xml"))
    parts["word/_rels/document.xml.rels"] = rels(*doc_rels)
    return package(parts)


# -- pptx -----------------------------------------------------------------------------------------------------------


def sp(*paragraphs: str, placeholder: str | None = None) -> str:
    """A shape with a text body, one a:p per paragraph; `placeholder` sets its p:ph type."""
    ph = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
    paras = "".join(f"<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>" for t in paragraphs)
    return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{ph}</p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>'


def table(*rows: Sequence[str]) -> str:
    """A graphic frame holding a table."""
    body = "".join(
        "<a:tr>"
        + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{escape(c)}</a:t></a:r></a:p></a:txBody></a:tc>" for c in row)
        + "</a:tr>"
        for row in rows
    )
    return (
        f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )


def proot(name: str, inner: str, extra: str = "") -> str:
    return f'{DECL}<p:{name} xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:mc="{MC}"{extra}>{inner}</p:{name}>'


@dataclass(frozen=True)
class Slide:
    shapes: str
    notes: str | None = None
    hidden: bool = False


def pptx(slides: Sequence[Slide], *, creator: str | None = None, order: Sequence[int] | None = None) -> bytes:
    """A deck of these slides; `order` lists the slides' places in the deck when it isn't their file order."""
    parts: dict[str, str | bytes] = {
        "_rels/.rels": rels(("rId1", DOC_REL, "ppt/presentation.xml"), ("rId2", CORE_REL, "docProps/core.xml")),
        "docProps/core.xml": core(creator),
    }
    pres_rels: list[tuple[str, str, str]] = []
    for n, slide in enumerate(slides, start=1):
        hidden = ' show="0"' if slide.hidden else ""
        parts[f"ppt/slides/slide{n}.xml"] = proot(
            "sld", f"<p:cSld><p:spTree>{slide.shapes}</p:spTree></p:cSld>", hidden
        )
        pres_rels.append((f"rIdS{n}", "slide", f"slides/slide{n}.xml"))
        if slide.notes is not None:
            notes = sp("", placeholder="sldImg") + sp(slide.notes, placeholder="body") + sp("7", placeholder="sldNum")
            parts[f"ppt/notesSlides/notesSlide{n}.xml"] = proot(
                "notes", f"<p:cSld><p:spTree>{notes}</p:spTree></p:cSld>"
            )
            parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = rels(
                ("rIdN", "notesSlide", f"../notesSlides/notesSlide{n}.xml")
            )
    ids = "".join(f'<p:sldId id="{255 + k}" r:id="rIdS{k}"/>' for k in (order or range(1, len(slides) + 1)))
    parts["ppt/presentation.xml"] = proot("presentation", f"<p:sldIdLst>{ids}</p:sldIdLst>")
    parts["ppt/_rels/presentation.xml.rels"] = rels(*pres_rels)
    return package(parts)


# -- xlsx -----------------------------------------------------------------------------------------------------------


def xlsx(build: Callable[[Workbook], None], *, creator: str | None = None) -> bytes:
    """A workbook openpyxl writes, after `build` fills its first sheet and adds any others."""
    book = Workbook()
    if creator:
        book.properties.creator = creator
    build(book)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def sheet_xml(rows: str, *, dimension: str = "A1") -> str:
    """A worksheet part of `rows` (sheetData's inner markup)."""
    return (
        f'{DECL}<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetData>{rows}</sheetData></worksheet>'
    )


# -- pdf ------------------------------------------------------------------------------------------------------------


def _pdf_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """A pdf whose pages hold these lines in Helvetica; a page with no lines has no text layer."""
    objects: list[str] = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    font = 3
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids: list[str] = []
    for lines in pages:
        ops = "".join(f"{_pdf_string(line)} Tj T* " for line in lines)
        stream = f"BT /F1 12 Tf 14 TL 72 720 Td {ops}ET" if lines else ""
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font} 0 R >> >> "
            f"/Contents {content} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def encrypted(data: bytes, user_password: str, owner_password: str = "owner-only") -> bytes:  # noqa: S107
    """The pdf encrypted with AES-256: an empty user password opens it, any other doesn't."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
