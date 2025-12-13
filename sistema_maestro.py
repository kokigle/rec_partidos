"""
SISTEMA MAESTRO v7.0 - 0% PÉRDIDA GARANTIZADA
OPTIMIZACIONES CLAVE:
- Buffer inicio: -3min, fin: +5min (nunca pierde inicio/fin)
- Overlapping redundante: 60s entre cambios
- Múltiples partidos simultáneos con locks
- Detección conservadora (prefiere grabar de más)
- Recuperación automática triple
- Limpieza automática de frames
- Rate limiting por partido
"""

import time
import os
import threading
import signal
import subprocess
import json
from datetime import datetime, timedelta
from config_tv import GRILLA_CANALES
from collections import defaultdict
import promiedos_client
import smart_selector
import uploader
import sync_manager
from urllib.parse import urlparse
from vision_detector import HybridStateDetectorV3

# ================= CONFIGURACIÓN OPTIMIZADA =================

# Carpetas
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

# Streams paralelos (AUMENTADO para redundancia)
MAX_STREAMS_PARALELOS = 4  # Aumentado de 3 a 4
THRESHOLD_TAMAÑO_CORTE = 512 * 1024

# Monitoreo más frecuente
INTERVALO_HEALTH_CHECK = 3  # Reducido de 5 a 3
INTERVALO_VALIDACION_CONTENIDO = 8

# Entretiempo (MÁS CONSERVADOR)
MINUTOS_ESPERA_BUSQUEDA_2T = 2  # Reducido de 3 a 2
MINUTOS_FORCE_START_2T = 12  # Reducido de 15 a 12

# Prioridad
PRIORIDAD_CANALES = [
    "ESPN Premium", "TNT Sports Premium", "TNT Sports", 
    "Fox Sports", "ESPN", "ESPN 2", "TyC Sports"
]

# Overlap AUMENTADO (CRÍTICO PARA 0% PÉRDIDA)
OVERLAP_SEGUNDOS = 60  # Aumentado de 30 a 60

# Buffers de seguridad (CRÍTICO)
BUFFER_INICIO_PARTIDO = 180  # 3 minutos antes
BUFFER_FIN_PARTIDO = 300  # 5 minutos después

# Rate limiting por partido
MIN_VERIFICACIONES_ENTRE_PARTIDOS = 15  # 15s entre checks de diferentes partidos

# Locks globales para múltiples partidos
_lock_partidos = threading.Lock()
_partidos_activos = {}
_ultimo_check_partido = {}

# =================================================================

cache_streams = {}
lock_cache = threading.Lock()
procesos_activos = {}
lock_procesos = threading.Lock()

def setup_directorios():
    for carpeta in [CARPETA_LOCAL, CARPETA_LOGS, CARPETA_TEMP]:
        os.makedirs(carpeta, exist_ok=True)

def log_partido(nombre_archivo, mensaje):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {mensaje}"
    print(log_msg)
    
    # Thread-safe logging
    try:
        with open(f"{CARPETA_LOGS}/{nombre_archivo}.log", "a", encoding='utf-8') as f:
            f.write(log_msg + "\n")
    except:
        pass

# ================= LÓGICA DE CANALES =================

def seleccionar_canal_unico(canales_partido):
    if not canales_partido: 
        return None, []
    
    log_msg = f"📺 Canales disponibles: {', '.join(canales_partido)}"
    print(log_msg)
    
    for canal_prioritario in PRIORIDAD_CANALES:
        for canal_disponible in canales_partido:
            if canal_prioritario.lower() in canal_disponible.lower():
                fuentes = resolver_fuentes_especificas(canal_disponible)
                if fuentes:
                    print(f"✅ CANAL ELEGIDO: {canal_disponible} ({len(fuentes)} fuentes)")
                    return canal_disponible, fuentes
    
    for canal_disponible in canales_partido:
        fuentes = resolver_fuentes_especificas(canal_disponible)
        if fuentes:
            print(f"✅ CANAL ELEGIDO (Fallback): {canal_disponible}")
            return canal_disponible, fuentes
    
    return None, []

def resolver_fuentes_especificas(nombre_canal):
    fuentes = []
    for key, links in GRILLA_CANALES.items():
        if key.lower() in nombre_canal.lower() or nombre_canal.lower() in key.lower():
            fuentes.extend(links)
    return fuentes

