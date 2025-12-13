#!/usr/bin/env python3
"""
vision_detector.py - Sistema de detección de estado del partido mediante IA
USA GEMINI (Google AI) - API gratuita con cuota generosa
Analiza frames del stream en vivo para detectar:
- Inicio del partido (kickoff)
- Fin del primer tiempo
- Entretiempo
- Inicio del segundo tiempo
- Final del partido

MODELOS DISPONIBLES Y CUOTAS (según tu cuenta):
- gemini-2.5-flash: 5 RPM, 250K TPM, 20 RPD (RECOMENDADO para análisis)
- gemini-2.5-flash-lite: 10 RPM, 250K TPM, 20 RPD (más rápido pero menos preciso)
- gemma-3-12b: 30 RPM, 15K TPM, 14.4K RPD (modelo de texto, no visión)

RPM = Requests Per Minute
TPM = Tokens Per Minute  
RPD = Requests Per Day

NOTA: Con 20 RPD, puedes analizar ~1 partido completo por día (intervalo 30s)
"""

import time
import subprocess
import os
import json
from datetime import datetime
from pathlib import Path

# ============ CONFIGURACIÓN ============
INTERVALO_CAPTURA = 270  # 4.5 minutos (20 capturas en 90 min = justo dentro de 20 RPD)
CARPETA_FRAMES = "./frames_analisis"
CONFIANZA_MINIMA = 0.65  # Reducido porque tenemos menos muestras

# IMPORTANTE: Configurar tu API key de Gemini
# Obtenerla en: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = "AIzaSyBXHAYLlDLZQHQkaYl_oCpHWGUoNG3D3cU"  # O poner directamente aquí

# Cache para evitar consultas repetidas
_cache_analisis = {}  # Cache de análisis por hash de imagen
_ultimo_estado = "DESCONOCIDO"

# Rate limiting
_ultimo_request_time = 0
MIN_TIEMPO_ENTRE_REQUESTS = 12  # Mínimo 12 segundos (5 RPM = 12s por request)

# ============ UTILIDADES DE CAPTURA ============

def capturar_frame_del_stream(stream_url, output_path, headers=None):
    """
    Captura un frame del stream HLS usando FFmpeg
    """
    try:
        # Headers para FFmpeg
        headers_str = ""
        if headers:
            for k, v in headers.items():
                headers_str += f"{k}: {v}\\r\\n"
        
        cmd = [
            'ffmpeg',
            '-headers', headers_str if headers_str else 'User-Agent: Mozilla/5.0\\r\\n',
            '-i', stream_url,
            '-vframes', '1',  # Solo 1 frame
            '-q:v', '2',  # Alta calidad
            '-y',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )
        
        if result.returncode == 0 and os.path.exists(output_path):
            size = os.path.getsize(output_path)
            if size > 1000:  # Al menos 1KB
                return True
        
        return False
        
    except Exception as e:
        print(f"⚠️ Error capturando frame: {e}")
        return False

# ============ ANÁLISIS CON GEMINI API ============

