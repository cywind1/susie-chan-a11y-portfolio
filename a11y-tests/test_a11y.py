from selenium import webdriver
from selenium.webdriver.common.by import By
from axe_selenium_python import Axe

URL = "http://localhost:3000"

options = webdriver.ChromeOptions()
options.add_argument("--headless")

driver = webdriver.Chrome(options=options)

try:
    print(f"\n🔍 Scanning: {URL}\n")
    driver.get(URL)

    axe = Axe(driver)
    axe.inject()
    results = axe.run()

    violations = results["violations"]

    if not violations:
        print("✅ No accessibility violations found!")
    else:
        print(f"❌ Found {len(violations)} violation(s):\n")
        for v in violations:
            print(f"{'='*60}")
            print(f"  Rule:    {v['id']}")
            print(f"  Impact:  {v['impact']}")
            print(f"  Desc:    {v['description']}")
            print(f"  Fix:     {v['helpUrl']}")
            print(f"  Elements affected: {len(v['nodes'])}\n")

            for i, node in enumerate(v['nodes']):
                print(f"  Element {i+1}:")
                print(f"    HTML:    {node['html']}")
                print(f"    Target:  {node['target']}")
                print(f"    Summary: {node['failureSummary']}")

                # ← NEW: find element and get its location info
                try:
                    selector = node['target'][0]
                    element = driver.find_element(By.CSS_SELECTOR, selector)

                    # Get source location via JS
                    tag      = element.tag_name
                    location = driver.execute_script("""
                        var el = arguments[0];
                        return {
                            id:        el.id || '(no id)',
                            className: el.className || '(no class)',
                            tagName:   el.tagName,
                            outerHTML: el.outerHTML.substring(0, 150)
                        };
                    """, element)

                    print(f"    Tag:     <{location['tagName'].lower()}>")
                    print(f"    Classes: {location['className'][:80]}")

                    # Hint which component file to look in
                    html_snippet = node['html'].lower()
                    classes      = location['className'].lower()

                    if any(x in html_snippet for x in ['header', 'nav', 'logo', 'aria-label="susie']):
                        hint = "👉 Look in: components/Header.tsx"
                    elif any(x in html_snippet for x in ['footer', 'copyright', '©', 'mb-2']):
                        hint = "👉 Look in: components/Footer.tsx"
                    elif any(x in html_snippet for x in ['tags/', 'tag']):
                        hint = "👉 Look in: components/Tag.tsx or layouts/"
                    elif any(x in html_snippet for x in ['sign up', 'subscribe', 'newsletter']):
                        hint = "👉 Look in: components/NewsletterForm.tsx"
                    elif any(x in html_snippet for x in ['read more', 'blog/']):
                        hint = "👉 Look in: layouts/ListLayout.tsx or ListLayoutWithTags.tsx"
                    elif 'no-scrollbar' in classes:
                        hint = "👉 Look in: components/Header.tsx"
                    elif 'sr-only' in html_snippet:
                        hint = "👉 Look in: components/Footer.tsx"
                    else:
                        hint = "👉 Look in: components/ or layouts/"

                    print(f"    {hint}")

                except Exception as e:
                    print(f"    ⚠️  Could not locate element: {e}")

                print()

finally:
    driver.quit()
    print("Done.")