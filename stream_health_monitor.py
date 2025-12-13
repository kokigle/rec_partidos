#!/usr/bin/env python3
"""
stream_health_monitor.py - MONITOREO EN TIEMPO REAL DE CONTENIDO
Detecta pantallas negras, congelamiento y pérdida de señal
USANDO FFMPEG DIRECTAMENTE - Sin depender de IA
"""

import subprocess
import os
import time
import threading
from datetime import datetime
import json

# ============ CONFIGURACIÓN ============

# Thresholds para detección de problemas
THRESHOLD_PANTALLA_NEGRA = 15  # Si promedio de brillo < 15/255 → negro
THRESHOLD_CONGELAMIENTO = 0.02  # Si cambio entre frames < 2% → congelado
THRESHOLD_SILENCIO_AUDIO = -60  # dB - Si audio < -60dB → silencio

# Intervalos de verificación
INTERVALO_CHECK_FRAMES = 10  # Cada 10 segundos
FRAMES_PARA_ANALISIS = 3  # Analizar 3 frames consecutivos

# Límites de tolerancia
MAX_FRAMES_NEGROS_CONSECUTIVOS = 3  # 3 checks = 30s de negro = problema
MAX_FRAMES_CONGELADOS_CONSECUTIVOS = 4  # 4 checks = 40s congelado = problema

# ============ FUNCIONES DE ANÁLISIS ============

