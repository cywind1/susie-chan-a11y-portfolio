import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================
LOCAL_URL = "http://localhost:3000"
DEPLOYED_URL = "https://susie-chan-a11y-portfolio.vercel.app"
AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
DEFAULT_WCAG = "wcag21aa"
DEFAULT_MAX_PAGES = 50

# Choose which WCAG rules Axe should run.
WCAG_PRESETS = {
    "wcag2a": ["wcag2a"],
    "wcag2aa": ["wcag2a", "wcag2aa"],
    "wcag21aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    "wcag22aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"],
}


@dataclass(frozen=True)
class AuditConfig:
    url: str
    wcag_version: str
    wcag_tags: list[str]
    max_pages: int
    headed: bool
    output: Path | None


# ============================================================
# CLI
# ============================================================
def parse_args() -> AuditConfig:
    parser = argparse.ArgumentParser(
        description="Run a multi-page axe-core accessibility audit with Playwright."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="local",
        help="local, deployed, or a custom URL. Default: local",
    )
    parser.add_argument(
        "wcag",
        nargs="?",
        default=DEFAULT_WCAG,
        choices=WCAG_PRESETS.keys(),
        help=f"WCAG preset. Default: {DEFAULT_WCAG}",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Maximum internal pages to crawl. Default: {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while scanning.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path for full axe results.",
    )
    args = parser.parse_args()

    target_map = {
        "local": LOCAL_URL,
        "deployed": DEPLOYED_URL,
    }
    url = target_map.get(args.target, args.target)

    return AuditConfig(
        url=url,
        wcag_version=args.wcag,
        wcag_tags=WCAG_PRESETS[args.wcag],
        max_pages=args.max_pages,
        headed=args.headed,
        output=args.output,
    )


# ============================================================
# HINT ENGINE
# Layer 1: Generic, works on any website
# Layer 2: Project specific, edit per project
# ============================================================
def get_hint(html_snippet: str, classes: str) -> str:
    html_snippet = html_snippet.lower()
    classes = classes.lower()

    if any(x in html_snippet for x in ["<header", "site-header"]):
        return "Likely in: header component"
    if any(x in html_snippet for x in ["<footer", "copyright", "©"]):
        return "Likely in: footer component"
    if any(x in html_snippet for x in ["<button", "submit", "btn"]):
        return "Likely in: button or form component"
    if any(x in html_snippet for x in ["<img", "image", "photo"]):
        return "Likely in: image component or content"
    if any(x in html_snippet for x in ["<input", "<form", "<label"]):
        return "Likely in: form component"
    if any(x in html_snippet for x in ["<html", "lang", "<title"]):
        return "Likely in: root layout file"
    if any(x in html_snippet for x in ["<a ", "href"]):
        return "Likely in: link or navigation component"
    if "nav" in html_snippet or "nav" in classes:
        return "Likely in: navigation component"

    if "newsletter" in html_snippet or "sign up" in html_snippet:
        return "Look in: components/NewsletterForm.tsx"
    if "no-scrollbar" in classes:
        return "Look in: components/Header.tsx"
    if "tags/" in html_snippet:
        return "Look in: components/Tag.tsx"

    return "Inspect element in DevTools to locate"


def get_search_term(html: str) -> str:
    marker = 'class="'
    if marker in html:
        start = html.find(marker) + len(marker)
        end = html.find('"', start)
        classes = html[start:end].split()
        if classes:
            return classes[0]

    return html[:60].strip()


# ============================================================
# AXE / PLAYWRIGHT HELPERS
# ============================================================
def load_axe_source() -> str:
    response = requests.get(AXE_CDN, timeout=20)
    response.raise_for_status()
    return response.text


def wait_for_page(page: Page, url: str) -> None:
    # Open the page and wait until the initial HTML document is loaded.
    # This is faster and more reliable than waiting for every asset to finish.
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    # Wait until the <body> exists in the DOM.
    # "attached" is important because some pages may briefly hide <body>.
    page.wait_for_selector("body", state="attached", timeout=10_000)

    try:
        # Give deployed/production pages a short chance to finish network activity.
        # Local dev servers may keep connections open, so this must be optional.
        page.wait_for_load_state("networkidle", timeout=3_000)
    except PlaywrightTimeoutError:
        pass

    try:
        # Wait briefly for links because the crawler discovers subpages from <a href>.
        # Some valid pages have no links, so the scan should continue if none appear.
        page.wait_for_selector("a[href]", state="attached", timeout=3_000)
    except PlaywrightTimeoutError:
        pass

    # Small buffer for React/Next.js hydration before axe scans or links are collected.
    page.wait_for_timeout(500)


def normalize_internal_url(current_url: str, href: str, start_domain: str) -> str | None:
    href, _ = urldefrag(href)
    absolute_url = urljoin(current_url, href)
    parsed = urlparse(absolute_url)

    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.netloc != start_domain:
        return None

    return absolute_url


# ============================================================
# INTERNAL LINK CRAWLER
# ============================================================
def discover_pages(page: Page, start_url: str, max_pages: int) -> list[str]:
    visited: set[str] = set()
    queued: set[str] = {start_url}
    queue: deque[str] = deque([start_url])
    start_domain = urlparse(start_url).netloc

    while queue and len(visited) < max_pages:
        current_url = queue.popleft()
        if current_url in visited:
            continue

        try:
            wait_for_page(page, current_url)
        except Exception as exc:
            print(f"  WARN: Could not crawl {current_url}: {exc}")
            continue

        visited.add(current_url)

        for href in page.locator("a[href]").evaluate_all("els => els.map(a => a.href)"):
            next_url = normalize_internal_url(current_url, href, start_domain)
            if next_url and next_url not in visited and next_url not in queued:
                queued.add(next_url)
                queue.append(next_url)

    return sorted(visited)


# ============================================================
# MAIN AUDIT
# ============================================================
def run_audit(page: Page, axe_source: str, url: str, wcag_version: str, wcag_tags: list[str]) -> dict[str, Any]:
    print(f"\n{'=' * 60}")
    print("  Accessibility Audit")
    print(f"  URL: {url}")
    print(f"  WCAG: {wcag_version.upper()}")
    print(f"{'=' * 60}\n")

    wait_for_page(page, url)
    page.add_script_tag(content=axe_source)

    results = page.evaluate(
        """
        async (wcagTags) => await axe.run(document, {
            runOnly: {
                type: "tag",
                values: wcagTags
            }
        })
        """,
        wcag_tags,
    )

    violations = results.get("violations", [])

    if not violations:
        print("  PASS: No accessibility violations found!")
    else:
        print(f"  FAIL: Found {len(violations)} violation(s):\n")

    for violation in violations:
        print_violation(page, violation)

    print_summary(url, violations)
    print("  Audit complete.\n")

    return results


def print_violation(page: Page, violation: dict[str, Any]) -> None:
    print(f"{'=' * 60}")
    print(f"  Rule:     {violation['id']}")
    print(f"  Impact:   {violation.get('impact', 'unknown').upper()}")
    print(f"  Desc:     {violation['description']}")
    print(f"  Fix:      {violation['helpUrl']}")
    print(f"  Affected: {len(violation['nodes'])} element(s)\n")

    for index, node in enumerate(violation["nodes"], start=1):
        html = node.get("html", "")
        print(f"  Element {index}:")
        print(f"    HTML:    {html[:120]}")
        print(f"    Summary: {node.get('failureSummary', '').strip()}")
        print_debug_tip(page, node)
        print()


def print_debug_tip(page: Page, node: dict[str, Any]) -> None:
    target = node.get("target") or []
    selector = target[0] if target and isinstance(target[0], str) else None

    if not selector:
        print("    INFO: Complex selector; inspect this element manually.")
        return

    try:
        location = page.locator(selector).first.evaluate(   # ← removed ()
            """
            el => ({
                id: el.id || "(no id)",
                className: typeof el.className === "string" ? el.className : "(no class)",
                tagName: el.tagName
            })
            """
        )
    except Exception as exc:
        print(f"    WARN: Could not locate element: {exc}")
        return

    html = node.get("html", "")
    classes = location["className"]
    hint = get_hint(html, classes)
    search_term = get_search_term(html)

    print(f"    Tag:     <{location['tagName'].lower()}>")
    print(f"    Classes: {classes[:80]}")
    print(f"    Hint:    {hint}")
    print(f"    Search:  VS Code Ctrl+Shift+F -> {search_term}")


def print_summary(url: str, violations: list[dict[str, Any]]) -> None:
    print(f"{'=' * 60}")
    print(f"  Summary - {url}")
    print(f"{'=' * 60}")
    print(f"  Total violations: {len(violations)}")

    if not violations:
        return

    impact_counts: dict[str, int] = {}
    for violation in violations:
        impact = violation.get("impact", "unknown").upper()
        impact_counts[impact] = impact_counts.get(impact, 0) + 1

    for impact, count in sorted(impact_counts.items()):
        print(f"  {impact}: {count}")


# ============================================================
# RUN
# ============================================================
def main() -> int:
    config = parse_args()
    axe_source = load_axe_source()
    all_results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not config.headed)
        page = browser.new_page()

        try:
            pages = discover_pages(page, config.url, config.max_pages)

            print(f"\nDiscovered {len(pages)} page(s):")
            for discovered_page in pages:
                print(f"  - {discovered_page}")

            for discovered_page in pages:
                result = run_audit(page, axe_source, discovered_page, config.wcag_version, config.wcag_tags)
                all_results.append({"url": discovered_page, "results": result})
        finally:
            browser.close()

    total_violations = sum(len(item["results"].get("violations", [])) for item in all_results)

    if config.output:
        config.output.parent.mkdir(parents=True, exist_ok=True)
        config.output.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
        print(f"Saved JSON report to: {config.output}")

    return 1 if total_violations else 0


if __name__ == "__main__":
    sys.exit(main())
