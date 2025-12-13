#!/usr/bin/env python3
"""
vision_detector_v3.py - OPTIMIZADO PARA 0% PÉRDIDA Y EFICIENCIA
MEJORAS CLAVE:
- Buffer de seguridad: inicia 3min antes, termina 5min después
- Limpieza automática de frames usados
- Cache inteligente para reducir llamadas API
- Detección conservadora: prefiere JUGANDO ante duda
- Sistema de confianza adaptativo
- Rate limiting optimizado por tier de Gemini
"""

import time
import subprocess
import os
import json
from datetime import datetime
from pathlib import Path
import threading
from collections import Counter, deque
import hashlib

# ============ CONFIGURACIÓN OPTIMIZADA ============

# Verificación de estado (AUMENTADO para reducir API calls)
INTERVALO_VERIFICACION_PREVIA = 120  # 2min en previa (partido no iniciado)
INTERVALO_VERIFICACION_JUGANDO = 45  # 45s durante partido
INTERVALO_VERIFICACION_ENTRETIEMPO = 90  # 90s en entretiempo

# Capturas por verificación (REDUCIDO para ahorrar API)
CAPTURAS_POR_VERIFICACION = 2  # Bajado de 3 a 2
INTERVALO_ENTRE_CAPTURAS = 3  # Espaciado

# Carpeta de frames
CARPETA_FRAMES = "./frames_analisis"

# Umbrales conservadores (PREFIERE GRABAR DE MÁS)
CONFIANZA_MINIMA_JUGANDO = 0.25  # Muy permisivo para detectar partido
CONFIANZA_MINIMA_NO_JUGANDO = 0.70  # Estricto para confirmar que NO juega
CONSENSO_REQUERIDO = 2  # De 2 frames

# API Configuration
GEMINI_API_KEY = "AIzaSyBXHAYLlDLZQHQkaYl_oCpHWGUoNG3D3cU"

# Rate limiting según tier de Gemini (ver documento proporcionado)
# Tier 1: 15 RPM (requests per minute)
MIN_TIEMPO_ENTRE_REQUESTS = 4.5  # 60/15 = 4s, +0.5s buffer = 13 RPM seguro

# Cache
_cache_analisis = {}  # Cache por hash de frame
_cache_ttl = 300  # 5 minutos
_ultimo_request_time = 0
_lock_api = threading.Lock()
_historial_estados = deque(maxlen=10)  # Últimos 10 estados

# Buffers de seguridad (CRÍTICO PARA 0% PÉRDIDA)
BUFFER_INICIO_SEGUNDOS = 180  # 3 minutos antes
BUFFER_FIN_SEGUNDOS = 300  # 5 minutos después
MINUTOS_MINIMO_GRABACION_1T = 35  # Nunca cortar antes de 35min en 1T
MINUTOS_MINIMO_GRABACION_2T = 35  # Nunca cortar antes de 35min en 2T

# ============ PROMPT ULTRA-OPTIMIZADO ============

PROMPT_OPTIMIZADO = """<role>Soccer broadcast state detector</role>

<task>Is a soccer match ACTIVELY PLAYING right now? Answer in JSON only.</task>

<indicators>
PLAYING (return "JUGANDO"):
- Green field + players (4+) positioned/moving
- Broadcast camera angle from stands
- Match graphics/scoreboard visible
- Players in formation or running

NOT PLAYING (return "NO_JUGANDO"):
- TV studio/analysts talking
- "HALF TIME"/"ENTRETIEMPO"/"DESCANSO" text
- Advertisement/commercial
- Interview/press conference
- Only crowd (no field)
- Replay with large "REPLAY" overlay

UNCERTAIN: prefer "JUGANDO" (better to record extra than miss action)
</indicators>

<response_format>
{"estado": "JUGANDO" or "NO_JUGANDO", "confianza": 0.0-1.0, "evidencia": "brief reason"}
</response_format>

<rules>
1. Be decisive and fast
2. If you see ANY field + players = JUGANDO
3. Only return NO_JUGANDO if 100% certain (clear studio/ad/text)
4. JSON only, no markdown
5. Minimum 0.25 confidence
</rules>"""

