"""Render case JSON into realistic hospital-statement PDF + PNG pairs.

Jinja2 builds the HTML (three interchangeable bill layouts plus an EOB
layout); Playwright's Chromium renders each page to a PDF and a PNG
about 1275px wide, matching what a vision extractor would actually be
handed.

Adversarial text is placed according to which case it belongs to (by
case_id, not stored in the case JSON, so the schema stays exactly the
one Task A pre-registers): most render as a visible memo line; the
white-on-white case renders the same text styled invisible-on-page;
the fake-approval-stamp case renders it as a stamp graphic; the
altered-contact case swaps it in for the billing contact line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

PNG_WIDTH = 1275

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


def usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["usd"] = usd
    return env


def _adversarial_context(case: dict) -> dict:
    case_id = case["case_id"]
    text = case.get("adversarial_text")
    ctx = {"memo_text": None, "hidden_text": None, "stamp_text": None, "altered_contact": None}
    if not text:
        return ctx
    if case_id == "adversarial_white_on_white":
        ctx["hidden_text"] = text
    elif case_id == "adversarial_fake_stamp":
        ctx["stamp_text"] = text
    elif case_id == "adversarial_altered_contact":
        ctx["altered_contact"] = text
    else:
        ctx["memo_text"] = text
    return ctx


def bill_context(case: dict) -> dict:
    bill = case["bill"]
    dos_list = sorted({line["dos"] for line in bill["lines"]})
    ctx = {
        "case_id": case["case_id"],
        "hospital": bill["provider"],
        "account_no": bill["account_no"],
        "statement_date": bill["statement_date"],
        "setting": bill["setting"],
        "self_pay": bill["self_pay"],
        "patient": case["patient"],
        "lines": bill["lines"],
        "totals": bill["totals"],
        "dos_from": dos_list[0],
        "dos_to": dos_list[-1],
    }
    ctx.update(_adversarial_context(case))
    return ctx


def eob_context(case: dict) -> dict:
    eob = case["eob"]
    return {
        "case_id": case["case_id"],
        "patient": case["patient"],
        "hospital": case["bill"]["provider"],
        "eob": eob,
    }


async def _render_page(browser, html: str, out_pdf: Path, out_png: Path) -> None:
    # Start with a short viewport so scrollHeight reflects the content's own
    # height rather than stretching to fill a tall initial viewport, then
    # resize to fit exactly before shooting -- otherwise a short bill (e.g.
    # two line items) screenshots with a page's worth of blank space below it.
    page = await browser.new_page(viewport={"width": PNG_WIDTH, "height": 200})
    await page.set_content(html, wait_until="load")
    height = await page.evaluate("document.documentElement.scrollHeight")
    await page.set_viewport_size({"width": PNG_WIDTH, "height": max(height, 400)})
    out_png.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(out_png))
    await page.pdf(path=str(out_pdf), print_background=True, prefer_css_page_size=False)
    await page.close()


async def render_all(cases_dir: Path, out_dir: Path, only_one_per_layout: bool = False) -> list[str]:
    from playwright.async_api import async_playwright

    env = _env()
    bill_templates = {
        "bill_classic": env.get_template("bill_classic.html"),
        "bill_modern": env.get_template("bill_modern.html"),
        "bill_statement": env.get_template("bill_statement.html"),
    }
    eob_template = env.get_template("eob.html")

    case_files = sorted(cases_dir.glob("*.json"))
    seen_layouts: set[str] = set()
    written: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for case_file in case_files:
                case = json.loads(case_file.read_text(encoding="utf-8"))
                layout = case["layout"]
                if only_one_per_layout and layout in seen_layouts and case.get("eob") is None:
                    continue
                seen_layouts.add(layout)

                html = bill_templates[layout].render(**bill_context(case))
                out_pdf = out_dir / f"{case['case_id']}.pdf"
                out_png = out_dir / f"{case['case_id']}.png"
                await _render_page(browser, html, out_pdf, out_png)
                written.append(case["case_id"])

                if case.get("eob") is not None:
                    html = eob_template.render(**eob_context(case))
                    out_pdf = out_dir / f"{case['case_id']}_eob.pdf"
                    out_png = out_dir / f"{case['case_id']}_eob.png"
                    await _render_page(browser, html, out_pdf, out_png)
                    written.append(f"{case['case_id']}_eob")
        finally:
            await browser.close()

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parents[2]
    parser.add_argument("--cases", type=Path, default=here / "corpus" / "cases")
    parser.add_argument("--out", type=Path, default=here / "corpus" / "rendered")
    parser.add_argument("--only-one-per-layout", action="store_true")
    args = parser.parse_args()

    written = asyncio.run(render_all(args.cases, args.out, args.only_one_per_layout))
    print(f"rendered {len(written)} documents to {args.out}")


if __name__ == "__main__":
    main()
