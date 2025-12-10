import requests
import json
from bs4 import BeautifulSoup
from datetime import datetime
import time

# Cache para evitar consultas repetitivas
_cache_metadata = {}
_cache_estado = {}
CACHE_ESTADO_TTL = 20  # 20 segundos de vida para estado

def obtener_metadata_partido(url_promiedos, reintentos=3):
    """
    Extrae metadata del partido con sistema de reintentos y caché.
    Retorna: dict con nombre, hora, canales, estado_obj
    """
    # Verificar caché
    if url_promiedos in _cache_metadata:
        print(f"📦 Usando metadata cacheada para {url_promiedos}")
        return _cache_metadata[url_promiedos]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9',
    }
    
    for intento in range(reintentos):
        try:
            resp = requests.get(url_promiedos, headers=headers, timeout=15)
            
            if resp.status_code != 200:
                print(f"⚠️ Promiedos respondió con código {resp.status_code}")
                if intento < reintentos - 1:
                    time.sleep(2)
                    continue
                return None

            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Extraer JSON de Next.js
            script_data = soup.find("script", id="__NEXT_DATA__")
            if not script_data:
                print("❌ No se encontró __NEXT_DATA__ en Promiedos")
                if intento < reintentos - 1:
                    time.sleep(2)
                    continue
                return None
            
            data = json.loads(script_data.string)
            
            # Navegar estructura JSON
            try:
                game_props = data['props']['pageProps']['initialData']['game']
                
                # 1. Extraer equipos
                team_1 = game_props['teams'][0]['short_name']
                team_2 = game_props['teams'][1]['short_name']
                nombre_archivo = f"{team_1}_vs_{team_2}".replace(" ", "_").replace("/", "-")
                
                # 2. Extraer hora
                start_str = game_props['start_time']
                try:
                    dt_obj = datetime.strptime(start_str, "%d-%m-%Y %H:%M")
                    hora_inicio = dt_obj.strftime("%H:%M")
                except ValueError:
                    # Formato alternativo
                    print(f"⚠️ Formato de hora no estándar: {start_str}")
                    hora_inicio = "00:00"
                
                # 3. Extraer canales
                canales_detectados = []
                if 'game_info' in game_props:
                    for info in game_props['game_info']:
                        if info['name'] == 'Arg TV':
                            canales_raw = info['value']
                            canales_detectados = [
                                c.strip() 
                                for c in canales_raw.split(',')
                                if c.strip()
                            ]
                            break
                
                # Si no hay canales argentinos, intentar buscar TV internacional
                if not canales_detectados and 'game_info' in game_props:
                    for info in game_props['game_info']:
                        if 'TV' in info['name']:
                            canales_raw = info['value']
                            canales_detectados = [
                                c.strip() 
                                for c in canales_raw.split(',')
                                if c.strip()
                            ]
                            print(f"⚠️ Usando canales de {info['name']}: {canales_detectados}")
                            break
                
                metadata = {
                    "nombre": nombre_archivo,
                    "hora": hora_inicio,
                    "canales": canales_detectados,
                    "estado_obj": game_props,
                    "url": url_promiedos
                }
                
                # Guardar en caché
                _cache_metadata[url_promiedos] = metadata
                
                return metadata

            except KeyError as e:
                print(f"❌ Error parseando JSON de Promiedos: {e}")
                print(f"   Estructura disponible: {list(data.get('props', {}).keys())}")
                
                if intento < reintentos - 1:
                    time.sleep(2)
                    continue
                return None

        except requests.Timeout:
            print(f"⏱️ Timeout conectando a Promiedos (intento {intento + 1}/{reintentos})")
            if intento < reintentos - 1:
                time.sleep(3)
        except requests.RequestException as e:
            print(f"❌ Error de red: {e}")
            if intento < reintentos - 1:
                time.sleep(3)
        except json.JSONDecodeError as e:
            print(f"❌ Error decodificando JSON: {e}")
            return None
        except Exception as e:
            print(f"❌ Error inesperado: {e}")
            if intento < reintentos - 1:
                time.sleep(3)
    
    return None

