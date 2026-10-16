"""Office Open XML packages (docx, pptx, xlsx) opened under the caps: every part's real size counted before any is
read, every part we parse refused with a DTD, and parts found through the package's relationships, never by fixed names
(DR-0484, DR-0485).

A part's declared sizes can lie, and `zipfile` inflates a whole part before it cuts it to what it declared; bzip2 and
lzma inflate a whole chunk at once. So only stored and deflated parts are read, and each one's deflate stream is
inflated once, a MiB at a time from its raw bytes, counting what it really holds against the caps.
"""

from __future__ import annotations

import io
import itertools
import posixpath
import struct
import zipfile
import zlib
from dataclasses import dataclass
from urllib.parse import unquote
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring

from docket.documents.limits import MIB, Limits, NotReadError, mib

# An OLE compound file: an Office file under a password or a sensitivity label, or one of the old binary formats.
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PACKAGE_RELS = "_rels/.rels"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
DC = "{http://purl.org/dc/elements/1.1/}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
UNSAFE_XML = "its XML declares a DTD or entities"
LOCAL_HEADER = b"PK\x03\x04"
METHODS = {zipfile.ZIP_BZIP2: "bzip2", zipfile.ZIP_LZMA: "lzma"}


def local(tag: str) -> str:
    """An element's name without its namespace."""
    return tag.rpartition("}")[2]


def namespace(tag: str) -> str:
    return tag[1:].partition("}")[0] if tag.startswith("{") else ""


def damaged(error: BaseException) -> NotReadError:
    """A file that didn't read, named by the error's type only: its message can quote the file."""
    return NotReadError("damaged", f"damaged ({type(error).__name__})")


@dataclass(frozen=True)
class Rel:
    id: str
    type: str
    target: str


@dataclass(frozen=True)
class Span:
    """Where a part's raw bytes are in the package, after its local header."""

    info: zipfile.ZipInfo
    begin: int
    end: int


def span(data: bytes, info: zipfile.ZipInfo) -> Span:
    start = info.header_offset
    if len(data) < start + 30 or data[start : start + 4] != LOCAL_HEADER:
        raise NotReadError("damaged", "damaged (a part's header is missing)")
    name_length, extra_length = struct.unpack_from("<HH", data, start + 26)
    begin = start + 30 + name_length + extra_length
    if begin + info.compress_size > len(data):
        raise NotReadError("damaged", "damaged (a part is cut short)")
    return Span(info, begin, begin + info.compress_size)


def check(data: bytes, infos: list[zipfile.ZipInfo], limits: Limits) -> None:
    """The package's parts counted before any is read: how many, how they are packed, where they are, and what each
    really holds, not what it declares. Parts that share or overlap their bytes are refused, as zipfile refuses them
    on a read, and what is left of the package's cap caps the next part, so many entries over one stream stop early."""
    if len(infos) > limits.zip_members:
        raise NotReadError("zip", f"{len(infos):,} parts in its package, over the cap of {limits.zip_members:,}")
    if any(info.flag_bits & 0x1 for info in infos):
        raise NotReadError("protected", "protected, or in an old binary format")
    for info in infos:
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            method = METHODS.get(info.compress_type, f"method {info.compress_type}")
            raise NotReadError("zip", f"a part in its package packed with {method}, which Docket doesn't read")
    spans = sorted((span(data, info) for info in infos), key=lambda s: s.info.header_offset)
    for before, after in itertools.pairwise(spans):
        if after.info.header_offset < before.end:
            raise NotReadError("damaged", "damaged (its parts overlap)")
    total = 0
    for part in spans:
        info = part.info
        size = max(info.file_size, real_size(data, part, min(limits.member_bytes, limits.package_bytes - total)))
        if size > limits.member_bytes:
            raise NotReadError(
                "zip", f"a part of {mib(size)} in its package, over the cap of {mib(limits.member_bytes)}"
            )
        if size > limits.ratio_from and size > limits.ratio * max(info.compress_size, 1):
            ratio = size // max(info.compress_size, 1)
            raise NotReadError(
                "zip", f"a part compressed {ratio:,} to 1 in its package, over the cap of {limits.ratio:,} to 1"
            )
        total += size
        if total > limits.package_bytes:
            raise NotReadError(
                "zip", f"{mib(total)} of parts in its package, over the cap of {mib(limits.package_bytes)}"
            )


def real_size(data: bytes, part: Span, cap: int) -> int:
    """What a part really holds: a stored part's length, or its deflate stream inflated a MiB at a time, counting
    stopped once past `cap`."""
    raw = data[part.begin : part.end]
    if part.info.compress_type == zipfile.ZIP_STORED:
        return len(raw)
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    count = 0
    pending = raw
    try:
        while pending and not inflater.eof:
            count += len(inflater.decompress(pending, MIB))
            if count > cap:
                return count
            pending = inflater.unconsumed_tail
        return count + len(inflater.flush())
    except zlib.error as e:
        raise damaged(e) from None


def rels_name(part: str) -> str:
    """Where a part's relationships are: `word/document.xml` has `word/_rels/document.xml.rels`."""
    folder, _, name = part.rpartition("/")
    return f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels"


def resolve(source: str, target: str) -> str:
    """A relationship's target as a part's name: absolute from the package's root, else from the source's folder."""
    target = unquote(target)
    if target.startswith("/"):
        return posixpath.normpath(target[1:])
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


class Package:
    """A docx, pptx or xlsx package, its sizes checked; every read can raise `NotReadError`."""

    def __init__(self, data: bytes, limits: Limits) -> None:
        if data.startswith(OLE):
            raise NotReadError("protected", "protected, or in an old binary format")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError) as e:
            raise damaged(e) from None
        infos = self.zip.infolist()
        check(data, infos, limits)
        self.names = frozenset(info.filename for info in infos)

    def read(self, name: str) -> bytes:
        if name not in self.names:
            raise NotReadError("damaged", "damaged (a part it names is missing)")
        try:
            return self.zip.read(name)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
            raise damaged(e) from None

    def xml(self, name: str) -> Element:
        """A part parsed with no DTD allowed: defusedxml's refusal goes up, and `formats.extract` names it."""
        try:
            return fromstring(self.read(name), forbid_dtd=True)
        except ParseError as e:
            raise damaged(e) from None

    def rels(self, part: str) -> list[Rel]:
        """A part's relationships to other parts in the package, in their file's order; none when it has no file."""
        name = rels_name(part) if part else PACKAGE_RELS
        if name not in self.names:
            return []
        out: list[Rel] = []
        for rel in self.xml(name).iter(REL):
            if rel.get("TargetMode") == "External":
                continue
            out.append(Rel(rel.get("Id", ""), rel.get("Type", ""), resolve(part, rel.get("Target", ""))))
        return out

    def related(self, part: str, kind: str) -> list[str]:
        """The parts a part points at by a relationship whose type ends in `/<kind>` and that the package holds."""
        return [r.target for r in self.rels(part) if r.type.endswith(f"/{kind}") and r.target in self.names]

    def main(self) -> str:
        found = self.related("", "officeDocument")
        if not found:
            raise NotReadError("damaged", "damaged (no main part)")
        return found[0]

    def core(self) -> tuple[str | None, str | None]:
        """The core properties' creator and the last to modify it, as the people the file names."""
        found = self.related("", "core-properties")
        if not found:
            return None, None
        root = self.xml(found[0])
        return _value(root.find(f"{DC}creator")), _value(root.find(f"{CP}lastModifiedBy"))


def _value(element: Element | None) -> str | None:
    if element is None or not element.text or not element.text.strip():
        return None
    return " ".join(element.text.split())
