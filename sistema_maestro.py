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
from urllib.parse import urlparse

# ================= CONFIGURACIÓN "ULTRA-ROBUSTA" =================
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

# --- TIEMPOS Y TIMEOUTS ---
MARGEN_SEGURIDAD = 90           
MINUTOS_PREVIA = 5
MINUTOS_PREBUSQUEDA = 15        
TIMEOUT_ENTRETIEMPO = 1800      
MAX_REINTENTOS_STREAM = 5       

# --- MONITOREO ---
INTERVALO_HEALTH_CHECK = 5      
INTERVALO_VALIDACION_CONTENIDO = 8  
INTERVALO_REFRESCO_ESTADO = 10  

# --- REDUNDANCIA ---
MAX_STREAMS_PARALELOS = 2       
THRESHOLD_TAMAÑO_CORTE = 512 * 1024  

# --- ENTRETIEMPO INTELIGENTE ---
MINUTOS_ESPERA_BUSQUEDA_2T = 3  
MINUTOS_FORCE_START_2T = 14     

# --- PRIORIDAD ---
PRIORIDAD_CANALES = ["ESPN Premium", "TNT Sports", "Fox Sports", "ESPN", "ESPN 2", "TyC Sports"]
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
    print(f"📺 Canales disponibles: {', '.join(canales_partido)}")
    
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
def iniciar_grabacion_ffmpeg(stream_obj, ruta_salida, nombre_partido, sufijo=""):
    """
    Graba HLS usando FFMPEG directamente (más confiable que yt-dlp para streams protegidos)
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
    
    # Agregar cookies si existen
    if hasattr(stream_obj, 'cookies') and stream_obj.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in stream_obj.cookies.items()])
        headers_str += f"Cookie: {cookie_str}\\r\\n"
        log_partido(nombre_partido, f"   🍪 {len(stream_obj.cookies)} cookies")
    
    # Comando ffmpeg optimizado para HLS
    cmd = [
        "ffmpeg",
        "-headers", headers_str,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-timeout", "30000000",  # 30 segundos en microsegundos
        "-i", stream_obj.url,
        "-c", "copy",  # No re-encodear (más rápido y estable)
        "-bsf:a", "aac_adtstoasc",  # Fix para audio AAC
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "1024",
        "-loglevel", "warning",
        "-y",  # Sobrescribir si existe
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
        
        # Verificar que inició correctamente
        time.sleep(3)
        
        if proceso.poll() is not None:
            # Proceso murió inmediatamente
            stdout = proceso.stdout.read().decode('utf-8', errors='ignore')
            stderr = proceso.stderr.read().decode('utf-8', errors='ignore')
            
            log_partido(nombre_partido, f"   ❌ FFMPEG murió inmediatamente")
            log_partido(nombre_partido, f"      STDERR: {stderr[:500]}")
            
            # Guardar logs completos
            with open(f"{CARPETA_LOGS}/{nombre_partido}_ffmpeg_error.log", "a") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"URL: {stream_obj.url}\n")
                f.write(f"Command: {' '.join(cmd)}\n")
                f.write(f"\nSTDERR:\n{stderr}\n")
            
            return None
        
        # Verificar que el archivo se está escribiendo
        time.sleep(5)
        if os.path.exists(ruta_salida):
            size = os.path.getsize(ruta_salida)
            if size > 0:
                log_partido(nombre_partido, f"   ✅ Grabación iniciada ({size} bytes)")
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
            # FFMPEG acepta 'q' para terminar gracefully
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

# ================= GRABACIÓN RESILIENTE =================
def grabar_fase_con_redundancia(fuentes_canal, ruta_base, nombre_partido, fase, 
                              url_promiedos, estados_fin, canal_nombre, streams_precargados=None):
    log_partido(nombre_partido, f"🚀 INICIANDO {fase} (Modo Redundante - Canal: {canal_nombre})")
    
    procesos = []
    cambios_stream = 0
    candidatos = []

    # 1. SELECCIÓN DE STREAMS (USAR PRECARGADOS SI EXISTEN)
    if streams_precargados and len(streams_precargados) > 0:
        log_partido(nombre_partido, f"📦 Usando {len(streams_precargados)} streams ya escaneados anteriormente")
        candidatos = streams_precargados
    else:
        log_partido(nombre_partido, "🔍 Buscando streams frescos...")
        candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # Filtrar duplicados por URL
    urls_usadas = set()
    streams_unicos = []
    for s in candidatos:
        if s.url not in urls_usadas:
            streams_unicos.append(s)
            urls_usadas.add(s.url)
    
    if not streams_unicos:
        log_partido(nombre_partido, f"❌ No hay streams iniciales para {canal_nombre} (Reintentando escaneo profundo...)")
        # Último intento si falló lo precargado
        candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
        streams_unicos = [s for s in candidatos if s.url not in urls_usadas]

    # Iniciar procesos (Top N streams)
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    for i in range(max_streams):
        stream = streams_unicos[i]
        ruta = f"{ruta_base}_p{cambios_stream}_s{i}.mp4"
        p = iniciar_grabacion_ffmpeg(stream, ruta, nombre_partido, f" [S{i}]")
        if p:
            procesos.append({
                "proc": p, "ruta": ruta, "stream": stream, 
                "idx": i, "estado": "ok", "last_check": time.time(), "last_size": 0
            })

    # 2. BUCLE DE MONITOREO
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        
        # A) Fin de fase
        estado_actual = promiedos_client.obtener_estado_partido(url_promiedos)
        if estado_actual in estados_fin or estado_actual == "FINAL":
            log_partido(nombre_partido, f"🏁 Fin de fase detectado: {estado_actual}")
            break
            
        # B) Health Check
        procesos_vivos = 0
        now = time.time()
        
        for p_obj in procesos:
            if p_obj["estado"] == "dead": continue
            
            if p_obj["proc"].poll() is not None:
                log_partido(nombre_partido, f"⚠️ Stream {p_obj['idx']} murió (proceso cerrado)")
                p_obj["estado"] = "dead"
                continue
                
            if now - p_obj["last_check"] > INTERVALO_VALIDACION_CONTENIDO:
                size_now = obtener_tamanio_archivo(p_obj["ruta"])
                if size_now <= p_obj["last_size"] and size_now > THRESHOLD_TAMAÑO_CORTE:
                    log_partido(nombre_partido, f"⚠️ Stream {p_obj['idx']} congelado")
                    detener_grabacion_suave(p_obj["proc"], nombre_partido, "congelado")
                    p_obj["estado"] = "dead"
                    continue
                p_obj["last_size"] = size_now
                p_obj["last_check"] = now
            
            procesos_vivos += 1
            
        # C) Rescate
        if procesos_vivos == 0:
            log_partido(nombre_partido, "🚨 TODOS LOS STREAMS CAÍDOS - Iniciando rescate...")
            cambios_stream += 1
            
            # Buscar stream fresco (aquí sí escaneamos de nuevo porque todo falló)
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            if nuevos:
                nuevo_s = nuevos[0]
                ruta_res = f"{ruta_base}_rescue{cambios_stream}.mp4"
                proc_res = iniciar_grabacion_ffmpeg(nuevo_s, ruta_res, nombre_partido, " [RESCUE]")
                if proc_res:
                    procesos.append({
                        "proc": proc_res, "ruta": ruta_res, "stream": nuevo_s,
                        "idx": 99, "estado": "ok", "last_check": now, "last_size": 0
                    })
            else:
                log_partido(nombre_partido, "❌ Falló rescate: No hay streams disponibles")
                time.sleep(5)

    time.sleep(60) # Buffer final
    for p_obj in procesos:
        if p_obj["estado"] == "ok":
            detener_grabacion_suave(p_obj["proc"], nombre_partido, "final fase")
            
    rutas_validas = [p["ruta"] for p in procesos if obtener_tamanio_archivo(p["ruta"]) > THRESHOLD_TAMAÑO_CORTE]
    return rutas_validas

# ================= UNIÓN Y POST-PRODUCCIÓN =================
def seleccionar_mejor_video(rutas, nombre_partido):
    if not rutas: return None
    mejor = max(rutas, key=lambda r: obtener_tamanio_archivo(r))
    log_partido(nombre_partido, f"🏆 Mejor segmento: {os.path.basename(mejor)} ({obtener_tamanio_archivo(mejor)/1024/1024:.1f} MB)")
    for r in rutas:
        if r != mejor:
            try: os.remove(r)
            except: pass
    return mejor

def unir_videos_final(rutas_1t, rutas_2t, salida, nombre_partido):
    v1 = seleccionar_mejor_video(rutas_1t, nombre_partido)
    v2 = seleccionar_mejor_video(rutas_2t, nombre_partido)
    
    if not v1 and not v2: return False
    
    log_partido(nombre_partido, "🎬 Generando video final...")
    
    if v1 and v2:
        lista = f"{CARPETA_TEMP}/{nombre_partido}_list.txt"
        with open(lista, "w") as f:
            f.write(f"file '{os.path.abspath(v1)}'\nfile '{os.path.abspath(v2)}'\n")
        subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", "-y", salida],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(lista)
        os.remove(v1)
        os.remove(v2)
    elif v1: os.rename(v1, salida)
    elif v2: os.rename(v2, salida)
        
    return os.path.exists(salida)

# ================= GESTOR PRINCIPAL =================
def gestionar_partido(url_promiedos, nombre_archivo, hora_inicio):
    log_partido(nombre_archivo, f"📅 GESTIONANDO: {nombre_archivo}")
    
    meta = promiedos_client.obtener_metadata_partido(url_promiedos)
    if not meta: return
    
    canal_nombre, fuentes_canal = seleccionar_canal_unico(meta['canales'])
    if not canal_nombre:
        log_partido(nombre_archivo, "❌ Sin canal compatible disponible")
        return

    ahora = datetime.now()
    h_match = datetime.strptime(hora_inicio, "%H:%M").replace(year=ahora.year, month=ahora.month, day=ahora.day)
    if h_match < ahora - timedelta(hours=4): h_match += timedelta(days=1)
    
    # 1. PRE-CALENTAMIENTO Y BÚSQUEDA
    sec_wait = 0 #(h_match - timedelta(minutes=MINUTOS_PREBUSQUEDA) - datetime.now()).total_seconds()
    if sec_wait > 0:
        log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m para pre-búsqueda...")
        time.sleep(sec_wait)
        
    log_partido(nombre_archivo, "⚡ Pre-calentando y guardando streams...")
    # AQUÍ ESTÁ EL CAMBIO: Guardamos el resultado en una variable
    streams_precargados_1t = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    # 2. ESPERA FINAL
    sec_wait_rec = (h_match - timedelta(minutes=MINUTOS_PREVIA) - datetime.now()).total_seconds()
    if sec_wait_rec > 0: time.sleep(sec_wait_rec)
    
    ruta_base_1t = f"{CARPETA_LOCAL}/{nombre_archivo}_1T"
    ruta_base_2t = f"{CARPETA_LOCAL}/{nombre_archivo}_2T"
    ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
    
    rutas_gen_1t = []
    rutas_gen_2t = []
    streams_precargados_2t = None
    
    # 3. FASE 1T
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    if estado in ["PREVIA", "JUGANDO_1T"]:
        # AQUÍ ESTÁ EL CAMBIO: Pasamos la variable a la función
        rutas_gen_1t = grabar_fase_con_redundancia(
            fuentes_canal, ruta_base_1t, nombre_archivo, "1T", 
            url_promiedos, ["ENTRETIEMPO", "JUGANDO_2T", "FINAL"], canal_nombre,
            streams_precargados=streams_precargados_1t
        )
        
    # 4. ENTRETIEMPO
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    if estado not in ["FINAL", "JUGANDO_2T"]:
        log_partido(nombre_archivo, "☕ En Entretiempo...")
        inicio_et = time.time()
        streams_2t_listos = False
        
        while True:
            time.sleep(10)
            mins = (time.time() - inicio_et) / 60
            
            # Buscar streams para el 2T una sola vez y guardarlos
            if mins >= MINUTOS_ESPERA_BUSQUEDA_2T and not streams_2t_listos:
                 log_partido(nombre_archivo, "🕵️ Buscando streams para 2T anticipadamente...")
                 streams_precargados_2t = smart_selector.obtener_mejores_streams(fuentes_canal)
                 streams_2t_listos = True
            
            estado = promiedos_client.obtener_estado_partido(url_promiedos)
            if estado == "JUGANDO_2T" or mins >= MINUTOS_FORCE_START_2T: break
            if estado == "FINAL": return

    # 5. FASE 2T
    # AQUÍ ESTÁ EL CAMBIO: Pasamos la variable precargada del entretiempo
    rutas_gen_2t = grabar_fase_con_redundancia(
        fuentes_canal, ruta_base_2t, nombre_archivo, "2T", 
        url_promiedos, ["FINAL"], canal_nombre,
        streams_precargados=streams_precargados_2t
    )
    
    # 6. FINALIZAR
    if unir_videos_final(rutas_gen_1t, rutas_gen_2t, ruta_final, nombre_archivo):
        log_partido(nombre_archivo, "☁️ Subiendo...")
        link = uploader.subir_video(ruta_final)
        if link:
            log_partido(nombre_archivo, f"✅ LINK: {link}")
            with open(f"{CARPETA_LOCAL}/links.txt", "a") as f: f.write(f"{nombre_archivo}: {link}\n")

if __name__ == "__main__":
    setup_directorios()
    print("🚀 SISTEMA MAESTRO v4.1 (SIN RE-ESCANEO)")
    
    URLS = ["https://www.promiedos.com.ar/game/racing-club-vs-estudiantes-de-la-plata/egcjbed"]
    
    hilos = []
    for url in URLS:
        meta = promiedos_client.obtener_metadata_partido(url)
        if meta:
            t = threading.Thread(target=gestionar_partido, args=(url, meta['nombre'], meta['hora']))
            t.start()
            hilos.append(t)
    for t in hilos: t.join()