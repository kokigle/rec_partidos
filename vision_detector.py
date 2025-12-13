#!/usr/bin/env python3
"""
vision_detector_v2.py - Sistema MEJORADO con prompts optimizados
CAMBIOS CLAVE:
- Prompt estructurado con XML tags (recomendación Gemini)
- Instrucciones directas y precisas
- Ejemplos concretos en el prompt
- media_resolution_high para mejor detección
- Thinking level configurado
"""

import time
import subprocess
import os
import json
from datetime import datetime
from pathlib import Path
import threading
from collections import Counter

# ============ CONFIGURACIÓN ============
INTERVALO_VERIFICACION = 30
CAPTURAS_POR_VERIFICACION = 3
INTERVALO_ENTRE_CAPTURAS = 2
CARPETA_FRAMES = "./frames_analisis"

# Umbrales más permisivos
CONFIANZA_MINIMA = 0.30  # Bajado a 30%
CONSENSO_REQUERIDO = 2

# API Key
GEMINI_API_KEY = "AIzaSyBXHAYLlDLZQHQkaYl_oCpHWGUoNG3D3cU"

# Rate limiting
MIN_TIEMPO_ENTRE_REQUESTS = 3

# Cache y locks
_cache_analisis = {}
_ultimo_request_time = 0
_lock_api = threading.Lock()

# ============ PROMPT OPTIMIZADO CON XML TAGS ============

PROMPT_OPTIMIZADO = """<role>
You are a sports broadcast analyzer. Your job is to determine if a live soccer/football match is currently being played.
</role>

<task>
Analyze this image from a sports broadcast and answer ONE question:
"Is a soccer/football match actively being PLAYED right now?"
</task>

<instructions>
1. Look for these PRIMARY indicators of "MATCH IN PROGRESS":
   - Green soccer field visible (grass, lines, goals)
   - Multiple players (4+) on the field
   - Players in active positions (running, standing ready, positioned for play)
   - Broadcast camera angle (from stands, following action)
   - Match graphics/score overlay present

2. Look for these indicators of "NOT PLAYING NOW":
   - TV studio with analysts/commentators
   - Commercial/advertisement screen
   - Interview or press conference
   - Only crowd/fans visible (no field)
   - Large text: "HALF TIME", "FULL TIME", "BREAK", "ENTRETIEMPO"
   - Replay in slow motion with large graphics

3. Return ONLY this JSON format (no markdown, no backticks):
{"estado": "JUGANDO", "confianza": 0.85, "evidencia": "brief description"}

4. Valid estados: "JUGANDO" or "NO_JUGANDO" only
</instructions>

<examples>
PLAYING:
- Field + 10+ players + ball visible = JUGANDO (0.95)
- Field + players positioned + broadcast angle = JUGANDO (0.85)
- Partial field + players running = JUGANDO (0.75)

NOT PLAYING:
- Studio with 3 people talking = NO_JUGANDO (0.90)
- "ENTRETIEMPO" text on screen = NO_JUGANDO (0.95)
- Advertisement = NO_JUGANDO (1.0)
- Only crowd shots = NO_JUGANDO (0.80)
</examples>

<constraints>
- Be decisive. If you see a field with players, it's JUGANDO
- Don't overthink. This is binary: match is ON or OFF
- Minimum 0.30 confidence required
- Respond ONLY with JSON, no explanation outside it
</constraints>"""

# ============ CAPTURA MEJORADA ============

def capturar_frame_optimizado(stream_url, output_path, headers=None, intento=1):
    """Captura con mejor calidad y validación"""
    max_intentos = 2
    
    for i in range(max_intentos):
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
                '-q:v', '1',  # Máxima calidad
                '-vf', 'scale=1920:1080',  # Full HD
                '-y',
                output_path
            ]
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20
            )
            
            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                if size > 5000:
                    return True
                else:
                    print(f"      ⚠️ Frame pequeño ({size}B), reintentando...")
            
            time.sleep(1)
            
        except Exception as e:
            if i < max_intentos - 1:
                print(f"      ⚠️ Intento {i+1} falló: {str(e)[:50]}")
                time.sleep(2)
            
    return False

# ============ ANÁLISIS CON GEMINI MEJORADO ============

