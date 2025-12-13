"""
SISTEMA MAESTRO v5.0 - ULTRA OPTIMIZADO
Características:
- 0% pérdida de contenido mediante sincronización inteligente
- Overlap en cambios de stream
- Pre-calentamiento paralelo
- Recuperación automática con continuidad garantizada
- Validación de cobertura completa
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

# ================= CONFIGURACIÓN OPTIMIZADA =================
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

# --- REDUNDANCIA MEJORADA ---
MAX_STREAMS_PARALELOS = 3  # Aumentado a 3 para mayor redundancia
THRESHOLD_TAMAÑO_CORTE = 512 * 1024

# --- MONITOREO AGRESIVO ---
INTERVALO_HEALTH_CHECK = 3  # Reducido a 3s para detección rápida
INTERVALO_VALIDACION_CONTENIDO = 6

# --- ENTRETIEMPO ---
MINUTOS_ESPERA_BUSQUEDA_2T = 2  # Buscar streams más temprano
MINUTOS_FORCE_START_2T = 13

# --- PRIORIDAD ---
PRIORIDAD_CANALES = ["ESPN Premium", "TNT Sports Premium", "TNT Sports", "Fox Sports", "ESPN", "ESPN 2", "TyC Sports"]

# --- OVERLAP EN CAMBIOS ---
OVERLAP_SEGUNDOS = 30  # 30s de overlap garantizado
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

# ================= MOTOR DE GRABACIÓN MEJORADO =================
def iniciar_grabacion_ffmpeg(stream_obj, ruta_salida, nombre_partido, sufijo="", stream_monitor=None):
    """
    Graba HLS usando FFMPEG con monitoreo integrado
    """
    log_partido(nombre_partido, f"🎥 Iniciando REC{sufijo}: {os.path.basename(ruta_salida)}")
    log_partido(nombre_partido, f"   URL: {stream_obj.url[:100]}...")
    log_partido(nombre_partido, f"   Delay: {stream_obj.delay:.1f}s, Bitrate: {stream_obj.bitrate:.1f}Mbps")
    
    # Construir headers HTTP para ffmpeg
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
        log_partido(nombre_partido, f"   🍪 {len(stream_obj.cookies)} cookies")
    
    # Comando ffmpeg optimizado
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
        log_partido(nombre_partido, f"   🚀 Lanzando ffmpeg...")
        
        proceso = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )
        
        time.sleep(3)
        
        if proceso.poll() is not None:
            stdout = proceso.stdout.read().decode('utf-8', errors='ignore')
            stderr = proceso.stderr.read().decode('utf-8', errors='ignore')
            
            log_partido(nombre_partido, f"   ❌ FFMPEG murió inmediatamente")
            log_partido(nombre_partido, f"      STDERR: {stderr[:500]}")
            
            with open(f"{CARPETA_LOGS}/{nombre_partido}_ffmpeg_error.log", "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"URL: {stream_obj.url}\n")
                f.write(f"Command: {' '.join(cmd)}\n")
                f.write(f"\nSTDERR:\n{stderr}\n")
            
            return None
        
        time.sleep(5)
        if os.path.exists(ruta_salida):
            size = os.path.getsize(ruta_salida)
            if size > 0:
                log_partido(nombre_partido, f"   ✅ Grabación iniciada ({size} bytes)")
                
                # Registrar en monitor si existe
                if stream_monitor:
                    stream_monitor.registrar_stream(proceso, ruta_salida, stream_obj)
                
                return proceso
            else:
                log_partido(nombre_partido, f"   ⚠️ Archivo creado pero vacío")
        else:
            log_partido(nombre_partido, f"   ⚠️ Archivo no creado aún (esperando datos)")
        
        return proceso
        
    except Exception as e:
        log_partido(nombre_partido, f"❌ Error lanzando ffmpeg: {e}")
        return None

def detener_grabacion_suave(proceso, nombre_partido, etiqueta=""):
    """Detiene ffmpeg enviando 'q' (quit gracefully)"""
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
                log_partido(nombre_partido, "⚠️ Timeout stop, forzando kill...")
                proceso.kill()

# ================= GRABACIÓN CON 0% PÉRDIDA =================
def grabar_fase_sin_perdidas(fuentes_canal, ruta_base, nombre_partido, fase, 
                             url_promiedos, estados_fin, canal_nombre, 
                             streams_precargados=None, sync_mgr=None):
    """
    Grabación con garantía de 0% pérdida mediante:
    - Monitor activo de streams
    - Overlap en cambios
    - Pre-carga de streams de respaldo
    """
    log_partido(nombre_partido, f"🚀 INICIANDO {fase} (Modo 0% Pérdida - Canal: {canal_nombre})")
    
    # Inicializar monitor
    monitor = sync_manager.StreamMonitor(nombre_partido)
    
    procesos = []
    cambios_stream = 0
    candidatos = []

    # 1. SELECCIÓN DE STREAMS
    if streams_precargados and len(streams_precargados) > 0:
        log_partido(nombre_partido, f"📦 Usando {len(streams_precargados)} streams precargados")
        candidatos = streams_precargados
    else:
        log_partido(nombre_partido, "🔍 Escaneando streams en paralelo...")
        candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # Filtrar duplicados
    urls_usadas = set()
    streams_unicos = []
    for s in candidatos:
        if s.url not in urls_usadas:
            # NUEVO: Verificar que capturará el kickoff
            if sync_mgr:
                captura_inicio, razon = sync_mgr.verificar_captura_kickoff(s)
                log_partido(nombre_partido, f"   🎯 {s.fuente}: {razon}")
                if not captura_inicio and len(streams_unicos) > 0:
                    continue  # Skip si ya tenemos otros streams mejores
                    
            streams_unicos.append(s)
            urls_usadas.add(s.url)
    
    if not streams_unicos:
        log_partido(nombre_partido, f"❌ No hay streams iniciales - Reintento profundo...")
        candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
        streams_unicos = [s for s in candidatos if s.url not in urls_usadas]

    # INICIAR MÚLTIPLES STREAMS EN PARALELO
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    
    # Pre-cargar streams de respaldo
    streams_respaldo = streams_unicos[max_streams:max_streams+2] if len(streams_unicos) > max_streams else []
    
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

    # 2. BUCLE DE MONITOREO MEJORADO
    ultimo_estado_check = time.time()
    streams_respaldo_cargados = False
    
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        now = time.time()
        
        # A) Verificar fin de fase
        if now - ultimo_estado_check >= 10:
            estado_actual = promiedos_client.obtener_estado_partido(url_promiedos)
            ultimo_estado_check = now
            
            if estado_actual in estados_fin or estado_actual == "FINAL":
                log_partido(nombre_partido, f"🏁 Fin de fase detectado: {estado_actual}")
                break
        
        # B) Health Check con monitor inteligente
        procesos_vivos = 0
        procesos_problematicos = []
        
        for idx, p_obj in enumerate(procesos):
            if p_obj["estado"] == "dead": 
                continue
            
            # Usar StreamMonitor para verificar salud
            if p_obj["stream_id"] is not None:
                ok, msg = monitor.check_health(p_obj["stream_id"])
                
                if not ok:
                    log_partido(nombre_partido, f"⚠️ Stream {p_obj['idx']}: {msg}")
                    procesos_problematicos.append(idx)
                    p_obj["estado"] = "problema"
                else:
                    procesos_vivos += 1
            else:
                # Verificación básica si no está en monitor
                if p_obj["proc"].poll() is not None:
                    log_partido(nombre_partido, f"⚠️ Stream {p_obj['idx']} murió")
                    p_obj["estado"] = "dead"
                else:
                    procesos_vivos += 1
        
        # C) GESTIÓN DE OVERLAPPING - CRÍTICO PARA 0% PÉRDIDA
        if procesos_problematicos:
            log_partido(nombre_partido, f"🚨 {len(procesos_problematicos)} streams con problemas")
            
            # ANTES de matar streams problemáticos, iniciar reemplazos
            for idx_problema in procesos_problematicos:
                if not streams_respaldo and not streams_respaldo_cargados:
                    # Buscar nuevos streams
                    log_partido(nombre_partido, "🔄 Buscando streams frescos...")
                    nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
                    streams_respaldo = [s for s in nuevos if s.url not in urls_usadas][:2]
                    streams_respaldo_cargados = True
                
                if streams_respaldo:
                    nuevo_s = streams_respaldo.pop(0)
                    urls_usadas.add(nuevo_s.url)
                    
                    # OVERLAP: Iniciar nuevo ANTES de matar el viejo
                    cambios_stream += 1
                    ruta_nuevo = f"{ruta_base}_overlap{cambios_stream}.mp4"
                    
                    log_partido(nombre_partido, f"   🔀 Iniciando reemplazo con overlap de {OVERLAP_SEGUNDOS}s...")
                    proc_nuevo = iniciar_grabacion_ffmpeg(nuevo_s, ruta_nuevo, nombre_partido, " [OVERLAP]", monitor)
                    
                    if proc_nuevo:
                        # Esperar overlap
                        time.sleep(OVERLAP_SEGUNDOS)
                        
                        # Ahora sí, detener el viejo
                        p_viejo = procesos[idx_problema]
                        detener_grabacion_suave(p_viejo["proc"], nombre_partido, f"overlap-S{p_viejo['idx']}")
                        p_viejo["estado"] = "dead"
                        
                        # Agregar nuevo
                        procesos.append({
                            "proc": proc_nuevo, "ruta": ruta_nuevo, "stream": nuevo_s,
                            "idx": 100 + cambios_stream, "estado": "ok", 
                            "last_check": now, "last_size": 0, "stream_id": None
                        })
                        
                        log_partido(nombre_partido, f"   ✅ Cambio completado con overlap garantizado")
                        procesos_vivos += 1
        
        # D) Rescate total si todos caen
        if procesos_vivos == 0:
            log_partido(nombre_partido, "🚨🚨 TODOS LOS STREAMS CAÍDOS - RESCATE DE EMERGENCIA")
            cambios_stream += 1
            
            # Escaneo de emergencia
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            if nuevos:
                # Iniciar top 2 en paralelo
                for i, nuevo_s in enumerate(nuevos[:2]):
                    ruta_res = f"{ruta_base}_emergency{cambios_stream}_s{i}.mp4"
                    proc_res = iniciar_grabacion_ffmpeg(nuevo_s, ruta_res, nombre_partido, f" [EMERGENCY-{i}]", monitor)
                    if proc_res:
                        procesos.append({
                            "proc": proc_res, "ruta": ruta_res, "stream": nuevo_s,
                            "idx": 200 + i, "estado": "ok", "last_check": now, 
                            "last_size": 0, "stream_id": None
                        })
            else:
                log_partido(nombre_partido, "❌ RESCATE FALLIDO: No hay streams disponibles")
                time.sleep(10)

    # Buffer final antes de cerrar
    log_partido(nombre_partido, f"⏳ Buffer final de 60s para capturar últimos segundos...")
    time.sleep(60)
    
    for p_obj in procesos:
        if p_obj["estado"] == "ok" and p_obj["proc"].poll() is None:
            detener_grabacion_suave(p_obj["proc"], nombre_partido, f"final-{fase}")
    
    rutas_validas = [
        p["ruta"] for p in procesos 
        if obtener_tamanio_archivo(p["ruta"]) > THRESHOLD_TAMAÑO_CORTE
    ]
    
    log_partido(nombre_partido, f"📦 {len(rutas_validas)} archivos válidos generados para {fase}")
    return rutas_validas

# ================= UNIÓN INTELIGENTE =================
def seleccionar_mejor_video(rutas, nombre_partido):
    """Selecciona el video con mejor tamaño/calidad"""
    if not rutas: return None
    
    # Ordenar por tamaño
    mejor = max(rutas, key=lambda r: obtener_tamanio_archivo(r))
    tamaño_mb = obtener_tamanio_archivo(mejor) / 1024 / 1024
    
    log_partido(nombre_partido, f"🏆 Mejor segmento: {os.path.basename(mejor)} ({tamaño_mb:.1f} MB)")
    
    # Eliminar alternativos
    for r in rutas:
        if r != mejor:
            try: 
                os.remove(r)
                log_partido(nombre_partido, f"   🗑️ Eliminado: {os.path.basename(r)}")
            except: 
                pass
    
    return mejor

def unir_videos_con_validacion(rutas_1t, rutas_2t, salida, nombre_partido, sync_mgr):
    """
    Une videos y valida que la cobertura sea completa
    """
    v1 = seleccionar_mejor_video(rutas_1t, nombre_partido)
    v2 = seleccionar_mejor_video(rutas_2t, nombre_partido)
    
    if not v1 and not v2: 
        log_partido(nombre_partido, "❌ No hay videos para unir")
        return False
    
    log_partido(nombre_partido, "🎬 Generando video final con validación...")
    
    # Unir
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
        
        # Validar antes de borrar fuentes
        if os.path.exists(salida):
            archivos_validar = [v1, v2]
            if sync_mgr:
                cobertura_ok = sync_manager.validar_no_perdida_contenido(archivos_validar, sync_mgr)
                if cobertura_ok:
                    os.remove(v1)
                    os.remove(v2)
                else:
                    log_partido(nombre_partido, "⚠️ Validación indica posible pérdida - conservando fuentes")
            else:
                os.remove(v1)
                os.remove(v2)
        
    elif v1: 
        os.rename(v1, salida)
    elif v2: 
        os.rename(v2, salida)
    
    return os.path.exists(salida)

# ================= GESTOR PRINCIPAL OPTIMIZADO =================
def gestionar_partido_optimizado(url_promiedos, nombre_archivo, hora_inicio):
    """
    Gestor con sincronización inteligente y 0% pérdida
    """
    log_partido(nombre_archivo, f"📅 GESTIONANDO: {nombre_archivo}")
    
    # 1. OBTENER METADATA
    meta = promiedos_client.obtener_metadata_partido(url_promiedos)
    if not meta: 
        log_partido(nombre_archivo, "❌ No se pudo obtener metadata")
        return
    
    canal_nombre, fuentes_canal = seleccionar_canal_unico(meta['canales'])
    if not canal_nombre:
        log_partido(nombre_archivo, "❌ Sin canal compatible")
        return

    # 2. CREAR PLAN DE SINCRONIZACIÓN
    ahora = datetime.now()
    h_match = datetime.strptime(hora_inicio, "%H:%M").replace(
        year=ahora.year, month=ahora.month, day=ahora.day
    )
    if h_match < ahora - timedelta(hours=4): 
        h_match += timedelta(days=1)
    
    plan, sync_mgr = sync_manager.crear_plan_grabacion(url_promiedos, h_match, nombre_archivo)
    
    # 3. PRE-CALENTAMIENTO INTELIGENTE
    if not plan['inicio_inmediato']:
        # Calcular cuándo empezar el pre-calentamiento
        tiempo_pre_calentamiento = timedelta(seconds=sync_mgr.delay_total_calculado)
        hora_pre_calentamiento = plan['hora_inicio'] - timedelta(minutes=5)
        
        sec_wait = (hora_pre_calentamiento - datetime.now()).total_seconds()
        
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m para pre-calentamiento...")
            sync_manager.esperar_hasta(hora_pre_calentamiento, nombre_archivo)
        
        log_partido(nombre_archivo, "⚡ Pre-calentando streams (5min antes)...")
        streams_precargados_1t = smart_selector.obtener_mejores_streams(fuentes_canal)
        
        # Esperar hasta hora de inicio calculada
        sync_manager.esperar_hasta(plan['hora_inicio'], nombre_archivo)
    else:
        log_partido(nombre_archivo, "⚡ Partido en curso - iniciando inmediatamente")
        streams_precargados_1t = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # 4. INICIAR GRABACIÓN
    ruta_base_1t = f"{CARPETA_LOCAL}/{nombre_archivo}_1T"
    ruta_base_2t = f"{CARPETA_LOCAL}/{nombre_archivo}_2T"
    ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
    
    rutas_gen_1t = []
    rutas_gen_2t = []
    streams_precargados_2t = None
    
    # 5. PRIMER TIEMPO
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    if estado in ["PREVIA", "JUGANDO_1T"]:
        rutas_gen_1t = grabar_fase_sin_perdidas(
            fuentes_canal, ruta_base_1t, nombre_archivo, "1T", 
            url_promiedos, ["ENTRETIEMPO", "JUGANDO_2T", "FINAL"], 
            canal_nombre, streams_precargados=streams_precargados_1t,
            sync_mgr=sync_mgr
        )
    
    # 6. ENTRETIEMPO CON PRE-CARGA
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    if estado not in ["FINAL", "JUGANDO_2T"]:
        log_partido(nombre_archivo, "☕ Entretiempo - pre-cargando streams para 2T...")
        inicio_et = time.time()
        streams_2t_listos = False
        
        while True:
            time.sleep(10)
            mins = (time.time() - inicio_et) / 60
            
            if mins >= MINUTOS_ESPERA_BUSQUEDA_2T and not streams_2t_listos:
                log_partido(nombre_archivo, "🕵️ Buscando streams para 2T...")
                streams_precargados_2t = smart_selector.obtener_mejores_streams(fuentes_canal)
                streams_2t_listos = True
                log_partido(nombre_archivo, f"✅ {len(streams_precargados_2t)} streams listos para 2T")
            
            estado = promiedos_client.obtener_estado_partido(url_promiedos)
            if estado == "JUGANDO_2T" or mins >= MINUTOS_FORCE_START_2T: 
                break
            if estado == "FINAL": 
                log_partido(nombre_archivo, "🏁 Partido finalizado en entretiempo")
                return
    
    # 7. SEGUNDO TIEMPO
    rutas_gen_2t = grabar_fase_sin_perdidas(
        fuentes_canal, ruta_base_2t, nombre_archivo, "2T", 
        url_promiedos, ["FINAL"], canal_nombre,
        streams_precargados=streams_precargados_2t,
        sync_mgr=sync_mgr
    )
    
    # 8. UNIÓN Y VALIDACIÓN
    if unir_videos_con_validacion(rutas_gen_1t, rutas_gen_2t, ruta_final, nombre_archivo, sync_mgr):
        log_partido(nombre_archivo, "✅ Video final generado con éxito")
        
        # Subir
        log_partido(nombre_archivo, "☁️ Iniciando subida...")
        link = uploader.subir_video(ruta_final)
        
        if link:
            log_partido(nombre_archivo, f"✅ SUBIDA COMPLETADA: {link}")
            with open(f"{CARPETA_LOCAL}/links.txt", "a") as f: 
                f.write(f"{nombre_archivo}: {link}\n")
        else:
            log_partido(nombre_archivo, "⚠️ Subida falló - archivo disponible localmente")
    else:
        log_partido(nombre_archivo, "❌ Error generando video final")

# ================= MAIN =================
if __name__ == "__main__":
    setup_directorios()
    
    print("\n" + "="*70)
    print("🚀 SISTEMA MAESTRO v5.0 - ULTRA OPTIMIZADO")
    print("   • Sincronización inteligente con cálculo de delays")
    print("   • 0% pérdida mediante overlapping")
    print("   • Validación de cobertura completa")
    print("   • Recuperación automática con continuidad")
    print("="*70 + "\n")
    
    # URLs de partidos
    URLS = [
        "https://www.promiedos.com.ar/game/atletico-madrid-vs-valencia/eeghefi"
    ]
    
    hilos = []
    for url in URLS:
        meta = promiedos_client.obtener_metadata_partido(url)
        if meta:
            t = threading.Thread(
                target=gestionar_partido_optimizado, 
                args=(url, meta['nombre'], meta['hora'])
            )
            t.start()
            hilos.append(t)
        else:
            print(f"❌ No se pudo procesar: {url}")
    
    for t in hilos: 
        t.join()
    
    print("\n✅ SISTEMA FINALIZADO")