def analizar_frame_con_gemini(ruta_imagen, contexto_anterior=None):
    """
    Analiza un frame usando Gemini API para detectar el estado del partido
    Con sistema de reintentos, fallback a modelos alternativos y rate limiting
    """
    global _ultimo_request_time
    
    try:
        import google.generativeai as genai
    except ImportError:
        print("❌ ERROR: Instalar google-generativeai")
        print("   pip install google-generativeai")
        return None
    
    if not GEMINI_API_KEY:
        print("❌ ERROR: GEMINI_API_KEY no configurada")
        print("   1. Obtener key en: https://aistudio.google.com/app/apikey")
        print("   2. Configurar: export GEMINI_API_KEY='tu-key'")
        print("   3. O editar vision_detector.py y poner la key directamente")
        return None
    
    # Rate limiting: esperar si es necesario
    tiempo_desde_ultimo = time.time() - _ultimo_request_time
    if tiempo_desde_ultimo < MIN_TIEMPO_ENTRE_REQUESTS:
        espera = MIN_TIEMPO_ENTRE_REQUESTS - tiempo_desde_ultimo
        print(f"      ⏱️  Rate limit: esperando {espera:.1f}s...")
        time.sleep(espera)
    
    # Verificar cache
    try:
        import hashlib
        with open(ruta_imagen, 'rb') as f:
            img_hash = hashlib.md5(f.read()).hexdigest()
        
        if img_hash in _cache_analisis:
            print(f"      💾 Usando resultado cacheado")
            return _cache_analisis[img_hash]
    except:
        img_hash = None
    
    # Configurar Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Lista de modelos a probar (en orden de preferencia)
    # Basado en los modelos disponibles en tu cuenta
    modelos = [
        'gemini-2.5-flash',        # Recomendado: 5 RPM, 250K TPM, 20 RPD
        'gemini-2.5-flash-lite',   # Alternativa: 10 RPM, más rápido pero menos preciso
    ]
    
    ultimo_error = None
    
    # Prompt optimizado para detección de estados
    prompt = """Analiza esta imagen de un partido de fútbol y determina el estado actual.

ESTADOS POSIBLES:
1. PREVIA - Antes del inicio (jugadores alineados, cámaras del estadio, publicidad)
2. JUGANDO_1T - Primer tiempo en curso (jugadores en el campo, balón en movimiento, reloj 0-45min)
3. ENTRETIEMPO - Descanso entre tiempos (jugadores fuera del campo, análisis en estudio, publicidad)
4. JUGANDO_2T - Segundo tiempo en curso (jugadores en el campo, reloj 45-90min)
5. FINAL - Partido terminado (jugadores celebrando/despidiéndose, resumen, entrevistas)

INDICADORES CLAVE A BUSCAR:
- Reloj del partido (minuto actual) - MUY IMPORTANTE
- Marcador visible
- Posición de jugadores
- Gráficos de TV (ej: "ENTRETIEMPO", "FINAL DEL PARTIDO", "1T", "2T")
- Publicidad estática vs jugadores en movimiento
- Textos en pantalla que indiquen el estado

IMPORTANTE: Busca primero el RELOJ o TIEMPO del partido. Si ves un número entre 1-45, es 1T. Si ves 45-90, es 2T.

Responde ÚNICAMENTE con un JSON en este formato (sin markdown, sin ```):
{
  "estado": "JUGANDO_1T",
  "confianza": 0.95,
  "minuto": 23,
  "marcador": "2-1",
  "evidencia": "Se ve el reloj en 23', jugadores corriendo, balón en juego"
}"""

    if contexto_anterior:
        prompt += f"\n\nCONTEXTO: El estado anterior era '{contexto_anterior}'. Solo cambia si hay evidencia clara del cambio."
    
    # Intentar con cada modelo
    for i, modelo_nombre in enumerate(modelos):
        try:
            print(f"      Probando con {modelo_nombre}...")
            model = genai.GenerativeModel(modelo_nombre)
            
            # Cargar imagen
            from PIL import Image
            img = Image.open(ruta_imagen)
            
            # Generar contenido con timeout
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError("Timeout de Gemini")
            
            # Configurar timeout de 30 segundos
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(30)
            
            try:
                response = model.generate_content([prompt, img])
                signal.alarm(0)  # Cancelar timeout
            except TimeoutError:
                print(f"      ⏱️  Timeout con {modelo_nombre}")
                continue
            
            if not response or not response.text:
                print(f"      ⚠️ {modelo_nombre} no devolvió respuesta")
                continue
            
            # Extraer texto
            text_response = response.text.strip()
            
            # Limpiar markdown si viene
            if text_response.startswith("```json"):
                text_response = text_response[7:]
            if text_response.startswith("```"):
                text_response = text_response[3:]
            if text_response.endswith("```"):
                text_response = text_response[:-3]
            
            text_response = text_response.strip()
            
            # Parsear JSON
            result = json.loads(text_response)
            
            # Guardar en cache
            if img_hash:
                _cache_analisis[img_hash] = result
            
            # Actualizar timestamp
            _ultimo_request_time = time.time()
            
            print(f"      ✅ Análisis exitoso con {modelo_nombre}")
            return result
            
        except ImportError:
            print("❌ ERROR: Instalar Pillow para cargar imágenes")
            print("   pip install Pillow")
            return None
        except json.JSONDecodeError as e:
            print(f"      ⚠️ Error JSON con {modelo_nombre}: {e}")
            ultimo_error = e
            continue
        except Exception as e:
            error_str = str(e)
            
            # Si es error 429 (rate limit), esperar y reintentar
            if "429" in error_str or "quota" in error_str.lower():
                if i < len(modelos) - 1:
                    print(f"      ⏱️  Cuota excedida en {modelo_nombre}, probando siguiente...")
                    time.sleep(2)
                    continue
                else:
                    print(f"      ❌ Cuota excedida en todos los modelos")
                    print(f"      💡 Espera 60 segundos o usa otra API key")
            else:
                print(f"      ⚠️ Error con {modelo_nombre}: {error_str[:80]}")
            
            ultimo_error = e
            continue
    
    # Si llegamos aquí, todos los modelos fallaron
    if ultimo_error:
        print(f"❌ Error en análisis Gemini (todos los modelos): {ultimo_error}")
    
    return None