def analizar_con_gemini_v2(ruta_imagen):
    """
    Análisis con configuración óptima de Gemini:
    - media_resolution_high para mejor detección
    - thinking_level low para respuestas rápidas
    - temperature 1.0 (default recomendado)
    """
    global _ultimo_request_time
    
    try:
        import google.generativeai as genai
        from PIL import Image
    except ImportError:
        print("      ❌ Instalar: pip install google-generativeai Pillow")
        return None
    
    if not GEMINI_API_KEY:
        return None
    
    # Rate limiting con lock
    with _lock_api:
        tiempo_desde_ultimo = time.time() - _ultimo_request_time
        if tiempo_desde_ultimo < MIN_TIEMPO_ENTRE_REQUESTS:
            espera = MIN_TIEMPO_ENTRE_REQUESTS - tiempo_desde_ultimo
            time.sleep(espera)
        
        _ultimo_request_time = time.time()
    
    # Configurar
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Probar modelos en orden (nombres correctos según tu cuota)
    modelos_configs = [
        ('gemini-2.5-flash', {
            'temperature': 1.0,
            'top_p': 0.95,
            'top_k': 40,
        }),
        ('gemini-2.5-flash-lite', {
            'temperature': 1.0,
        }),
    ]
    
    for modelo_nombre, config in modelos_configs:
        try:
            # Crear modelo con configuración válida
            generation_config = genai.GenerationConfig(**config)
            model = genai.GenerativeModel(
                modelo_nombre,
                generation_config=generation_config
            )
            
            # Cargar imagen con alta resolución
            img = Image.open(ruta_imagen)
            
            # Si es muy grande, redimensionar (max 4MB)
            max_size = (1920, 1080)
            if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Generar con timeout más largo para dar tiempo al análisis
            try:
                response = model.generate_content(
                    [PROMPT_OPTIMIZADO, img],
                    request_options={'timeout': 30}
                )
            except Exception as timeout_error:
                if "timeout" in str(timeout_error).lower():
                    print(f"      ⏱️  Timeout con {modelo_nombre}")
                    continue
                raise
            
            if not response or not response.text:
                print(f"      ⚠️ {modelo_nombre} sin respuesta")
                continue
            
            # Limpiar respuesta
            text = response.text.strip()
            
            # Remover markdown si existe
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0]
            elif '```' in text:
                text = text.split('```')[1].split('```')[0]
            
            text = text.strip()
            
            # Parsear JSON
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                # Intentar extraer JSON del texto
                import re
                json_match = re.search(r'\{[^}]+\}', text)
                if json_match:
                    result = json.loads(json_match.group(0))
                else:
                    print(f"      ⚠️ No se pudo parsear JSON de {modelo_nombre}")
                    continue
            
            # Validar y normalizar estado
            estado = result.get('estado', '').upper()
            
            if estado not in ['JUGANDO', 'NO_JUGANDO']:
                # Intentar mapear
                if any(x in estado for x in ['1T', '2T', 'PLAYING', 'MATCH', 'GAME']):
                    estado = 'JUGANDO'
                elif any(x in estado for x in ['HALF', 'BREAK', 'STUDIO', 'AD', 'INTERVIEW']):
                    estado = 'NO_JUGANDO'
                else:
                    print(f"      ⚠️ Estado inválido: {estado}")
                    continue
            
            result['estado'] = estado
            
            # Asegurar confianza
            if 'confianza' not in result or not isinstance(result['confianza'], (int, float)):
                result['confianza'] = 0.50
            
            # Asegurar evidencia
            if 'evidencia' not in result:
                result['evidencia'] = "Análisis visual completado"
            
            print(f"      ✅ {modelo_nombre}: {estado} ({result['confianza']:.0%})")
            return result
            
        except json.JSONDecodeError as e:
            print(f"      ⚠️ Error JSON con {modelo_nombre}")
            continue
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower():
                if modelo_nombre == modelos_configs[-1][0]:
                    print(f"      ❌ Cuota excedida en todos los modelos")
                else:
                    print(f"      ⏱️  Cuota excedida en {modelo_nombre}, probando siguiente...")
                    time.sleep(3)
                    continue
            else:
                print(f"      ⚠️ Error {modelo_nombre}: {error_str[:60]}")
                continue
    
    return None

# ============ SISTEMA DE CONSENSO MEJORADO ============

def capturar_multiples_frames(stream_url, headers, nombre_partido, num_capturas=3):
    """Captura frames espaciados"""
    carpeta = Path(CARPETA_FRAMES) / nombre_partido
    carpeta.mkdir(parents=True, exist_ok=True)
    
    timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")
    frames_capturados = []
    
    print(f"      📸 Capturando {num_capturas} frames...")
    
    for i in range(num_capturas):
        frame_path = carpeta / f"frame_{timestamp_base}_{i}.jpg"
        
        if capturar_frame_optimizado(stream_url, str(frame_path), headers):
            frames_capturados.append(str(frame_path))
            print(f"         ✅ Frame {i+1}/{num_capturas}")
        else:
            print(f"         ❌ Frame {i+1}/{num_capturas} falló")
        
        if i < num_capturas - 1:
            time.sleep(INTERVALO_ENTRE_CAPTURAS)
    
    return frames_capturados

