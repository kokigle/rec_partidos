import threading
import time
import re
import requests
import warnings
from datetime import datetime, timezone
from dateutil import parser
from urllib.parse import urljoin, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
from seleniumwire import webdriver 
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources.*")

# ============ CONFIGURACIÓN OPTIMIZADA ============
MAX_WORKERS = 3
TIMEOUT_PAGINA = 40  # Aumentado
ESPERA_CLAPPR = 8    # Tiempo para que Clappr inicie
TIMEOUT_AUDITAR = 10
MAX_INTENTOS_AUDITAR = 2
# ==================================================

class StreamCandidato:
    def __init__(self, fuente, url, ua, referer, cookies=None):
        self.fuente = fuente
        self.url = url
        self.ua = ua
        self.referer = referer
        self.cookies = cookies or {}
        self.delay = 0
        self.score = -1
        self.bitrate = 0

def auditar_stream(candidato):
    """Auditoría con resolución de master playlist"""
    for intento in range(MAX_INTENTOS_AUDITAR):
        try:
            headers = {
                'User-Agent': candidato.ua, 
                'Referer': candidato.referer,
                'Origin': urlparse(candidato.referer).scheme + '://' + urlparse(candidato.referer).netloc,
                'Accept': '*/*',
                'Connection': 'keep-alive',
            }
            
            session = requests.Session()
            for k, v in candidato.cookies.items():
                session.cookies.set(k, v)
            
            resp = session.get(
                candidato.url, 
                headers=headers, 
                timeout=TIMEOUT_AUDITAR, 
                allow_redirects=True, 
                verify=False
            )
            
            if resp.status_code != 200:
                if intento < MAX_INTENTOS_AUDITAR - 1:
                    time.sleep(2)
                    continue
                candidato.score = -1
                return

            m3u8_txt = resp.text
            
            # Validación
            if len(m3u8_txt) < 50 or '#EXTM3U' not in m3u8_txt:
                if intento < MAX_INTENTOS_AUDITAR - 1: 
                    time.sleep(2)
                    continue
                candidato.score = -1
                return

            # Anti-DRM
            if any(p in m3u8_txt.lower() for p in ['method=sample-aes', 'widevine', 'playready']):
                candidato.score = -1
                return

            # CRÍTICO: Si es master playlist, resolver al playlist final
            if '#EXT-X-STREAM-INF' in m3u8_txt and 'tracks-v1a1' in m3u8_txt:
                # Es un master, extraer URL del primer variant
                for line in m3u8_txt.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Esta es la URL del playlist
                        playlist_url = urljoin(candidato.url, line)
                        print(f"      🔀 Resolviendo master → {playlist_url[:80]}...")
                        
                        # Actualizar URL y volver a auditar
                        candidato.url = playlist_url
                        return auditar_stream(candidato)  # Recursión para auditar el playlist final

            # Bitrate
            bitrate_match = re.search(r'BANDWIDTH[=:](\d+)', m3u8_txt, re.IGNORECASE)
            candidato.bitrate = int(bitrate_match.group(1)) / 1_000_000 if bitrate_match else 2.0

            # Delay
            match = re.search(r'#EXT-X-PROGRAM-DATE-TIME:(.*)', m3u8_txt)
            if match:
                try:
                    stream_time = parser.parse(match.group(1).strip())
                    diff = (datetime.now(timezone.utc) - stream_time).total_seconds()
                    candidato.delay = diff if 0 <= diff <= 300 else 10.0
                except: 
                    candidato.delay = 10.0
            else:
                candidato.delay = 8.0
            
            candidato.score = (100 - min(candidato.delay, 100)) + (candidato.bitrate * 5)
            
            if candidato.score > 0:
                print(f"   ✅ {candidato.fuente}: OK (Delay: {candidato.delay:.1f}s, Bitrate: {candidato.bitrate:.1f}Mbps)")
                print(f"      URL final: {candidato.url[:80]}...")
            return

        except requests.Timeout:
            if intento < MAX_INTENTOS_AUDITAR - 1: 
                time.sleep(2)
        except Exception as e:
            if intento < MAX_INTENTOS_AUDITAR - 1: 
                time.sleep(2)
    
    candidato.score = -1

