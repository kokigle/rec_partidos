"""
SISTEMA MAESTRO v6.0 - CON DETECCIÓN POR VISIÓN GEMINI
Características principales:
- Gemini Vision AI como detector PRIMARIO
- Promiedos como backup secundario
- Detección simplificada: JUGANDO vs NO_JUGANDO
- 0% pérdida mediante sincronización y overlap
- Recuperación automática
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
from vision_detector import HybridStateDetector

# ================= CONFIGURACIÓN =================
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

MAX_STREAMS_PARALELOS = 3
THRESHOLD_TAMAÑO_CORTE = 512 * 1024

# Monitoreo
INTERVALO_HEALTH_CHECK = 5
INTERVALO_VALIDACION_CONTENIDO = 10

# Entretiempo
MINUTOS_ESPERA_BUSQUEDA_2T = 3
MINUTOS_FORCE_START_2T = 15

# Prioridad
PRIORIDAD_CANALES = ["ESPN Premium", "TNT Sports Premium", "TNT Sports", "Fox Sports", "ESPN", "ESPN 2", "TyC Sports"]

# Overlap
OVERLAP_SEGUNDOS = 30

# NUEVO: Configuración de detección
USAR_VISION_AI = True  # True = Gemini primario, False = Solo Promiedos
INTERVALO_VERIFICACION_ESTADO = 30  # Verificar estado cada 30s
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
    with open(f"{CARPETA_LOGS}/{nombre_archivo}.log", "a", encoding='utf-8') as f:
        f.write(log_msg + "\n")

# ================= LÓGICA DE CANALES =================
def seleccionar_canal_unico(canales_partido):
    if not canales_partido: return None, []
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
    try: return os.path.getsize(ruta)
    except: return 0

# ================= MOTOR DE GRABACIÓN =================
def iniciar_grabacion_ffmpeg(stream_obj, ruta_salida, nombre_partido, sufijo="", stream_monitor=None):
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
    
    cmd = [
        "ffmpeg",
        "-headers", headers_str,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-timeout", "30000000",
        "-i", stream_obj.url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "1024",
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
            log_partido(nombre_partido, f"   ❌ FFMPEG murió inmediatamente: {stderr[:200]}")
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

# ================= GRABACIÓN CON DETECCIÓN POR VISIÓN =================
def grabar_con_vision_detector(fuentes_canal, ruta_base, nombre_partido, 
                                url_promiedos, detector, estados_fin,
                                streams_precargados=None):
    """
    Grabación usando Vision AI como detector primario
    """
    log_partido(nombre_partido, f"🚀 GRABACIÓN CON VISION AI")
    log_partido(nombre_partido, f"   Detector: Gemini Vision (primario) + Promiedos (backup)")
    
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
    
    # Iniciar streams en paralelo
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    streams_respaldo = streams_unicos[max_streams:]
    
    # Obtener headers del primer stream para Vision AI
    stream_principal = streams_unicos[0]
    headers_vision = {
        'User-Agent': stream_principal.ua,
        'Referer': stream_principal.referer
    }
    
    for i in range(max_streams):
        stream = streams_unicos[i]
        ruta = f"{ruta_base}_p{cambios_stream}_s{i}.mp4"
        p = iniciar_grabacion_ffmpeg(stream, ruta, nombre_partido, f" [S{i}]", monitor)
        if p:
            procesos.append({
                "proc": p, "ruta": ruta, "stream": stream,
                "idx": i, "estado": "ok", "last_check": time.time(),
                "last_size": 0, "stream_id": None
            })
    
    # BUCLE DE MONITOREO CON VISION AI
    ultimo_check_estado = time.time()
    ultimo_estado_vision = "DESCONOCIDO"
    
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        now = time.time()
        
        # A) VERIFICACIÓN CON VISION AI
        if now - ultimo_check_estado >= INTERVALO_VERIFICACION_ESTADO:
            log_partido(nombre_partido, "👁️ Verificando estado con Vision AI...")
            
            estado_detectado = detector.verificar_estado(
                stream_principal.url,
                headers_vision
            )
            
            ultimo_check_estado = now
            
            # Si cambió el estado
            if estado_detectado != ultimo_estado_vision:
                log_partido(nombre_partido, f"🔄 Estado cambió: {ultimo_estado_vision} → {estado_detectado}")
                ultimo_estado_vision = estado_detectado
            
            # Verificar si debe terminar la fase
            if estado_detectado == "NO_JUGANDO" and ultimo_estado_vision == "JUGANDO":
                # Cambió de jugando a no jugando = fin de tiempo
                log_partido(nombre_partido, f"🏁 Fin de fase detectado por Vision AI")
                break
            
            # Verificar estados fin tradicionales (para compatibilidad)
            if estado_detectado in estados_fin:
                log_partido(nombre_partido, f"🏁 Estado fin detectado: {estado_detectado}")
                break
        
        # B) Health Check normal
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
                else:
                    procesos_vivos += 1
            else:
                if p_obj["proc"].poll() is not None:
                    p_obj["estado"] = "dead"
                else:
                    procesos_vivos += 1
        
        # C) Gestión de overlapping
        if procesos_problematicos:
            for idx_problema in procesos_problematicos:
                if streams_respaldo:
                    nuevo_s = streams_respaldo.pop(0)
                    cambios_stream += 1
                    ruta_nuevo = f"{ruta_base}_overlap{cambios_stream}.mp4"
                    
                    proc_nuevo = iniciar_grabacion_ffmpeg(nuevo_s, ruta_nuevo, nombre_partido, " [OVERLAP]", monitor)
                    
                    if proc_nuevo:
                        time.sleep(OVERLAP_SEGUNDOS)
                        p_viejo = procesos[idx_problema]
                        detener_grabacion_suave(p_viejo["proc"], nombre_partido, f"overlap-S{p_viejo['idx']}")
                        p_viejo["estado"] = "dead"
                        
                        procesos.append({
                            "proc": proc_nuevo, "ruta": ruta_nuevo, "stream": nuevo_s,
                            "idx": 100 + cambios_stream, "estado": "ok",
                            "last_check": now, "last_size": 0, "stream_id": None
                        })
                        
                        procesos_vivos += 1
        
        # D) Rescate si todos caen
        if procesos_vivos == 0:
            log_partido(nombre_partido, "🚨 RESCATE DE EMERGENCIA")
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            if nuevos:
                for i, nuevo_s in enumerate(nuevos[:2]):
                    ruta_res = f"{ruta_base}_emergency{cambios_stream}_s{i}.mp4"
                    proc_res = iniciar_grabacion_ffmpeg(nuevo_s, ruta_res, nombre_partido, f" [EMERGENCY-{i}]", monitor)
                    if proc_res:
                        procesos.append({
                            "proc": proc_res, "ruta": ruta_res, "stream": nuevo_s,
                            "idx": 200 + i, "estado": "ok", "last_check": now,
                            "last_size": 0, "stream_id": None
                        })
    
    # Buffer final
    log_partido(nombre_partido, "⏳ Buffer final 60s...")
    time.sleep(60)
    
    for p_obj in procesos:
        if p_obj["estado"] == "ok" and p_obj["proc"].poll() is None:
            detener_grabacion_suave(p_obj["proc"], nombre_partido, "final")
    
    rutas_validas = [
        p["ruta"] for p in procesos
        if obtener_tamanio_archivo(p["ruta"]) > THRESHOLD_TAMAÑO_CORTE
    ]
    
    log_partido(nombre_partido, f"📦 {len(rutas_validas)} archivos válidos generados")
    return rutas_validas

# ================= UNIÓN INTELIGENTE =================
def seleccionar_mejor_video(rutas, nombre_partido):
    if not rutas: return None
    mejor = max(rutas, key=lambda r: obtener_tamanio_archivo(r))
    tamaño_mb = obtener_tamanio_archivo(mejor) / 1024 / 1024
    log_partido(nombre_partido, f"🏆 Mejor segmento: {os.path.basename(mejor)} ({tamaño_mb:.1f} MB)")
    for r in rutas:
        if r != mejor:
            try: os.remove(r)
            except: pass
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
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", "-y", salida],
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

# ================= GESTOR PRINCIPAL CON VISION =================
def gestionar_partido_con_vision(url_promiedos, nombre_archivo, hora_inicio):
    """Gestor que usa Vision AI como detector primario"""
    log_partido(nombre_archivo, f"📅 GESTIONANDO CON VISION AI: {nombre_archivo}")
    
    # 1. Metadata de Promiedos (solo para canales y hora)
    meta = promiedos_client.obtener_metadata_partido(url_promiedos)
    if not meta:
        log_partido(nombre_archivo, "❌ No se pudo obtener metadata")
        return
    
    canal_nombre, fuentes_canal = seleccionar_canal_unico(meta['canales'])
    if not canal_nombre:
        log_partido(nombre_archivo, "❌ Sin canal compatible")
        return
    
    # 2. CREAR DETECTOR HÍBRIDO
    log_partido(nombre_archivo, "🔍 Inicializando Vision AI Detector...")
    detector = HybridStateDetector(nombre_archivo, url_promiedos)
    
    # 3. Calcular hora de inicio (pre-calentamiento)
    ahora = datetime.now()
    h_match = datetime.strptime(hora_inicio, "%H:%M").replace(
        year=ahora.year, month=ahora.month, day=ahora.day
    )
    if h_match < ahora - timedelta(hours=4):
        h_match += timedelta(days=1)
    
    # 4. Pre-calentamiento
    hora_pre_calentamiento = h_match - timedelta(minutes=5)
    sec_wait = (hora_pre_calentamiento - datetime.now()).total_seconds()
    
    if sec_wait > 0:
        log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m para pre-calentamiento...")
        time.sleep(max(0, sec_wait))
    
    log_partido(nombre_archivo, "⚡ Pre-calentando streams...")
    streams_precargados = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # Esperar hasta inicio
    sec_wait = (h_match - datetime.now()).total_seconds()
    if sec_wait > 0:
        log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m hasta inicio...")
        time.sleep(max(0, sec_wait))
    
    # 5. GRABACIÓN CON VISION AI
    ruta_base = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL"
    ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
    
    log_partido(nombre_archivo, "🎬 INICIANDO GRABACIÓN COMPLETA CON VISION AI")
    
    # Grabar todo el partido en una sola fase, Vision AI detectará cambios
    rutas_generadas = grabar_con_vision_detector(
        fuentes_canal, ruta_base, nombre_archivo,
        url_promiedos, detector, ["NO_JUGANDO", "FINAL"],
        streams_precargados=streams_precargados
    )
    
    # 6. Procesar video final
    if rutas_generadas:
        mejor_video = seleccionar_mejor_video(rutas_generadas, nombre_archivo)
        if mejor_video:
            os.rename(mejor_video, ruta_final)
            
            log_partido(nombre_archivo, "✅ Video final generado")
            
            # Mostrar estadísticas del detector
            stats = detector.obtener_estadisticas()
            log_partido(nombre_archivo, f"📊 Estadísticas Vision AI:")
            log_partido(nombre_archivo, f"   Checks Gemini: {stats['checks_gemini']}")
            log_partido(nombre_archivo, f"   Checks Promiedos: {stats['checks_promiedos']}")
            log_partido(nombre_archivo, f"   Uso Gemini: {stats.get('uso_gemini_pct', 0):.1f}%")
            log_partido(nombre_archivo, f"   Precisión Gemini: {stats.get('precision_gemini', 0):.1f}%")
            
            # Subir
            log_partido(nombre_archivo, "☁️ Iniciando subida...")
            link = uploader.subir_video(ruta_final)
            
            if link:
                log_partido(nombre_archivo, f"✅ SUBIDA COMPLETADA: {link}")
                with open(f"{CARPETA_LOCAL}/links.txt", "a") as f:
                    f.write(f"{nombre_archivo}: {link}\n")
            else:
                log_partido(nombre_archivo, "⚠️ Subida falló - archivo local disponible")
    else:
        log_partido(nombre_archivo, "❌ No se generaron videos válidos")

# ================= MAIN =================
if __name__ == "__main__":
    setup_directorios()
    
    print("\n" + "="*70)
    print("🚀 SISTEMA MAESTRO v6.0 - CON VISION AI (GEMINI)")
    print("   • Detección inteligente con Gemini Vision")
    print("   • Promiedos como backup secundario")
    print("   • Detección simplificada: JUGANDO / NO_JUGANDO")
    print("   • 0% pérdida con overlapping")
    print("="*70 + "\n")
    
    if not USAR_VISION_AI:
        print("⚠️  VISION AI DESACTIVADO - Usando solo Promiedos")
        print("   Para activar: USAR_VISION_AI = True en este archivo\n")
    
    # URLs de partidos
    URLS = [
        "https://www.promiedos.com.ar/game/atletico-madrid-vs-valencia/eeghefi"
    ]
    
    hilos = []
    for url in URLS:
        meta = promiedos_client.obtener_metadata_partido(url)
        if meta:
            if USAR_VISION_AI:
                t = threading.Thread(
                    target=gestionar_partido_con_vision,
                    args=(url, meta['nombre'], meta['hora'])
                )
            else:
                # Usar sistema tradicional sin Vision AI
                print("⚠️ Modo sin Vision AI no implementado en esta versión")
                continue
            
            t.start()
            hilos.append(t)
        else:
            print(f"❌ No se pudo procesar: {url}")
    
    for t in hilos:
        t.join()
    
    print("\n✅ SISTEMA FINALIZADO")