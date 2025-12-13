#!/usr/bin/env python3
"""
angulismo_scraper.py - Módulo para Sistema Maestro v9.0
"""

import time
import json
import re
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- CONFIGURACIÓN ---
MODO_VISIBLE = False  # False para producción (Headless)
URL_ANGULISMO = "https://angulismotv-dnh.pages.dev"
UA_DEFAULT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class AngulismoStream:
    """
    Clase HÍBRIDA: Funciona como Objeto y como Tupla.
    Compatible con smart_selector (que espera tuplas) y sistema_maestro (que espera objetos).
    """
    def __init__(self, nombre, url, referer=None):
        self.name = nombre        # Para logs
        self.url = url            # URL para ffmpeg
        self.ua = UA_DEFAULT      # User Agent
        self.referer = referer or URL_ANGULISMO
        self.cookies = {}         # Cookies si fueran necesarias

    # --- MAGIA PARA COMPATIBILIDAD CON SMART_SELECTOR ---
    def __iter__(self):
        """Permite hacer: nombre, url = stream_obj"""
        yield self.name
        yield self.url

    def __getitem__(self, index):
        """Permite acceder como stream_obj[0] o stream_obj[1]"""
        if index == 0: return self.name
        if index == 1: return self.url
        raise IndexError("AngulismoStream solo tiene índices 0 (nombre) y 1 (url)")

    def __repr__(self):
        return f"<AngulismoStream: {self.name}>"

# ==========================================
# 1. FUNCIÓN PROMIEDOS (Metadata)
# ==========================================
def extraer_nombre_partido_de_promiedos(url_promiedos):
    """Extrae el nombre del partido (ej: 'Metz vs PSG') de Promiedos."""
    if not url_promiedos or "promiedos.com.ar" not in url_promiedos:
        return None

    headers = {'User-Agent': UA_DEFAULT}
    try:
        resp = requests.get(url_promiedos, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Método JSON Next.js
        script_data = soup.find("script", id="__NEXT_DATA__")
        if script_data:
            try:
                data = json.loads(script_data.string)
                game = data['props']['pageProps']['initialData']['game']
                return f"{game['teams'][0]['name']} vs {game['teams'][1]['name']}"
            except: pass

        # Método Título (Backup)
        if soup.title and (" Vs " in soup.title.string or " vs " in soup.title.string):
            return soup.title.string.split("-")[0].replace(" Vs ", " vs ").strip()

    except Exception as e:
        print(f"⚠️ Error scraper Promiedos: {e}")
    
    return None

# ==========================================
# 2. AYUDAS SELENIUM
# ==========================================
def cerrar_modal_bizarro(driver):
    try:
        checkbox = WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.ID, "entendidoCheckbox"))
        )
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(0.5)
        boton = driver.find_element(By.XPATH, "//button[contains(text(), 'Aceptar')]")
        driver.execute_script("arguments[0].click();", boton)
        time.sleep(1)
    except: pass

def intentar_extraer_url_real(item_element, driver):
    """
    Intenta sacar la URL del stream del elemento <li>.
    """
    try:
        # 1. Buscar en el HTML si hay un link directo (regex simple)
        html = item_element.get_attribute('outerHTML')
        match = re.search(r"['\"](https?://.*?\.(m3u8|mp4|php).*?)['\"]", html)
        if match:
            return match.group(1)
        
        # 2. Si no hay link evidente, devolvemos la URL base para que sistema maestro
        # intente manejarlo o al menos no falle vacío.
        return URL_ANGULISMO 
    except:
        return URL_ANGULISMO

# ==========================================
# 3. FUNCIÓN PRINCIPAL (Interfaz Sistema Maestro)
# ==========================================
def obtener_streams_para_partido(url_promiedos, preferir_canales=None):
    """
    Función principal llamada por el Sistema Maestro.
    Devuelve una lista de objetos AngulismoStream.
    """
    print(f"[{time.strftime('%H:%M:%S')}] 🔍 Scraper: Analizando {url_promiedos}...")
    
    nombre_partido = extraer_nombre_partido_de_promiedos(url_promiedos)
    if not nombre_partido:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Scraper: No se pudo obtener nombre de Promiedos.")
        return []

    print(f"[{time.strftime('%H:%M:%S')}] 🎯 Scraper: Buscando '{nombre_partido}' en AngulismoTV")

    options = Options()
    if not MODO_VISIBLE:
        options.add_argument('--headless')
    options.add_argument('--disable-blink-features=AutomationControlled') 
    options.add_argument('--start-maximized')
    options.add_argument('--log-level=3')

    driver = webdriver.Chrome(options=options)
    streams_encontrados = []
    
    try:
        driver.get(URL_ANGULISMO)
        cerrar_modal_bizarro(driver)

        # Entrar al Iframe Agenda
        try:
            WebDriverWait(driver, 10).until(
                EC.frame_to_be_available_and_switch_to_it((By.ID, "agendaFrame"))
            )
        except:
            print(f"[{time.strftime('%H:%M:%S')}] ❌ Scraper: No se encontró la agenda.")
            driver.quit()
            return []

        # Esperar partidos
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CLASS_NAME, "match-card"))
            )
        except:
            driver.quit()
            return []

        cards = driver.find_elements(By.CLASS_NAME, "match-card")
        
        # Lógica de búsqueda
        partido_lower = nombre_partido.lower()
        keywords = [p for p in partido_lower.split() if len(p) > 2 and p != "vs"]

        for card in cards:
            try:
                txt = card.text.lower()
                # Coincidencia: Nombre exacto o al menos 2 palabras clave
                match = (partido_lower in txt) or (len(keywords) > 0 and sum(1 for k in keywords if k in txt) >= 2)
                
                if match:
                    nombre_real = card.find_element(By.CLASS_NAME, "teams").text
                    
                    # Expandir opciones
                    try:
                        icon = card.find_element(By.CLASS_NAME, "expand-icon")
                        driver.execute_script("arguments[0].click();", icon)
                        time.sleep(0.5)
                    except: pass

                    # Recolectar canales
                    items = card.find_elements(By.CSS_SELECTOR, "ul.channel-menu li.channel-item")
                    
                    for item in items:
                        texto_opcion = item.text.replace("\n", " ").strip()
                        if not texto_opcion:
                            # Intentar sacar texto de spans internos
                            texto_opcion = " ".join([s.text for s in item.find_elements(By.TAG_NAME, "span")])
                        
                        if not texto_opcion:
                            texto_opcion = "Opción Desconocida"

                        # Crear objeto Stream
                        url_stream = intentar_extraer_url_real(item, driver)
                        
                        nombre_final = f"{nombre_real} - {texto_opcion}"
                        
                        stream_obj = AngulismoStream(
                            nombre=nombre_final,
                            url=url_stream,
                            referer=URL_ANGULISMO
                        )
                        streams_encontrados.append(stream_obj)
                        
            except Exception:
                continue

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Error en Selenium: {e}")
    finally:
        driver.quit()

    # Filtro de preferencias (Básico)
    if streams_encontrados and preferir_canales:
        streams_encontrados.sort(
            key=lambda s: any(p.lower() in s.name.lower() for p in preferir_canales),
            reverse=True
        )

    return streams_encontrados

# Test rápido
if __name__ == "__main__":
    MODO_VISIBLE = True
    url_test = input("URL Promiedos: ")
    if url_test:
        res = obtener_streams_para_partido(url_test)
        print(f"Encontrados: {len(res)}")
        for r in res:
            # Probamos que funcione como objeto y como tupla
            print(f"Obj: {r.name} | Tpl: {r[0]}")