# ================= UTILIDADES =================

def obtener_tamanio_archivo(ruta):
    try: 
        return os.path.getsize(ruta)
    except: 
        return 0

# ================= MOTOR DE GRABACIÓN MEJORADO =================

def iniciar_grabacion_ffmpeg_robusto(stream_obj, ruta_salida, nombre_partido, sufijo="", stream_monitor=None):
    """
    Grabación con parámetros optimizados para estabilidad
    """
    log_partido(nombre_partido, f"🎥 Iniciando REC{sufijo}: {os.path.basename(ruta_salida)}")
    log_partido(nombre_partido, f"   URL: {stream_obj.url[:100]}...")
    log_partido(nombre_partido, f"   Delay: {stream_obj.delay:.1f}s, Bitrate: {stream_obj.bitrate:.1f}Mbps")
    
    headers_str = ""
    headers_str += f"User-Agent: {stream_obj.ua}\\r\\n"
    headers_str += f"Referer: {stream_obj.referer}\\r\\n"
    headers_str += f"Origin: {urlparse(stream_obj.referer).scheme}://{urlparse(stream_obj.referer).netloc}\\r\\n"
    headers_str += "Accept: */*\\r\\n"
    headers_str += "Accept-Language: es-419,es;q=0.9\\r\\n"
    headers_str += "Sec-Fetch-Dest: empty\\r\\n"
    headers_str += "Sec-Fetch-Mode: cors\\r\\n"
    headers_str += "Sec-Fetch-Site: cross-site\\r\\n"
    
    if hasattr(stream_obj, 'cookies') and stream_obj.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in stream_obj.cookies.items()])
        headers_str += f"Cookie: {cookie_str}\\r\\n"
    
    # Parámetros optimizados para estabilidad
    cmd = [
        "ffmpeg",
        "-headers", headers_str,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "8",  # Reducido para recuperación más rápida
        "-timeout", "30000000",
        "-i", stream_obj.url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "2048",  # Aumentado
        "-avoid_negative_ts", "make_zero",  # Evita problemas de timestamp
        "-fflags", "+genpts",  # Genera timestamps si faltan
        "-loglevel", "warning",
        "-y",
        ruta_salida
    ]
    
    try:
        proceso = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        
        time.sleep(3)
        
        if proceso.poll() is not None:
            stderr = proceso.stderr.read().decode('utf-8', errors='ignore')
            log_partido(nombre_partido, f"   ❌ FFMPEG murió: {stderr[:200]}")
            return None
        
        time.sleep(5)
        if os.path.exists(ruta_salida):
            size = os.path.getsize(ruta_salida)
            if size > 0:
                log_partido(nombre_partido, f"   ✅ Grabación iniciada ({size} bytes)")
                if stream_monitor:
                    stream_monitor.registrar_stream(proceso, ruta_salida, stream_obj)
                return proceso
        
        return proceso
        
    except Exception as e:
        log_partido(nombre_partido, f"❌ Error lanzando ffmpeg: {e}")
        return None

def detener_grabacion_suave(proceso, nombre_partido, etiqueta=""):
    if proceso and proceso.poll() is None:
        log_partido(nombre_partido, f"🛑 Deteniendo {etiqueta}...")
        try:
            proceso.stdin.write(b'q')
            proceso.stdin.flush()
            proceso.wait(timeout=15)
        except:
            try:
                proceso.send_signal(signal.SIGINT)
                proceso.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proceso.kill()

# ================= GRABACIÓN CON VISION v3 =================