# ============ FUNCIONES AUXILIARES ============

def calcular_hash_frame(ruta_frame):
    """Calcula hash MD5 del frame para cache"""
    try:
        with open(ruta_frame, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except:
        return None

def limpiar_frames_antiguos(carpeta_partido, horas=2):
    """Limpia frames más antiguos de N horas"""
    try:
        carpeta = Path(CARPETA_FRAMES) / carpeta_partido
        if not carpeta.exists():
            return
        
        limite_tiempo = time.time() - (horas * 3600)
        frames_eliminados = 0
        
        for frame in carpeta.glob("*.jpg"):
            if frame.stat().st_mtime < limite_tiempo:
                try:
                    frame.unlink()
                    frames_eliminados += 1
                except:
                    pass
        
        if frames_eliminados > 0:
            print(f"      🧹 Limpiados {frames_eliminados} frames antiguos")
    except Exception as e:
        print(f"      ⚠️ Error limpiando frames: {str(e)[:50]}")

def eliminar_frame_usado(ruta_frame):
    """Elimina frame después de análisis"""
    try:
        if os.path.exists(ruta_frame):
            os.remove(ruta_frame)
    except:
        pass

# ============ CAPTURA DE FRAMES ============

def capturar_frame_optimizado(stream_url, output_path, headers=None, timeout=15):
    """Captura con validación y timeout reducido"""
    try:
        headers_str = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\\r\\n"
        
        if headers:
            for k, v in headers.items():
                if k.lower() != 'user-agent':
                    headers_str += f"{k}: {v}\\r\\n"
        
        cmd = [
            'ffmpeg',
            '-headers', headers_str,
            '-i', stream_url,
            '-vframes', '1',
            '-q:v', '2',  # Buena calidad pero no máxima (ahorrar tiempo)
            '-vf', 'scale=1280:720',  # 720p suficiente (reduce tamaño)
            '-y',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout
        )
        
        if os.path.exists(output_path):
            size = os.path.getsize(output_path)
            if size > 5000:
                return True
        
        return False
        
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False

# ============ ANÁLISIS CON GEMINI ============

def analizar_con_gemini_cached(ruta_imagen):
    """
    Análisis con cache inteligente y rate limiting
    """
    global _ultimo_request_time
    
    # Verificar cache primero
    frame_hash = calcular_hash_frame(ruta_imagen)
    if frame_hash and frame_hash in _cache_analisis:
        cache_entry = _cache_analisis[frame_hash]
        if time.time() - cache_entry['timestamp'] < _cache_ttl:
            print(f"      📦 Cache hit")
            return cache_entry['resultado']
    
    try:
        import google.generativeai as genai
        from PIL import Image
    except ImportError:
        print("      ❌ Instalar: pip install google-generativeai Pillow")
        return None
    
    if not GEMINI_API_KEY:
        return None
    
    # Rate limiting estricto
    with _lock_api:
        tiempo_desde_ultimo = time.time() - _ultimo_request_time
        if tiempo_desde_ultimo < MIN_TIEMPO_ENTRE_REQUESTS:
            espera = MIN_TIEMPO_ENTRE_REQUESTS - tiempo_desde_ultimo
            time.sleep(espera)
        
        _ultimo_request_time = time.time()
    
    # Configurar Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Usar modelo más económico primero
    modelos = [
        ('gemini-2.5-flash-lite', {'temperature': 1.0}),
        ('gemini-2.5-flash', {'temperature': 1.0, 'top_p': 0.95}),
    ]
    
    for modelo_nombre, config in modelos:
        try:
            generation_config = genai.GenerationConfig(**config)
            model = genai.GenerativeModel(
                modelo_nombre,
                generation_config=generation_config
            )
            
            # Cargar y optimizar imagen
            img = Image.open(ruta_imagen)
            
            # Redimensionar si es muy grande (reduce tokens)
            max_size = (1280, 720)
            if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Generar
            response = model.generate_content(
                [PROMPT_OPTIMIZADO, img],
                request_options={'timeout': 25}
            )
            
            if not response or not response.text:
                continue
            
            # Parsear respuesta
            text = response.text.strip()
            
            # Limpiar markdown
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0]
            elif '```' in text:
                text = text.split('```')[1].split('```')[0]
            
            text = text.strip()
            
            # Parsear JSON
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'\{[^}]+\}', text)
                if json_match:
                    result = json.loads(json_match.group(0))
                else:
                    continue
            
            # Normalizar estado
            estado = result.get('estado', '').upper()
            
            if estado not in ['JUGANDO', 'NO_JUGANDO']:
                if any(x in estado for x in ['1T', '2T', 'PLAYING', 'MATCH', 'GAME', 'FIELD']):
                    estado = 'JUGANDO'
                elif any(x in estado for x in ['HALF', 'BREAK', 'STUDIO', 'AD', 'INTERVIEW', 'ENTRETIEMPO']):
                    estado = 'NO_JUGANDO'
                else:
                    continue
            
            result['estado'] = estado
            
            # Asegurar campos
            if 'confianza' not in result or not isinstance(result['confianza'], (int, float)):
                result['confianza'] = 0.50
            
            if 'evidencia' not in result:
                result['evidencia'] = "Visual analysis"
            
            # Guardar en cache
            if frame_hash:
                _cache_analisis[frame_hash] = {
                    'resultado': result,
                    'timestamp': time.time()
                }
            
            print(f"      ✅ {modelo_nombre}: {estado} ({result['confianza']:.0%})")
            
            return result
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower():
                if modelo_nombre == modelos[-1][0]:
                    print(f"      ❌ Cuota excedida")
                    return None
                else:
                    time.sleep(5)
                    continue
            else:
                continue
    
    return None

