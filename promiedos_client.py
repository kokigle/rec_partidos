import requests
import json
from bs4 import BeautifulSoup

import requests
import json
import re
from datetime import datetime
from bs4 import BeautifulSoup

def obtener_metadata_partido(url_promiedos):
    """
    Entra al link de Promiedos y extrae:
    1. Hora del partido.
    2. Equipos (Nombre archivo).
    3. Canales de TV que lo pasan.
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url_promiedos, headers=headers, timeout=10)
        if resp.status_code != 200: return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # EL TESORO: Extraemos el JSON oculto de Next.js
        script_data = soup.find("script", id="__NEXT_DATA__")
        if not script_data: 
            print("❌ No se encontró la data oculta de Promiedos.")
            return None
        
        data = json.loads(script_data.string)
        
        # Navegamos la estructura JSON (basado en el HTML que me pasaste)
        try:
            game_props = data['props']['pageProps']['initialData']['game']
            
            # 1. Extraer Equipos
            team_1 = game_props['teams'][0]['short_name']
            team_2 = game_props['teams'][1]['short_name']
            nombre_archivo = f"{team_1}_vs_{team_2}".replace(" ", "_")
            
            # 2. Extraer Hora (Formato Promiedos: "08-12-2025 17:00")
            start_str = game_props['start_time']
            dt_obj = datetime.strptime(start_str, "%d-%m-%Y %H:%M")
            hora_inicio = dt_obj.strftime("%H:%M") # Formato para nuestro sistema
            hora_inicio = "00:01"
            
            # 3. Extraer Canales de TV
            # Buscamos en 'game_info' donde name sea 'Arg TV'
            canales_detectados = []
            if 'game_info' in game_props:
                for info in game_props['game_info']:
                    if info['name'] == 'Arg TV':
                        # Ejemplo value: "ESPN Premium, TNT Sports Premium"
                        canales_raw = info['value']
                        canales_detectados = [c.strip() for c in canales_raw.split(',')]
                        break
            
            return {
                "nombre": nombre_archivo,
                "hora": hora_inicio,
                "canales": canales_detectados,
                "estado_obj": game_props # Guardamos esto para chequear estado luego
            }

        except KeyError as e:
            print(f"❌ Error parseando JSON de Promiedos: {e}")
            return None

    except Exception as e:
        print(f"❌ Error conectando a Promiedos: {e}")
        return None

def obtener_estado_partido(url_promiedos):
    """
    Consulta el estado exacto del partido usando el link específico de Promiedos.
    Retorna: 'PREVIA', 'JUGANDO_1T', 'ENTRETIEMPO', 'JUGANDO_2T', 'FINAL', 'ERROR'
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url_promiedos, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        script_data = soup.find("script", id="__NEXT_DATA__")
        data = json.loads(script_data.string)
        game_data = data['props']['pageProps']['initialData']['game']
        estado_texto = game_data['status']['name'].lower()
        
        if "primer" in estado_texto or "1t" in estado_texto: return "JUGANDO_1T"
        if "entretiempo" in estado_texto: return "ENTRETIEMPO"
        if "segundo" in estado_texto or "2t" in estado_texto: return "JUGANDO_2T"
        if "final" in estado_texto: return "FINAL"
        return "PREVIA"
    except:
        return "ERROR"