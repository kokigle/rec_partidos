import threading
import time
import re
import requests
import base64
import warnings
from datetime import datetime, timezone
from dateutil import parser
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from seleniumwire import webdriver 
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException

# Suprimir warnings molestos
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources.*")

# ============ CONFIGURACIÓN OPTIMIZADA ============
MAX_WORKERS = 5 
TIMEOUT_PAGINA = 25      
TIMEOUT_IFRAME = 15      
MAX_IFRAMES_NIVEL = 2    
ESPERA_CARGA_INICIAL = 3 
ESPERA_ENTRE_INTENTOS = 1
ESPERA_CIERRE_DRIVER = 0.5
TIMEOUT_AUDITAR = 5
MAX_INTENTOS_AUDITAR = 2
MODO_FAST_SCAN = True    
# ==================================================

class StreamCandidato:
    def __init__(self, fuente, url, ua, referer):
        self.fuente = fuente
        self.url = url
        self.ua = ua
        self.referer = referer
        self.delay = 0
        self.score = -1
        self.bitrate = 0

def auditar_stream(candidato):
    """Auditoría mejorada con detección de bitrate y validación estricta"""
    for intento in range(MAX_INTENTOS_AUDITAR):
        try:
            try:
                origin_netloc = urlparse(candidato.referer).netloc or 'localhost'
            except:
                origin_netloc = 'localhost'
            
            headers = {
                'User-Agent': candidato.ua, 
                'Referer': candidato.referer,
                'Origin': f'https://{origin_netloc}' if origin_netloc != 'localhost' else 'https://localhost'
            }
            
            resp = requests.get(candidato.url, headers=headers, timeout=TIMEOUT_AUDITAR, allow_redirects=True, verify=False)
            
            if resp.status_code != 200:
                if intento < MAX_INTENTOS_AUDITAR - 1:
                    time.sleep(ESPERA_ENTRE_INTENTOS)
                    continue
                candidato.score = -1
                return

            m3u8_txt = resp.text
            
            # --- FILTROS ANTI-DRM ---
            patrones_drm = ['METHOD=SAMPLE-AES', 'urn:mpeg:dash:profile:isoff', 'widevine', 'playready', 'fairplay']
            if any(p.lower() in m3u8_txt.lower() for p in patrones_drm):
                candidato.score = -1
                return

            # --- VALIDACIÓN STREAM ---
            if len(m3u8_txt) < 50 or '#EXTM3U' not in m3u8_txt:
                if intento < MAX_INTENTOS_AUDITAR - 1: continue
                candidato.score = -1
                return

            # --- BITRATE ---
            bitrate_match = re.search(r'BANDWIDTH[=:](\d+)', m3u8_txt, re.IGNORECASE)
            if bitrate_match:
                candidato.bitrate = int(bitrate_match.group(1)) / 1_000_000
            else:
                avg_match = re.search(r'AVERAGE-BANDWIDTH[=:](\d+)', m3u8_txt, re.IGNORECASE)
                if avg_match: candidato.bitrate = int(avg_match.group(1)) / 1_000_000

            # --- DELAY ---
            match = re.search(r'#EXT-X-PROGRAM-DATE-TIME:(.*)', m3u8_txt)
            if match:
                try:
                    stream_time = parser.parse(match.group(1).strip())
                    diff = (datetime.now(timezone.utc) - stream_time).total_seconds()
                    candidato.delay = diff if 0 <= diff <= 300 else 10.0
                except: candidato.delay = 10.0
            else:
                candidato.delay = 8.0
            
            # Score compuesto
            candidato.score = (100 - min(candidato.delay, 100)) + (candidato.bitrate * 5)
            
            if candidato.score > 0:
                print(f"   ✅ {candidato.fuente}: OK (Delay: {candidato.delay:.1f}s, Bitrate: {candidato.bitrate:.1f}Mbps)")
            return

        except Exception as e:
            if intento < MAX_INTENTOS_AUDITAR - 1: time.sleep(ESPERA_ENTRE_INTENTOS)
            else: candidato.score = -1

def buscar_m3u8_en_trafico(driver):
    """Búsqueda optimizada en tráfico de red"""
    urls_bloqueadas = ['ad.', 'doubleclick', 'analytics', 'favicon', 'google', 'facebook', 'twitter', 'pixel', 'track', '.jpg', '.png', '.css', '.js', 'captcha', 'font']
    extensiones_prioritarias = ['.m3u8', '.mpd', '/playlist.', '/manifest.', 'master.m3u8', 'chunks.m3u8']
    
    candidatos = []
    
    try:
        requests_list = list(reversed(driver.requests))
    except:
        return None, None, None

    for request in requests_list:
        if not request.response: continue
        
        url = request.url.lower()
        if any(bloq in url for bloq in urls_bloqueadas): continue
        
        if any(ext in url for ext in extensiones_prioritarias):
            if '.ts' in url or 'segment' in url or '.png' in url: continue

            headers = dict(request.headers)
            referer = headers.get('Referer', driver.current_url)
            ua = headers.get('User-Agent', driver.execute_script("return navigator.userAgent;"))
            
            prioridad = 3 if 'master' in url else (2 if 'playlist' in url else 1)
            candidatos.append((prioridad, request.url, referer, ua))
    
    candidatos.sort(reverse=True, key=lambda x: x[0])
    
    if candidatos:
        return candidatos[0][1], candidatos[0][2], candidatos[0][3]
    return None, None, None

