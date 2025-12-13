import time
from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

opts = Options()
opts.add_argument('--headless=new')
opts.add_argument('--no-sandbox')
opts.add_argument('--disable-dev-shm-usage')

sw_options = {
    'disable_encoding': True,
    'verify_ssl': False,
}

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=opts,
    seleniumwire_options=sw_options
)

# CAMBIA ESTA URL POR UNA DE TUS FUENTES REALES
url = "https://streamtpcloud.com/global1.php?stream=fanatiz1"

print(f"🔍 Analizando: {url}")
driver.get(url)

# Esperar carga
time.sleep(8)

# Intentar reproducir
scripts = [
    "document.querySelectorAll('video').forEach(v => v.play());",
    "document.querySelector('button')?.click();",
]
for s in scripts:
    try: driver.execute_script(s)
    except: pass

time.sleep(5)

print("\n📡 REQUESTS CAPTURADAS:")
print("="*80)

for idx, req in enumerate(driver.requests):
    if any(x in req.url.lower() for x in ['m3u8', 'mpd', 'playlist', 'manifest']):
        print(f"\n🎯 REQUEST {idx}")
        print(f"URL: {req.url}")
        print(f"Method: {req.method}")
        print(f"Status: {req.response.status_code if req.response else 'No response'}")
        print(f"Headers: {dict(req.headers)}")
        if req.response:
            print(f"Response Headers: {dict(req.response.headers)}")

driver.quit()
print("\n✅ Análisis completado")