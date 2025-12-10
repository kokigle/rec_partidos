import threading
import time
import re
import requests
import base64
import warnings
from datetime import datetime, timezone
from dateutil import parser
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from seleniumwire import webdriver 
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException

# Suprimir warnings molestos
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources.*")

# ============ CONFIGURACIÓN ============
MAX_WORKERS = 2  # CRÍTICO: Reducido a 2 para evitar saturación de file descriptors
TIMEOUT_PAGINA = 40  # Timeout por página
TIMEOUT_IFRAME = 30  # Timeout por iframe
MAX_IFRAMES_NIVEL = 2  # Máximo de iframes por nivel
ESPERA_CARGA_INICIAL = 6  # Segundos de espera inicial
ESPERA_ENTRE_INTENTOS = 3  # Segundos entre reintentos
ESPERA_CIERRE_DRIVER = 2  # Espera después de cerrar driver
# ========================================

class StreamCandidato:
    def __init__(self, fuente, url, ua, referer):
        self.fuente = fuente
        self.url = url
        self.ua = ua
        self.referer = referer
        self.delay = 0
        self.score = -1
        self.bitrate = 0  # Nuevo: para comparar calidad

def auditar_stream(candidato):
    """
    Auditoría mejorada con detección de bitrate y validación más estricta
    """
    try:
        headers = {
            'User-Agent': candidato.ua, 
            'Referer': candidato.referer,
            'Origin': 'https://' + urlparse(candidato.referer).netloc
        }
        
        resp = requests.get(candidato.url, headers=headers, timeout=8, allow_redirects=True)
        
        if resp.status_code != 200:
            candidato.score = -1
            return

        m3u8_txt = resp.text
        
        # --- FILTROS ANTI-DRM MEJORADOS ---
        patrones_drm = [
            'METHOD=SAMPLE-AES',
            'urn:mpeg:dash:profile:isoff',
            'widevine',
            'playready',
            'fairplay',
        ]
        
        for patron in patrones_drm:
            if patron.lower() in m3u8_txt.lower():
                print(f"   ⚠️ {candidato.fuente}: DRM detectado ({patron})")
                candidato.score = -1
                return

        # --- VALIDAR QUE SEA UN STREAM REAL ---
        if len(m3u8_txt) < 50:
            candidato.score = -1
            return
        
        if '#EXTM3U' not in m3u8_txt:
            candidato.score = -1
            return

        # --- EXTRAER BITRATE (CORREGIDO) ---
        # Buscar BANDWIDTH en bytes/seg y convertir a Mbps
        bitrate_match = re.search(r'BANDWIDTH[=:](\d+)', m3u8_txt, re.IGNORECASE)
        if bitrate_match:
            bandwidth_bps = int(bitrate_match.group(1))
            candidato.bitrate = bandwidth_bps / 1_000_000  # Convertir a Mbps
        
        # Si no hay BANDWIDTH, buscar AVERAGE-BANDWIDTH
        if candidato.bitrate == 0:
            avg_match = re.search(r'AVERAGE-BANDWIDTH[=:](\d+)', m3u8_txt, re.IGNORECASE)
            if avg_match:
                candidato.bitrate = int(avg_match.group(1)) / 1_000_000

        # --- CALCULAR DELAY ---
        match = re.search(r'#EXT-X-PROGRAM-DATE-TIME:(.*)', m3u8_txt)
        
        if match:
            try:
                stream_time = parser.parse(match.group(1).strip())
                now = datetime.now(timezone.utc)
                diff = (now - stream_time).total_seconds()
                
                # Validar delay razonable
                if 0 <= diff <= 300:
                    candidato.delay = diff
                else:
                    candidato.delay = 10.0
            except:
                candidato.delay = 10.0
        else:
            candidato.delay = 8.0
        
        # --- SCORING COMPUESTO ---
        candidato.score = candidato.delay + (candidato.bitrate * 0.5)
        
        if candidato.score > 0:
            print(f"   ✅ {candidato.fuente}: OK (Delay: {candidato.delay:.1f}s, Bitrate: {candidato.bitrate:.1f}Mbps, Score: {candidato.score:.1f})")

    except requests.Timeout:
        print(f"   ⏱️ {candidato.fuente}: Timeout en auditoría")
        candidato.score = -1
    except Exception as e:
        print(f"   ❌ {candidato.fuente}: Error auditoría - {str(e)[:40]}")
        candidato.score = -1