# ============ GESTOR DE ESTADO CON IA ============

class VisionStateDetector:
    """
    Detector de estado del partido usando visión por computadora con Gemini
    """
    
    def __init__(self, nombre_partido):
        self.nombre_partido = nombre_partido
        self.carpeta_frames = Path(CARPETA_FRAMES) / nombre_partido
        self.carpeta_frames.mkdir(parents=True, exist_ok=True)
        
        self.estado_actual = "DESCONOCIDO"
        self.historial_estados = []
        self.ultima_captura = 0
        self.confirmaciones_requeridas = 2
        self.contador_confirmaciones = 0
        self.candidato_nuevo_estado = None
        
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🔍 {msg}")
        
    def capturar_y_analizar(self, stream_url, headers=None):
        """
        Captura un frame del stream y lo analiza con IA
        """
        ahora = time.time()
        
        # Evitar capturas muy frecuentes
        if ahora - self.ultima_captura < INTERVALO_CAPTURA:
            return self.estado_actual
        
        self.ultima_captura = ahora
        
        # Generar nombre de archivo con timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        frame_path = self.carpeta_frames / f"frame_{timestamp}.jpg"
        
        self.log(f"Capturando frame del stream...")
        
        # Capturar frame
        if not capturar_frame_del_stream(stream_url, str(frame_path), headers):
            self.log("❌ No se pudo capturar frame")
            return self.estado_actual
        
        self.log(f"Frame capturado: {frame_path.name}")
        
        # Analizar con Gemini
        self.log("Analizando con Gemini AI...")
        resultado = analizar_frame_con_gemini(str(frame_path), self.estado_actual)
        
        if not resultado:
            self.log("⚠️ No se pudo analizar frame")
            return self.estado_actual
        
        # Procesar resultado
        nuevo_estado = resultado.get("estado", "DESCONOCIDO")
        confianza = resultado.get("confianza", 0)
        evidencia = resultado.get("evidencia", "N/A")
        minuto = resultado.get("minuto")
        marcador = resultado.get("marcador")
        
        self.log(f"Detección: {nuevo_estado} (confianza: {confianza:.0%})")
        self.log(f"Evidencia: {evidencia}")
        
        if minuto:
            self.log(f"Minuto: {minuto}'")
        if marcador:
            self.log(f"Marcador: {marcador}")
        
        # Validar confianza
        if confianza < CONFIANZA_MINIMA:
            self.log(f"⚠️ Confianza baja, manteniendo estado: {self.estado_actual}")
            return self.estado_actual
        
        # Sistema de confirmación para evitar falsos positivos
        if nuevo_estado != self.estado_actual:
            if self.candidato_nuevo_estado == nuevo_estado:
                self.contador_confirmaciones += 1
                self.log(f"Confirmación {self.contador_confirmaciones}/{self.confirmaciones_requeridas} para cambio a {nuevo_estado}")
                
                if self.contador_confirmaciones >= self.confirmaciones_requeridas:
                    # CAMBIO DE ESTADO CONFIRMADO
                    self.log(f"✅ CAMBIO DE ESTADO: {self.estado_actual} → {nuevo_estado}")
                    self.estado_actual = nuevo_estado
                    self.historial_estados.append({
                        'timestamp': datetime.now(),
                        'estado': nuevo_estado,
                        'confianza': confianza,
                        'minuto': minuto,
                        'marcador': marcador,
                        'evidencia': evidencia,
                        'frame': str(frame_path)
                    })
                    self.contador_confirmaciones = 0
                    self.candidato_nuevo_estado = None
                    
                    # Eliminar frames antiguos para ahorrar espacio (mantener últimos 10)
                    self._limpiar_frames_antiguos()
            else:
                # Nuevo candidato
                self.candidato_nuevo_estado = nuevo_estado
                self.contador_confirmaciones = 1
                self.log(f"Nuevo candidato de estado: {nuevo_estado}")
        else:
            # Estado se mantiene igual, resetear contador
            self.contador_confirmaciones = 0
            self.candidato_nuevo_estado = None
        
        return self.estado_actual
    
    def _limpiar_frames_antiguos(self):
        """Mantiene solo los últimos 10 frames para ahorrar espacio"""
        try:
            frames = sorted(self.carpeta_frames.glob("frame_*.jpg"))
            if len(frames) > 10:
                for frame in frames[:-10]:
                    frame.unlink()
        except Exception as e:
            self.log(f"⚠️ Error limpiando frames: {e}")
    
    def obtener_estado(self):
        """Retorna el estado actual sin capturar nuevo frame"""
        return self.estado_actual
    
    def obtener_historial(self):
        """Retorna el historial de cambios de estado"""
        return self.historial_estados
    
    def forzar_analisis(self, stream_url, headers=None):
        """Fuerza un análisis inmediato sin respetar intervalo"""
        self.ultima_captura = 0
        return self.capturar_y_analizar(stream_url, headers)