def analizar_con_consenso(frames):
    """Análisis paralelo con votación"""
    if not frames:
        return None, []
    
    print(f"      🔍 Analizando {len(frames)} frames con Gemini...")
    
    resultados = []
    
    # Analizar cada frame
    for i, frame in enumerate(frames):
        print(f"         Frame {i+1}/{len(frames)}...")
        resultado = analizar_con_gemini_v2(frame)
        if resultado:
            resultados.append(resultado)
    
    if not resultados:
        print(f"      ❌ Ningún análisis exitoso")
        return None, []
    
    # Sistema de votación
    print(f"      🗳️  Procesando {len(resultados)} análisis...")
    
    # Filtrar por confianza mínima
    resultados_confiables = [
        r for r in resultados 
        if r.get('confianza', 0) >= CONFIANZA_MINIMA
    ]
    
    if not resultados_confiables:
        # Usar el de mayor confianza aunque sea bajo
        mejor = max(resultados, key=lambda x: x.get('confianza', 0))
        print(f"      ⚠️ Ninguno alcanza confianza mínima, usando mejor: {mejor['confianza']:.0%}")
        return mejor, resultados
    
    # Contar votos
    estados = [r['estado'] for r in resultados_confiables]
    votos = Counter(estados)
    estado_ganador, num_votos = votos.most_common(1)[0]
    
    print(f"      📊 Votos: {dict(votos)}")
    
    # Verificar consenso
    if num_votos >= CONSENSO_REQUERIDO:
        # Consenso alcanzado
        resultados_ganadores = [
            r for r in resultados_confiables 
            if r['estado'] == estado_ganador
        ]
        confianza_promedio = sum(r['confianza'] for r in resultados_ganadores) / len(resultados_ganadores)
        
        mejor_evidencia = max(resultados_ganadores, key=lambda x: x['confianza'])['evidencia']
        
        resultado_final = {
            'estado': estado_ganador,
            'confianza': confianza_promedio,
            'evidencia': f"Consenso {num_votos}/{len(estados)}: {mejor_evidencia}",
            'votos': dict(votos)
        }
        
        return resultado_final, resultados
    else:
        # Sin consenso, usar el de mayor confianza
        mejor = max(resultados_confiables, key=lambda x: x['confianza'])
        print(f"      ⚠️ Sin consenso claro, usando análisis con mayor confianza")
        return mejor, resultados

# ============ DETECTOR HÍBRIDO ============

