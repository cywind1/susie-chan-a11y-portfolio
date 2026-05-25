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
    project_root: Path | None
    debug: bool


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
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Path to source root to auto-detect which file contains the bug.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print debug info for class index matching.",
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
        project_root=args.project_root,
        debug=args.debug,
    )


# ============================================================
# CLASS INDEX
# Scans source files and maps className strings to file paths.
# Handles: className="...", className={`...`}, className={'...'}
# ============================================================
def build_class_index(project_root: Path) -> dict[str, str]:
    """Scan source files and map className strings to their relative file paths."""
    import re

    index = {}
    extensions = ("*.tsx", "*.jsx", "*.ts", "*.js")
    skip_dirs = {"node_modules", ".next", ".git", "dist", ".yarn"}

    # Matches className="...", className={`...`}, className={'...'}, className={"..."}
    pattern = re.compile(r'className=["\'{`]([^"\'{`\n]+)["\'{`]')

    for ext in extensions:
        for filepath in project_root.rglob(ext):
            if any(p in filepath.parts for p in skip_dirs):
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(content):
                class_str = match.group(1).strip()
                if class_str:
                    rel = str(filepath.relative_to(project_root))
                    index[class_str] = rel

    return index


# ============================================================
# HINT ENGINE
# Layer 1: Auto-detect from source index (most accurate)
# Layer 2: Generic HTML/class pattern matching
# Layer 3: Project-specific fallbacks
# ============================================================
def get_hint(
    html_snippet: str,
    classes: str,
    class_index: dict[str, str] | None = None,
    debug: bool = False,
) -> str:
    # Layer 1: Auto-detect from indexed source files
    if class_index:
        classes_set = set(classes.strip().split())

        if debug:
            print(f"\n  DEBUG: DOM classes ({len(classes_set)}): {classes_set}")
            print(f"  DEBUG: Searching {len(class_index)} index entries...")

        best_match: tuple[int, str] | None = None
        for key, filepath in class_index.items():
            key_set = set(key.strip().split())
            overlap = len(classes_set & key_set)
            if overlap >= 2:
                if best_match is None or overlap > best_match[0]:
                    best_match = (overlap, filepath)

        if debug:
            if best_match:
                print(f"  DEBUG: Best match ({best_match[0]} classes overlap) -> {best_match[1]}")
            else:
                print("  DEBUG: No index match (overlap < 2). Falling back to pattern matching.")
                # Show closest misses to help diagnose
                scored = []
                for key, filepath in class_index.items():
                    key_set = set(key.strip().split())
                    overlap = len(classes_set & key_set)
                    if overlap > 0:
                        scored.append((overlap, filepath, key[:60]))
                for score, fp, key in sorted(scored, reverse=True)[:5]:
                    print(f"  DEBUG:   overlap={score} [{fp}] {key}")

        if best_match:
            return f"Look in: {best_match[1]}"

    # Layer 2: Generic pattern matching
    html_lower = html_snippet.lower()
    classes_lower = classes.lower()

    if any(x in html_lower for x in ["<header", "site-header"]):
        return "Likely in: header component"
    if any(x in html_lower for x in ["<footer", "copyright", "©"]):
        return "Likely in: footer component"
    if any(x in html_lower for x in ["<button", "submit", "btn"]):
        return "Likely in: button or form component"
    if any(x in html_lower for x in ["<img", "image", "photo"]):
        return "Likely in: image component or content"
    if any(x in html_lower for x in ["<input", "<form", "<label"]):
        return "Likely in: form component"
    if any(x in html_lower for x in ["<html", "lang", "<title"]):
        return "Likely in: root layout file"
    if any(x in html_lower for x in ["<a ", "href"]):
        return "Likely in: link or navigation component"
    if "nav" in html_lower or "nav" in classes_lower:
        return "Likely in: navigation component"

    # Layer 3: Project-specific fallbacks
    if "newsletter" in html_lower or "sign up" in html_lower:
        return "Look in: components/NewsletterForm.tsx"
    if "no-scrollbar" in classes_lower:
        return "Look in: components/Header.tsx"
    if "tags/" in html_lower:
        return "Look in: components/Tag.tsx"

    return "Inspect element in DevTools to locate"


def get_search_term(html: str) -> str:
    marker = 'class="'
    if marker in html:
        start = html.find(marker) + len(marker)
        end = html.find('"', start)
        classes = html[start:end].split()
        if len(classes) >= 2:
            return " ".join(classes[:3])
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
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("body", state="attached", timeout=10_000)

    try:
        page.wait_for_load_state("networkidle", timeout=3_000)
    except PlaywrightTimeoutError:
        pass

    try:
        page.wait_for_selector("a[href]", state="attached", timeout=3_000)
    except PlaywrightTimeoutError:
        pass

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
def run_audit(
    page: Page,
    axe_source: str,
    url: str,
    wcag_version: str,
    wcag_tags: list[str],
    class_index: dict[str, str] | None = None,
    debug: bool = False,
) -> dict[str, Any]:
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
            runOnly: { type: "tag", values: wcagTags }
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
        print_violation(page, violation, class_index, debug)

    print_summary(url, violations)
    print("  Audit complete.\n")

    return results


def print_violation(
    page: Page,
    violation: dict[str, Any],
    class_index: dict[str, str] | None = None,
    debug: bool = False,
) -> None:
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
        print_debug_tip(page, node, class_index, debug)
        print()


def print_debug_tip(
    page: Page,
    node: dict[str, Any],
    class_index: dict[str, str] | None = None,
    debug: bool = False,
) -> None:
    target = node.get("target") or []
    selector = target[0] if target and isinstance(target[0], str) else None

    if not selector:
        print("    INFO: Complex selector; inspect this element manually.")
        return

    try:
        location = page.locator(selector).first.evaluate(
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
    hint = get_hint(html, classes, class_index, debug)
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

            class_index = None
            if config.project_root:
                class_index = build_class_index(config.project_root)
                print(f"\n  Indexed {len(class_index)} className entries from {config.project_root}")

                if config.debug:
                    print("\n  DEBUG: Sample index entries:")
                    for k, v in list(class_index.items())[:10]:
                        print(f"    [{v}] {k[:80]}")
                    print()

            for discovered_page in pages:
                result = run_audit(
                    page,
                    axe_source,
                    discovered_page,
                    config.wcag_version,
                    config.wcag_tags,
                    class_index,
                    config.debug,
                )
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