def buscar_m3u8_en_trafico(driver):
    """Búsqueda optimizada con filtros mejorados"""
    # Filtrar URLs irrelevantes
    urls_bloqueadas = ['ad.', 'doubleclick', 'analytics', 'favicon', 'google', 
                       'facebook', 'twitter', 'pixel', 'track', '.jpg', '.png', 
                       '.gif', '.css', '.js', 'captcha']
    
    # Priorizar URLs con extensiones de streaming
    extensiones_prioritarias = ['.m3u8', '.mpd', '/playlist.', '/manifest.']
    
    candidatos = []
    
    for request in reversed(driver.requests):
        if not request.response:
            continue
        
        url = request.url.lower()
        
        # Filtrar basura
        if any(bloq in url for bloq in urls_bloqueadas):
            continue
        
        # Buscar extensiones de streaming
        if any(ext in url for ext in extensiones_prioritarias):
            headers = dict(request.headers)
            referer = headers.get('Referer', driver.current_url)
            ua = headers.get('User-Agent', driver.execute_script("return navigator.userAgent;"))
            
            # Priorizar master playlists
            prioridad = 2 if 'master' in url or 'playlist' in url else 1
            candidatos.append((prioridad, request.url, referer, ua))
    
    # Ordenar por prioridad
    candidatos.sort(reverse=True, key=lambda x: x[0])
    
    if candidatos:
        return candidatos[0][1], candidatos[0][2], candidatos[0][3]
    
    return None, None, None

def intentar_reproducir_fuerza_bruta(driver):
    """Activación inteligente de reproductores"""
    try:
        # 1. Buscar y clickear botones de reproducción
        scripts_play = [
            # Clic en el centro (típico overlay)
            "document.elementFromPoint(window.innerWidth/2, window.innerHeight/2)?.click();",
            
            # Buscar botones de play
            "document.querySelectorAll('button').forEach(b => { if(b.innerText.includes('Play') || b.className.includes('play')) b.click(); });",
            
            # Activar todos los videos
            "document.querySelectorAll('video').forEach(v => { v.muted = true; v.play().catch(()=>{}); });",
            
            # Buscar iframes y activarlos
            "document.querySelectorAll('iframe').forEach(f => { try { f.contentWindow.postMessage('play', '*'); } catch(e){} });",
        ]
        
        for script in scripts_play:
            driver.execute_script(script)
            time.sleep(0.3)
            
    except Exception as e:
        pass

def escanear_pagina_actual(driver, nivel=0):
    """Escaneo mejorado con nivel de profundidad"""
    intentar_reproducir_fuerza_bruta(driver)
    
    # Esperar proporcional al nivel (más tiempo en página principal)
    tiempo_espera = ESPERA_CARGA_INICIAL if nivel == 0 else max(2, 4 - nivel)
    time.sleep(tiempo_espera)
    
    return buscar_m3u8_en_trafico(driver)

def obtener_opciones_chrome():
    """Configuración optimizada de Chrome"""
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--mute-audio')
    opts.add_argument('--disable-extensions')
    opts.add_argument('--disable-software-rasterizer')
    opts.add_argument('--disable-webgl')
    opts.add_argument('--ignore-certificate-errors')
    opts.add_argument('--disable-popup-blocking')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    
    # CRÍTICO: Limitar conexiones para evitar saturación de file descriptors
    opts.add_argument('--max-connections-per-host=6')
    opts.add_argument('--disable-features=NetworkService')
    
    opts.page_load_strategy = 'eager'
    
    # User agent realista
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    
    # Deshabilitar imágenes para acelerar carga
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    opts.add_experimental_option("prefs", prefs)
    
    # Suprimir logs
    opts.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    return opts

