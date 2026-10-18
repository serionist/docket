"""The speech models Docket can run, pinned (DR-0567): each one's repo and revision on Hugging Face, its files with
their size and sha256, its licence, and the engine string a transcript made with it carries (DR-0570).

large-v3 is every user's; tiny is for CI's smoke test, and for a machine that wants one to try.
"""

from __future__ import annotations

from dataclasses import dataclass

from docket.models.catalogue import ModelFile

# The version of faster-whisper the speech environments pin, which the engine string names.
FASTER_WHISPER = "1.2.1"


@dataclass(frozen=True)
class SpeechModel:
    name: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]
    licence: str

    @property
    def download(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def engine(self) -> str:
        """What a transcript made with this model says made it: the model, its revision, and the runtime."""
        return f"whisper-{self.name}@{self.revision[:7]} faster-whisper-{FASTER_WHISPER}"


WHISPER_LARGE_V3 = SpeechModel(
    name="large-v3",
    repo="Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    files=(
        ModelFile("config.json", 2394, "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"),
        ModelFile("model.bin", 3087284237, "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
        ModelFile("preprocessor_config.json", 340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
        ModelFile("tokenizer.json", 2480617, "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"),
        ModelFile("vocabulary.json", 1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    ),
    licence="MIT",
)

WHISPER_TINY = SpeechModel(
    name="tiny",
    repo="Systran/faster-whisper-tiny",
    revision="d90ca5fe260221311c53c58e660288d3deb8d356",
    files=(
        ModelFile("config.json", 2249, "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
        ModelFile("model.bin", 75538270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
        ModelFile("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        ModelFile("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    ),
    licence="MIT",
)

SPEECH = {m.name: m for m in (WHISPER_LARGE_V3, WHISPER_TINY)}
DEFAULT_SPEECH = WHISPER_LARGE_V3.name
