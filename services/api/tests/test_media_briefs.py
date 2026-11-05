"""Image and audio briefs.

A model reads these, which is new for ingestion, so the tests pin the
boundaries: what is accepted, which provider may read what, that a provider
which cannot read a file refuses instead of answering, and that the resulting
document says how its text was produced.

No network: providers are doubles, and the cloud request bodies are checked by
intercepting the HTTP call.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field

import pytest

from app.config import Settings
from app.ingestion.media import (
    MAX_AUDIO_BYTES,
    MediaReader,
    sniff_media_type,
    wav_duration_seconds,
)
from app.ingestion.pdf import UnreadableDocumentError
from app.ingestion.pipeline import UnsupportedFileError, validate_upload
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo
from app.models.llm import AnthropicProvider, GeminiProvider, OllamaProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + struct.pack("<I", 100) + b"WEBP" + b"\x00" * 64
MP3_ID3 = b"ID3\x04\x00" + b"\x00" * 64
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64


def wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    frames = int(seconds * rate)
    pcm = b"\x00\x00" * frames
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --- sniffing ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (PNG, "brief.png", "image/png"),
        (JPEG, "brief.jpg", "image/jpeg"),
        (WEBP, "brief.webp", "image/webp"),
        (wav(), "brief.wav", "audio/wav"),
        (MP3_ID3, "brief.mp3", "audio/mpeg"),
        (M4A, "brief.m4a", "audio/mp4"),
        # The name says PNG; the bytes say otherwise. Bytes win.
        (b"not an image at all" * 4, "brief.png", None),
    ],
)
def test_media_type_comes_from_the_bytes(data, name, expected):
    assert sniff_media_type(data, name) == expected


def test_validate_upload_accepts_images_and_audio(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert validate_upload("a.png", PNG, settings) == "image/png"
    assert validate_upload("a.wav", wav(), settings) == "audio/wav"
    with pytest.raises(UnsupportedFileError, match="Supported files"):
        validate_upload("a.txt", b"plain text, not a brief", settings)


def test_wav_duration_is_read_from_the_header():
    assert wav_duration_seconds(wav(seconds=2.5)) == pytest.approx(2.5, abs=0.01)
    assert wav_duration_seconds(PNG) is None


# --- doubles ----------------------------------------------------------------
@dataclass
class FakeVision:
    """A provider that reads images and audio, recording what it was sent."""

    images: bool = True
    audio: bool = True
    text: str = "1. Record a 5 second clip.\n2. Plot its spectrum."
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake",
            label="Fake Vision",
            sends_data_off_device=True,
            available=True,
            accepts_images=self.images,
            accepts_audio=self.audio,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(text=self.text, provider="fake", model="fake-1", generative=True)


class TextOnly:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="text", label="Text Only", sends_data_off_device=False, available=True
        )

    def generate(self, request):  # pragma: no cover
        raise AssertionError("must not be asked to read an image")


def settings(tmp_path, **overrides) -> Settings:
    return Settings(data_dir=tmp_path, **overrides)


# --- images -----------------------------------------------------------------
def test_an_image_brief_becomes_one_page_with_the_transcript_and_a_preview(tmp_path):
    vision = FakeVision()
    stored: dict[int, tuple[bytes, str]] = {}

    def sink(page_number, data, media_type):
        stored[page_number] = (data, media_type)
        return f"previews/{page_number}"

    doc = MediaReader(settings=settings(tmp_path), provider=vision).read_image(
        PNG, "image/png", title="CW2 photo", preview_sink=sink
    )

    assert doc.page_count == 1
    assert doc.pages[0].text.startswith("1. Record")
    assert doc.pages[0].heading == "CW2 photo"
    assert doc.extractor == "vision:fake"
    assert any("read from an image" in w for w in doc.warnings)
    # The image itself was handed to the model, not a description of it.
    sent = vision.requests[0].attachments
    assert len(sent) == 1 and sent[0].media_type == "image/png" and sent[0].data == PNG
    # The preview is the image, as PNG, when PyMuPDF could decode it; this
    # fixture is not a decodable PNG, so no preview — and no failure either.
    assert doc.pages[0].preview_key in {"", "previews/1"}


def test_a_text_only_provider_refuses_an_image_rather_than_guessing(tmp_path):
    with pytest.raises(UnreadableDocumentError, match="needs a model that accepts images"):
        MediaReader(settings=settings(tmp_path), provider=TextOnly()).read_image(
            PNG, "image/png", title="x", preview_sink=None
        )


def test_no_provider_at_all_is_a_clear_message_not_an_empty_document(tmp_path):
    with pytest.raises(UnreadableDocumentError, match="upload the brief as a PDF"):
        MediaReader(settings=settings(tmp_path), provider=None).read_image(
            PNG, "image/png", title="x", preview_sink=None
        )


# --- audio ------------------------------------------------------------------
def test_audio_falls_back_to_a_cloud_provider_when_no_local_model_is_installed(
    tmp_path, monkeypatch
):
    """faster-whisper is optional; absent, a consented audio-capable provider is used."""
    from app.ingestion import media

    monkeypatch.setattr(
        media,
        "_transcribe_locally",
        lambda *a, **k: (_ for _ in ()).throw(
            media._LocalTranscriptionUnavailable("not installed")
        ),
    )
    vision = FakeVision(text="[00:00] Welcome. [00:12] Your task is to filter the signal.")
    doc = MediaReader(
        settings=settings(tmp_path, audio_transcriber="auto"), provider=vision
    ).read_audio(wav(), "audio/wav", title="Lecturer explains CW2", source_path=None)

    assert doc.extractor == "transcribe:fake"
    assert "[00:12]" in doc.pages[0].text
    assert vision.requests[0].attachments[0].media_type == "audio/wav"
    assert any("transcribed from audio by a cloud model" in w for w in doc.warnings)


def test_audio_with_local_pinned_does_not_go_to_the_cloud(tmp_path, monkeypatch):
    from app.ingestion import media

    monkeypatch.setattr(
        media,
        "_transcribe_locally",
        lambda *a, **k: (_ for _ in ()).throw(
            media._LocalTranscriptionUnavailable("not installed")
        ),
    )
    vision = FakeVision()
    with pytest.raises(UnreadableDocumentError, match="Local transcription is not available"):
        MediaReader(
            settings=settings(tmp_path, audio_transcriber="local"), provider=vision
        ).read_audio(wav(), "audio/wav", title="x", source_path=None)
    assert vision.requests == [], "nothing may leave the machine when local is pinned"


def test_audio_off_refuses(tmp_path):
    with pytest.raises(UnreadableDocumentError, match="switched off"):
        MediaReader(
            settings=settings(tmp_path, audio_transcriber="off"), provider=FakeVision()
        ).read_audio(wav(), "audio/wav", title="x", source_path=None)


def test_a_provider_that_cannot_hear_refuses_audio(tmp_path, monkeypatch):
    from app.ingestion import media

    monkeypatch.setattr(
        media,
        "_transcribe_locally",
        lambda *a, **k: (_ for _ in ()).throw(
            media._LocalTranscriptionUnavailable("not installed")
        ),
    )
    with pytest.raises(UnreadableDocumentError, match="accepts audio"):
        MediaReader(settings=settings(tmp_path), provider=FakeVision(audio=False)).read_audio(
            wav(), "audio/wav", title="x", source_path=None
        )


def test_oversized_audio_is_refused_before_anything_is_sent(tmp_path):
    big = wav(seconds=1) + b"\x00" * MAX_AUDIO_BYTES
    vision = FakeVision()
    with pytest.raises(UnreadableDocumentError, match="limited to"):
        MediaReader(settings=settings(tmp_path), provider=vision).read_audio(
            big, "audio/wav", title="x", source_path=None
        )
    assert vision.requests == []


# --- providers --------------------------------------------------------------
def test_ollama_refuses_attachments_instead_of_ignoring_them():
    from app.models.base import Attachment, ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError, match="cannot read image/png"):
        OllamaProvider("llama3.1:8b").generate(
            GenerationRequest(
                system="s", user="u", attachments=[Attachment(media_type="image/png", data=PNG)]
            )
        )


def test_anthropic_sends_an_image_as_a_base64_content_block(monkeypatch):
    from app.models import llm
    from app.models.base import Attachment

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    AnthropicProvider(api_key="k").generate(
        GenerationRequest(
            system="s",
            user="transcribe",
            attachments=[Attachment(media_type="image/png", data=PNG)],
        )
    )
    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(PNG).decode("ascii"),
    }
    assert content[1] == {"type": "text", "text": "transcribe"}
    assert captured["headers"]["x-api-key"] == "k"


def test_gemini_request_shape_and_audio_routing(monkeypatch):
    from app.models import llm
    from app.models.base import Attachment

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": "[00:00] hello"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    provider = GeminiProvider(api_key="g")

    result = provider.generate(
        GenerationRequest(
            system="sys",
            user="transcribe",
            attachments=[Attachment(media_type="audio/wav", data=wav())],
            json_schema=None,
        )
    )
    # Audio-only requests go to the dedicated transcription model.
    assert captured["url"].endswith(f"/models/{GeminiProvider.TRANSCRIBE_MODEL}:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "g"
    body = captured["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    parts = body["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "audio/wav"
    assert parts[-1] == {"text": "transcribe"}
    assert result.text == "[00:00] hello"
    assert result.model == GeminiProvider.TRANSCRIBE_MODEL
    assert (result.input_tokens, result.output_tokens) == (5, 3)

    # Text and images go to the general model, and JSON mode is requested when asked.
    provider.generate(GenerationRequest(system="s", user="u", json_schema={"type": "object"}))
    assert captured["url"].endswith(f"/models/{GeminiProvider.DEFAULT_MODEL}:generateContent")
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_with_no_candidates_is_an_error_not_an_empty_answer(monkeypatch):
    from app.models import llm
    from app.models.base import ProviderUnavailableError

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse())
    with pytest.raises(ProviderUnavailableError, match="SAFETY"):
        GeminiProvider(api_key="g").generate(GenerationRequest(system="s", user="u"))


def test_the_provider_catalogue_offers_gemini_not_openai():
    from app.models.llm import probe_llm_providers

    keys = [p.key for p in probe_llm_providers()]
    assert "gemini" in keys
    assert "openai" not in keys


def test_json_helpers_are_unaffected():
    assert json.loads("{}") == {}


# --- local decoding -------------------------------------------------------
def test_pcm_wav_is_decoded_to_mono_16k_float(tmp_path):
    """WAV never goes through PyAV, whose API has broken across versions."""
    import numpy as np

    from app.ingestion.media import _decode_pcm_wav

    path = tmp_path / "a.wav"
    path.write_bytes(wav(seconds=0.5, rate=8000))
    samples = _decode_pcm_wav(path)
    assert samples is not None
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    # 0.5 s at 8 kHz resampled to 16 kHz is about 8000 samples.
    assert abs(len(samples) - 8000) < 20
    assert float(abs(samples).max()) <= 1.0


def test_non_wav_input_is_left_to_the_library(tmp_path):
    from app.ingestion.media import _decode_pcm_wav

    path = tmp_path / "a.wav"
    path.write_bytes(b"not a wav")
    assert _decode_pcm_wav(path) is None