class HybridStateDetector:
    """Detector con Gemini optimizado + Promiedos backup"""
    
    def __init__(self, nombre_partido, url_promiedos=None):
        self.nombre_partido = nombre_partido
        self.url_promiedos = url_promiedos
        self.carpeta = Path(CARPETA_FRAMES) / nombre_partido
        self.carpeta.mkdir(parents=True, exist_ok=True)
        
        self.estado_actual = "DESCONOCIDO"
        self.ultimo_check = 0
        self.historial = []
        
        self.stats = {
            'checks_gemini': 0,
            'checks_promiedos': 0,
            'aciertos_gemini': 0,
            'fallos_gemini': 0
        }
    
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🔍 {msg}")
    
    def verificar_estado(self, stream_url, headers=None):
        """Verifica usando Gemini primero, Promiedos como backup"""
        ahora = time.time()
        
        if ahora - self.ultimo_check < INTERVALO_VERIFICACION:
            return self.estado_actual
        
        self.ultimo_check = ahora
        
        self.log("="*50)
        self.log("🎯 Verificando estado del partido...")
        self.log("📸 Método primario: Gemini Vision AI (v2 optimizado)")
        
        # Capturar frames
        frames = capturar_multiples_frames(
            stream_url, headers, self.nombre_partido, CAPTURAS_POR_VERIFICACION
        )
        
        if frames:
            # Analizar con consenso
            resultado_consenso, todos_resultados = analizar_con_consenso(frames)
            
            self.stats['checks_gemini'] += 1
            
            if resultado_consenso and resultado_consenso.get('confianza', 0) >= CONFIANZA_MINIMA:
                estado = resultado_consenso['estado']
                confianza = resultado_consenso['confianza']
                
                self.log(f"   ✅ GEMINI CONCLUYENTE: {estado}")
                self.log(f"   📊 Confianza: {confianza:.0%}")
                
                if 'votos' in resultado_consenso:
                    self.log(f"   🗳️  Votos: {resultado_consenso['votos']}")
                
                self.log(f"   💡 {resultado_consenso['evidencia']}")
                
                if estado != self.estado_actual:
                    self.log(f"   🚨 CAMBIO: {self.estado_actual} → {estado}")
                    self.estado_actual = estado
                    
                    self.historial.append({
                        'timestamp': datetime.now(),
                        'estado': estado,
                        'metodo': 'gemini',
                        'confianza': confianza,
                        'votos': resultado_consenso.get('votos', {}),
                        'frames': frames
                    })
                
                self.stats['aciertos_gemini'] += 1
                return self.estado_actual
        
        # Fallback a Promiedos
        self.stats['fallos_gemini'] += 1
        self.log("   ⚠️ Gemini no concluyente o sin frames")
        
        if self.url_promiedos:
            self.log("📡 Método secundario: Promiedos")
            
            try:
                import promiedos_client
                estado_promiedos = promiedos_client.obtener_estado_partido(self.url_promiedos)
                
                self.stats['checks_promiedos'] += 1
                
                # Mapear
                if estado_promiedos in ['JUGANDO_1T', 'JUGANDO_2T']:
                    estado_mapeado = 'JUGANDO'
                elif estado_promiedos in ['PREVIA', 'ENTRETIEMPO', 'FINAL']:
                    estado_mapeado = 'NO_JUGANDO'
                else:
                    estado_mapeado = 'DESCONOCIDO'
                
                self.log(f"   📡 Promiedos: {estado_promiedos} → {estado_mapeado}")
                
                if estado_mapeado != self.estado_actual and estado_mapeado != 'DESCONOCIDO':
                    self.estado_actual = estado_mapeado
                    self.log(f"   🚨 CAMBIO: → {estado_mapeado}")
                    
                    self.historial.append({
                        'timestamp': datetime.now(),
                        'estado': estado_mapeado,
                        'metodo': 'promiedos',
                        'estado_original': estado_promiedos
                    })
                
                return self.estado_actual
                
            except Exception as e:
                self.log(f"   ❌ Error Promiedos: {str(e)[:60]}")
        
        self.log(f"   ⚠️ Manteniendo estado: {self.estado_actual}")
        return self.estado_actual
    
    def obtener_estado(self):
        return self.estado_actual
    
    def forzar_verificacion(self, stream_url, headers=None):
        self.ultimo_check = 0
        return self.verificar_estado(stream_url, headers)
    
    def obtener_estadisticas(self):
        total = self.stats['checks_gemini'] + self.stats['checks_promiedos']
        if total == 0:
            return self.stats
        
        return {
            **self.stats,
            'uso_gemini_pct': (self.stats['checks_gemini'] / total) * 100,
            'uso_promiedos_pct': (self.stats['checks_promiedos'] / total) * 100,
            'precision_gemini': (self.stats['aciertos_gemini'] / max(self.stats['checks_gemini'], 1)) * 100
        }

# ============ FUNCIÓN COMPATIBLE ============

def obtener_estado_partido_hibrido(detector, stream_url, headers=None):
    return detector.verificar_estado(stream_url, headers)

# ============ TEST ============

def test_detector_hibrido():
    print("\n" + "="*70)
    print("🧪 TEST DETECTOR HÍBRIDO v2 (PROMPTS OPTIMIZADOS)")
    print("="*70 + "\n")
    
    if not GEMINI_API_KEY:
        print("❌ Configurar GEMINI_API_KEY primero")
        return False
    
    detector = HybridStateDetector(
        "TEST_PARTIDO",
        url_promiedos="https://www.promiedos.com.ar/game/atletico-madrid-vs-valencia/eeghefi"
    )
    
    test_stream = "https://8c51.crackstreamslivehd.com/sporttvbr1/tracks-v1a1/mono.m3u8?ip=181.27.51.162&token=0719160106c9d9b121c2c07e959c49f316022adf-d2-1765681261-1765627261"
    
    print("Ejecutando verificación (esto puede tardar ~60s)...\n")
    
    estado = detector.forzar_verificacion(test_stream)
    
    print(f"\n{'='*70}")
    print(f"📊 RESULTADO FINAL: {estado}")
    print(f"{'='*70}\n")
    
    stats = detector.obtener_estadisticas()
    print("📈 ESTADÍSTICAS:")
    print(f"   Checks Gemini: {stats['checks_gemini']}")
    print(f"   Aciertos Gemini: {stats['aciertos_gemini']}")
    print(f"   Checks Promiedos: {stats['checks_promiedos']}")
    print(f"   Precisión Gemini: {stats.get('precision_gemini', 0):.0f}%")
    
    if detector.historial:
        print(f"\n📜 Historial:")
        for h in detector.historial:
            print(f"   {h['estado']} (método: {h['metodo']})")
            if 'confianza' in h:
                print(f"   Confianza: {h['confianza']:.0%}")
    
    return True

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_detector_hibrido()
    else:
        print(__doc__)
        print("\nPara test: python vision_detector_v2.py test")