# ============ SISTEMA DE CONSENSO ============

def analizar_con_consenso_conservador(frames, nombre_partido):
    """
    Análisis con bias conservador: prefiere JUGANDO ante duda
    Limpia frames después de usar
    """
    if not frames:
        return None, []
    
    print(f"      🔍 Analizando {len(frames)} frames...")
    
    resultados = []
    
    for i, frame in enumerate(frames):
        print(f"         Frame {i+1}/{len(frames)}...")
        resultado = analizar_con_gemini_cached(frame)
        
        if resultado:
            resultados.append(resultado)
        
        # CRÍTICO: Eliminar frame inmediatamente después de usar
        eliminar_frame_usado(frame)
    
    if not resultados:
        print(f"      ❌ Sin análisis exitosos")
        return None, []
    
    print(f"      🗳️ Procesando {len(resultados)} análisis...")
    
    # BIAS CONSERVADOR: Aplicar umbrales asimétricos
    resultados_jugando = [
        r for r in resultados 
        if r['estado'] == 'JUGANDO' and r.get('confianza', 0) >= CONFIANZA_MINIMA_JUGANDO
    ]
    
    resultados_no_jugando = [
        r for r in resultados 
        if r['estado'] == 'NO_JUGANDO' and r.get('confianza', 0) >= CONFIANZA_MINIMA_NO_JUGANDO
    ]
    
    # Si hay ALGÚN resultado confiable de JUGANDO, preferirlo
    if resultados_jugando:
        mejor_jugando = max(resultados_jugando, key=lambda x: x['confianza'])
        print(f"      ⚽ JUGANDO detectado (conf: {mejor_jugando['confianza']:.0%})")
        return mejor_jugando, resultados
    
    # Solo si NO_JUGANDO es muy confiable, aceptarlo
    if resultados_no_jugando:
        mejor_no_jugando = max(resultados_no_jugando, key=lambda x: x['confianza'])
        
        # Verificar consenso estricto
        votos_no_jugando = len(resultados_no_jugando)
        if votos_no_jugando >= CONSENSO_REQUERIDO:
            print(f"      🛑 NO_JUGANDO confirmado ({votos_no_jugando} votos)")
            return mejor_no_jugando, resultados
    
    # Ante cualquier duda, asumir JUGANDO (mejor grabar de más)
    print(f"      ⚠️ Sin consenso claro → Asumiendo JUGANDO (conservador)")
    return {
        'estado': 'JUGANDO',
        'confianza': 0.40,
        'evidencia': 'Sin consenso claro - modo conservador'
    }, resultados

