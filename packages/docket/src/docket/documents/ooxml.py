"""Office Open XML packages (docx, pptx, xlsx) opened under the caps: the zip's sizes checked before any part is read,
every XML part parsed by defusedxml with no DTD, and parts found through the package's relationships, never by fixed
names (DR-0484, DR-0485)."""

from __future__ import annotations

import io
import posixpath
import zipfile
import zlib
from dataclasses import dataclass
from urllib.parse import unquote
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from docket.documents.limits import Limits, NotReadError, mib

# An OLE compound file: an Office file under a password or a sensitivity label, or one of the old binary formats.
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PACKAGE_RELS = "_rels/.rels"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
DC = "{http://purl.org/dc/elements/1.1/}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
UNSAFE_XML = "its XML declares a DTD or entities"


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


def check(infos: list[zipfile.ZipInfo], limits: Limits) -> None:
    """The package's declared sizes, before any part is read; `zipfile` then reads no more than a part declares."""
    if len(infos) > limits.zip_members:
        raise NotReadError("zip", f"{len(infos):,} parts in its package, over the cap of {limits.zip_members:,}")
    total = 0
    for info in infos:
        if info.file_size > limits.member_bytes:
            raise NotReadError(
                "zip", f"a part of {mib(info.file_size)} in its package, over the cap of {mib(limits.member_bytes)}"
            )
        if info.file_size > limits.ratio_from and info.file_size > limits.ratio * max(info.compress_size, 1):
            ratio = info.file_size // max(info.compress_size, 1)
            raise NotReadError(
                "zip", f"a part compressed {ratio:,} to 1 in its package, over the cap of {limits.ratio:,} to 1"
            )
        total += info.file_size
    if total > limits.package_bytes:
        raise NotReadError("zip", f"{mib(total)} of parts in its package, over the cap of {mib(limits.package_bytes)}")


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
        check(infos, limits)
        if any(info.flag_bits & 0x1 for info in infos):
            raise NotReadError("protected", "protected, or in an old binary format")
        self.names = frozenset(info.filename for info in infos)

    def read(self, name: str) -> bytes:
        if name not in self.names:
            raise NotReadError("damaged", "damaged (a part it names is missing)")
        try:
            return self.zip.read(name)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
            raise damaged(e) from None

    def xml(self, name: str) -> Element:
        try:
            return fromstring(self.read(name), forbid_dtd=True)
        except DefusedXmlException:
            raise NotReadError("xml", UNSAFE_XML) from None
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
