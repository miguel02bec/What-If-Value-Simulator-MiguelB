from playwright.sync_api import sync_playwright
import json, re, time

# Cargar cookies
with open("ws_cookies.json", "r") as f:
    cookies = json.load(f)

# Adaptar formato para Playwright
pw_cookies = []
for c in cookies:
    cookie = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c["path"],
        "secure": c.get("secure", False),
        "httpOnly": c.get("httpOnly", False),
    }
    if c.get("expirationDate"):
        cookie["expires"] = int(c["expirationDate"])
    pw_cookies.append(cookie)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080}
    )
    
    # Inyectar cookies antes de navegar
    context.add_cookies(pw_cookies)
    
    page = context.new_page()
    page.goto("https://www.whoscored.com/matches/1974942/live/europe-champions-league-2025-2026-bayern-munich-real-madrid")
    time.sleep(8)
    
    source = page.content()
    
    pattern = r"matchCentreData\s*=\s*(\{.*?\});"
    m = re.search(pattern, source, re.DOTALL)
    if m:
        print("✅ Datos encontrados")
        data = json.loads(m.group(1))
        print(list(data.keys()))
    else:
        print("❌ No encontrado")
        with open("ws_debug.html", "w", encoding="utf-8") as f:
            f.write(source)
        print("HTML guardado en ws_debug.html")
    
    browser.close()