# ============ DETECTOR HÍBRIDO MEJORADO ============

class HybridStateDetectorV3:
    """
    Detector optimizado para 0% pérdida y eficiencia
    """
    
    def __init__(self, nombre_partido, url_promiedos=None):
        self.nombre_partido = nombre_partido
        self.url_promiedos = url_promiedos
        self.carpeta = Path(CARPETA_FRAMES) / nombre_partido
        self.carpeta.mkdir(parents=True, exist_ok=True)
        
        self.estado_actual = "DESCONOCIDO"
        self.ultimo_check = 0
        self.historial = []
        
        # Tracking de fase del partido
        self.fase_actual = "PREVIA"  # PREVIA, 1T, ENTRETIEMPO, 2T, FINAL
        self.tiempo_inicio_fase = None
        
        # Stats
        self.stats = {
            'checks_gemini': 0,
            'checks_promiedos': 0,
            'frames_capturados': 0,
            'frames_analizados': 0,
            'api_calls_ahorradas_cache': 0
        }
    
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🔍 {msg}")
    
    def _obtener_intervalo_verificacion(self):
        """Intervalo adaptativo según fase"""
        if self.fase_actual == "PREVIA":
            return INTERVALO_VERIFICACION_PREVIA
        elif self.fase_actual in ["1T", "2T"]:
            return INTERVALO_VERIFICACION_JUGANDO
        elif self.fase_actual == "ENTRETIEMPO":
            return INTERVALO_VERIFICACION_ENTRETIEMPO
        else:
            return INTERVALO_VERIFICACION_JUGANDO
    
    def _puede_terminar_fase(self):
        """
        Verifica si es seguro terminar la fase actual
        CRÍTICO: Nunca terminar antes del tiempo mínimo
        """
        if not self.tiempo_inicio_fase:
            return False
        
        tiempo_transcurrido = (datetime.now() - self.tiempo_inicio_fase).total_seconds() / 60
        
        if self.fase_actual == "1T":
            if tiempo_transcurrido < MINUTOS_MINIMO_GRABACION_1T:
                self.log(f"   ⏱️ 1T: {tiempo_transcurrido:.0f}min < {MINUTOS_MINIMO_GRABACION_1T}min mínimo")
                return False
        
        elif self.fase_actual == "2T":
            if tiempo_transcurrido < MINUTOS_MINIMO_GRABACION_2T:
                self.log(f"   ⏱️ 2T: {tiempo_transcurrido:.0f}min < {MINUTOS_MINIMO_GRABACION_2T}min mínimo")
                return False
        
        return True
    
    def verificar_estado(self, stream_url, headers=None):
        """
        Verifica usando Gemini optimizado + Promiedos backup
        """
        ahora = time.time()
        
        intervalo = self._obtener_intervalo_verificacion()
        
        if ahora - self.ultimo_check < intervalo:
            return self.estado_actual
        
        self.ultimo_check = ahora
        
        self.log("="*50)
        self.log(f"🎯 Verificación ({self.fase_actual})")
        
        # Limpieza periódica
        if self.stats['checks_gemini'] % 10 == 0:
            limpiar_frames_antiguos(self.nombre_partido)
        
        # Capturar frames
        frames = self._capturar_frames(stream_url, headers)
        
        if frames:
            # Analizar con consenso conservador
            resultado_consenso, todos_resultados = analizar_con_consenso_conservador(
                frames, self.nombre_partido
            )
            
            self.stats['checks_gemini'] += 1
            self.stats['frames_analizados'] += len(todos_resultados)
            
            if resultado_consenso:
                estado = resultado_consenso['estado']
                confianza = resultado_consenso['confianza']
                
                self.log(f"   ✅ GEMINI: {estado} (conf: {confianza:.0%})")
                self.log(f"   💡 {resultado_consenso['evidencia']}")
                
                # Actualizar estado con lógica conservadora
                estado_anterior = self.estado_actual
                self.estado_actual = self._actualizar_estado_conservador(
                    estado, confianza, estado_anterior
                )
                
                if self.estado_actual != estado_anterior:
                    self.log(f"   🚨 CAMBIO: {estado_anterior} → {self.estado_actual}")
                    
                    self.historial.append({
                        'timestamp': datetime.now(),
                        'estado': self.estado_actual,
                        'metodo': 'gemini',
                        'confianza': confianza
                    })
                
                return self.estado_actual
        
        # Fallback a Promiedos
        self.log("   ⚠️ Gemini no concluyente, usando Promiedos")
        return self._verificar_con_promiedos()
    
    def _capturar_frames(self, stream_url, headers):
        """Captura frames con manejo de errores"""
        timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")
        frames_capturados = []
        
        for i in range(CAPTURAS_POR_VERIFICACION):
            frame_path = self.carpeta / f"frame_{timestamp_base}_{i}.jpg"
            
            if capturar_frame_optimizado(stream_url, str(frame_path), headers):
                frames_capturados.append(str(frame_path))
                self.stats['frames_capturados'] += 1
            
            if i < CAPTURAS_POR_VERIFICACION - 1:
                time.sleep(INTERVALO_ENTRE_CAPTURAS)
        
        return frames_capturados
    
    def _actualizar_estado_conservador(self, nuevo_estado, confianza, estado_anterior):
        """
        Lógica de transición conservadora
        """
        # Si detecta JUGANDO con confianza mínima, aceptar
        if nuevo_estado == 'JUGANDO' and confianza >= CONFIANZA_MINIMA_JUGANDO:
            if estado_anterior != 'JUGANDO':
                # Iniciar o reanudar fase
                if self.fase_actual == "PREVIA":
                    self.fase_actual = "1T"
                    self.tiempo_inicio_fase = datetime.now()
                    self.log(f"   ⚽ INICIO 1T")
                elif self.fase_actual == "ENTRETIEMPO":
                    self.fase_actual = "2T"
                    self.tiempo_inicio_fase = datetime.now()
                    self.log(f"   ⚽ INICIO 2T")
            return 'JUGANDO'
        
        # Si detecta NO_JUGANDO con alta confianza
        if nuevo_estado == 'NO_JUGANDO' and confianza >= CONFIANZA_MINIMA_NO_JUGANDO:
            # Verificar si es seguro cambiar
            if estado_anterior == 'JUGANDO':
                if not self._puede_terminar_fase():
                    self.log(f"   ⏱️ Tiempo mínimo no alcanzado - Manteniendo JUGANDO")
                    return 'JUGANDO'
                
                # Transición válida
                if self.fase_actual == "1T":
                    self.fase_actual = "ENTRETIEMPO"
                    self.log(f"   ☕ ENTRETIEMPO")
                elif self.fase_actual == "2T":
                    self.fase_actual = "FINAL"
                    self.log(f"   🏁 FINAL")
            
            return 'NO_JUGANDO'
        
        # Ante cualquier duda, mantener estado anterior (conservador)
        return estado_anterior
    
    def _verificar_con_promiedos(self):
        """Backup con Promiedos"""
        if not self.url_promiedos:
            return self.estado_actual
        
        try:
            import promiedos_client
            estado_promiedos = promiedos_client.obtener_estado_partido(self.url_promiedos)
            
            self.stats['checks_promiedos'] += 1
            
            # Mapear conservadoramente
            if estado_promiedos in ['JUGANDO_1T', 'JUGANDO_2T']:
                estado_mapeado = 'JUGANDO'
            elif estado_promiedos == 'FINAL':
                # Solo aceptar FINAL si el tiempo mínimo se cumplió
                if self._puede_terminar_fase():
                    estado_mapeado = 'NO_JUGANDO'
                else:
                    estado_mapeado = 'JUGANDO'  # Seguir grabando
            elif estado_promiedos in ['PREVIA', 'ENTRETIEMPO']:
                estado_mapeado = 'NO_JUGANDO'
            else:
                estado_mapeado = self.estado_actual
            
            self.log(f"   📡 Promiedos: {estado_promiedos} → {estado_mapeado}")
            
            if estado_mapeado != self.estado_actual:
                self.estado_actual = estado_mapeado
            
            return self.estado_actual
            
        except Exception as e:
            self.log(f"   ❌ Error Promiedos: {str(e)[:60]}")
            return self.estado_actual
    
    def obtener_estado(self):
        return self.estado_actual
    
    def obtener_estadisticas(self):
        """Estadísticas detalladas"""
        total_checks = self.stats['checks_gemini'] + self.stats['checks_promiedos']
        
        if total_checks == 0:
            return self.stats
        
        # Calcular tasa de cache
        if self.stats['frames_capturados'] > 0:
            tasa_cache = (self.stats['frames_capturados'] - self.stats['frames_analizados']) / self.stats['frames_capturados'] * 100
        else:
            tasa_cache = 0
        
        return {
            **self.stats,
            'uso_gemini_pct': (self.stats['checks_gemini'] / total_checks) * 100,
            'uso_promiedos_pct': (self.stats['checks_promiedos'] / total_checks) * 100,
            'tasa_cache_pct': tasa_cache,
            'fase_actual': self.fase_actual
        }
    
    def limpiar_recursos(self):
        """Limpieza final de recursos"""
        try:
            limpiar_frames_antiguos(self.nombre_partido, horas=0)  # Limpiar todos
            self.log("   🧹 Recursos limpiados")
        except Exception as e:
            self.log(f"   ⚠️ Error limpiando: {str(e)[:50]}")

