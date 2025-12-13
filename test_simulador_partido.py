#!/usr/bin/env python3
"""
SIMULADOR DE PARTIDO - Testing sin partido real
Simula un partido completo con streams de prueba
"""

import time
import os
import subprocess
import threading
from datetime import datetime, timedelta
import json

print("\n" + "="*70)
print("🧪 SIMULADOR DE PARTIDO - TEST COMPLETO DEL SISTEMA")
print("="*70 + "\n")

# ============ CONFIGURACIÓN DEL TEST ============
CARPETA_TEST = "./test_partido"
DURACION_SIMULACION = 5  # minutos (simula un partido de 5 min)
USAR_STREAMS_REALES = True  # False = generar video sintético

# Streams de prueba públicos (24/7 live)
STREAMS_PRUEBA = [
    # NASA TV (24/7 público)
    "https://51a1.crackstreamslivehd.com/espndeportes/tracks-v1a1/mono.m3u8?ip=181.27.51.162&token=fe52e9fc44d4c1b02da588457291229d87ef2fa2-98-1765675000-1765621000",
]

# ============ CLASE MOCK DE PROMIEDOS ============
class MockPromiedos:
    """Simula respuestas de Promiedos para testing"""
    
    def __init__(self, duracion_1t=2, duracion_et=1, duracion_2t=2):
        self.inicio = None
        self.duracion_1t = duracion_1t  # minutos
        self.duracion_et = duracion_et
        self.duracion_2t = duracion_2t
        self.estado_actual = "PREVIA"
        
    def iniciar_partido(self):
        """Inicia el cronómetro del partido simulado"""
        self.inicio = datetime.now()
        self.estado_actual = "JUGANDO_1T"
        print(f"⚽ KICKOFF SIMULADO: {self.inicio.strftime('%H:%M:%S')}")
        
        # Thread que cambia estados automáticamente
        def cambiar_estados():
            # 1T
            time.sleep(self.duracion_1t * 60)
            self.estado_actual = "ENTRETIEMPO"
            print(f"\n☕ ENTRETIEMPO ({datetime.now().strftime('%H:%M:%S')})")
            
            # Entretiempo
            time.sleep(self.duracion_et * 60)
            self.estado_actual = "JUGANDO_2T"
            print(f"\n⚽ INICIO 2T ({datetime.now().strftime('%H:%M:%S')})")
            
            # 2T
            time.sleep(self.duracion_2t * 60)
            self.estado_actual = "FINAL"
            print(f"\n🏁 FINAL DEL PARTIDO ({datetime.now().strftime('%H:%M:%S')})")
            
        t = threading.Thread(target=cambiar_estados, daemon=True)
        t.start()
        
    def obtener_estado(self):
        """Simula promiedos_client.obtener_estado_partido()"""
        if self.inicio is None:
            return "PREVIA"
        return self.estado_actual
        
    def obtener_metadata(self):
        """Simula promiedos_client.obtener_metadata_partido()"""
        return {
            'nombre': 'TEST_Racing_vs_Estudiantes',
            'hora': (datetime.now() + timedelta(seconds=30)).strftime('%H:%M'),
            'canales': ['ESPN Premium', 'TNT Sports'],
            'estado_obj': {}
        }

