#!/usr/bin/env python3
"""
sofascore_client.py - Cliente de respaldo para metadata y estado de partidos
Backup cuando Promiedos falla o está caído
"""

import requests
import json
import re
from datetime import datetime
import time

# Cache para evitar spam
_cache_metadata = {}
_cache_estado = {}
CACHE_TTL = 30  # 30 segundos

def extraer_id_partido(url_sofascore):
    """
    Extrae ID del partido de URL de SofaScore
    Ejemplo: https://www.sofascore.com/es-la/football/match/liverpool-brighton/FsU#id:14025198
    → 14025198
    """
    match = re.search(r'#id:(\d+)', url_sofascore)
    if match:
        return match.group(1)
    
    # Intentar formato alternativo
    match = re.search(r'/match/[^/]+/(\w+)', url_sofascore)
    if match:
        # Esto es el slug, necesitamos convertirlo
        return None
    
    return None

def obtener_metadata_partido(url_sofascore, reintentos=3):
    """
    Obtiene metadata del partido desde SofaScore API
    Retorna: dict con nombre, hora, canales (vacío), estado_obj
    """
    if url_sofascore in _cache_metadata:
        return _cache_metadata[url_sofascore]
    
    match_id = extraer_id_partido(url_sofascore)
    if not match_id:
        print("❌ No se pudo extraer ID de SofaScore")
        return None
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'es-ES,es;q=0.9',
    }
    
    api_url = f"https://api.sofascore.com/api/v1/event/{match_id}"
    
    for intento in range(reintentos):
        try:
            resp = requests.get(api_url, headers=headers, timeout=10)
            
            if resp.status_code != 200:
                if intento < reintentos - 1:
                    time.sleep(2)
                    continue
                return None
            
            data = resp.json()
            
            if 'event' not in data:
                return None
            
            event = data['event']
            
            # Extraer equipos
            team_1 = event.get('homeTeam', {}).get('shortName', 'Team1')
            team_2 = event.get('awayTeam', {}).get('shortName', 'Team2')
            nombre_archivo = f"{team_1}_vs_{team_2}".replace(" ", "_").replace("/", "-")
            
            # Extraer hora
            start_timestamp = event.get('startTimestamp', 0)
            dt_obj = datetime.fromtimestamp(start_timestamp)
            hora_inicio = dt_obj.strftime("%H:%M")
            
            # SofaScore no provee canales de TV directamente
            # Retornamos lista vacía
            canales_detectados = []
            
            metadata = {
                "nombre": nombre_archivo,
                "hora": hora_inicio,
                "canales": canales_detectados,
                "estado_obj": event,
                "url": url_sofascore
            }
            
            _cache_metadata[url_sofascore] = metadata
            return metadata
            
        except requests.Timeout:
            if intento < reintentos - 1:
                time.sleep(3)
        except Exception as e:
            print(f"❌ Error obteniendo metadata SofaScore: {str(e)[:80]}")
            if intento < reintentos - 1:
                time.sleep(3)
    
    return None

def obtener_estado_partido(url_sofascore, usar_cache=True):
    """
    Consulta el estado del partido
    Retorna: 'PREVIA', 'JUGANDO_1T', 'ENTRETIEMPO', 'JUGANDO_2T', 'FINAL', 'ERROR'
    """
    if usar_cache and url_sofascore in _cache_estado:
        cache_entry = _cache_estado[url_sofascore]
        if time.time() - cache_entry['timestamp'] < CACHE_TTL:
            return cache_entry['estado']
    
    match_id = extraer_id_partido(url_sofascore)
    if not match_id:
        return "ERROR"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    
    api_url = f"https://api.sofascore.com/api/v1/event/{match_id}"
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            return "ERROR"
        
        data = resp.json()
        event = data.get('event', {})
        
        # Extraer estado
        status_code = event.get('status', {}).get('code', 0)
        status_type = event.get('status', {}).get('type', '')
        
        # Mapear estados de SofaScore
        # Códigos: 0=not started, 6=1st half, 7=halftime, 8=2nd half, 100=finished
        estado = "ERROR"
        
        if status_code == 0:
            estado = "PREVIA"
        elif status_code == 6 or 'inprogress' in status_type.lower() or 'first half' in status_type.lower():
            estado = "JUGANDO_1T"
        elif status_code == 7 or 'halftime' in status_type.lower():
            estado = "ENTRETIEMPO"
        elif status_code == 8 or 'second half' in status_type.lower():
            estado = "JUGANDO_2T"
        elif status_code == 100 or 'finished' in status_type.lower() or 'ended' in status_type.lower():
            estado = "FINAL"
        else:
            # Intentar inferir del periodo actual
            current_period = event.get('status', {}).get('description', '').lower()
            
            if '1st' in current_period or 'primer' in current_period:
                estado = "JUGANDO_1T"
            elif '2nd' in current_period or 'segundo' in current_period:
                estado = "JUGANDO_2T"
            elif 'half' in current_period or 'descanso' in current_period:
                estado = "ENTRETIEMPO"
            else:
                estado = "PREVIA"
        
        # Guardar en cache
        _cache_estado[url_sofascore] = {
            'estado': estado,
            'timestamp': time.time()
        }
        
        return estado
        
    except Exception as e:
        print(f"❌ Error obteniendo estado SofaScore: {str(e)[:50]}")
        return "ERROR"

# Test
if __name__ == "__main__":
    test_url = "https://www.sofascore.com/es-la/football/match/liverpool-brighton-and-hove-albion/FsU#id:14025198"
    
    print("🧪 TEST CLIENTE SOFASCORE")
    print("="*50)
    
    print("\n1. Obteniendo metadata...")
    meta = obtener_metadata_partido(test_url)
    
    if meta:
        print(f"✅ Partido: {meta['nombre']}")
        print(f"   Hora: {meta['hora']}")
        print(f"   Canales: {meta['canales']} (vacío en SofaScore)")
    else:
        print("❌ No se pudo obtener metadata")
    
    print("\n2. Consultando estado...")
    estado = obtener_estado_partido(test_url)
    print(f"   Estado: {estado}")
    
    print("\n✅ Tests completados")