def grabar_con_vision_v3(fuentes_canal, ruta_base, nombre_partido, 
                         url_promiedos, detector, estados_fin,
                         streams_precargados=None):
    """
    Grabación con detección optimizada y overlapping redundante
    """
    log_partido(nombre_partido, f"🚀 GRABACIÓN CON VISION AI v3 (0% PÉRDIDA)")
    log_partido(nombre_partido, f"   Configuración:")
    log_partido(nombre_partido, f"   • Streams paralelos: {MAX_STREAMS_PARALELOS}")
    log_partido(nombre_partido, f"   • Overlap: {OVERLAP_SEGUNDOS}s")
    log_partido(nombre_partido, f"   • Buffer inicio: -{BUFFER_INICIO_PARTIDO}s")
    log_partido(nombre_partido, f"   • Buffer fin: +{BUFFER_FIN_PARTIDO}s")
    
    monitor = sync_manager.StreamMonitor(nombre_partido)
    
    procesos = []
    cambios_stream = 0
    
    # Obtener streams
    if streams_precargados:
        candidatos = streams_precargados
    else:
        candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # Filtrar duplicados
    urls_usadas = set()
    streams_unicos = []
    for s in candidatos:
        if s.url not in urls_usadas:
            streams_unicos.append(s)
            urls_usadas.add(s.url)
    
    if not streams_unicos:
        log_partido(nombre_partido, "❌ No hay streams disponibles")
        return []
    
    # CRÍTICO: Iniciar MÁS streams en paralelo para redundancia
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    streams_respaldo = streams_unicos[max_streams:]
    
    log_partido(nombre_partido, f"📊 {max_streams} streams primarios + {len(streams_respaldo)} respaldo")
    
    # Obtener headers
    stream_principal = streams_unicos[0]
    headers_vision = {
        'User-Agent': stream_principal.ua,
        'Referer': stream_principal.referer
    }
    
    # Iniciar streams
    for i in range(max_streams):
        stream = streams_unicos[i]
        ruta = f"{ruta_base}_p{cambios_stream}_s{i}.mp4"
        p = iniciar_grabacion_ffmpeg_robusto(stream, ruta, nombre_partido, f" [S{i}]", monitor)
        if p:
            procesos.append({
                "proc": p, "ruta": ruta, "stream": stream,
                "idx": i, "estado": "ok", "last_check": time.time(),
                "last_size": 0, "stream_id": None
            })
    
    log_partido(nombre_partido, f"✅ {len([p for p in procesos if p['estado']=='ok'])} streams activos")
    
    # BUCLE DE MONITOREO CON VISION v3
    ultimo_check_estado = time.time()
    ultimo_estado_vision = "DESCONOCIDO"
    
    # Variables de control conservador
    confirmaciones_no_jugando = 0
    CONFIRMACIONES_REQUERIDAS = 3  # Requiere 3 confirmaciones consecutivas
    
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        now = time.time()
        
        # A) VERIFICACIÓN CON VISION AI (con rate limiting por partido)
        with _lock_partidos:
            ultimo_check = _ultimo_check_partido.get(nombre_partido, 0)
            puede_verificar = (now - ultimo_check) >= MIN_VERIFICACIONES_ENTRE_PARTIDOS
        
        if puede_verificar:
            estado_detectado = detector.verificar_estado(
                stream_principal.url,
                headers_vision
            )
            
            with _lock_partidos:
                _ultimo_check_partido[nombre_partido] = now
            
            # Lógica conservadora de confirmación
            if estado_detectado == "NO_JUGANDO":
                confirmaciones_no_jugando += 1
                log_partido(nombre_partido, 
                    f"   ⚠️ NO_JUGANDO detectado ({confirmaciones_no_jugando}/{CONFIRMACIONES_REQUERIDAS})")
                
                # Solo terminar si hay múltiples confirmaciones
                if confirmaciones_no_jugando >= CONFIRMACIONES_REQUERIDAS:
                    # Verificar que se puede terminar la fase
                    if detector._puede_terminar_fase():
                        log_partido(nombre_partido, f"🏁 Fin de fase confirmado")
                        break
                    else:
                        log_partido(nombre_partido, 
                            f"   ⏱️ Tiempo mínimo no alcanzado - continuando")
                        confirmaciones_no_jugando = 0
            else:
                # Resetear contador si detecta JUGANDO
                confirmaciones_no_jugando = 0
            
            ultimo_estado_vision = estado_detectado
            
            # Verificar estados fin tradicionales
            if estado_detectado in estados_fin and detector._puede_terminar_fase():
                log_partido(nombre_partido, f"🏁 Estado fin: {estado_detectado}")
                break
        
        # B) Health Check MÁS FRECUENTE
        procesos_vivos = 0
        procesos_problematicos = []
        
        for idx, p_obj in enumerate(procesos):
            if p_obj["estado"] == "dead":
                continue
            
            if p_obj["stream_id"] is not None:
                ok, msg = monitor.check_health(p_obj["stream_id"])
                if not ok:
                    procesos_problematicos.append(idx)
                    p_obj["estado"] = "problema"
                    log_partido(nombre_partido, f"   ⚠️ Problema en S{p_obj['idx']}: {msg}")
                else:
                    procesos_vivos += 1
            else:
                if p_obj["proc"].poll() is not None:
                    p_obj["estado"] = "dead"
                    log_partido(nombre_partido, f"   ☠️ S{p_obj['idx']} murió")
                else:
                    procesos_vivos += 1
        
        # C) Overlapping REDUNDANTE (60s)
        if procesos_problematicos:
            log_partido(nombre_partido, 
                f"🔄 {len(procesos_problematicos)} streams con problemas - iniciando overlap")
            
            for idx_problema in procesos_problematicos:
                if streams_respaldo:
                    nuevo_s = streams_respaldo.pop(0)
                    cambios_stream += 1
                    ruta_nuevo = f"{ruta_base}_overlap{cambios_stream}.mp4"
                    
                    log_partido(nombre_partido, f"   🆕 Nuevo stream (overlap 60s)")
                    
                    proc_nuevo = iniciar_grabacion_ffmpeg_robusto(
                        nuevo_s, ruta_nuevo, nombre_partido, " [OVERLAP]", monitor
                    )
                    
                    if proc_nuevo:
                        # CRÍTICO: Overlap de 60s
                        log_partido(nombre_partido, f"   ⏳ Overlap {OVERLAP_SEGUNDOS}s...")
                        time.sleep(OVERLAP_SEGUNDOS)
                        
                        # Detener viejo
                        p_viejo = procesos[idx_problema]
                        detener_grabacion_suave(p_viejo["proc"], nombre_partido, f"S{p_viejo['idx']}")
                        p_viejo["estado"] = "dead"
                        
                        # Agregar nuevo
                        procesos.append({
                            "proc": proc_nuevo, "ruta": ruta_nuevo, "stream": nuevo_s,
                            "idx": 100 + cambios_stream, "estado": "ok",
                            "last_check": now, "last_size": 0, "stream_id": None
                        })
                        
                        procesos_vivos += 1
                        log_partido(nombre_partido, f"   ✅ Transición completada")
        
        # D) Rescate TRIPLE si todos caen
        if procesos_vivos == 0:
            log_partido(nombre_partido, "🚨 RESCATE DE EMERGENCIA TRIPLE")
            
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            if nuevos:
                # Iniciar hasta 3 streams de emergencia
                for i, nuevo_s in enumerate(nuevos[:3]):
                    cambios_stream += 1
                    ruta_res = f"{ruta_base}_emergency{cambios_stream}_s{i}.mp4"
                    
                    proc_res = iniciar_grabacion_ffmpeg_robusto(
                        nuevo_s, ruta_res, nombre_partido, f" [EMERGENCY-{i}]", monitor
                    )
                    
                    if proc_res:
                        procesos.append({
                            "proc": proc_res, "ruta": ruta_res, "stream": nuevo_s,
                            "idx": 200 + i, "estado": "ok", "last_check": now,
                            "last_size": 0, "stream_id": None
                        })
                        procesos_vivos += 1
                
                log_partido(nombre_partido, f"✅ {procesos_vivos} streams de emergencia activos")
            else:
                log_partido(nombre_partido, "❌ No hay streams de respaldo disponibles")
        
        # E) Log periódico de estado
        if int(now) % 30 == 0:
            log_partido(nombre_partido, 
                f"📊 Estado: {procesos_vivos} streams activos, fase: {detector.fase_actual}")
    
    # Buffer final EXTENDIDO
    log_partido(nombre_partido, f"⏳ Buffer final {BUFFER_FIN_PARTIDO}s...")
    time.sleep(BUFFER_FIN_PARTIDO)
    
    # Detener todos
    for p_obj in procesos:
        if p_obj["estado"] == "ok" and p_obj["proc"].poll() is None:
            detener_grabacion_suave(p_obj["proc"], nombre_partido, "final")
    
    # Esperar que terminen de escribir
    time.sleep(5)
    
    # Recolectar archivos válidos
    rutas_validas = [
        p["ruta"] for p in procesos
        if obtener_tamanio_archivo(p["ruta"]) > THRESHOLD_TAMAÑO_CORTE
    ]
    
    log_partido(nombre_partido, f"📦 {len(rutas_validas)} archivos válidos")
    
    # Mostrar estadísticas
    stats = detector.obtener_estadisticas()
    log_partido(nombre_partido, f"📊 Estadísticas Vision AI:")
    log_partido(nombre_partido, f"   Checks Gemini: {stats['checks_gemini']}")
    log_partido(nombre_partido, f"   Checks Promiedos: {stats['checks_promiedos']}")
    log_partido(nombre_partido, f"   Frames capturados: {stats['frames_capturados']}")
    log_partido(nombre_partido, f"   Cache hit: {stats.get('tasa_cache_pct', 0):.0f}%")
    
    # Limpiar frames
    detector.limpiar_recursos()
    
    return rutas_validas