def obtener_estado_partido(url_promiedos, usar_cache=True):
    """
    Consulta el estado del partido con caché temporal.
    Retorna: 'PREVIA', 'JUGANDO_1T', 'ENTRETIEMPO', 'JUGANDO_2T', 'FINAL', 'ERROR'
    """
    # Verificar caché
    if usar_cache and url_promiedos in _cache_estado:
        cache_entry = _cache_estado[url_promiedos]
        edad = time.time() - cache_entry['timestamp']
        
        if edad < CACHE_ESTADO_TTL:
            return cache_entry['estado']
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml',
    }
    
    try:
        resp = requests.get(url_promiedos, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            return "ERROR"
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        script_data = soup.find("script", id="__NEXT_DATA__")
        
        if not script_data:
            return "ERROR"
        
        data = json.loads(script_data.string)
        game_data = data['props']['pageProps']['initialData']['game']
        
        # Extraer estado
        estado_texto = game_data['status']['name'].lower()
        
        # Mapear estados
        estado = "ERROR"
        
        if any(x in estado_texto for x in ["primer", "1t", "first half"]):
            estado = "JUGANDO_1T"
        elif any(x in estado_texto for x in ["entretiempo", "half time", "descanso"]):
            estado = "ENTRETIEMPO"
        elif any(x in estado_texto for x in ["segundo", "2t", "second half"]):
            estado = "JUGANDO_2T"
        elif any(x in estado_texto for x in ["final", "finalizado", "finished"]):
            estado = "FINAL"
        elif any(x in estado_texto for x in ["previa", "previo", "not started", "programado", "prog.", "prog"]):
            estado = "PREVIA"
        else:
            # Estado desconocido, intentar inferir del minuto
            if 'minute' in game_data.get('status', {}):
                minuto = game_data['status'].get('minute', 0)
                if minuto == 0:
                    estado = "PREVIA"
                elif 1 <= minuto <= 45:
                    estado = "JUGANDO_1T"
                elif 46 <= minuto <= 90:
                    estado = "JUGANDO_2T"
                else:
                    estado = "FINAL"
            else:
                # Log más completo para debugging
                print(f"⚠️ Estado desconocido de Promiedos: '{estado_texto}'")
                print(f"   Estructura status disponible: {game_data.get('status', {})}")
                estado = "PREVIA"  # Asumir previa por defecto
        
        # Guardar en caché
        _cache_estado[url_promiedos] = {
            'estado': estado,
            'timestamp': time.time()
        }
        
        return estado
        
    except requests.Timeout:
        print("⏱️ Timeout consultando estado")
        return "ERROR"
    except Exception as e:
        print(f"❌ Error obteniendo estado: {str(e)[:50]}")
        return "ERROR"

def limpiar_cache():
    """Limpia cachés antiguos"""
    global _cache_estado
    
    ahora = time.time()
    keys_eliminar = []
    
    for key, value in _cache_estado.items():
        if ahora - value['timestamp'] > 300:  # 5 minutos
            keys_eliminar.append(key)
    
    for key in keys_eliminar:
        del _cache_estado[key]

def obtener_info_completa(url_promiedos):
    """
    Obtiene metadata + estado actual en una sola función.
    Útil para debugging o consultas únicas.
    """
    metadata = obtener_metadata_partido(url_promiedos)
    
    if not metadata:
        return None
    
    estado = obtener_estado_partido(url_promiedos)
    metadata['estado_actual'] = estado
    
    return metadata

# Función de utilidad para testing
if __name__ == "__main__":
    test_url = "https://www.promiedos.com.ar/game/villarreal-vs-fc-copenhagen/efdieji"
    
    print("🧪 TEST DEL CLIENTE PROMIEDOS")
    print("="*50)
    
    print("\n1. Obteniendo metadata...")
    meta = obtener_metadata_partido(test_url)
    
    if meta:
        print(f"✅ Partido: {meta['nombre']}")
        print(f"   Hora: {meta['hora']}")
        print(f"   Canales: {', '.join(meta['canales'])}")
    else:
        print("❌ No se pudo obtener metadata")
    
    print("\n2. Consultando estado...")
    estado = obtener_estado_partido(test_url)
    print(f"   Estado: {estado}")
    
    print("\n3. Test de caché...")
    inicio = time.time()
    estado2 = obtener_estado_partido(test_url)
    tiempo = time.time() - inicio
    print(f"   Estado (cacheado): {estado2} en {tiempo:.3f}s")
    
    print("\n✅ Tests completados")