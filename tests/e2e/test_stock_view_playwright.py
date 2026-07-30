"""Playwright E2E for the progressive stock view (Prompt 3).

Drives the REAL page and asserts the progressive-load contract:
  * header (name + exchange) paints fast from the local master,
  * the fast decision SUMMARY appears with a "computing deep analysis" badge
    before the deep engine finishes,
  * the deep analysis then flips the badge to "analysis complete",
  * no console errors, and one slow/failed panel never blanks the page.

This test SKIPS cleanly unless Playwright and a running server are both present,
so it never breaks the default `pytest tests/` run.

Setup (one time):
    pip install playwright pytest-playwright
    playwright install chromium

Run (with the app running on :5057):
    python dashboard/app.py            # in one shell
    STOCK_VIEW_E2E=1 pytest tests/e2e/test_stock_view_playwright.py -q
"""
from __future__ import annotations

import os
import time
import urllib.request

import pytest

BASE = os.environ.get("STOCK_VIEW_BASE", "http://127.0.0.1:5057")

pytest.importorskip("playwright", reason="pip install playwright pytest-playwright")


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not os.environ.get("STOCK_VIEW_E2E") or not _server_up(),
    reason="set STOCK_VIEW_E2E=1 and run the app on :5057 to enable the E2E test",
)


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        yield pg
        browser.close()


def _pick_ticker(page, sym: str):
    page.goto(BASE, wait_until="domcontentloaded")
    page.evaluate(
        """(sym)=>{ state.ticker=sym; state.view='stock'; state.tab='overview'; save(); go('stock'); }""",
        sym,
    )


def test_header_paints_fast(page):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    _pick_ticker(page, "MSFT")
    # header name (from the local master) should appear well under 1.5s
    page.wait_for_function("() => (document.querySelector('#st-name')||{}).textContent", timeout=1500)
    name = page.eval_on_selector("#st-name", "el => el.textContent")
    assert name and len(name) > 0
    assert not errors, f"console errors: {errors}"


def test_summary_before_deep_then_complete(page):
    _pick_ticker(page, "AAPL")
    # 1) the fast summary shows a "computing deep analysis" status first
    page.wait_for_function(
        "() => (document.querySelector('#ov-status')||{}).innerText?.toLowerCase().includes('computing')",
        timeout=8000,
    )
    # summary must already carry a provisional decision + scenarios
    main = page.eval_on_selector("#ov-main", "el => el.innerText")
    assert "PROVISIONAL" in main.upper()
    assert "SCENARIOS" in main.upper()
    # 2) the deep analysis then completes
    page.wait_for_function(
        "() => (document.querySelector('#ov-status')||{}).innerText?.toLowerCase().includes('complete')",
        timeout=30000,
    )
    fam = page.eval_on_selector("#ov-fam-status", "el => el.innerText")
    assert "9 of 9" in fam


def test_no_blank_page_on_tab_switch(page):
    _pick_ticker(page, "NVDA")
    # switch tabs quickly; each panel must show a skeleton/state, never a blank host
    for tab in ("technicals", "fundamentals", "catalysts", "overview"):
        page.evaluate("(t)=>{ state.tab=t; save(); loadTab(); }", tab)
        page.wait_for_timeout(200)
        host = page.eval_on_selector("#tabHost", "el => el.innerHTML.trim().length")
        assert host > 0, f"tab {tab} rendered a blank host"