def explorar_iframes_recursivo(driver, nivel, max_nivel=2):
    """Exploración recursiva limitada de iframes"""
    if nivel >= max_nivel:
        return None, None, None
    
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")[:MAX_IFRAMES_NIVEL]
        srcs = [f.get_attribute("src") for f in iframes if f.get_attribute("src")]
        
        for src in srcs:
            if not src or "http" not in src or any(x in src for x in ["google", "facebook", "twitter"]):
                continue
            
            try:
                del driver.requests
                driver.set_page_load_timeout(TIMEOUT_IFRAME)
                driver.get(src)
                
                m3u8, ref, ua = escanear_pagina_actual(driver, nivel)
                
                if m3u8:
                    return m3u8, ref, ua
                
                # Recursión
                resultado = explorar_iframes_recursivo(driver, nivel + 1, max_nivel)
                if resultado[0]:
                    return resultado
                    
            except TimeoutException:
                continue
            except Exception:
                continue
    
    except Exception:
        pass
    
    return None, None, None

def extraer_de_web(nombre, url_web, resultados):
    """Extractor optimizado y robusto"""
    print(f"🕵️  Escaneando {nombre}...")
    
    driver = None
    try:
        # Configuración de seleniumwire para limitar conexiones
        seleniumwire_options = {
            'disable_encoding': True,  # Desactivar decodificación automática
            'max_workers': 1,  # Un worker por driver
            'connection_timeout': 30,  # Timeout de conexión
        }
        
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=obtener_opciones_chrome(),
            seleniumwire_options=seleniumwire_options
        )
        driver.set_page_load_timeout(TIMEOUT_PAGINA)
        
        # --- NIVEL 0: PÁGINA PRINCIPAL ---
        intento = 0
        max_intentos = 2
        
        while intento < max_intentos:
            try:
                del driver.requests
                driver.get(url_web)
                break  # Carga exitosa
            except TimeoutException:
                intento += 1
                if intento < max_intentos:
                    print(f"   ⏱️ {nombre}: Timeout intento {intento}, reintentando...")
                    time.sleep(ESPERA_ENTRE_INTENTOS)
                else:
                    print(f"   ⏱️ {nombre}: Timeout en carga inicial tras {max_intentos} intentos")
                    return
            except Exception as e:
                print(f"   ❌ {nombre}: Error en carga - {str(e)[:30]}")
                return
        
        m3u8, ref, ua = escanear_pagina_actual(driver, nivel=0)
        
        # --- EXPLORACIÓN DE IFRAMES ---
        if not m3u8:
            m3u8, ref, ua = explorar_iframes_recursivo(driver, nivel=0, max_nivel=2)
        
        # --- LÓGICAS ESPECÍFICAS POR SITIO ---
        if not m3u8:
            dominio = urlparse(url_web).netloc.lower()
            
            # TVLibree: Probar botones de servidor
            if "tvlibree" in dominio:
                try:
                    botones = driver.find_elements(By.CSS_SELECTOR, "nav.server-links a")[:3]
                    for btn in botones:
                        try:
                            del driver.requests
                            driver.execute_script("arguments[0].click();", btn)
                            m3u8, ref, ua = escanear_pagina_actual(driver, nivel=1)
                            if m3u8:
                                break
                        except:
                            continue
                except:
                    pass
            
            # RusticoTV: Esperar más tiempo
            elif "rusticotv" in dominio or "rustico" in dominio:
                time.sleep(3)
                m3u8, ref, ua = escanear_pagina_actual(driver, nivel=1)
        
        # --- VALIDACIÓN Y RESULTADO ---
        if m3u8:
            # Corregir URLs relativas
            if not m3u8.startswith('http'):
                m3u8 = urljoin(ref or url_web, m3u8)
            
            # Filtrar archivos estáticos
            if '.mp4' not in m3u8 and '.avi' not in m3u8:
                cand = StreamCandidato(nombre, m3u8, ua, ref or url_web)
                auditar_stream(cand)
                
                if cand.score > 0:
                    resultados.append(cand)
                else:
                    print(f"   ⚠️ {nombre}: Stream encontrado pero falló auditoría")
            else:
                print(f"   ⚠️ {nombre}: Archivo estático detectado (.mp4)")
        else:
            print(f"   ❌ {nombre}: Sin stream tras escaneo completo")
    
    except WebDriverException as e:
        print(f"   💀 {nombre}: Error Selenium - {str(e)[:50]}")
    except Exception as e:
        print(f"   💀 {nombre}: Error crítico - {str(e)[:50]}")
    finally:
        if driver:
            try:
                # Cierre limpio y ordenado
                driver.quit()
                # Espera crítica para liberar file descriptors
                time.sleep(ESPERA_CIERRE_DRIVER)
            except Exception as e:
                # Silenciar errores de cierre
                pass