def intentar_reproducir_fuerza_bruta(driver):
    scripts = [
        "document.querySelectorAll('video').forEach(v => { v.muted=true; v.play().catch(e=>{}); });",
        "document.querySelectorAll('.jw-display-icon-container').forEach(b => b.click());",
        "document.querySelectorAll('button[class*=play], div[class*=play], a[class*=play]').forEach(b => b.click());",
        "if(typeof Clappr !== 'undefined' && Clappr.players.length > 0) { Clappr.players[0].play(); }",
        "document.elementFromPoint(window.innerWidth/2, window.innerHeight/2)?.click();"
    ]
    for s in scripts:
        try: driver.execute_script(s)
        except: pass

def escanear_pagina_actual(driver, nivel=0):
    intentar_reproducir_fuerza_bruta(driver)
    time.sleep(ESPERA_CARGA_INICIAL if nivel == 0 else 2)
    return buscar_m3u8_en_trafico(driver)

def obtener_opciones_chrome():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--mute-audio')
    opts.add_argument('--disable-extensions')
    opts.add_argument('--ignore-certificate-errors')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    opts.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})
    opts.add_experimental_option('excludeSwitches', ['enable-logging'])
    return opts

def decodificar_url_oculta(url_web):
    try:
        parsed = urlparse(url_web)
        params = parse_qs(parsed.query)
        candidatos_b64 = []
        for key in ['r', 'embed', 'p', 'get']:
            if key in params: candidatos_b64.extend(params[key])
        
        for cand in candidatos_b64:
            try:
                decoded = base64.b64decode(cand).decode('utf-8')
                if decoded.startswith('http'):
                    print(f"   ⚡ URL Oculta detectada: {decoded}")
                    return decoded
            except: pass
    except: pass
    return None

def explorar_iframes_inteligente(driver):
    selectores_clave = [
        "iframe#embedIframe", "iframe#playerFrame", "iframe#streamIframe", 
        "iframe[name='iframe']", "iframe.embed-responsive-item", "iframe.aspect-video", 
        "div.video-container iframe", "iframe[src*='streamtp']", 
        "iframe[src*='pelotalibre']", "iframe[src*='futbollibre']"
    ]
    for selector in selectores_clave:
        try:
            iframe = driver.find_element(By.CSS_SELECTOR, selector)
            src = iframe.get_attribute("src")
            if src and "http" in src:
                print(f"   🎯 Iframe encontrado ({selector}): {src}")
                return src
        except: continue
    return None

def extraer_de_web(nombre, url_web, resultados):
    print(f"🕵️  Escaneando {nombre}...")
    url_directa = decodificar_url_oculta(url_web)
    url_destino = url_directa if url_directa else url_web

    driver = None
    try:
        seleniumwire_options = {'disable_encoding': True, 'connection_timeout': 20}
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=obtener_opciones_chrome(), seleniumwire_options=seleniumwire_options)
        driver.set_page_load_timeout(TIMEOUT_PAGINA)
        
        try:
            del driver.requests
            driver.get(url_destino)
        except TimeoutException:
            print(f"   ⏱️ {nombre}: Timeout carga (continuando)")
        
        m3u8, ref, ua = escanear_pagina_actual(driver, nivel=0)
        
        if not m3u8:
            iframe_src = explorar_iframes_inteligente(driver)
            if iframe_src:
                try:
                    del driver.requests
                    driver.get(iframe_src)
                    m3u8, ref, ua = escanear_pagina_actual(driver, nivel=1)
                except: pass
        
        if not m3u8:
            dominio = urlparse(url_web).netloc.lower()
            if "tvlibree" in dominio or "angulismo" in dominio:
                try:
                    botones = driver.find_elements(By.CSS_SELECTOR, "nav.server-links a, .option-select button")
                    for i, btn in enumerate(botones[:3]):
                        try:
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(2)
                            m3u8, ref, ua = escanear_pagina_actual(driver, nivel=1)
                            if m3u8: break
                        except: continue
                except: pass
            elif "rustico" in dominio:
                time.sleep(2)
                m3u8, ref, ua = escanear_pagina_actual(driver, nivel=1)

        if m3u8:
            if not m3u8.startswith('http'): 
                base = driver.current_url if driver else url_destino
                m3u8 = urljoin(base, m3u8)
            
            if '.mp4' not in m3u8 and '.avi' not in m3u8 and '.png' not in m3u8:
                cand = StreamCandidato(nombre, m3u8, ua, ref or driver.current_url)
                auditar_stream(cand)
                if cand.score > 0: resultados.append(cand)
            else:
                print(f"   ⚠️ {nombre}: Descartado (archivo estático)")
        else:
            print(f"   ❌ {nombre}: Sin stream")

    except Exception as e:
        print(f"   💀 {nombre}: Error - {str(e)[:50]}")
    finally:
        if driver:
            try: driver.quit()
            except: pass

def obtener_mejores_streams(lista_fuentes):
    """
    Retorna UNA LISTA de streams válidos, ordenados por score.
    """
    if not lista_fuentes: return []
    
    candidatos = []
    total = len(lista_fuentes)
    print(f"\n{'='*70}\n🔬 ANÁLISIS: {total} fuentes (Modo {'FAST' if MODO_FAST_SCAN else 'NORMAL'})\n{'='*70}")
    
    workers = min(MAX_WORKERS, total)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(extraer_de_web, nombre, url, candidatos) for nombre, url in lista_fuentes]
        for f in futures:
            try: f.result(timeout=TIMEOUT_PAGINA + 25)
            except: pass

    validos = [c for c in candidatos if c.score > 0]
    validos.sort(key=lambda x: x.score, reverse=True)

    if validos:
        print(f"\n✅ {len(validos)} streams encontrados.")
        print(f"🏆 MEJOR: {validos[0].fuente} (Score: {validos[0].score:.1f})")
        return validos
    
    print("\n❌ NINGUNA FUENTE VÁLIDA")
    return []