def analizar_brillo_frame(ruta_frame):
    """
    Analiza el brillo promedio de un frame usando ffmpeg
    Retorna: float 0-255 (0=negro total, 255=blanco total)
    """
    try:
        cmd = [
            'ffmpeg',
            '-i', ruta_frame,
            '-vf', 'signalstats',
            '-f', 'null',
            '-'
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Parsear output para encontrar YAVG (brillo promedio)
        for line in result.stderr.split('\n'):
            if 'lavfi.signalstats.YAVG' in line:
                # Formato: [Parsed_signalstats_0 @ ...] lavfi.signalstats.YAVG=123.456
                try:
                    brillo = float(line.split('=')[-1])
                    return brillo
                except:
                    pass
        
        return None
        
    except Exception as e:
        return None

def analizar_diferencia_frames(frame1, frame2):
    """
    Calcula la diferencia entre dos frames usando ffmpeg
    Retorna: float 0.0-1.0 (0=idénticos, 1=completamente diferentes)
    """
    try:
        # Usar filtro de diferencia de ffmpeg
        cmd = [
            'ffmpeg',
            '-i', frame1,
            '-i', frame2,
            '-filter_complex', '[0:v][1:v]blend=all_mode=difference,signalstats',
            '-f', 'null',
            '-'
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Parsear diferencia promedio
        for line in result.stderr.split('\n'):
            if 'lavfi.signalstats.YAVG' in line:
                try:
                    diff = float(line.split('=')[-1])
                    # Normalizar a 0-1
                    return diff / 255.0
                except:
                    pass
        
        return None
        
    except Exception as e:
        return None

def analizar_nivel_audio(ruta_video, duracion_segundos=2):
    """
    Analiza el nivel de audio promedio usando ffmpeg
    Retorna: float en dB (-90 a 0, típicamente)
    """
    try:
        cmd = [
            'ffmpeg',
            '-i', ruta_video,
            '-t', str(duracion_segundos),
            '-af', 'volumedetect',
            '-f', 'null',
            '-'
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duracion_segundos + 5
        )
        
        # Parsear mean_volume
        for line in result.stderr.split('\n'):
            if 'mean_volume:' in line:
                try:
                    # Formato: [Parsed_volumedetect_0 @ ...] mean_volume: -23.5 dB
                    db = float(line.split(':')[-1].replace('dB', '').strip())
                    return db
                except:
                    pass
        
        return None
        
    except Exception as e:
        return None

def capturar_frame_para_analisis(ruta_video, timestamp_segundos, output_path):
    """
    Captura un frame específico del video en curso
    """
    try:
        cmd = [
            'ffmpeg',
            '-ss', str(timestamp_segundos),
            '-i', ruta_video,
            '-vframes', '1',
            '-q:v', '2',
            '-y',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        
        return os.path.exists(output_path) and os.path.getsize(output_path) > 5000
        
    except:
        return False

# ============ MONITOR DE STREAM ============

class StreamHealthMonitor:
    """
    Monitorea la salud de un stream en tiempo real
    Detecta: pantalla negra, congelamiento, pérdida de señal
    """
    
    def __init__(self, stream_id, ruta_archivo, nombre_partido):
        self.stream_id = stream_id
        self.ruta_archivo = ruta_archivo
        self.nombre_partido = nombre_partido
        
        self.estado = "iniciando"  # iniciando, ok, advertencia, critico
        self.problemas_detectados = []
        
        # Contadores de problemas consecutivos
        self.frames_negros_consecutivos = 0
        self.frames_congelados_consecutivos = 0
        self.checks_sin_audio_consecutivos = 0
        
        # Historial
        self.historial_checks = []
        self.ultimo_check = 0
        
        # Threading
        self.monitoring = False
        self.thread = None
        
        # Carpeta temporal para frames de análisis
        self.carpeta_temp = f"./temp_health_check/{nombre_partido}"
        os.makedirs(self.carpeta_temp, exist_ok=True)
    
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🔍 [S{self.stream_id}] {msg}")
    
    def iniciar_monitoreo(self):
        """Inicia el monitoreo en background"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.thread = threading.Thread(target=self._loop_monitoreo, daemon=True)
        self.thread.start()
        
        self.log("✅ Monitoreo iniciado")
    
    def detener_monitoreo(self):
        """Detiene el monitoreo"""
        self.monitoring = False
        if self.thread:
            self.thread.join(timeout=5)
        
        # Limpiar frames temporales
        try:
            import shutil
            if os.path.exists(self.carpeta_temp):
                shutil.rmtree(self.carpeta_temp)
        except:
            pass
        
        self.log("🛑 Monitoreo detenido")
    
    def _loop_monitoreo(self):
        """Loop principal de monitoreo"""
        while self.monitoring:
            try:
                ahora = time.time()
                
                # Esperar intervalo
                if ahora - self.ultimo_check < INTERVALO_CHECK_FRAMES:
                    time.sleep(1)
                    continue
                
                self.ultimo_check = ahora
                
                # Verificar que el archivo existe y está creciendo
                if not os.path.exists(self.ruta_archivo):
                    self.estado = "advertencia"
                    self.log("⚠️ Archivo no existe")
                    time.sleep(5)
                    continue
                
                # Realizar checks
                resultado_check = self._realizar_check()
                
                # Actualizar estado según resultados
                self._actualizar_estado(resultado_check)
                
            except Exception as e:
                self.log(f"❌ Error en monitoreo: {str(e)[:60]}")
                time.sleep(5)
    
    def _realizar_check(self):
        """
        Realiza un check completo del stream
        Retorna: dict con resultados
        """
        resultado = {
            'timestamp': datetime.now(),
            'pantalla_negra': False,
            'congelado': False,
            'sin_audio': False,
            'detalles': {}
        }
        
        try:
            # Obtener tamaño y duración actuales
            tamaño_actual = os.path.getsize(self.ruta_archivo)
            
            # Si es muy pequeño, esperar
            if tamaño_actual < 1024 * 1024:  # < 1MB
                resultado['detalles']['razon'] = 'Archivo muy pequeño, esperando...'
                return resultado
            
            # 1. CHECK DE PANTALLA NEGRA
            # Capturar frame actual (últimos 5 segundos del archivo)
            frame_path = f"{self.carpeta_temp}/check_{int(time.time())}.jpg"
            
            # Calcular timestamp (últimos 5s del archivo)
            duracion_estimada = tamaño_actual / (2 * 1024 * 1024)  # Asumir ~2MB/s
            timestamp_frame = max(0, duracion_estimada - 5)
            
            if capturar_frame_para_analisis(self.ruta_archivo, timestamp_frame, frame_path):
                brillo = analizar_brillo_frame(frame_path)
                
                if brillo is not None:
                    resultado['detalles']['brillo'] = brillo
                    
                    if brillo < THRESHOLD_PANTALLA_NEGRA:
                        resultado['pantalla_negra'] = True
                        self.log(f"⚫ Pantalla negra detectada (brillo: {brillo:.1f}/255)")
                
                # Limpiar frame
                try:
                    os.remove(frame_path)
                except:
                    pass
            
            # 2. CHECK DE CONGELAMIENTO
            # Comparar con frame anterior si existe
            frame_anterior = f"{self.carpeta_temp}/frame_anterior.jpg"
            
            if os.path.exists(frame_anterior) and os.path.exists(frame_path):
                diferencia = analizar_diferencia_frames(frame_anterior, frame_path)
                
                if diferencia is not None:
                    resultado['detalles']['diferencia'] = diferencia
                    
                    if diferencia < THRESHOLD_CONGELAMIENTO:
                        resultado['congelado'] = True
                        self.log(f"❄️ Congelamiento detectado (diff: {diferencia:.3f})")
            
            # Guardar frame actual como referencia para próximo check
            if os.path.exists(frame_path):
                try:
                    import shutil
                    shutil.copy(frame_path, frame_anterior)
                except:
                    pass
            
            # 3. CHECK DE AUDIO (cada 3 checks para no sobrecargar)
            if len(self.historial_checks) % 3 == 0:
                nivel_audio = analizar_nivel_audio(self.ruta_archivo, duracion_segundos=2)
                
                if nivel_audio is not None:
                    resultado['detalles']['audio_db'] = nivel_audio
                    
                    if nivel_audio < THRESHOLD_SILENCIO_AUDIO:
                        resultado['sin_audio'] = True
                        self.log(f"🔇 Silencio detectado (audio: {nivel_audio:.1f}dB)")
            
        except Exception as e:
            resultado['detalles']['error'] = str(e)[:100]
        
        return resultado
    
    def _actualizar_estado(self, resultado_check):
        """Actualiza el estado según resultados del check"""
        self.historial_checks.append(resultado_check)
        
        # Actualizar contadores
        if resultado_check['pantalla_negra']:
            self.frames_negros_consecutivos += 1
        else:
            self.frames_negros_consecutivos = 0
        
        if resultado_check['congelado']:
            self.frames_congelados_consecutivos += 1
        else:
            self.frames_congelados_consecutivos = 0
        
        if resultado_check['sin_audio']:
            self.checks_sin_audio_consecutivos += 1
        else:
            self.checks_sin_audio_consecutivos = 0
        
        # Evaluar estado
        if self.frames_negros_consecutivos >= MAX_FRAMES_NEGROS_CONSECUTIVOS:
            self.estado = "critico"
            self.problemas_detectados.append({
                'tipo': 'pantalla_negra',
                'timestamp': datetime.now(),
                'duracion_segundos': self.frames_negros_consecutivos * INTERVALO_CHECK_FRAMES
            })
            self.log(f"🚨 CRÍTICO: Pantalla negra por {self.frames_negros_consecutivos * INTERVALO_CHECK_FRAMES}s")
        
        elif self.frames_congelados_consecutivos >= MAX_FRAMES_CONGELADOS_CONSECUTIVOS:
            self.estado = "critico"
            self.problemas_detectados.append({
                'tipo': 'congelado',
                'timestamp': datetime.now(),
                'duracion_segundos': self.frames_congelados_consecutivos * INTERVALO_CHECK_FRAMES
            })
            self.log(f"🚨 CRÍTICO: Stream congelado por {self.frames_congelados_consecutivos * INTERVALO_CHECK_FRAMES}s")
        
        elif any([resultado_check['pantalla_negra'], resultado_check['congelado']]):
            self.estado = "advertencia"
        
        else:
            self.estado = "ok"
    
    def obtener_estado(self):
        """Retorna el estado actual"""
        return {
            'estado': self.estado,
            'frames_negros_consecutivos': self.frames_negros_consecutivos,
            'frames_congelados_consecutivos': self.frames_congelados_consecutivos,
            'checks_sin_audio_consecutivos': self.checks_sin_audio_consecutivos,
            'total_checks': len(self.historial_checks),
            'problemas': self.problemas_detectados
        }
    
    def hay_problema_critico(self):
        """Retorna True si hay un problema crítico"""
        return self.estado == "critico"


# ============ MANAGER DE MÚLTIPLES STREAMS ============

class MultiStreamHealthManager:
    """
    Gestiona el monitoreo de múltiples streams simultáneos
    """
    
    def __init__(self, nombre_partido):
        self.nombre_partido = nombre_partido
        self.monitores = {}
    
    def registrar_stream(self, stream_id, ruta_archivo):
        """Registra y comienza a monitorear un stream"""
        if stream_id in self.monitores:
            return
        
        monitor = StreamHealthMonitor(stream_id, ruta_archivo, self.nombre_partido)
        monitor.iniciar_monitoreo()
        
        self.monitores[stream_id] = monitor
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Stream S{stream_id} registrado para monitoreo")
    
    def obtener_stream_mas_saludable(self):
        """Retorna el ID del stream más saludable"""
        if not self.monitores:
            return None
        
        # Filtrar solo streams OK o con advertencia
        streams_validos = {
            sid: mon for sid, mon in self.monitores.items()
            if mon.estado in ['ok', 'advertencia']
        }
        
        if not streams_validos:
            return None
        
        # Priorizar por menos problemas acumulados
        mejor_stream = min(
            streams_validos.items(),
            key=lambda x: (
                x[1].frames_negros_consecutivos,
                x[1].frames_congelados_consecutivos,
                len(x[1].problemas_detectados)
            )
        )
        
        return mejor_stream[0]
    
    def obtener_streams_problematicos(self):
        """Retorna lista de IDs de streams con problemas críticos"""
        return [
            sid for sid, mon in self.monitores.items()
            if mon.hay_problema_critico()
        ]
    
    def detener_todos(self):
        """Detiene el monitoreo de todos los streams"""
        for monitor in self.monitores.values():
            monitor.detener_monitoreo()
        
        self.monitores.clear()
    
    def obtener_reporte(self):
        """Genera un reporte completo de todos los streams"""
        reporte = {
            'timestamp': datetime.now(),
            'total_streams': len(self.monitores),
            'streams': {}
        }
        
        for sid, monitor in self.monitores.items():
            reporte['streams'][sid] = monitor.obtener_estado()
        
        return reporte


# ============ TEST ============

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🧪 TEST DE MONITOREO DE SALUD DE STREAMS")
    print("="*70 + "\n")
    
    print("Este módulo se usa en conjunto con sistema_maestro.py")
    print("\nFuncionalidades:")
    print("  • Detecta pantalla negra (brillo < 15/255)")
    print("  • Detecta congelamiento (cambio < 2% entre frames)")
    print("  • Detecta pérdida de audio (nivel < -60dB)")
    print("  • Monitoreo cada 10 segundos")
    print("  • Sin dependencia de IA - Solo FFmpeg")
    
    print("\n✅ Módulo listo para usar")