# ============ FUNCIONES DE CONVENIENCIA ============

def crear_detector(nombre_partido):
    """
    Crea un detector de estado para un partido
    """
    return VisionStateDetector(nombre_partido)

def monitorear_partido_continuo(detector, stream_url, headers=None, callback=None):
    """
    Monitorea continuamente el partido y ejecuta callback cuando cambia el estado
    
    Args:
        detector: VisionStateDetector
        stream_url: URL del stream a monitorear
        headers: Headers HTTP opcionales
        callback: Función a ejecutar cuando cambia estado (recibe nuevo_estado, info)
    """
    print(f"\n{'='*70}")
    print(f"👁️ MONITOREO CONTINUO CON GEMINI AI - {detector.nombre_partido}")
    print(f"{'='*70}\n")
    
    estado_anterior = detector.obtener_estado()
    
    try:
        while True:
            # Analizar
            estado_actual = detector.capturar_y_analizar(stream_url, headers)
            
            # Detectar cambio
            if estado_actual != estado_anterior:
                print(f"\n🚨 CAMBIO DE ESTADO DETECTADO:")
                print(f"   {estado_anterior} → {estado_actual}")
                
                if callback:
                    historial = detector.obtener_historial()
                    ultima_deteccion = historial[-1] if historial else {}
                    callback(estado_actual, ultima_deteccion)
                
                estado_anterior = estado_actual
            
            # Verificar si terminó
            if estado_actual == "FINAL":
                print("\n🏁 PARTIDO FINALIZADO - Deteniendo monitoreo")
                break
            
            # Esperar antes de próxima captura
            time.sleep(INTERVALO_CAPTURA)
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Monitoreo detenido por usuario")

