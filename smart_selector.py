import threading
import time
import re
import requests
import base64
from datetime import datetime, timezone
from dateutil import parser
from urllib.parse import urljoin

from seleniumwire import webdriver 
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException

class StreamCandidato:
    def __init__(self, fuente, url, ua, referer):
        self.fuente = fuente
        self.url = url
        self.ua = ua
        self.referer = referer
        self.delay = 0 # Inicializamos en 0
        self.score = -1 # Inicializamos en negativo

def auditar_stream(candidato):
    """
    Verifica status 200, busca DRM y calcula delay.
    """
    try:
        headers = {
            'User-Agent': candidato.ua, 
            'Referer': candidato.referer,
            'Origin': 'https://' + candidato.referer.split('/')[2] if 'http' in candidato.referer else candidato.referer
        }
        resp = requests.get(candidato.url, headers=headers, timeout=5)
        
        if resp.status_code != 200:
            candidato.score = -1
            return

        m3u8_txt = resp.text
        
        # --- NUEVO: FILTRO ANTI-DRM ---
        # Si vemos indicios de DRM fuerte, matamos el candidato.
        # METHOD=AES-128 suele ser pasable por ffmpeg, pero SAMPLE-AES o cenc suelen ser DRM.
        if 'METHOD=SAMPLE-AES' in m3u8_txt or 'urn:mpeg:dash:profile:isoff-on-demand' in m3u8_txt:
            print(f"   ⚠️ {candidato.fuente}: Detectado DRM/Encriptación fuerte. Descartado.")
            candidato.score = -1
            return

        # Buscamos la marca de tiempo
        match = re.search(r'#EXT-X-PROGRAM-DATE-TIME:(.*)', m3u8_txt)
        
        if match:
            try:
                stream_time = parser.parse(match.group(1).strip())
                now = datetime.now(timezone.utc)
                diff = (now - stream_time).total_seconds()
                
                # Un delay negativo o absurdo (mayor a 5 min) es error
                if diff < 0 or diff > 300: 
                    candidato.delay = 5.0 # Castigo por dato raro
                else:
                    candidato.delay = diff
            except:
                candidato.delay = 5.0 # Castigo por fallo en parseo
        else:
            # Si no tiene fecha, es un stream "tonto". 
            # Le damos poco delay para que pierda contra los que sí tienen fecha confirmada,
            # salvo que sean la única opción.
            candidato.delay = 5.0 
            
        # El score es el delay. 
        # Ganará el que tenga MÁS delay comprobado (ej: 45s), 
        # pero los fallidos tendrán solo 5s, perdiendo contra los buenos.
        candidato.score = candidato.delay

    except Exception as e:
        candidato.score = -1

def buscar_m3u8_en_trafico(driver):
    # Buscamos en las ultimas peticiones primero (LIFO)
    for request in reversed(driver.requests):
        if request.response:
            if ('.m3u8' in request.url or '.mpd' in request.url) and \
               not any(x in request.url for x in ['ad.', 'doubleclick', 'analytics', 'favicon', 'google']):
                
                url_final = request.url
                headers = dict(request.headers)
                referer = headers.get('Referer', driver.current_url)
                ua = headers.get('User-Agent', driver.execute_script("return navigator.userAgent;"))
                return url_final, referer, ua
    return None, None, None

def intentar_reproducir_fuerza_bruta(driver):
    """Intenta despertar reproductores dormidos y saltar overlays"""
    try:
        # 1. Clic al centro (Overlay típico de RojaDirecta/FutbolLibre)
        driver.execute_script("try{ document.elementFromPoint(window.innerWidth/2, window.innerHeight/2).click(); }catch(e){}")
        time.sleep(0.5)
        # 2. Buscar video tags y dar play forzado
        driver.execute_script("document.querySelectorAll('video').forEach(v => v.play())")
    except: pass

def escanear_pagina_actual(driver):
    """Helper para buscar m3u8 en la url actual"""
    intentar_reproducir_fuerza_bruta(driver)
    time.sleep(2)
    return buscar_m3u8_en_trafico(driver)