def buscar_m3u8_en_trafico(driver, timeout=15):
    """Espera activa hasta que aparezca el m3u8"""
    inicio = time.time()
    urls_bloqueadas = [
        'ad.', 'doubleclick', 'analytics', 'favicon', 'google', 'facebook', 
        'twitter', 'pixel', 'track', '.jpg', '.png', '.css', '.js', 'captcha'
    ]
    
    master_backup = None
    
    while (time.time() - inicio) < timeout:
        try:
            for request in reversed(list(driver.requests)):
                if not request.response:
                    continue
                
                url = request.url.lower()
                
                # Filtrar basura
                if any(bloq in url for bloq in urls_bloqueadas):
                    continue
                
                # Buscar m3u8 (PRIORIZAR PLAYLIST FINAL)
                if '.m3u8' in url:
                    headers = dict(request.headers)
                    referer = headers.get('Referer', driver.current_url)
                    ua = headers.get('User-Agent', driver.execute_script("return navigator.userAgent;"))
                    
                    cookies = {}
                    try:
                        for cookie in driver.get_cookies():
                            cookies[cookie['name']] = cookie['value']
                    except:
                        pass
                    
                    # PRIORIDAD 1: Playlist final con tracks
                    if 'tracks-v1a1/mono' in url or '/mono.m3u8' in url:
                        print(f"      🎯 PLAYLIST FINAL: {request.url[:80]}...")
                        return request.url, referer, ua, cookies
                    
                    # PRIORIDAD 2: Cualquier otro m3u8 que NO sea index
                    elif '/index.m3u8' not in url and '.m3u8' in url:
                        print(f"      📺 Playlist detectado: {request.url[:80]}...")
                        # Seguir buscando por si hay uno mejor
                        time.sleep(0.5)
                        continue
                    
                    # PRIORIDAD 3: Master playlist (último recurso)
                    elif '/index.m3u8' in url:
                        if not master_backup:
                            print(f"      📋 Master detectado (guardando como respaldo)")
                            master_backup = (request.url, referer, ua, cookies)
        
        except:
            pass
        
        time.sleep(0.5)
    
    # Si no encontramos playlist final, devolver master
    if master_backup:
        print(f"      ⚠️ Usando master playlist (no se encontró playlist final)")
        return master_backup
    
    return None, None, None, {}

def intentar_reproducir_clappr(driver):
    """Scripts específicos para Clappr"""
    scripts = [
        # Forzar play en Clappr
        "if(window.player && window.player.play) { window.player.play(); }",
        "document.querySelectorAll('video').forEach(v => { v.muted=true; v.play().catch(e=>{}); });",
        # Click en overlay de Clappr
        "document.querySelector('[data-player]')?.click();",
        "document.querySelector('.player-poster')?.click();",
    ]
    
    for s in scripts:
        try: 
            driver.execute_script(s)
            time.sleep(0.5)
        except: 
            pass

def obtener_opciones_chrome():
    """Chrome optimizado"""
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--mute-audio')
    opts.add_argument('--disable-extensions')
    opts.add_argument('--ignore-certificate-errors')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")
    
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
    
    return opts

