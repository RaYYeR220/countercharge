"""Agents-as-tools run by the case agent: intake extraction, charity research, and
letter drafting. Each is exposed as an ``@tool`` function so the top-level case
agent can call it like any other tool.

Unit tests never launch a browser or call a real vision/reasoning model -- ``build_model``
and the browser tool are only imported/invoked inside the returned closures, so tests
can monkeypatch :func:`countercharge_agent.models.build_model` and inject fakes for
``s3_client``/``gateway_caller`` without ever touching Playwright or a network model.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from strands import Agent, tool

from countercharge_engine.models import Bill, EOB

from countercharge_agent import models
from countercharge_agent.gateway import GatewayCaller, call_gateway_tool
from countercharge_agent.prompts import (
    CHARITY_RESEARCHER_SYSTEM_PROMPT,
    INTAKE_EXTRACTOR_SYSTEM_PROMPT,
    LETTER_WRITER_SYSTEM_PROMPT,
)

_IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

_MAX_PDF_PAGES = 5


def _image_format(media_type: str) -> str:
    return _IMAGE_FORMATS.get(media_type.lower(), "png")


def _default_s3_client():
    import boto3

    return boto3.client("s3")


def _pdf_to_png_pages(pdf_bytes: bytes, *, max_pages: int = _MAX_PDF_PAGES) -> list[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return [_pil_to_png(pdf[index].render(scale=2.0)) for index in range(min(len(pdf), max_pages))]
    finally:
        pdf.close()


def _pil_to_png(bitmap) -> bytes:
    import io

    buffer = io.BytesIO()
    bitmap.to_pil().save(buffer, format="PNG")
    return buffer.getvalue()


def make_intake_extractor(
    *,
    gateway_caller: GatewayCaller,
    bucket: str,
    s3_client_factory: Callable[[], Any] | None = None,
) -> Callable[..., dict]:
    """Build the ``intake_extractor(case_id, s3_key, kind)`` agent-as-tool (C1 ``save_extraction``)."""

    @tool
    def intake_extractor(case_id: str, s3_key: str, kind: str, media_type: str = "image/png") -> dict:
        """Extract a bill or EOB from an uploaded image/PDF and save it to the case."""
        client = (s3_client_factory or _default_s3_client)()
        raw_bytes = client.get_object(Bucket=bucket, Key=s3_key)["Body"].read()

        if media_type.lower() == "application/pdf":
            pages = _pdf_to_png_pages(raw_bytes)
            image_format = "png"
        else:
            pages = [raw_bytes]
            image_format = _image_format(media_type)

        output_model = Bill if kind == "bill" else EOB
        extractor = Agent(
            model=models.build_model(role="vision"),
            system_prompt=INTAKE_EXTRACTOR_SYSTEM_PROMPT,
            structured_output_model=output_model,
            callback_handler=None,
        )
        content: list[dict[str, Any]] = [{"text": f"Extract this {kind} exactly as printed."}]
        for page_bytes in pages:
            content.append({"image": {"format": image_format, "source": {"bytes": page_bytes}}})

        result = extractor([{"role": "user", "content": content}])
        extracted = result.structured_output
        if extracted is None:
            raise ValueError(f"intake extraction of {kind} produced no structured output")

        payload = extracted.model_dump(mode="json")
        saved = call_gateway_tool(
            gateway_caller,
            "case___save_extraction",
            case_id=case_id,
            kind=kind,
            payload=payload,
            confidence=0.9,
        )
        return {"doc_id": (saved or {}).get("doc_id"), "extracted": payload}

    return intake_extractor


def make_charity_researcher(*, browser_mode: str | None = None) -> Callable[..., dict]:
    """Build the ``charity_researcher(hospital_id)`` agent-as-tool.

    Uses strands-agents-tools' local Chromium browser by default; set ``BROWSER_MODE=
    agentcore`` (or pass ``browser_mode``) to route through the AgentCore Browser
    service instead. Neither import happens until the tool actually runs, so building
    this tool -- and every unit test that never invokes it -- never touches Playwright.
    """

    @tool
    def charity_researcher(hospital_id: str, hospital_name: str, website_url: str) -> dict:
        """Research a hospital's financial-assistance (charity care) policy from its website."""
        mode = (browser_mode or os.environ.get("BROWSER_MODE", "local")).strip().lower()
        if mode == "agentcore":
            from strands_tools.browser import AgentCoreBrowser

            browser = AgentCoreBrowser()
        else:
            from strands_tools.browser import LocalChromiumBrowser

            browser = LocalChromiumBrowser()

        researcher = Agent(
            model=models.build_model(role="reasoning"),
            system_prompt=CHARITY_RESEARCHER_SYSTEM_PROMPT,
            tools=[browser.browser],
            callback_handler=None,
        )
        result = researcher(
            f"Find the financial assistance (charity care) policy for {hospital_name}, "
            f"starting from {website_url}. Report the free-care and discount income "
            "thresholds as a percentage of the federal poverty level, and the exact "
            "page URL where you found them."
        )
        return {"hospital_id": hospital_id, "summary": str(result)}

    return charity_researcher


