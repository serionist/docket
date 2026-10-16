"""The models Docket can run, pinned: each one's repo and revision, its files with their size and sha256, and what the
core needs to build an input for it (DR-0333, DR-0334, DR-0450).

A vector is made from one string, its input, and keyed by the input's hash (DR-0336): the embedder's passage prefix, the
chunk's title on a line of its own unless the text's first 200 characters hold it, then the chunk's text. A query is
the query prefix and the query's words.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

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


# -- the rerankers (#88) ---------------------------------------------------------------------------------------------

RerankDevice = Literal["cpu", "cuda"]
# A reranker's pairs go to the model in batches of this many: as fast as one batch on a GPU with half the memory,
# faster on a CPU, and a point to check the deadline between (DR-0451).
RERANK_BATCH = 10
# A chunk's text as the core sends it: the tokenizer cuts it to the model's limit, so more only costs the pipe.
RERANK_CHARS = 4096


@dataclass(frozen=True)
class Reranker:
    """A cross-encoder: the device it runs on, its pinned files, its token limit and how many pairs a search sends."""

    name: str
    repo: str
    revision: str
    device: RerankDevice
    model_file: ModelFile
    tokenizer_file: ModelFile
    max_tokens: int
    pairs: int
    licence: str
    trained_on: str

    @property
    def files(self) -> tuple[ModelFile, ...]:
        return (self.model_file, self.tokenizer_file)

    @property
    def download(self) -> int:
        return sum(f.size for f in self.files)


def rerank_passage(title: str | None, text: str) -> str:
    """A chunk's side of a pair: its input without a passage prefix, cut to what the pipe needs to carry."""
    return passage_input("", title, text)[:RERANK_CHARS]


BGE_RERANKER = Reranker(
    name="bge-reranker-v2-m3",
    # onnx-community's automatic conversion of BAAI/bge-reranker-v2-m3 (Apache-2.0), with no licence file of its own.
    repo="onnx-community/bge-reranker-v2-m3-ONNX",
    revision="6f5ff65298512715a1e669753bc754d2bc8f367b",
    device="cuda",
    model_file=ModelFile(
        "onnx/model_fp16.onnx", 1_136_209_678, "9b26f9185d2d26051f724c9fc3565e5d9614a9dd459b784c806c246232a70fe3"
    ),
    tokenizer_file=ModelFile(
        "tokenizer.json", 17_082_900, "8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3"
    ),
    max_tokens=512,
    pairs=50,
    licence="Apache-2.0",
    trained_on="trained on bge-m3-data, which includes MS MARCO",
)
MMARCO_RERANKER = Reranker(
    name="mmarco-minilm",
    repo="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    revision="1427fd652930e4ba29e8149678df786c240d8825",
    device="cpu",
    model_file=ModelFile(
        "onnx/model.onnx", 470_883_696, "3e9a03ed1e966f7c5288dd4230e3d6a9bf5e3a170a06f1f4241c5bca12c6487c"
    ),
    tokenizer_file=ModelFile(
        "tokenizer.json", 17_082_660, "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"
    ),
    max_tokens=256,
    pairs=30,
    licence="Apache-2.0",
    trained_on="trained on mMARCO, a machine translation of MS MARCO",
)

RERANKERS: dict[str, Reranker] = {r.name: r for r in (BGE_RERANKER, MMARCO_RERANKER)}
# Each device's reranker (DR-0456): one per device, none for a device with no entry.
RERANKER_FOR: dict[str, str] = {"cuda": BGE_RERANKER.name, "cpu": MMARCO_RERANKER.name}
