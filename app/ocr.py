"""Optional Cloudflare Workers AI OCR hook for scanned PDFs (M4).

Off by default. Uses only the standard library (``urllib`` + ``base64``) and
``pypdf`` (already a dependency) — no new third-party packages. Never raises;
returns ``None`` on any error so callers degrade gracefully to the text layer.

The Cloudflare endpoint is only contacted when ``ocr_enabled()`` is true, i.e.
the provider is ``"cloudflare"`` and both account id and API token are set.
"""

from __future__ import annotations

import re
import base64
import json
import urllib.request
from typing import Any

from pypdf import PdfReader

from .config import Settings

# Cloudflare Workers AI image-to-text endpoint.
_CLOUDFLARE_AI_URL = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
)

_TIMEOUT_SECONDS = 30
# Cap OCR work so a huge scanned book can't block the upload pipeline.
_MAX_OCR_IMAGES = 20
_MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB per image
# Model ids look like "@cf/unum/uform-gen2-qwen-500m": letters, digits, -., /, @.
# No ".." segments, so the id can never traverse the URL path.
_MODEL_RE = re.compile(r"^[A-Za-z0-9_./@-]{1,100}$")


def ocr_enabled(settings: Settings) -> bool:
    """Return True only when OCR is explicitly enabled via Cloudflare and both
    credentials are configured."""
    return (
        settings.ocr_provider == "cloudflare"
        and bool(settings.cf_account_id)
        and bool(settings.cf_api_token)
    )


def _post_json(url: str, token: str, body: dict[str, Any]) -> str | None:
    """POST JSON to ``url`` with a Bearer token; return the raw response or
    ``None`` on any error (network, timeout, HTTP error, …)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def _parse_ocr_text(raw: str | None) -> str | None:
    """Parse a Cloudflare Workers AI response body into stripped text or None."""
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    text = result.get("response") or result.get("description") or ""
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def ocr_image(raw: bytes, settings: Settings) -> str | None:
    """OCR a single image (JPEG/PNG bytes) via Cloudflare Workers AI.

    Returns the stripped extracted text, or ``None`` when OCR is disabled or
    on any error. Never raises.
    """
    if not ocr_enabled(settings):
        return None
    model = settings.ocr_model or ""
    if not _MODEL_RE.fullmatch(model) or ".." in model:
        return None  # refuse to interpolate an unvalidated model into the URL
    if len(raw) > _MAX_OCR_IMAGE_BYTES:
        return None  # oversized image: skip rather than hammer the API
    try:
        image_b64 = base64.b64encode(raw).decode("ascii")
    except Exception:
        return None
    url = _CLOUDFLARE_AI_URL.format(
        account_id=settings.cf_account_id, model=model
    )
    # Cloudflare Workers AI image-to-text format: {"image": [base64, ...]}.
    # A prompt is also accepted by the endpoint ("{"prompt": ..., "image": [...]}").
    body: dict[str, Any] = {"image": [image_b64]}
    raw_text = _post_json(url, settings.cf_api_token, body)
    return _parse_ocr_text(raw_text)


def extract_pdf_images(path: str) -> list[bytes]:
    """Return the raw bytes of every embedded image in a PDF, using pypdf.

    Returns ``[]`` when there are no images or the file cannot be read.
    Never raises.
    """
    images: list[bytes] = []
    try:
        reader = PdfReader(path)
    except Exception:
        return []
    for page in reader.pages:
        try:
            page_images = page.images or []
        except Exception:
            continue
        for img in page_images:
            data = getattr(img, "data", None)
            if isinstance(data, (bytes, bytearray)):
                images.append(bytes(data))
            if len(images) >= _MAX_OCR_IMAGES:
                return images
    return images


def ocr_pdf(path: str, settings: Settings) -> str | None:
    """OCR every embedded image in a PDF and join page texts with ``\\n\\n``.

    Returns ``None`` when OCR is disabled, there are no images, or every OCR
    attempt fails. Never raises.
    """
    if not ocr_enabled(settings):
        return None
    images = extract_pdf_images(path)
    if not images:
        return None
    texts: list[str] = []
    for raw in images:
        text = ocr_image(raw, settings)
        if text:
            texts.append(text)
    if not texts:
        return None
    return "\n\n".join(texts)
