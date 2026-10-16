"""The models Docket can run, pinned: each one's repo and revision, its files with their size and sha256, and what the
core needs to build an input for it (DR-0333, DR-0334).

A vector is made from one string, its input, and keyed by the input's hash (DR-0336): the embedder's passage prefix, the
chunk's title on a line of its own unless the text's first 200 characters hold it, then the chunk's text. A query is
the query prefix and the query's words.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# A title already in the text's opening adds nothing; a transcript window carries its recording's name only in its
# title, so there it is added.
TITLE_IN = 200


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Embedder:
    name: str
    repo: str
    revision: str
    model_file: ModelFile
    tokenizer_file: ModelFile
    dims: int
    max_tokens: int
    query_prefix: str
    passage_prefix: str
    licence: str

    @property
    def files(self) -> tuple[ModelFile, ...]:
        return (self.model_file, self.tokenizer_file)

    @property
    def key(self) -> str:
        """The name a table carries: the model's name and its revision's first eight characters."""
        return f"{self.name.replace('-', '_')}_{self.revision[:8]}"

    @property
    def table(self) -> str:
        return f"chunk_vec_{self.key}"

    @property
    def download(self) -> int:
        return sum(f.size for f in self.files)

    def passage(self, title: str | None, text: str) -> str:
        return passage_input(self.passage_prefix, title, text)

    def query(self, text: str) -> str:
        return f"{self.query_prefix}{text}"


def passage_input(prefix: str, title: str | None, text: str) -> str:
    """A chunk's input: the passage prefix, its title unless the text's opening holds it, then its text."""
    head = f"{title}\n" if title and title not in text[:TITLE_IN] else ""
    return f"{prefix}{head}{text}"


def input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


E5_SMALL = Embedder(
    name="e5-small",
    repo="intfloat/multilingual-e5-small",
    revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
    model_file=ModelFile(
        "onnx/model.onnx", 470_268_510, "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"
    ),
    tokenizer_file=ModelFile(
        "tokenizer.json", 17_082_730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"
    ),
    dims=384,
    max_tokens=512,
    query_prefix="query: ",
    passage_prefix="passage: ",
    licence="MIT",
)

EMBEDDERS: dict[str, Embedder] = {E5_SMALL.name: E5_SMALL}
DEFAULT_EMBEDDER = E5_SMALL.name
