from selenium import webdriver
from axe_selenium_python import Axe

# Your local blog URL
URL = "http://localhost:3000"

# Start Chrome
options = webdriver.ChromeOptions()
options.add_argument("--headless")

driver = webdriver.Chrome(options=options)

try:
    print(f"\n🔍 Scanning: {URL}")
    driver.get(URL)

    # Inject axe-core and run the scan
    axe = Axe(driver)
    axe.inject()
    results = axe.run()

    violations = results["violations"]

    if not violations:
        print("✅ No accessibility violations found!")
    else:
        print(f"❌ Found {len(violations)} violation(s):\n")
        for v in violations:
            print(f"  Rule:    {v['id']}")
            print(f"  Impact:  {v['impact']}")
            print(f"  Desc:    {v['description']}")
            print(f"  Fix:     {v['helpUrl']}")
            print(f"  Elements affected: {len(v['nodes'])}")
            print()

finally:
    driver.quit()
    print("Done.")