# ============ INTEGRACIÓN CON SISTEMA EXISTENTE ============

def obtener_estado_partido_vision(detector, stream_url, headers=None):
    """
    Función compatible con la API de promiedos_client
    Retorna: 'PREVIA', 'JUGANDO_1T', 'ENTRETIEMPO', 'JUGANDO_2T', 'FINAL', 'ERROR'
    """
    try:
        estado = detector.capturar_y_analizar(stream_url, headers)
        
        # Mapear estados si es necesario
        if estado == "DESCONOCIDO":
            return "ERROR"
        
        return estado
        
    except Exception as e:
        print(f"❌ Error obteniendo estado: {e}")
        return "ERROR"

# ============ TESTING ============

def test_vision_detector():
    """Test básico del detector"""
    print("\n" + "="*70)
    print("🧪 TEST DEL DETECTOR DE VISIÓN CON GEMINI")
    print("="*70 + "\n")
    
    # Verificar API key
    if not GEMINI_API_KEY:
        print("❌ ERROR: GEMINI_API_KEY no configurada")
        print("\n📝 PASOS PARA CONFIGURAR:")
        print("   1. Ve a: https://aistudio.google.com/app/apikey")
        print("   2. Crea/copia tu API key")
        print("   3. Ejecuta: export GEMINI_API_KEY='tu-key-aqui'")
        print("   4. O edita vision_detector.py línea 23")
        return False
    
    # Crear detector de prueba
    detector = crear_detector("TEST_PARTIDO")
    
    # Stream de prueba (NASA TV)
    test_stream = "https://doc1.crackstreamslivehd.com/espndeportes/tracks-v1a1/mono.m3u8?ip=181.27.51.162&token=b59bd7c52bdbd4d2b13860793707976c1bd78ab9-9a-1765676511-1765622511"
    
    print("1. Capturando frame de prueba...")
    estado = detector.forzar_analisis(test_stream)
    
    print(f"\n2. Estado detectado: {estado}")
    
    historial = detector.obtener_historial()
    if historial:
        print(f"\n3. Historial:")
        for i, h in enumerate(historial, 1):
            print(f"   {i}. {h['estado']} (confianza: {h['confianza']:.0%})")
            print(f"      Evidencia: {h['evidencia']}")
            if h.get('minuto'):
                print(f"      Minuto: {h['minuto']}'")
    
    print("\n✅ Test completado")
    print(f"   Frames guardados en: {detector.carpeta_frames}")
    
    return True

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_vision_detector()
    else:
        print("\n" + "="*70)
        print("👁️ DETECTOR DE ESTADO POR VISIÓN - GEMINI AI")
        print("="*70)
        print("\n🔑 CONFIGURACIÓN DE API KEY:")
        print("""
1. Obtener API key GRATUITA de Gemini:
   https://aistudio.google.com/app/apikey

2. Configurar (opción A - recomendada):
   export GEMINI_API_KEY='tu-key-aqui'

3. O configurar (opción B):
   Editar vision_detector.py línea 23:
   GEMINI_API_KEY = "tu-key-aqui"
""")
        
        print("\nUSO BÁSICO:")
        print("""
from vision_detector import crear_detector, obtener_estado_partido_vision

# Crear detector
detector = crear_detector("Racing_vs_Estudiantes")

# Durante la grabación, consultar estado
estado = obtener_estado_partido_vision(
    detector, 
    stream_url="https://...",
    headers={'User-Agent': '...', 'Referer': '...'}
)

print(f"Estado actual: {estado}")
# Output: JUGANDO_1T, ENTRETIEMPO, JUGANDO_2T, FINAL, etc.
""")
        
        print("\n💡 VENTAJAS DE GEMINI:")
        print("  ✅ API gratuita generosa (1500 requests/día)")
        print("  ✅ Gemini 2.0 Flash - súper rápido")
        print("  ✅ Excelente para análisis visual")
        print("  ✅ Sin costos hasta uso intensivo")
        
        print("\nPARA TESTING:")
        print("  python vision_detector.py test")