def extraer_de_web(nombre, url_web, resultados):
    print(f"🕵️  Escarbando {nombre}...")
    
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--mute-audio')
    opts.add_argument('--ignore-certificate-errors')
    opts.add_argument("--disable-popup-blocking")
    opts.page_load_strategy = 'eager' 
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = None
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        driver.set_page_load_timeout(25)
        
        # --- NIVEL 0: PÁGINA PRINCIPAL ---
        try:
            del driver.requests
            driver.get(url_web)
        except TimeoutException: pass
        
        time.sleep(3)
        m3u8, ref, ua = escanear_pagina_actual(driver)

        # --- NIVEL 1: IFRAMES DIRECTOS ---
        if not m3u8:
            iframes_n1 = driver.find_elements(By.TAG_NAME, "iframe")
            # Recolectamos SRCs primero para no perder referencia al navegar
            srcs_n1 = [f.get_attribute("src") for f in iframes_n1 if f.get_attribute("src")]
            
            for src1 in srcs_n1:
                if "http" in src1 and "google" not in src1:
                    try:
                        del driver.requests
                        driver.get(src1) # Navegamos al iframe 1
                        time.sleep(3)
                        
                        m3u8, ref, ua = escanear_pagina_actual(driver)
                        
                        # --- NIVEL 2: IFRAMES ANIDADOS ---
                        if not m3u8:
                            iframes_n2 = driver.find_elements(By.TAG_NAME, "iframe")
                            srcs_n2 = [f.get_attribute("src") for f in iframes_n2 if f.get_attribute("src")]
                            
                            for src2 in srcs_n2:
                                if "http" in src2 and "google" not in src2:
                                    del driver.requests
                                    driver.get(src2)
                                    time.sleep(3)
                                    m3u8, ref, ua = escanear_pagina_actual(driver)
                                    if m3u8: break
                        
                        if m3u8: break
                    except: continue

        # --- LOGICA EXTRA (Botones específicos tipo TVLibre) ---
        if not m3u8 and "tvlibree" in url_web:
             botones = driver.find_elements(By.CSS_SELECTOR, "nav.server-links a")
             for btn in botones:
                 try:
                     del driver.requests
                     driver.execute_script("arguments[0].click();", btn)
                     time.sleep(3)
                     m3u8, ref, ua = escanear_pagina_actual(driver)
                     if m3u8: break
                 except: continue

        # --- RESULTADO FINAL ---
        if m3u8:
            # Corregir URLs relativas
            if not m3u8.startswith('http'):
                m3u8 = urljoin(ref, m3u8)
            
            # Filtramos mp4 estáticos, queremos streams
            if '.mp4' not in m3u8:
                cand = StreamCandidato(nombre, m3u8, ua, ref)
                auditar_stream(cand)
                
                # Si el score es > 0 significa que conectó (status 200)
                if cand.score > 0:
                    print(f"   ✅ {nombre}: OK (Delay: {cand.delay:.1f}s)")
                    resultados.append(cand)
                else:
                    print(f"   ⚠️ {nombre}: Link offline (Status != 200).")
        else:
            print(f"   ❌ {nombre}: Sin señal tras escaneo profundo.")

    except Exception as e:
        print(f"   💀 Error {nombre}: {str(e)[:50]}")
            
    finally:
        if driver: 
            try: driver.quit()
            except: pass

def obtener_mejor_stream(lista_fuentes):
    candidatos = []
    
    print(f"\n🔬 ANALIZANDO {len(lista_fuentes)} OPCIONES (Buscando MAYOR delay seguro)...")
    
    for i in range(0, len(lista_fuentes), 3):
        lote = lista_fuentes[i:i+3]
        hilos_lote = []
        for nombre, url in lote:
            t = threading.Thread(target=extraer_de_web, args=(nombre, url, candidatos))
            hilos_lote.append(t)
            t.start()
        
        for t in hilos_lote: t.join()
    
    # Filtramos los que tienen score <= 0
    candidatos_validos = [c for c in candidatos if c.score > 0]

    if not candidatos_validos: 
        print("   ❌ Ningún proveedor funcionó o todos tienen DRM.")
        return None
    
    # Ordenamos: Mayor delay primero
    ganador = sorted(candidatos_validos, key=lambda x: x.score, reverse=True)[0]
    
    print(f"   🏆 GANADOR: {ganador.fuente} (Delay: {ganador.delay:.1f}s)\n")
    return ganador