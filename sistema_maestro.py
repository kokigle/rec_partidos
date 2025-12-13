"""
SISTEMA MAESTRO v8.0 - 0% PÉRDIDA + VALIDACIÓN EN TIEMPO REAL
MEJORAS CRÍTICAS v8:
- IA SOLO para validar contenido de streams (pantalla negra, congelamiento)
- Estado de partido: SOLO Promiedos/SofaScore (backup)
- Monitoreo en tiempo real de calidad de video
- Recuperación automática ante streams problemáticos
- Múltiples fuentes de metadata (Promiedos → SofaScore)
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
import sofascore_client  # NUEVO: Backup
import smart_selector
import uploader
import sync_manager
from stream_health_monitor import MultiStreamHealthManager  # NUEVO
from urllib.parse import urlparse

# ================= CONFIGURACIÓN OPTIMIZADA =================

# Carpetas
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

# Streams paralelos
MAX_STREAMS_PARALELOS = 5  # Aumentado de 4 a 5 para más redundancia
THRESHOLD_TAMAÑO_CORTE = 512 * 1024

# Monitoreo MÁS FRECUENTE
INTERVALO_HEALTH_CHECK = 2  # Cada 2 segundos
INTERVALO_VALIDACION_METADATA = 20  # Verificar Promiedos/SofaScore cada 20s

# Entretiempo
MINUTOS_ESPERA_BUSQUEDA_2T = 2
MINUTOS_FORCE_START_2T = 12

# Prioridad
PRIORIDAD_CANALES = [
    "ESPN Premium", "TNT Sports Premium", "TNT Sports", 
    "Fox Sports", "ESPN", "ESPN 2", "TyC Sports",
    "Disney+ Premium"
]

# Overlap
OVERLAP_SEGUNDOS = 60

# Buffers
BUFFER_INICIO_PARTIDO = 180
BUFFER_FIN_PARTIDO = 300

# Rate limiting
MIN_VERIFICACIONES_ENTRE_PARTIDOS = 15

# Locks
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
    
    try:
        with open(f"{CARPETA_LOGS}/{nombre_archivo}.log", "a", encoding='utf-8') as f:
            f.write(log_msg + "\n")
    except:
        pass

# ================= GESTIÓN DE METADATA CON BACKUP =================

def obtener_metadata_con_backup(url_promiedos, url_sofascore=None):
    """
    Intenta Promiedos primero, si falla usa SofaScore
    """
    log_partido("sistema", "📡 Obteniendo metadata...")
    
    # Intento 1: Promiedos
    try:
        meta = promiedos_client.obtener_metadata_partido(url_promiedos)
        if meta and meta.get('canales'):
            log_partido("sistema", f"✅ Promiedos: {len(meta['canales'])} canales")
            return meta, "promiedos"
    except Exception as e:
        log_partido("sistema", f"⚠️ Promiedos falló: {str(e)[:60]}")
    
    # Intento 2: SofaScore (backup)
    if url_sofascore:
        try:
            meta = sofascore_client.obtener_metadata_partido(url_sofascore)
            if meta:
                log_partido("sistema", f"✅ SofaScore (backup): {meta['nombre']}")
                
                # Si SofaScore no tiene canales, intentar extraer de Promiedos solo metadata
                if not meta.get('canales'):
                    try:
                        meta_prom = promiedos_client.obtener_metadata_partido(url_promiedos)
                        if meta_prom and meta_prom.get('canales'):
                            meta['canales'] = meta_prom['canales']
                            log_partido("sistema", f"✅ Canales de Promiedos: {len(meta['canales'])}")
                    except:
                        pass
                
                return meta, "sofascore"
        except Exception as e:
            log_partido("sistema", f"⚠️ SofaScore falló: {str(e)[:60]}")
    
    log_partido("sistema", "❌ No se pudo obtener metadata de ninguna fuente")
    return None, None

def obtener_estado_con_backup(url_promiedos, url_sofascore=None):
    """
    Intenta Promiedos primero, si falla usa SofaScore
    """
    # Intento 1: Promiedos
    try:
        estado = promiedos_client.obtener_estado_partido(url_promiedos)
        if estado != "ERROR":
            return estado, "promiedos"
    except Exception:
        pass
    
    # Intento 2: SofaScore
    if url_sofascore:
        try:
            estado = sofascore_client.obtener_estado_partido(url_sofascore)
            if estado != "ERROR":
                return estado, "sofascore"
        except Exception:
            pass
    
    return "ERROR", None

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

# ================= MOTOR DE GRABACIÓN CON MONITOREO =================

def iniciar_grabacion_con_monitoreo(stream_obj, ruta_salida, nombre_partido, 
                                    sufijo="", health_manager=None):
    """
    Grabación con monitoreo de salud en tiempo real
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
    
    if hasattr(stream_obj, 'cookies') and stream_obj.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in stream_obj.cookies.items()])
        headers_str += f"Cookie: {cookie_str}\\r\\n"
    
    cmd = [
        "ffmpeg",
        "-headers", headers_str,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "30000000",
        "-i", stream_obj.url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "4096",  # Aumentado
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts+discardcorrupt",  # Descartar frames corruptos
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
                
                # NUEVO: Registrar en health monitor
                if health_manager:
                    # Extraer ID del sufijo
                    import re
                    match = re.search(r's(\d+)', sufijo.lower())
                    if match:
                        stream_id = int(match.group(1))
                        health_manager.registrar_stream(stream_id, ruta_salida)
                
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

# ================= GRABACIÓN CON MONITOREO EN TIEMPO REAL =================

def grabar_con_monitoreo_salud(fuentes_canal, ruta_base, nombre_partido, 
                                url_promiedos, url_sofascore, estados_fin,
                                streams_precargados=None):
    """
    Grabación con monitoreo de salud en tiempo real
    IA SOLO para validar contenido, NO para detectar estado
    """
    log_partido(nombre_partido, f"🚀 GRABACIÓN CON MONITOREO EN TIEMPO REAL")
    log_partido(nombre_partido, f"   Configuración:")
    log_partido(nombre_partido, f"   • Streams paralelos: {MAX_STREAMS_PARALELOS}")
    log_partido(nombre_partido, f"   • Overlap: {OVERLAP_SEGUNDOS}s")
    log_partido(nombre_partido, f"   • Monitoreo cada {INTERVALO_HEALTH_CHECK}s")
    
    # NUEVO: Health Manager
    health_manager = MultiStreamHealthManager(nombre_partido)
    
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
    
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    streams_respaldo = streams_unicos[max_streams:]
    
    log_partido(nombre_partido, f"📊 {max_streams} streams primarios + {len(streams_respaldo)} respaldo")
    
    # Iniciar streams
    for i in range(max_streams):
        stream = streams_unicos[i]
        ruta = f"{ruta_base}_p{cambios_stream}_s{i}.mp4"
        p = iniciar_grabacion_con_monitoreo(
            stream, ruta, nombre_partido, f" [S{i}]", health_manager
        )
        if p:
            procesos.append({
                "proc": p, "ruta": ruta, "stream": stream,
                "idx": i, "estado": "ok", "last_check": time.time(),
                "last_size": 0, "stream_id": i
            })
    
    log_partido(nombre_partido, f"✅ {len([p for p in procesos if p['estado']=='ok'])} streams activos")
    
    # BUCLE DE MONITOREO
    ultimo_check_metadata = time.time()
    fase_actual = "1T"
    tiempo_inicio_fase = datetime.now()
    
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        now = time.time()
        
        # A) VERIFICAR ESTADO DEL PARTIDO (Promiedos/SofaScore)
        if now - ultimo_check_metadata >= INTERVALO_VALIDACION_METADATA:
            with _lock_partidos:
                ultimo_check = _ultimo_check_partido.get(nombre_partido, 0)
                puede_verificar = (now - ultimo_check) >= MIN_VERIFICACIONES_ENTRE_PARTIDOS
            
            if puede_verificar:
                estado, fuente = obtener_estado_con_backup(url_promiedos, url_sofascore)
                
                with _lock_partidos:
                    _ultimo_check_partido[nombre_partido] = now
                
                log_partido(nombre_partido, f"📡 Estado ({fuente}): {estado}")
                
                # Verificar si puede terminar
                tiempo_fase = (datetime.now() - tiempo_inicio_fase).total_seconds() / 60
                
                if estado in estados_fin:
                    # Solo terminar si pasó tiempo mínimo
                    if fase_actual == "1T" and tiempo_fase >= 35:
                        log_partido(nombre_partido, "🏁 Fin 1T confirmado")
                        break
                    elif fase_actual == "2T" and tiempo_fase >= 35:
                        log_partido(nombre_partido, "🏁 Fin 2T confirmado")
                        break
                    elif estado == "FINAL":
                        log_partido(nombre_partido, "🏁 FINAL")
                        break
                
                # Actualizar fase
                if estado == "JUGANDO_2T" and fase_actual == "1T":
                    fase_actual = "2T"
                    tiempo_inicio_fase = datetime.now()
                    log_partido(nombre_partido, "⚽ INICIO 2T")
                
                ultimo_check_metadata = now
        
        # B) VERIFICAR SALUD DE STREAMS (Health Monitor)
        streams_problematicos = health_manager.obtener_streams_problematicos()
        
        if streams_problematicos:
            log_partido(nombre_partido, f"🚨 {len(streams_problematicos)} streams con problemas críticos")
            
            for idx_problema in streams_problematicos:
                # Buscar proceso correspondiente
                proc_problema = next(
                    (p for p in procesos if p.get('stream_id') == idx_problema),
                    None
                )
                
                if not proc_problema:
                    continue
                
                log_partido(nombre_partido, f"   🔄 Reemplazando stream S{idx_problema}...")
                
                if streams_respaldo:
                    nuevo_s = streams_respaldo.pop(0)
                    cambios_stream += 1
                    ruta_nuevo = f"{ruta_base}_repair{cambios_stream}.mp4"
                    
                    # Iniciar nuevo con overlap
                    proc_nuevo = iniciar_grabacion_con_monitoreo(
                        nuevo_s, ruta_nuevo, nombre_partido, 
                        f" [REPAIR-{idx_problema}]", health_manager
                    )
                    
                    if proc_nuevo:
                        # Overlap
                        log_partido(nombre_partido, f"   ⏳ Overlap {OVERLAP_SEGUNDOS}s...")
                        time.sleep(OVERLAP_SEGUNDOS)
                        
                        # Detener viejo
                        detener_grabacion_suave(proc_problema["proc"], nombre_partido, f"S{idx_problema}")
                        proc_problema["estado"] = "dead"
                        
                        # Agregar nuevo
                        procesos.append({
                            "proc": proc_nuevo, "ruta": ruta_nuevo, "stream": nuevo_s,
                            "idx": 500 + cambios_stream, "estado": "ok",
                            "last_check": now, "last_size": 0, 
                            "stream_id": 500 + cambios_stream
                        })
                        
                        log_partido(nombre_partido, "   ✅ Reparación completada")
        
        # C) Health Check tradicional (tamaño de archivo)
        procesos_vivos = 0
        for p_obj in procesos:
            if p_obj["estado"] == "dead":
                continue
            
            if p_obj["proc"].poll() is None:
                # Verificar crecimiento
                try:
                    tamaño_actual = obtener_tamanio_archivo(p_obj["ruta"])
                    if tamaño_actual > p_obj["last_size"]:
                        p_obj["last_size"] = tamaño_actual
                        procesos_vivos += 1
                    else:
                        # Sin crecimiento
                        if now - p_obj["last_check"] > 30:
                            log_partido(nombre_partido, f"   ⚠️ S{p_obj['idx']} sin crecimiento 30s")
                except:
                    pass
            else:
                p_obj["estado"] = "dead"
                log_partido(nombre_partido, f"   ☠️ S{p_obj['idx']} murió")
        
        # D) Rescate si todos caen
        if procesos_vivos == 0:
            log_partido(nombre_partido, "🚨 RESCATE DE EMERGENCIA")
            
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            if nuevos:
                for i, nuevo_s in enumerate(nuevos[:3]):
                    cambios_stream += 1
                    ruta_res = f"{ruta_base}_emergency{cambios_stream}.mp4"
                    
                    proc_res = iniciar_grabacion_con_monitoreo(
                        nuevo_s, ruta_res, nombre_partido, 
                        f" [EMG-{i}]", health_manager
                    )
                    
                    if proc_res:
                        procesos.append({
                            "proc": proc_res, "ruta": ruta_res, "stream": nuevo_s,
                            "idx": 300 + i, "estado": "ok", "last_check": now,
                            "last_size": 0, "stream_id": 300 + i
                        })
                        procesos_vivos += 1
                
                log_partido(nombre_partido, f"✅ {procesos_vivos} streams de emergencia activos")
        
        # E) Log periódico
        if int(now) % 30 == 0:
            log_partido(nombre_partido, f"📊 Estado: {procesos_vivos} streams, fase: {fase_actual}")
    
    # Detener monitoreo
    health_manager.detener_todos()
    
    # Buffer final
    log_partido(nombre_partido, f"⏳ Buffer final {BUFFER_FIN_PARTIDO}s...")
    time.sleep(BUFFER_FIN_PARTIDO)
    
    # Detener todos
    for p_obj in procesos:
        if p_obj["estado"] == "ok" and p_obj["proc"].poll() is None:
            detener_grabacion_suave(p_obj["proc"], nombre_partido, "final")
    
    time.sleep(5)
    
    # Recolectar archivos válidos
    rutas_validas = [
        p["ruta"] for p in procesos
        if obtener_tamanio_archivo(p["ruta"]) > THRESHOLD_TAMAÑO_CORTE
    ]
    
    log_partido(nombre_partido, f"📦 {len(rutas_validas)} archivos válidos")
    
    # Reporte final
    reporte = health_manager.obtener_reporte()
    log_partido(nombre_partido, f"📊 Reporte de Salud:")
    log_partido(nombre_partido, f"   Total checks: {len(reporte['streams'])}")
    
    for sid, estado in reporte['streams'].items():
        if estado['problemas']:
            log_partido(nombre_partido, f"   S{sid}: {len(estado['problemas'])} problemas detectados")
    
    return rutas_validas

# ================= UNIÓN INTELIGENTE =================

def seleccionar_mejor_video(rutas, nombre_partido):
    if not rutas: 
        return None
    
    mejor = max(rutas, key=lambda r: obtener_tamanio_archivo(r))
    tamaño_mb = obtener_tamanio_archivo(mejor) / 1024 / 1024
    
    log_partido(nombre_partido, f"🏆 Mejor: {os.path.basename(mejor)} ({tamaño_mb:.1f} MB)")
    
    for r in rutas:
        if r != mejor:
            try: 
                os.remove(r)
            except: 
                pass
    
    return mejor

# ================= GESTOR PRINCIPAL v8 =================

def gestionar_partido_v8(url_promiedos, url_sofascore, nombre_archivo, hora_inicio):
    """
    Gestor con backup de fuentes y monitoreo en tiempo real
    """
    with _lock_partidos:
        if nombre_archivo in _partidos_activos:
            log_partido(nombre_archivo, "⚠️ Partido ya en proceso")
            return
        _partidos_activos[nombre_archivo] = {
            'inicio': datetime.now(),
            'estado': 'iniciando'
        }
    
    try:
        log_partido(nombre_archivo, f"📅 INICIANDO GESTIÓN v8.0")
        log_partido(nombre_archivo, f"   Monitoreo en tiempo real activado")
        
        # Metadata con backup
        meta, fuente = obtener_metadata_con_backup(url_promiedos, url_sofascore)
        if not meta:
            log_partido(nombre_archivo, "❌ No se pudo obtener metadata")
            return
        
        canal_nombre, fuentes_canal = seleccionar_canal_unico(meta['canales'])
        if not canal_nombre:
            log_partido(nombre_archivo, "❌ Sin canal compatible")
            return
        
        # Calcular hora
        ahora = datetime.now()
        h_match = datetime.strptime(hora_inicio, "%H:%M").replace(
            year=ahora.year, month=ahora.month, day=ahora.day
        )
        if h_match < ahora - timedelta(hours=4):
            h_match += timedelta(days=1)
        
        hora_inicio_real = h_match - timedelta(seconds=BUFFER_INICIO_PARTIDO)
        
        log_partido(nombre_archivo, f"⏰ Hora programada: {h_match.strftime('%H:%M:%S')}")
        log_partido(nombre_archivo, f"   Inicio grabación: {hora_inicio_real.strftime('%H:%M:%S')}")
        
        # Pre-calentamiento
        hora_precalentamiento = hora_inicio_real - timedelta(minutes=5)
        sec_wait = (hora_precalentamiento - datetime.now()).total_seconds()
        
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m para pre-calentamiento...")
            time.sleep(max(0, sec_wait))
        
        log_partido(nombre_archivo, "⚡ Pre-calentando streams...")
        streams_precargados = smart_selector.obtener_mejores_streams(fuentes_canal)
        
        # Esperar hasta inicio
        sec_wait = (hora_inicio_real - datetime.now()).total_seconds()
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m hasta inicio...")
            time.sleep(max(0, sec_wait))
        
        with _lock_partidos:
            _partidos_activos[nombre_archivo]['estado'] = 'grabando'
        
        # GRABACIÓN CON MONITOREO
        ruta_base = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL"
        ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
        
        log_partido(nombre_archivo, "🎬 INICIANDO GRABACIÓN CON MONITOREO")
        
        rutas_generadas = grabar_con_monitoreo_salud(
            fuentes_canal, ruta_base, nombre_archivo,
            url_promiedos, url_sofascore, ["NO_JUGANDO", "FINAL", "ENTRETIEMPO"],
            streams_precargados=streams_precargados
        )
        
        # Procesar video
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
        with _lock_partidos:
            if nombre_archivo in _partidos_activos:
                del _partidos_activos[nombre_archivo]
        
        log_partido(nombre_archivo, "🏁 Gestión finalizada")

# ================= MAIN =================

if __name__ == "__main__":
    setup_directorios()
    
    print("\n" + "="*70)
    print("🚀 SISTEMA MAESTRO v8.0 - MONITOREO EN TIEMPO REAL")
    print("="*70)
    print("\n🎯 MEJORAS v8:")
    print("   • IA SOLO valida contenido (no estado de partido)")
    print("   • Estado: Promiedos → SofaScore (backup automático)")
    print("   • Detección pantalla negra en tiempo real")
    print("   • Detección congelamiento en tiempo real")
    print("   • Recuperación automática ante problemas")
    print("   • 5 streams paralelos (mayor redundancia)")
    print("="*70 + "\n")
    
    # CONFIGURACIÓN DEL PARTIDO
    PARTIDOS = [
        {
            'promiedos': "https://www.promiedos.com.ar/game/liverpool-vs-brighton/eefchcc",
            'sofascore': "https://www.sofascore.com/es-la/football/match/liverpool-brighton-and-hove-albion/FsU#id:14025198"
        }
    ]
    
    hilos = []
    
    for partido in PARTIDOS:
        meta, fuente = obtener_metadata_con_backup(
            partido['promiedos'], 
            partido['sofascore']
        )
        
        if meta:
            t = threading.Thread(
                target=gestionar_partido_v8,
                args=(partido['promiedos'], partido['sofascore'], 
                      meta['nombre'], meta['hora']),
                daemon=False
            )
            t.start()
            hilos.append(t)
        else:
            print(f"❌ No se pudo procesar partido") 
    for t in hilos:
        t.join()
    
    print("\n✅ SISTEMA FINALIZADO")