# ============ MOCK DE SMART_SELECTOR ============
class MockSmartSelector:
    """Simula smart_selector con streams de prueba"""
    
    class StreamCandidato:
        def __init__(self, nombre, url):
            self.fuente = nombre
            self.url = url
            self.ua = "Mozilla/5.0"
            self.referer = "https://test.com"
            self.cookies = {}
            self.delay = 5.0
            self.bitrate = 2.0
            self.score = 50
            
    def obtener_mejores_streams(self, fuentes=None):
        """Retorna streams de prueba"""
        if not USAR_STREAMS_REALES:
            # Generar video sintético
            return [self._crear_stream_sintetico()]
        
        # Usar streams públicos reales
        streams = []
        for i, url in enumerate(STREAMS_PRUEBA[:3]):
            streams.append(self.StreamCandidato(f"Stream_Test_{i+1}", url))
        
        print(f"   🔍 Mock: {len(streams)} streams de prueba listos")
        return streams
        
    def _crear_stream_sintetico(self):
        """Crea un stream sintético local"""
        # Generar un video de prueba con ffmpeg
        output = f"{CARPETA_TEST}/stream_sintetico.mp4"
        
        if not os.path.exists(output):
            print("   🎨 Generando video sintético...")
            cmd = [
                'ffmpeg', '-f', 'lavfi', '-i', 
                'testsrc=duration=300:size=1280x720:rate=30',
                '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=300',
                '-c:v', 'libx264', '-c:a', 'aac', '-y', output
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return self.StreamCandidato("Stream_Sintetico", output)

# ============ SISTEMA DE TEST SIMPLIFICADO ============
class TestGrabadorSimplificado:
    """Versión simplificada del sistema para testing"""
    
    def __init__(self, carpeta_salida, mock_promiedos, mock_selector):
        self.carpeta = carpeta_salida
        self.promiedos = mock_promiedos
        self.selector = mock_selector
        self.procesos_activos = []
        
        os.makedirs(carpeta_salida, exist_ok=True)
        
    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")
        
    def grabar_fase(self, fase, duracion_max_minutos):
        """Graba una fase del partido"""
        self.log(f"🎥 Iniciando grabación {fase}")
        
        # Obtener streams
        streams = self.selector.obtener_mejores_streams()
        
        if not streams:
            self.log(f"❌ No hay streams disponibles")
            return []
        
        # Iniciar grabaciones (máximo 2 streams para test)
        archivos_generados = []
        max_streams = min(2, len(streams))
        
        for i in range(max_streams):
            stream = streams[i]
            archivo = f"{self.carpeta}/TEST_{fase}_stream_{i}.mp4"
            
            self.log(f"   📹 Stream {i+1}: {stream.fuente}")
            
            # Comando ffmpeg
            cmd = [
                'ffmpeg',
                '-i', stream.url,
                '-t', str(duracion_max_minutos * 60),  # Limitar duración
                '-c', 'copy',
                '-y',
                archivo
            ]
            
            try:
                proceso = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                self.procesos_activos.append({
                    'proceso': proceso,
                    'archivo': archivo,
                    'fase': fase
                })
                
                time.sleep(2)
                
                if os.path.exists(archivo):
                    self.log(f"   ✅ Grabación iniciada: {archivo}")
                    archivos_generados.append(archivo)
                else:
                    self.log(f"   ⚠️ Archivo no creado")
                    
            except Exception as e:
                self.log(f"   ❌ Error: {e}")
        
        return archivos_generados
        
    def monitorear_hasta_cambio_estado(self, estados_fin):
        """Monitorea hasta que cambie el estado del partido"""
        self.log(f"👀 Monitoreando (esperando: {estados_fin})")
        
        while True:
            time.sleep(5)
            
            estado = self.promiedos.obtener_estado()
            
            if estado in estados_fin:
                self.log(f"🏁 Estado alcanzado: {estado}")
                break
                
            # Verificar procesos
            for p in self.procesos_activos:
                if p['proceso'].poll() is not None:
                    self.log(f"⚠️ Proceso {p['archivo']} terminó")
                    
        # Detener grabaciones
        for p in self.procesos_activos:
            if p['proceso'].poll() is None:
                self.log(f"🛑 Deteniendo {os.path.basename(p['archivo'])}")
                p['proceso'].terminate()
                p['proceso'].wait(timeout=5)
                
        self.procesos_activos.clear()
        
    def unir_videos(self, archivos_1t, archivos_2t, salida):
        """Une los videos de ambos tiempos"""
        self.log("🎬 Uniendo videos...")
        
        # Seleccionar mejores archivos
        mejor_1t = self._seleccionar_mejor(archivos_1t)
        mejor_2t = self._seleccionar_mejor(archivos_2t)
        
        if not mejor_1t and not mejor_2t:
            self.log("❌ No hay videos para unir")
            return False
            
        if mejor_1t and mejor_2t:
            # Crear lista para concat
            lista = f"{self.carpeta}/lista.txt"
            with open(lista, 'w') as f:
                f.write(f"file '{os.path.abspath(mejor_1t)}'\n")
                f.write(f"file '{os.path.abspath(mejor_2t)}'\n")
                
            # Unir
            cmd = [
                'ffmpeg', '-f', 'concat', '-safe', '0',
                '-i', lista, '-c', 'copy', '-y', salida
            ]
            
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if os.path.exists(salida):
                self.log(f"✅ Video final: {salida}")
                return True
        elif mejor_1t:
            os.rename(mejor_1t, salida)
            return True
        elif mejor_2t:
            os.rename(mejor_2t, salida)
            return True
            
        return False
        
    def _seleccionar_mejor(self, archivos):
        """Selecciona el archivo más grande (mejor calidad)"""
        if not archivos:
            return None
            
        validos = [a for a in archivos if os.path.exists(a) and os.path.getsize(a) > 100000]
        
        if not validos:
            return None
            
        mejor = max(validos, key=lambda x: os.path.getsize(x))
        tamaño_mb = os.path.getsize(mejor) / 1024 / 1024
        
        self.log(f"   🏆 Mejor: {os.path.basename(mejor)} ({tamaño_mb:.1f} MB)")
        
        # Eliminar otros
        for a in validos:
            if a != mejor:
                try:
                    os.remove(a)
                except:
                    pass
                    
        return mejor
        
    def validar_video_final(self, archivo):
        """Valida el video final con ffprobe"""
        self.log("🔍 Validando video final...")
        
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', archivo
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            
            if 'format' in data and 'duration' in data['format']:
                duracion = float(data['format']['duration'])
                duracion_min = duracion / 60
                
                self.log(f"   ⏱️  Duración: {duracion_min:.1f} minutos")
                
                # Validar duración mínima
                if duracion_min >= (DURACION_SIMULACION * 0.8):  # 80% de lo esperado
                    self.log(f"   ✅ Duración válida")
                    return True
                else:
                    self.log(f"   ⚠️ Duración menor a la esperada")
                    return False
            else:
                self.log(f"   ❌ No se pudo obtener duración")
                return False
                
        except Exception as e:
            self.log(f"   ❌ Error validando: {e}")
            return False

# ============ FUNCIÓN PRINCIPAL DE TEST ============
def ejecutar_test_completo():
    """Ejecuta el test completo simulando un partido"""
    
    print("📋 CONFIGURACIÓN DEL TEST:")
    print(f"   Duración simulada: {DURACION_SIMULACION} minutos")
    print(f"   Usar streams reales: {'✅ Sí' if USAR_STREAMS_REALES else '❌ No (sintético)'}")
    print(f"   Carpeta salida: {CARPETA_TEST}")
    
    # Verificar FFmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], 
                      stdout=subprocess.DEVNULL, 
                      stderr=subprocess.DEVNULL,
                      check=True)
    except:
        print("\n❌ FFmpeg no encontrado. Instalar con:")
        print("   sudo apt install ffmpeg")
        return False
    
    print("\n" + "="*70)
    print("🚀 INICIANDO TEST")
    print("="*70 + "\n")
    
    # Crear mocks
    mock_promiedos = MockPromiedos(
        duracion_1t=DURACION_SIMULACION * 0.4,  # 40% del tiempo
        duracion_et=DURACION_SIMULACION * 0.2,  # 20% entretiempo
        duracion_2t=DURACION_SIMULACION * 0.4   # 40% del tiempo
    )
    
    mock_selector = MockSmartSelector()
    
    # Crear grabador
    grabador = TestGrabadorSimplificado(CARPETA_TEST, mock_promiedos, mock_selector)
    
    # FASE 1: PREVIA
    grabador.log("📅 Esperando inicio del partido...")
    grabador.log("   (En test real, esperaríamos ~30s)")
    time.sleep(2)  # Simular espera corta
    
    # Iniciar partido
    mock_promiedos.iniciar_partido()
    
    # FASE 2: PRIMER TIEMPO
    archivos_1t = grabador.grabar_fase("1T", DURACION_SIMULACION * 0.4)
    
    if archivos_1t:
        grabador.monitorear_hasta_cambio_estado(["ENTRETIEMPO", "FINAL"])
    else:
        grabador.log("❌ Test fallido: No se grabó el 1T")
        return False
    
    # FASE 3: ENTRETIEMPO
    estado = mock_promiedos.obtener_estado()
    if estado == "ENTRETIEMPO":
        grabador.log("☕ Entretiempo - esperando 2T...")
        grabador.monitorear_hasta_cambio_estado(["JUGANDO_2T", "FINAL"])
    
    # FASE 4: SEGUNDO TIEMPO
    archivos_2t = grabador.grabar_fase("2T", DURACION_SIMULACION * 0.4)
    
    if archivos_2t:
        grabador.monitorear_hasta_cambio_estado(["FINAL"])
    else:
        grabador.log("⚠️ No se grabó el 2T (puede ser normal si el partido ya finalizó)")
    
    # FASE 5: UNIÓN Y VALIDACIÓN
    archivo_final = f"{CARPETA_TEST}/TEST_PARTIDO_COMPLETO.mp4"
    
    if grabador.unir_videos(archivos_1t, archivos_2t, archivo_final):
        if os.path.exists(archivo_final):
            tamaño_mb = os.path.getsize(archivo_final) / 1024 / 1024
            
            print("\n" + "="*70)
            print("✅ TEST COMPLETADO EXITOSAMENTE")
            print("="*70)
            print(f"\n📹 Video final generado:")
            print(f"   Archivo: {archivo_final}")
            print(f"   Tamaño: {tamaño_mb:.1f} MB")
            
            # Validar
            if grabador.validar_video_final(archivo_final):
                print(f"\n✅ VALIDACIÓN: Video correcto")
                
                print(f"\n💡 Para ver el video:")
                print(f"   vlc {archivo_final}")
                print(f"   # o")
                print(f"   ffplay {archivo_final}")
                
                return True
            else:
                print(f"\n⚠️ VALIDACIÓN: Video con problemas")
                return False
    
    print("\n❌ TEST FALLIDO: No se pudo generar video final")
    return False

