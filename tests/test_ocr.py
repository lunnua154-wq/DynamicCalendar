"""Offline tests for the Cloudflare OCR hook (M4).

No network is used: ``urllib.request.urlopen`` is monkeypatched. OCR is off
by default, so every path degrades to ``None`` without credentials.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import DEFAULT_SETTINGS, Settings, parse_settings
from app import ocr


# --------------------------------------------------------------------------- helpers


def _settings(**overrides) -> Settings:
    base = dict(
        daily_capacity_hours=4.0,
        slot_minutes=50,
        min_slot_minutes=30,
        revision_intervals_days=[1, 3, 7, 14],
        llm_base_url="",
        llm_model="",
        llm_api_key="",
        default_effort_hours=1.0,
    )
    base.update(overrides)
    return Settings(**base)


class _FakeResponse:
    """Context-manager ``urllib`` response stub."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


# --------------------------------------------------------------------------- ocr_enabled


def test_ocr_enabled_off_by_default():
    assert not ocr.ocr_enabled(_settings(ocr_provider="off"))


def test_ocr_enabled_needs_account_and_token():
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_enabled(s) is True

    assert not ocr.ocr_enabled(
        _settings(ocr_provider="cloudflare", cf_account_id="", cf_api_token="tok")
    )
    assert not ocr.ocr_enabled(
        _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="")
    )
    assert not ocr.ocr_enabled(
        _settings(ocr_provider="cloudflare", cf_account_id="", cf_api_token="")
    )


def test_settings_ocr_defaults():
    s = _settings()
    assert s.ocr_provider == "off"
    assert s.cf_account_id == ""
    assert s.cf_api_token == ""
    assert s.ocr_model == "@cf/unum/uform-gen2-qwen-500m"
    assert s.ocr_min_chars == 40
    assert not s.ocr_enabled


# --------------------------------------------------------------------------- ocr_image (mocked urlopen)


def _monkeypatch_urlopen(monkeypatch, body: bytes | None = None, exc: Exception | None = None):
    if exc is not None:
        monkeypatch.setattr(
            ocr.urllib.request,
            "urlopen",
            lambda req, **kwargs: (_ for _ in ()).throw(exc),
        )
    else:
        monkeypatch.setattr(
            ocr.urllib.request,
            "urlopen",
            lambda req, **kwargs: _FakeResponse(body or b"{}"),
        )


def test_ocr_image_disabled_returns_none(monkeypatch):
    called = []

    def boom(req, **kwargs):
        called.append(req)
        raise AssertionError("urlopen must not be called when OCR is disabled")

    monkeypatch.setattr(ocr.urllib.request, "urlopen", boom)
    assert ocr.ocr_image(b"\xff\xd8\xfake", _settings(ocr_provider="off")) is None
    assert not called


def test_ocr_image_happy_path(monkeypatch):
    body = b'{"result":{"response":"  Extracted invoice text  "}}'
    _monkeypatch_urlopen(monkeypatch, body=body)
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"\xff\xd8\xfake-jpeg", s) == "Extracted invoice text"


def test_ocr_image_falls_back_to_description(monkeypatch):
    body = b'{"result":{"description":"text from description field"}}'
    _monkeypatch_urlopen(monkeypatch, body=body)
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"img", s) == "text from description field"


def test_ocr_image_network_error_returns_none(monkeypatch):
    _monkeypatch_urlopen(monkeypatch, exc=OSError("no network"))
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"img", s) is None


def test_ocr_image_invalid_json_returns_none(monkeypatch):
    _monkeypatch_urlopen(monkeypatch, body=b"not json {{{")
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"img", s) is None


def test_ocr_image_empty_result_returns_none(monkeypatch):
    _monkeypatch_urlopen(monkeypatch, body=b'{"result":{"response":"   "}}')
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"img", s) is None


# --------------------------------------------------------------------------- extract_pdf_images


def test_extract_pdf_images_missing_path_returns_empty():
    assert ocr.extract_pdf_images("no/such/file.pdf") == []


def test_extract_pdf_images_invalid_file_returns_empty(tmp_path):
    not_a_pdf = tmp_path / "not.pdf"
    not_a_pdf.write_bytes(b"this is not a pdf")
    assert ocr.extract_pdf_images(str(not_a_pdf)) == []