def extraer_de_web(nombre, url_web, resultados):
    """Extracción optimizada para streamtpcloud"""
    print(f"🕵️  Escaneando {nombre}...")
    
    driver = None
    try:
        seleniumwire_options = {
            'disable_encoding': True, 
            'connection_timeout': 30,
            'verify_ssl': False,
        }
        
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), 
            options=obtener_opciones_chrome(), 
            seleniumwire_options=seleniumwire_options
        )
        
        driver.set_page_load_timeout(TIMEOUT_PAGINA)
        
        # PASO 1: Cargar página
        try:
            del driver.requests
            driver.get(url_web)
        except TimeoutException:
            print(f"   ⏱️ {nombre}: Timeout carga (continuando)")
        
        # PASO 2: Esperar que Clappr cargue
        print(f"      ⏳ Esperando Clappr ({ESPERA_CLAPPR}s)...")
        time.sleep(ESPERA_CLAPPR)
        
        # PASO 3: Intentar reproducir
        intentar_reproducir_clappr(driver)
        time.sleep(2)
        
        # PASO 4: Esperar y buscar m3u8 activamente (aumentado a 25s para capturar playlist final)
        print(f"      🔍 Buscando playlist final en tráfico de red...")
        m3u8, ref, ua, cookies = buscar_m3u8_en_trafico(driver, timeout=25)
        
        # PASO 5: Si solo capturamos master, esperar a que aparezca el playlist final
        if m3u8 and '/index.m3u8' in m3u8.lower():
            print(f"      ⏳ Master detectado, esperando playlist final...")
            time.sleep(5)  # Dar tiempo a que Clappr cargue el playlist
            intentar_reproducir_clappr(driver)
            # Buscar específicamente el playlist final
            m3u8_final, ref2, ua2, cookies2 = buscar_m3u8_en_trafico(driver, timeout=15)
            if m3u8_final and 'tracks-v1a1' in m3u8_final.lower():
                print(f"      ✅ Playlist final capturado!")
                m3u8, ref, ua, cookies = m3u8_final, ref2, ua2, cookies2
            else:
                print(f"      ⚠️ Usando master (no se pudo capturar playlist final)")
        
        # PASO 6: Si no hay nada, reintento final
        if not m3u8:
            print(f"      🔄 No detectado, reintento final...")
            intentar_reproducir_clappr(driver)
            time.sleep(3)
            intentar_reproducir_clappr(driver)
            m3u8, ref, ua, cookies = buscar_m3u8_en_trafico(driver, timeout=10)
        
        # PASO 6: Validar y agregar
        if m3u8:
            if not m3u8.startswith('http'): 
                m3u8 = urljoin(driver.current_url, m3u8)
            
            # Filtrar archivos estáticos
            if any(ext in m3u8.lower() for ext in ['.mp4', '.avi', '.mkv']):
                print(f"   ⚠️ {nombre}: Descartado (archivo estático)")
            else:
                cand = StreamCandidato(nombre, m3u8, ua, ref or driver.current_url, cookies)
                auditar_stream(cand)
                if cand.score > 0: 
                    resultados.append(cand)
        else:
            print(f"   ❌ {nombre}: Sin stream detectado")

    except Exception as e:
        print(f"   💀 {nombre}: Error - {str(e)[:80]}")
    finally:
        if driver:
            try: 
                driver.quit()
            except: 
                pass
        time.sleep(1)

def obtener_mejores_streams(lista_fuentes):
    """Retorna streams válidos ordenados"""
    if not lista_fuentes: 
        return []
    
    candidatos = []
    total = len(lista_fuentes)
    
    print(f"\n{'='*70}")
    print(f"🔬 ANÁLISIS: {total} fuentes")
    print(f"{'='*70}")
    
    workers = min(MAX_WORKERS, total)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(extraer_de_web, nombre, url, candidatos) 
            for nombre, url in lista_fuentes
        ]
        
        for f in futures:
            try: 
                f.result(timeout=TIMEOUT_PAGINA + 30)
            except:
                pass

    validos = [c for c in candidatos if c.score > 0]
    validos.sort(key=lambda x: x.score, reverse=True)

    if validos:
        print(f"\n✅ {len(validos)} streams encontrados.")
        print(f"🏆 MEJOR: {validos[0].fuente} (Score: {validos[0].score:.1f})")
        return validos
    
    print("\n❌ NINGUNA FUENTE VÁLIDA")
    return []