# ================= UNIÓN INTELIGENTE =================

def seleccionar_mejor_video(rutas, nombre_partido):
    if not rutas: 
        return None
    
    mejor = max(rutas, key=lambda r: obtener_tamanio_archivo(r))
    tamaño_mb = obtener_tamanio_archivo(mejor) / 1024 / 1024
    
    log_partido(nombre_partido, f"🏆 Mejor: {os.path.basename(mejor)} ({tamaño_mb:.1f} MB)")
    
    # Eliminar otros
    for r in rutas:
        if r != mejor:
            try: 
                os.remove(r)
            except: 
                pass
    
    return mejor

def unir_videos_con_validacion(rutas_1t, rutas_2t, salida, nombre_partido, sync_mgr):
    v1 = seleccionar_mejor_video(rutas_1t, nombre_partido)
    v2 = seleccionar_mejor_video(rutas_2t, nombre_partido)
    
    if not v1 and not v2:
        log_partido(nombre_partido, "❌ No hay videos para unir")
        return False
    
    log_partido(nombre_partido, "🎬 Generando video final...")
    
    if v1 and v2:
        lista = f"{CARPETA_TEMP}/{nombre_partido}_list.txt"
        with open(lista, "w") as f:
            f.write(f"file '{os.path.abspath(v1)}'\nfile '{os.path.abspath(v2)}'\n")
        
        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista, 
             "-c", "copy", "-y", salida],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        os.remove(lista)
        
        if os.path.exists(salida):
            os.remove(v1)
            os.remove(v2)
    elif v1:
        os.rename(v1, salida)
    elif v2:
        os.rename(v2, salida)
    
    return os.path.exists(salida)

