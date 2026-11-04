"""Embedding providers.

Three implementations, in increasing quality and cost:

``hash``
    A deterministic hashed bag-of-n-grams projection.  Requires no download
    and no network, works on any machine, and is what the test suite uses.  It
    is a weak semantic model; hybrid retrieval leans on BM25 to compensate.
``model2vec``
    Static distilled embeddings (~30 MB, CPU-only, no PyTorch).  The
    recommended default for an 8 GB Apple Silicon machine.
``sentence-transformers``
    Full transformer encoders. Best quality, heaviest install.

All three are selected by configuration; the index records which model produced
each vector so a model change can be detected and the corpus re-embedded.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from itertools import pairwise

from app.models.base import EmbeddingProvider, ProviderInfo, ProviderUnavailableError

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic hashed-feature embedder.

    Maps word unigrams and bigrams into a fixed-width space with signed hashing
    and sublinear term weighting, then L2-normalises.  Deterministic across
    processes and platforms, which makes retrieval tests reproducible.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="hash",
            label="Built-in hashed embeddings",
            sends_data_off_device=False,
            available=True,
            generative=False,
            detail=(
                "No download required and fully offline, but it matches wording "
                "rather than meaning. Install a local embedding model for better "
                "semantic search."
            ),
            model=self.model_id,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return f"hash-ngram-v1-{self._dimensions}"

    def _vector(self, text: str) -> list[float]:
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self._dimensions
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for first, second in pairwise(tokens):
            bigram = f"{first}_{second}"
            counts[bigram] = counts.get(bigram, 0) + 1

        vector = [0.0] * self._dimensions
        for term, count in counts.items():
            digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            # Sublinear term frequency damps repeated words.
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class Model2VecEmbedder:
    """Static distilled embeddings via the optional ``model2vec`` package."""

    DEFAULT_MODEL = "minishlab/potion-base-8M"

    def __init__(self, model_name: str = "") -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailableError(
                "model2vec is not installed. Install the 'local-models' extra."
            ) from exc
        try:
            self._model = StaticModel.from_pretrained(self.model_name)
        except Exception as exc:  # pragma: no cover - needs a first-run download
            raise ProviderUnavailableError(
                f"Could not load {self.model_name}: {exc}. The first load needs network access."
            ) from exc
        self._dimensions = int(self._model.dim)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="model2vec",
            label=f"Local static embeddings ({self.model_name})",
            sends_data_off_device=False,
            available=True,
            generative=False,
            detail="Runs on-device on CPU. Downloads the model once on first use.",
            model=self.model_id,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return f"model2vec:{self.model_name}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.encode(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text])[0].tolist()


class SentenceTransformerEmbedder:
    """Transformer encoders via the optional ``sentence-transformers`` package."""

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str = "") -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailableError(
                "sentence-transformers is not installed. Install the 'strong-models' extra."
            ) from exc
        try:
            self._model = SentenceTransformer(self.model_name, device="cpu")
        except Exception as exc:  # pragma: no cover
            raise ProviderUnavailableError(f"Could not load {self.model_name}: {exc}") from exc
        self._dimensions = int(self._model.get_sentence_embedding_dimension())

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="sentence-transformers",
            label=f"Local transformer embeddings ({self.model_name})",
            sends_data_off_device=False,
            available=True,
            generative=False,
            detail="Highest local quality. Needs roughly 1 GB of disk for PyTorch.",
            model=self.model_id,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return f"sentence-transformers:{self.model_name}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=16
        )
        return [list(map(float, vector)) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return list(map(float, vector))


def build_embedder(provider: str, model: str = "") -> EmbeddingProvider:
    if provider == "hash":
        return HashingEmbedder()
    if provider == "model2vec":
        return Model2VecEmbedder(model)
    if provider == "sentence-transformers":
        return SentenceTransformerEmbedder(model)
    raise ProviderUnavailableError(f"Unknown embedding provider: {provider}")


@lru_cache(maxsize=4)
def cached_embedder(provider: str, model: str = "") -> EmbeddingProvider:
    return build_embedder(provider, model)


def probe_embedding_providers(current: str, model: str = "") -> list[ProviderInfo]:
    """Report which embedding providers this installation can actually use."""
    infos = [HashingEmbedder().info]
    for key, label, detail in (
        (
            "model2vec",
            "Local static embeddings (recommended)",
            "Install with: uv pip install -e '.[local-models]'",
        ),
        (
            "sentence-transformers",
            "Local transformer embeddings",
            "Install with: uv pip install -e '.[strong-models]'",
        ),
    ):
        try:
            infos.append(build_embedder(key, model if key == current else "").info)
        except ProviderUnavailableError as exc:
            infos.append(
                ProviderInfo(
                    key=key,
                    label=label,
                    sends_data_off_device=False,
                    available=False,
                    generative=False,
                    detail=f"{exc} {detail}",
                )
            )
    return infos