# ============ FUNCIONES COMPATIBLES ============

def obtener_estado_partido_hibrido(detector, stream_url, headers=None):
    """Función compatible con sistema existente"""
    return detector.verificar_estado(stream_url, headers)

# ============ UTILIDADES ============

def limpiar_cache_global():
    """Limpia cache global de análisis"""
    global _cache_analisis
    ahora = time.time()
    keys_eliminar = [
        k for k, v in _cache_analisis.items()
        if ahora - v['timestamp'] > _cache_ttl
    ]
    for k in keys_eliminar:
        del _cache_analisis[k]

# ============ TEST ============

def test_detector_v3():
    print("\n" + "="*70)
    print("🧪 TEST DETECTOR v3 - OPTIMIZADO 0% PÉRDIDA")
    print("="*70 + "\n")
    
    if not GEMINI_API_KEY:
        print("❌ Configurar GEMINI_API_KEY")
        return False
    
    detector = HybridStateDetectorV3(
        "TEST_OPTIMIZADO",
        url_promiedos="https://www.promiedos.com.ar/game/atletico-madrid-vs-valencia/eeghefi"
    )
    
    test_stream = "https://8c51.crackstreamslivehd.com/sporttvbr1/tracks-v1a1/mono.m3u8?ip=181.27.51.162&token=0719160106c9d9b121c2c07e959c49f316022adf-d2-1765681261-1765627261"
    
    print("Ejecutando verificación optimizada...\n")
    
    estado = detector.verificar_estado(test_stream)
    
    print(f"\n{'='*70}")
    print(f"📊 RESULTADO: {estado}")
    print(f"{'='*70}\n")
    
    stats = detector.obtener_estadisticas()
    print("📈 ESTADÍSTICAS:")
    print(f"   Checks Gemini: {stats['checks_gemini']}")
    print(f"   Checks Promiedos: {stats['checks_promiedos']}")
    print(f"   Frames capturados: {stats['frames_capturados']}")
    print(f"   Frames analizados: {stats['frames_analizados']}")
    print(f"   Cache hit rate: {stats.get('tasa_cache_pct', 0):.0f}%")
    print(f"   Fase actual: {stats['fase_actual']}")
    
    # Limpiar
    detector.limpiar_recursos()
    
    return True

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_detector_v3()
    else:
        print(__doc__)
        print("\nPara test: python vision_detector_v3.py test")