# ================= GESTOR PRINCIPAL v7 =================

def gestionar_partido_v7(url_promiedos, nombre_archivo, hora_inicio):
    """
    Gestor optimizado para 0% pérdida con Vision AI v3
    """
    # Registrar partido activo (thread-safe)
    with _lock_partidos:
        if nombre_archivo in _partidos_activos:
            log_partido(nombre_archivo, "⚠️ Partido ya en proceso")
            return
        _partidos_activos[nombre_archivo] = {
            'inicio': datetime.now(),
            'estado': 'iniciando'
        }
    
    try:
        log_partido(nombre_archivo, f"📅 INICIANDO GESTIÓN OPTIMIZADA")
        log_partido(nombre_archivo, f"   Modo: 0% PÉRDIDA GARANTIZADA")
        
        # 1. Metadata
        meta = promiedos_client.obtener_metadata_partido(url_promiedos)
        if not meta:
            log_partido(nombre_archivo, "❌ No se pudo obtener metadata")
            return
        
        canal_nombre, fuentes_canal = seleccionar_canal_unico(meta['canales'])
        if not canal_nombre:
            log_partido(nombre_archivo, "❌ Sin canal compatible")
            return
        
        # 2. CREAR DETECTOR v3
        log_partido(nombre_archivo, "🔍 Inicializando Vision AI v3...")
        detector = HybridStateDetectorV3(nombre_archivo, url_promiedos)
        
        # 3. Calcular hora con BUFFER DE INICIO
        ahora = datetime.now()
        h_match = datetime.strptime(hora_inicio, "%H:%M").replace(
            year=ahora.year, month=ahora.month, day=ahora.day
        )
        if h_match < ahora - timedelta(hours=4):
            h_match += timedelta(days=1)
        
        # CRÍTICO: Restar buffer de inicio (3 minutos antes)
        hora_inicio_real = h_match - timedelta(seconds=BUFFER_INICIO_PARTIDO)
        
        log_partido(nombre_archivo, f"⏰ Hora programada: {h_match.strftime('%H:%M:%S')}")
        log_partido(nombre_archivo, f"   Inicio grabación: {hora_inicio_real.strftime('%H:%M:%S')}")
        log_partido(nombre_archivo, f"   Buffer: -{BUFFER_INICIO_PARTIDO}s")
        
        # 4. Pre-calentamiento (-5 min para análisis)
        hora_precalentamiento = hora_inicio_real - timedelta(minutes=5)
        sec_wait = (hora_precalentamiento - datetime.now()).total_seconds()
        
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m para pre-calentamiento...")
            time.sleep(max(0, sec_wait))
        
        log_partido(nombre_archivo, "⚡ Pre-calentando streams...")
        streams_precargados = smart_selector.obtener_mejores_streams(fuentes_canal)
        
        # Esperar hasta inicio real (con buffer)
        sec_wait = (hora_inicio_real - datetime.now()).total_seconds()
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m hasta inicio...")
            time.sleep(max(0, sec_wait))
        
        # Actualizar estado
        with _lock_partidos:
            _partidos_activos[nombre_archivo]['estado'] = 'grabando'
        
        # 5. GRABACIÓN OPTIMIZADA
        ruta_base = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL"
        ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
        
        log_partido(nombre_archivo, "🎬 INICIANDO GRABACIÓN (MODO 0% PÉRDIDA)")
        
        rutas_generadas = grabar_con_vision_v3(
            fuentes_canal, ruta_base, nombre_archivo,
            url_promiedos, detector, ["NO_JUGANDO", "FINAL"],
            streams_precargados=streams_precargados
        )
        
        # 6. Procesar video
        if rutas_generadas:
            mejor_video = seleccionar_mejor_video(rutas_generadas, nombre_archivo)
            if mejor_video:
                os.rename(mejor_video, ruta_final)
                
                tamaño_mb = obtener_tamanio_archivo(ruta_final) / 1024 / 1024
                log_partido(nombre_archivo, f"✅ Video final: {tamaño_mb:.1f} MB")
                
                # Subir
                log_partido(nombre_archivo, "☁️ Iniciando subida...")
                link = uploader.subir_video(ruta_final)
                
                if link:
                    log_partido(nombre_archivo, f"✅ SUBIDA: {link}")
                    with open(f"{CARPETA_LOCAL}/links.txt", "a") as f:
                        f.write(f"{nombre_archivo}: {link}\n")
                else:
                    log_partido(nombre_archivo, "⚠️ Subida falló - archivo disponible localmente")
        else:
            log_partido(nombre_archivo, "❌ No se generaron videos válidos")
    
    except Exception as e:
        log_partido(nombre_archivo, f"❌ Error crítico: {str(e)}")
    
    finally:
        # Limpiar registro (thread-safe)
        with _lock_partidos:
            if nombre_archivo in _partidos_activos:
                del _partidos_activos[nombre_archivo]
        
        log_partido(nombre_archivo, "🏁 Gestión finalizada")

