"""age through pyrage (DR-0549): a fresh X25519 identity per media file, the file encrypted to it alone while it is
read, so no plain copy is written; deleting the identity forgets the file.

A file's key id is the sha256 of its age header, the bytes through the `---` line that closes it with its MAC: unique
per file, the same wherever the file moves, and readable from the file itself. pyrage ships wheels for the systems
`sqlite-vec` does; elsewhere `available()` is false and media says so.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from docket.errors import DocketError

MAGIC = b"age-encryption.org/v1\n"
# An age header is a few short lines per recipient; one this long isn't a header Docket wrote.
MAX_HEADER = 4096
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
MISSING = "media needs pyrage, which has no build for this machine, so media is off here"


class Reader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def _pyrage() -> Any:
    try:
        return importlib.import_module("pyrage")
    except ImportError:  # pragma: no cover - every system CI runs on has a wheel
        raise DocketError(MISSING) from None


def available() -> bool:
    try:
        _pyrage()
    except DocketError:  # pragma: no cover - as above
        return False
    return True


def new_identity() -> tuple[str, str]:
    """A fresh identity, as its secret and its public recipient."""
    identity = _pyrage().x25519.Identity.generate()
    return str(identity), str(identity.to_public())


def recipient_of(identity: str) -> str:
    return str(_identity(identity).to_public())


def _identity(secret: str) -> Any:
    try:
        return _pyrage().x25519.Identity.from_str(secret)
    except Exception as e:
        raise DocketError(f"a media key that isn't an age identity ({type(e).__name__})") from None


def encrypt(reader: Reader, out: BinaryIO, recipient: str) -> None:
    """Encrypt what `reader` gives into `out`, to one recipient, as it is read. A reader that raises stops it; the
    caller deletes `out`."""
    age = _pyrage()
    try:
        age.encrypt_io(reader, out, [age.x25519.Recipient.from_str(recipient)])
    except age.EncryptError as e:
        raise DocketError(f"couldn't encrypt the media: {e}") from None


def decrypt(data: bytes, identity: str) -> bytes:
    age = _pyrage()
    try:
        return age.decrypt(data, [_identity(identity)])
    except age.DecryptError:
        raise DocketError("the media's key doesn't open it") from None


def seal(text: str, recipient: str) -> bytes:
    """A small secret, sealed to a recipient: a key file in the store."""
    age = _pyrage()
    return age.encrypt(text.encode("utf-8"), [age.x25519.Recipient.from_str(recipient)])


def unseal(data: bytes, identity: str) -> str:
    return decrypt(data, identity).decode("utf-8")


def seal_with_passphrase(text: str, passphrase: str) -> bytes:
    """A file sealed with a passphrase, by age's scrypt mode: a key export (DR-0550)."""
    return _pyrage().passphrase.encrypt(text.encode("utf-8"), passphrase)


def open_with_passphrase(data: bytes, passphrase: str) -> str:
    age = _pyrage()
    try:
        return age.passphrase.decrypt(data, passphrase).decode("utf-8")
    except Exception:
        raise DocketError("the passphrase doesn't open it") from None


def header(head: bytes) -> bytes:
    """The age header at the start of `head`: through the newline after its `---` line."""
    if not head.startswith(MAGIC):
        raise DocketError("not an age file")
    at = head.find(b"\n---", len(MAGIC) - 1)
    end = head.find(b"\n", at + 1) if at >= 0 else -1
    if end < 0:
        raise DocketError("an age file whose header doesn't end")
    return head[: end + 1]


# age's STREAM: after the header a 16-byte nonce, then the payload in 64 KiB chunks, each sealed with a 16-byte tag.
NONCE = 16
CHUNK = 64 * 1024
TAG = 16


def plain_size(path: Path) -> int:
    """What an age file decrypts to, in bytes, from its framing alone: the last chunk may be short, and an empty
    payload is one empty chunk."""
    with path.open("rb") as f:
        head = f.read(MAX_HEADER)
    payload = path.stat().st_size - len(header(head)) - NONCE
    chunks = max(1, -(-payload // (CHUNK + TAG)))
    return payload - chunks * TAG


def key_id(path: Path) -> str:
    """The key id of an age file: the sha256 of its header."""
    with path.open("rb") as f:
        head = f.read(MAX_HEADER)
    if head.startswith(LFS_POINTER):
        raise DocketError("an LFS pointer, not the file")
    return hashlib.sha256(header(head)).hexdigest()


def is_pointer(path: Path) -> bool:
    """Whether a stored media file is an LFS pointer in place of its bytes: stored, but not downloaded here."""
    with path.open("rb") as f:
        return f.read(len(LFS_POINTER)) == LFS_POINTER
