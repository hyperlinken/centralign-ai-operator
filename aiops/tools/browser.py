"""
Browser tool — one Playwright-backed session the agent can drive.

Design
------
Rather than exposing one tool per action (click, fill, etc.) we expose a
single `browser` tool with an `action` discriminator. This keeps the tool
list in the planner prompt short and makes it obvious to the LLM that
browser calls share state (the same tab).

Every call captures:
  - a screenshot (base64) written to the run's evidence directory
  - the resulting URL + page title
  - an optional structured scrape of visible rows/forms

The returned ToolResult.evidence always includes `screenshot_path`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, List, Optional

from .base import Tool, ToolError, ToolResult, ToolSpec

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError as e:  # pragma: no cover
    raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install chromium") from e


BROWSER_SPEC = ToolSpec(
    name="browser",
    description=(
        "Drive a Chromium browser. Use `action` to pick: "
        "goto, click, fill, read_page, read_elements, wait_for, press, screenshot. "
        "The same tab is reused across calls, so state persists (cookies, forms)."
    ),
    parameters={
        "action": "one of: goto | click | fill | read_page | read_elements | wait_for | press | screenshot",
        "url": "(goto) absolute URL",
        "selector": "(click/fill/wait_for/read_elements) CSS selector",
        "value": "(fill) text to type",
        "key": "(press) e.g. 'Enter'",
        "limit": "(read_elements) max rows to return, default 20",
    },
)


class BrowserTool(Tool):
    def __init__(self, run_evidence_dir: Path, headless: bool = False):
        super().__init__(BROWSER_SPEC)
        self._evidence_dir = run_evidence_dir
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._headless = headless
        self._shot_idx = 0

    # -- lifecycle ----------------------------------------------------------

    def _ensure_page(self):
        if self._page:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 820})
        self._page = self._context.new_page()
        self._page.set_default_timeout(10_000)

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._page = self._context = self._browser = self._pw = None

    # -- helpers ------------------------------------------------------------

    def _snapshot(self, label: str) -> dict:
        self._shot_idx += 1
        path = self._evidence_dir / f"{self._shot_idx:03d}_{label}.png"
        try:
            self._page.screenshot(path=str(path), full_page=False)
        except Exception as e:
            return {"screenshot_error": str(e), "url": self._page.url}
        return {
            "screenshot_path": str(path),
            "url": self._page.url,
            "title": self._page.title() or "",
        }

    # -- entrypoint ---------------------------------------------------------

    def call(self, **kwargs) -> ToolResult:
        action = (kwargs.get("action") or "").strip().lower()
        if not action:
            return ToolResult.failure("missing `action` parameter")
        self._ensure_page()
        try:
            if action == "goto":
                return self._goto(kwargs.get("url"))
            if action == "click":
                return self._click(kwargs.get("selector"))
            if action == "fill":
                return self._fill(kwargs.get("selector"), kwargs.get("value"))
            if action == "press":
                return self._press(kwargs.get("key"))
            if action == "wait_for":
                return self._wait_for(kwargs.get("selector"))
            if action == "read_page":
                return self._read_page()
            if action == "read_elements":
                return self._read_elements(kwargs.get("selector"), int(kwargs.get("limit", 20) or 20))
            if action == "screenshot":
                ev = self._snapshot("adhoc")
                return ToolResult.success(f"screenshot saved to {ev.get('screenshot_path')}", evidence=ev)
            return ToolResult.failure(f"unknown action: {action}")
        except PWTimeout as e:
            ev = self._snapshot(f"timeout_{action}")
            return ToolResult.failure(f"timeout during {action}: {e}", evidence=ev)
        except Exception as e:
            ev = self._snapshot(f"error_{action}")
            return ToolResult.failure(f"error during {action}: {e}", evidence=ev)

    # -- action implementations --------------------------------------------

    def _goto(self, url: Optional[str]) -> ToolResult:
        if not url:
            return ToolResult.failure("missing `url`")
        self._page.goto(url, wait_until="domcontentloaded")
        time.sleep(0.1)  # yields to any lightweight JS
        ev = self._snapshot("goto")
        return ToolResult.success(f"navigated to {ev['url']} — '{ev['title']}'", evidence=ev)

    def _click(self, selector: Optional[str]) -> ToolResult:
        if not selector:
            return ToolResult.failure("missing `selector`")
        self._page.locator(selector).first.click()
        ev = self._snapshot("click")
        return ToolResult.success(f"clicked {selector}; now at {ev['url']}", evidence=ev)

    def _fill(self, selector: Optional[str], value: Optional[str]) -> ToolResult:
        if not selector:
            return ToolResult.failure("missing `selector`")
        if value is None:
            return ToolResult.failure("missing `value`")
        self._page.locator(selector).first.fill(str(value))
        ev = self._snapshot("fill")
        return ToolResult.success(f"filled {selector} with {len(str(value))}-char value", evidence=ev)

    def _press(self, key: Optional[str]) -> ToolResult:
        if not key:
            return ToolResult.failure("missing `key`")
        self._page.keyboard.press(key)
        ev = self._snapshot("press")
        return ToolResult.success(f"pressed {key}; now at {ev['url']}", evidence=ev)

    def _wait_for(self, selector: Optional[str]) -> ToolResult:
        if not selector:
            return ToolResult.failure("missing `selector`")
        self._page.locator(selector).first.wait_for(state="visible")
        ev = self._snapshot("wait_for")
        return ToolResult.success(f"{selector} is visible", evidence=ev)

    def _read_page(self) -> ToolResult:
        """
        Return a trimmed representation of the current page: title, URL, and
        the visible text content (truncated). The LLM uses this to decide
        what to do next.
        """
        text = self._page.evaluate(
            "() => document.body ? document.body.innerText.replace(/\\s+\\n/g, '\\n') : ''"
        ) or ""
        text = text.strip()
        if len(text) > 3000:
            text = text[:3000] + "\n…[truncated]"
        ev = self._snapshot("read_page")
        ev["text_length"] = len(text)
        return ToolResult.success(
            observation=f"page `{ev['title']}` at {ev['url']}:\n{text}",
            data={"text": text, "url": ev["url"], "title": ev["title"]},
            evidence=ev,
        )

    def _read_elements(self, selector: Optional[str], limit: int) -> ToolResult:
        """
        Return the inner text + key attributes of each matching element.
        Useful for scraping rows out of a listing without needing full-page text.
        """
        if not selector:
            return ToolResult.failure("missing `selector`")
        items: List[dict] = self._page.evaluate(
            """
            ({selector, limit}) => Array.from(document.querySelectorAll(selector))
              .slice(0, limit)
              .map(el => ({
                text: (el.innerText || '').trim(),
                html: el.outerHTML.length > 400 ? el.outerHTML.slice(0,400) + '…' : el.outerHTML,
                attrs: Object.fromEntries(Array.from(el.attributes).map(a => [a.name, a.value])),
              }))
            """,
            {"selector": selector, "limit": limit},
        )
        ev = self._snapshot("read_elements")
        return ToolResult.success(
            observation=f"found {len(items)} element(s) matching {selector}",
            data={"elements": items, "selector": selector},
            evidence=ev,
        )