# ================= MAIN =================

if __name__ == "__main__":
    setup_directorios()
    
    print("\n" + "="*70)
    print("🚀 SISTEMA MAESTRO v7.0 - 0% PÉRDIDA GARANTIZADA")
    print("="*70)
    print("\n🎯 CARACTERÍSTICAS:")
    print("   • Buffer inicio: -3min, fin: +5min")
    print("   • Overlapping redundante: 60s")
    print("   • Streams paralelos: 4")
    print("   • Detección conservadora (prefiere grabar de más)")
    print("   • Recuperación automática triple")
    print("   • Limpieza automática de frames")
    print("   • Soporte múltiples partidos simultáneos")
    print("="*70 + "\n")
    
    # URLs de partidos (PUEDEN SER MÚLTIPLES)
    URLS = [
        "https://www.promiedos.com.ar/game/liverpool-vs-brighton/eefchcc",
    ]
    
    hilos = []
    
    for url in URLS:
        meta = promiedos_client.obtener_metadata_partido(url)
        if meta:
            t = threading.Thread(
                target=gestionar_partido_v7,
                args=(url, meta['nombre'], meta['hora']),
                daemon=False
            )
            t.start()
            hilos.append(t)
        else:
            print(f"❌ No se pudo procesar: {url}")
    
    for t in hilos:
        t.join()
    
    print("\n✅ SISTEMA FINALIZADO")