import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from axe_selenium_python import Axe

# Helps build full URLs from relative links, inspect URL parts, and remove page fragments like "#section"
from urllib.parse import urljoin, urlparse, urldefrag
# Provides an efficient first-in, first-out queue for crawling pages
from collections import deque

# ============================================================
# CONFIGURATION
# ============================================================
LOCAL_URL    = "http://localhost:3000"
DEPLOYED_URL = "https://susie-chan-a11y-portfolio.vercel.app"

# Usage:
#   python test_a11y.py          → tests LOCAL
#   python test_a11y.py local    → tests LOCAL
#   python test_a11y.py deployed → tests DEPLOYED
#   python test_a11y.py https:// → tests any custom URL

arg = sys.argv[1] if len(sys.argv) > 1 else "local"

if arg == "local":
    URL = LOCAL_URL
elif arg == "deployed":
    URL = DEPLOYED_URL
else:
    URL = arg  # custom URL passed directly


# ============================================================
# HINT ENGINE
# Layer 1 — Generic (works on ANY website)
# Layer 2 — Project specific (edit per project)
# ============================================================
def get_hint(html_snippet, classes):
    """
    Returns a human-readable hint about where to find
    the failing element in source code.
    Based on HTML semantics — works on any website.
    """

    # --- Layer 1: Generic semantic hints ---
    if any(x in html_snippet for x in ['<header', 'site-header']):
        return "👉 Likely in: header component"

    elif any(x in html_snippet for x in ['<footer', 'copyright', '©']):
        return "👉 Likely in: footer component"

    elif any(x in html_snippet for x in ['<button', 'submit', 'btn']):
        return "👉 Likely in: button or form component"

    elif any(x in html_snippet for x in ['<img', 'image', 'photo']):
        return "👉 Likely in: image component or content"

    elif any(x in html_snippet for x in ['<input', '<form', '<label']):
        return "👉 Likely in: form component"

    elif any(x in html_snippet for x in ['<html', 'lang', '<title']):
        return "👉 Likely in: root layout file"

    elif any(x in html_snippet for x in ['<a ', 'href']):
        return "👉 Likely in: link or navigation component"

    elif 'nav' in html_snippet or 'nav' in classes:
        return "👉 Likely in: navigation component"

    # --- Layer 2: Project specific (optional) ---
    elif 'newsletter' in html_snippet or 'sign up' in html_snippet:
        return "👉 Look in: components/NewsletterForm.tsx"
    elif 'no-scrollbar' in classes:
        return "👉 Look in: components/Header.tsx"
    elif 'tags/' in html_snippet:
        return "👉 Look in: components/Tag.tsx"

    else:
        return "👉 Inspect element in DevTools to locate"


def get_search_term(html):
    """
    Extracts a clean search term for VS Code global search.
    Use Ctrl+Shift+F in VS Code and paste this term.
    """
    if 'class="' in html:
        start   = html.find('class="') + 7
        end     = html.find('"', start)
        classes = html[start:end].split()[0]
        return classes

    return html[:60].strip()


# ============================================================
# BROWSER SETUP
# ============================================================
def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)

        
# ============================================================
# INTERNAL LINK CRAWLER
# ============================================================

def discover_pages(start_url, max_pages=50):
    """
    Crawl same-domain links starting from start_url.
    Returns a sorted list of internal pages to audit.
    """
    driver = create_driver()
    visited = set()
    queue = deque([start_url])

    start_domain = urlparse(start_url).netloc

    try:
        while queue and len(visited) < max_pages:
            current_url = queue.popleft()

            if current_url in visited:
                continue

            driver.get(current_url)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            visited.add(current_url)

            links = driver.find_elements(By.TAG_NAME, "a")

            for link in links:
                href = link.get_attribute("href")

                if not href:
                    continue

                # Remove fragments like /blog/#section
                href, _ = urldefrag(href)

                # Correctly resolve relative links like /blog/
                absolute_url = urljoin(current_url, href)

                parsed = urlparse(absolute_url)

                # Skip external domains, mailto, tel, javascript, etc.
                if parsed.scheme not in ("http", "https"):
                    continue

                if parsed.netloc != start_domain:
                    continue

                if absolute_url not in visited:
                    queue.append(absolute_url)

        return sorted(visited)

    finally:
        driver.quit()


# ============================================================
# MAIN AUDIT
# ============================================================
def run_audit(url):
    driver = create_driver()

    try:
        print(f"\n{'='*60}")
        print(f"  🔍 Accessibility Audit")
        print(f"  URL: {url}")
        print(f"{'='*60}\n")

        driver.get(url)

        # Wait for page to fully load before scanning
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        axe = Axe(driver)
        axe.inject()
        results = axe.run()

        violations = results["violations"]

        if not violations:
            print("  ✅ No accessibility violations found!")
        else:
            print(f"  ❌ Found {len(violations)} violation(s):\n")

            for v in violations:
                print(f"{'='*60}")
                print(f"  Rule:     {v['id']}")
                print(f"  Impact:   {v['impact'].upper()}")
                print(f"  Desc:     {v['description']}")
                print(f"  Fix:      {v['helpUrl']}")
                print(f"  Affected: {len(v['nodes'])} element(s)\n")

                for i, node in enumerate(v['nodes']):
                    print(f"  Element {i+1}:")
                    print(f"    HTML:    {node['html'][:120]}")
                    print(f"    Summary: {node['failureSummary']}")

                    try:
                        selector = node['target'][0]
                        if not isinstance(selector, str):
                            raise ValueError("Complex selector — skipping")

                        element  = driver.find_element(By.CSS_SELECTOR, selector)
                        location = driver.execute_script("""
                            var el = arguments[0];
                            return {
                                id:        el.id || '(no id)',
                                className: el.className || '(no class)',
                                tagName:   el.tagName,
                            };
                        """, element)

                        print(f"    Tag:     <{location['tagName'].lower()}>")
                        print(f"    Classes: {location['className'][:80]}")

                        html_snippet = node['html'].lower()
                        classes      = location['className'].lower()
                        hint         = get_hint(html_snippet, classes)
                        search_term  = get_search_term(node['html'])

                        print(f"    {hint}")
                        print(f"    🔎 VS Code search (Ctrl+Shift+F): {search_term}")

                    except ValueError as ve:
                        print(f"    ℹ️  {ve}")
                    except Exception as e:
                        print(f"    ⚠️  Could not locate element: {e}")

                    print()

        # --------------------------------------------------------
        # SUMMARY
        # --------------------------------------------------------
        print(f"{'='*60}")
        print(f"  📊 Summary — {url}")
        print(f"{'='*60}")
        print(f"  Total violations: {len(violations)}")

        if violations:
            impact_counts = {}
            for v in violations:
                impact = v['impact'].upper()
                impact_counts[impact] = impact_counts.get(impact, 0) + 1

            emoji_map = {
                "CRITICAL": "🔴",
                "SERIOUS":  "🟠",
                "MODERATE": "🟡",
                "MINOR":    "🔵"
            }
            for impact, count in sorted(impact_counts.items()):
                emoji = emoji_map.get(impact, "⚪")
                print(f"  {emoji} {impact}: {count}")

        print()

    finally:
        driver.quit()
        print("  ✅ Audit complete.\n")


# ============================================================
# RUN
# ============================================================
pages = discover_pages(URL)

print(f"\nDiscovered {len(pages)} page(s):")
for page in pages:
    print(f"  - {page}")

for page in pages:
    run_audit(page)