def obtener_mejor_stream(lista_fuentes):
    """
    Selección inteligente del mejor stream con procesamiento paralelo
    """
    if not lista_fuentes:
        print("❌ No hay fuentes para analizar")
        return None
    
    candidatos = []
    total = len(lista_fuentes)
    
    print(f"\n{'='*70}")
    print(f"🔬 ANÁLISIS DE FUENTES: {total} opciones disponibles")
    print(f"{'='*70}")
    
    # Procesar en lotes PEQUEÑOS para no saturar file descriptors
    BATCH_SIZE = 2  # CRÍTICO: Solo 2 por lote
    
    for i in range(0, total, BATCH_SIZE):
        lote = lista_fuentes[i:i+BATCH_SIZE]
        print(f"\n📦 Procesando lote {i//BATCH_SIZE + 1}/{(total + BATCH_SIZE - 1)//BATCH_SIZE} ({len(lote)} fuentes)...")
        
        # Procesar cada fuente del lote de forma SECUENCIAL para evitar saturación
        for nombre, url in lote:
            try:
                extraer_de_web(nombre, url, candidatos)
            except Exception as e:
                print(f"   ⚠️ Error procesando {nombre}: {str(e)[:40]}")
        
        # Pausa LARGA entre lotes para liberar recursos
        if i + BATCH_SIZE < total:
            time.sleep(4)  # 4 segundos entre lotes
    
    # --- SELECCIÓN FINAL ---
    candidatos_validos = [c for c in candidatos if c.score > 0]
    
    print(f"\n{'='*70}")
    
    if not candidatos_validos:
        print("❌ NINGUNA FUENTE DISPONIBLE")
        print(f"   Se analizaron {total} fuentes, todas fallaron o tienen DRM")
        print(f"{'='*70}\n")
        return None
    
    # Ordenar por score (delay + bitrate)
    candidatos_validos.sort(key=lambda x: x.score, reverse=True)
    
    ganador = candidatos_validos[0]
    
    print(f"✅ RESULTADO: {len(candidatos_validos)}/{total} fuentes disponibles")
    print(f"\n🏆 STREAM SELECCIONADO:")
    print(f"   • Fuente: {ganador.fuente}")
    print(f"   • Delay: {ganador.delay:.1f}s")
    print(f"   • Bitrate: {ganador.bitrate:.1f}Mbps")
    print(f"   • Score: {ganador.score:.2f}")
    
    if len(candidatos_validos) > 1:
        print(f"\n📊 Alternativas disponibles:")
        for i, c in enumerate(candidatos_validos[1:4], 2):
            print(f"   {i}. {c.fuente} (Delay: {c.delay:.1f}s, Score: {c.score:.2f})")
    
    print(f"{'='*70}\n")
    
    return ganador