# ============ EJECUCIÓN ============
if __name__ == "__main__":
    
    print("\n⚠️  IMPORTANTE:")
    print("   • Este test grabará ~5 minutos de streams públicos")
    print("   • Verificará que el sistema funciona correctamente")
    print("   • Los archivos se guardarán en ./test_partido/")
    print("\n¿Continuar? (Enter para Sí, Ctrl+C para No)")
    
    try:
        input()
    except KeyboardInterrupt:
        print("\n\n❌ Test cancelado")
        exit(0)
    
    inicio = datetime.now()
    exito = ejecutar_test_completo()
    duracion = (datetime.now() - inicio).total_seconds()
    
    print(f"\n⏱️  Tiempo total: {duracion/60:.1f} minutos")
    
    if exito:
        print("\n🎉 Sistema funcionando correctamente")
        print("\n📝 Próximos pasos:")
        print("   1. Revisar el video en ./test_partido/")
        print("   2. Si está OK, el sistema está listo para partidos reales")
        print("   3. Ejecutar: python sistema_maestro_v5.py")
    else:
        print("\n❌ Hay problemas que resolver")
        print("   • Revisar logs arriba")
        print("   • Verificar que FFmpeg funcione: ffmpeg -version")
        print("   • Verificar conexión a Internet (si usa streams reales)")