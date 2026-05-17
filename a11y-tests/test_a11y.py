from selenium import webdriver
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

            # ← NEW: show each failing element
            for i, node in enumerate(v['nodes']):
                print(f"  Element {i+1}:")
                print(f"    HTML:    {node['html']}")
                print(f"    Target:  {node['target']}")
                print(f"    Summary: {node['failureSummary']}")
                print()

finally:
    driver.quit()
    print("Done.")