def test_extract_pdf_images_mocked(monkeypatch, tmp_path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    class FakeImg:
        data = b"\x89PNG image-bytes"

    class FakePage:
        images = [FakeImg()]

    class FakeReader:
        pages = [FakePage()]

    monkeypatch.setattr(ocr, "PdfReader", lambda path: FakeReader())
    imgs = ocr.extract_pdf_images(str(fake_pdf))
    assert imgs == [b"\x89PNG image-bytes"]


def test_extract_pdf_images_skips_pages_without_images(monkeypatch):
    class EmptyPage:
        images = []

    class FakeReader:
        pages = [EmptyPage()]

    monkeypatch.setattr(ocr, "PdfReader", lambda path: FakeReader())
    assert ocr.extract_pdf_images("x.pdf") == []


# --------------------------------------------------------------------------- ocr_pdf


def test_ocr_pdf_disabled_returns_none(monkeypatch):
    called = []

    def fake_ocr(raw, settings):
        called.append(raw)
        return "text"

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    monkeypatch.setattr(ocr, "extract_pdf_images", lambda p: [b"img"])
    assert ocr.ocr_pdf("x.pdf", _settings(ocr_provider="off")) is None
    assert not called


def test_ocr_pdf_no_images_returns_none(monkeypatch):
    monkeypatch.setattr(ocr, "extract_pdf_images", lambda p: [])
    s = _settings(ocr_provider="cloudflare", cf_account_id="a", cf_api_token="t")
    assert ocr.ocr_pdf("x.pdf", s) is None


def test_ocr_pdf_joins_page_texts(monkeypatch):
    calls = []

    def fake_ocr(raw, settings):
        calls.append(raw)
        return f"page-{len(calls)}"

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    monkeypatch.setattr(ocr, "extract_pdf_images", lambda p: [b"img1", b"img2"])
    s = _settings(ocr_provider="cloudflare", cf_account_id="a", cf_api_token="t")
    assert ocr.ocr_pdf("x.pdf", s) == "page-1\n\npage-2"
    assert calls == [b"img1", b"img2"]


def test_ocr_pdf_skips_failed_pages(monkeypatch):
    def fake_ocr(raw, settings):
        if raw == b"bad":
            return None
        return "good"

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    monkeypatch.setattr(ocr, "extract_pdf_images", lambda p: [b"bad", b"ok"])
    s = _settings(ocr_provider="cloudflare", cf_account_id="a", cf_api_token="t")
    assert ocr.ocr_pdf("x.pdf", s) == "good"


# --------------------------------------------------------------------------- config round-trip


def test_parse_settings_ocr_defaults():
    s = parse_settings([])
    assert s.ocr_provider == DEFAULT_SETTINGS["ocr_provider"]
    assert s.cf_account_id == ""
    assert s.cf_api_token == ""
    assert s.ocr_model == DEFAULT_SETTINGS["ocr_model"]
    assert s.ocr_min_chars == int(DEFAULT_SETTINGS["ocr_min_chars"])


def test_parse_settings_ocr_from_rows():
    rows = [
        {"key": "ocr_provider", "value": '"cloudflare"'},
        {"key": "cf_account_id", "value": '"acc-123"'},
        {"key": "cf_api_token", "value": '"tok-abc"'},
        {"key": "ocr_model", "value": '"@cf/my-model"'},
        {"key": "ocr_min_chars", "value": "100"},
    ]
    s = parse_settings(rows)
    assert s.ocr_provider == "cloudflare"
    assert s.cf_account_id == "acc-123"
    assert s.cf_api_token == "tok-abc"
    assert s.ocr_model == "@cf/my-model"
    assert s.ocr_min_chars == 100
    assert s.ocr_enabled is True


def test_ocr_image_rejects_unvalidated_model_name(monkeypatch):
    called = []

    def boom(*a, **kw):
        called.append(a)
        raise AssertionError("urlopen must not be called for an invalid model id")

    monkeypatch.setattr(ocr.urllib.request, "urlopen", boom)
    s = _settings(
        ocr_provider="cloudflare",
        cf_account_id="acc",
        cf_api_token="tok",
        ocr_model="../../etc/passwd",
    )
    assert ocr.ocr_image(b"img", s) is None
    assert not called


def test_ocr_image_rejects_oversized_image(monkeypatch):
    called = []

    def boom(*a, **kw):
        called.append(a)
        raise AssertionError("urlopen must not be called for an oversized image")

    monkeypatch.setattr(ocr.urllib.request, "urlopen", boom)
    s = _settings(ocr_provider="cloudflare", cf_account_id="acc", cf_api_token="tok")
    assert ocr.ocr_image(b"\x00" * (10 * 1024 * 1024 + 1), s) is None
    assert not called


def test_extract_pdf_images_caps_image_count(monkeypatch, tmp_path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    class _Img:
        def __init__(self, i):
            self.data = bytes([i]) * 8

    class _Page:
        images = [_Img(i) for i in range(50)]

    class _Reader:
        pages = [_Page()]

    monkeypatch.setattr(ocr, "PdfReader", lambda path: _Reader())
    assert len(ocr.extract_pdf_images(str(fake_pdf))) == 20