def make_letter_writer(*, gateway_caller: GatewayCaller) -> Callable[..., dict]:
    """Build the ``letter_writer(case_id, action_type, finding_ids, recipient_email)`` agent-as-tool.

    Writes a letter citing only the given findings, then calls ``case___draft_action``.
    If the grounding verifier rejects it, rewrites once with the unsupported items
    listed; a second failure is surfaced as an error rather than retried indefinitely.
    """

    @tool
    def letter_writer(case_id: str, action_type: str, finding_ids: list[str], recipient_email: str) -> dict:
        """Draft a grounded letter for this case and record it as a pending action."""
        case = call_gateway_tool(gateway_caller, "case___get_case", case_id=case_id)
        cited = [
            entry["finding"]
            for entry in (case or {}).get("findings", [])
            if entry.get("finding", {}).get("finding_id") in finding_ids
        ]
        disputed_amount_cents = sum(f["amount_cents"] for f in cited if f.get("disputable"))

        writer = Agent(
            model=models.build_model(role="reasoning"),
            system_prompt=LETTER_WRITER_SYSTEM_PROMPT,
            callback_handler=None,
        )
        letter_markdown = str(writer(_letter_prompt(action_type, cited, recipient_email)))

        draft = _draft_action(
            gateway_caller, case_id, action_type, recipient_email, finding_ids, disputed_amount_cents, letter_markdown
        )
        grounding = draft.get("grounding") or {}
        if grounding.get("ok", True):
            return draft

        letter_markdown = str(writer(_rewrite_prompt(grounding)))
        draft = _draft_action(
            gateway_caller, case_id, action_type, recipient_email, finding_ids, disputed_amount_cents, letter_markdown
        )
        if not (draft.get("grounding") or {}).get("ok", True):
            return {"error": "letter failed grounding twice", "draft": draft}
        return draft

    return letter_writer


def _letter_prompt(action_type: str, findings: list[dict], recipient_email: str) -> str:
    import json

    return (
        f"Write a {action_type} letter to {recipient_email}, citing only these findings "
        f"(JSON): {json.dumps(findings)}"
    )


def _rewrite_prompt(grounding: dict) -> str:
    return (
        "Your letter cited things that are not supported by the findings you were given. "
        f"Unsupported amounts: {grounding.get('unsupported_amounts')}. "
        f"Unsupported codes: {grounding.get('unsupported_codes')}. "
        "Rewrite the letter using only the amounts and codes from the findings you were given."
    )


def _draft_action(
    gateway_caller: GatewayCaller,
    case_id: str,
    action_type: str,
    recipient_email: str,
    finding_ids: list[str],
    disputed_amount_cents: int,
    letter_markdown: str,
) -> dict:
    return call_gateway_tool(
        gateway_caller,
        "case___draft_action",
        case_id=case_id,
        type=action_type,
        recipient_email=recipient_email,
        finding_ids=finding_ids,
        letter_markdown=letter_markdown,
        disputed_amount_cents=disputed_amount_cents,
    )
