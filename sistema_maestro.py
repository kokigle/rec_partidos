"""
SISTEMA MAESTRO v9.0 - CORREGIDO
CAMBIOS CRÍTICOS:
1. USA angulismo_scraper.py en lugar de config_tv.py
2. Detección AGRESIVA de streams congelados (15s en lugar de 30s)
3. Rescate inmediato sin esperar confirmación
4. Validación de archivos antes de usar
5. Rotación de streams cada 10 minutos preventivamente
"""

import time
import os
import threading
import signal
import subprocess
import json
from datetime import datetime, timedelta
from collections import defaultdict
import promiedos_client
import sofascore_client
import smart_selector
import uploader
import angulismo_scraper  # NUEVO
from urllib.parse import urlparse

# ================= CONFIGURACIÓN CRÍTICA =================

CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
CARPETA_TEMP = "./temp_segments"

# CORREGIDO: Detección más agresiva
MAX_STREAMS_PARALELOS = 4  # Reducido de 5 a 4 para estabilidad
INTERVALO_HEALTH_CHECK = 10  # Aumentado de 2s a 10s (menos overhead)
UMBRAL_SIN_CRECIMIENTO = 15  # CRÍTICO: 15s en lugar de 30s
MAX_RESCATES_CONSECUTIVOS = 3  # NUEVO: Límite de rescates

# Rotación preventiva
ROTACION_PREVENTIVA_MINUTOS = 10  # Rotar streams cada 10min preventivamente

# Overlap
OVERLAP_SEGUNDOS = 60

# Buffers
BUFFER_INICIO_PARTIDO = 180
BUFFER_FIN_PARTIDO = 300

# Thresholds
THRESHOLD_TAMAÑO_CORTE = 512 * 1024

# Locks
_lock_partidos = threading.Lock()
_partidos_activos = {}

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

# ================= METADATA CON SCRAPER =================

def obtener_metadata_con_scraper(url_promiedos, url_sofascore=None):
    """
    Obtiene metadata desde Promiedos/SofaScore
    """
    log_partido("sistema", "📡 Obteniendo metadata...")
    
    try:
        meta = promiedos_client.obtener_metadata_partido(url_promiedos)
        if meta:
            log_partido("sistema", f"✅ Promiedos: {meta['nombre']}")
            return meta, "promiedos"
    except Exception as e:
        log_partido("sistema", f"⚠️ Promiedos falló: {str(e)[:60]}")
    
    if url_sofascore:
        try:
            meta = sofascore_client.obtener_metadata_partido(url_sofascore)
            if meta:
                log_partido("sistema", f"✅ SofaScore: {meta['nombre']}")
                return meta, "sofascore"
        except Exception as e:
            log_partido("sistema", f"⚠️ SofaScore falló: {str(e)[:60]}")
    
    return None, None

def obtener_fuentes_dinamicas(url_promiedos):
    """
    Obtiene fuentes dinámicamente desde AngulismoTV
    REEMPLAZA config_tv.py
    """
    log_partido("sistema", "🌐 Obteniendo streams desde AngulismoTV...")
    
    try:
        fuentes = angulismo_scraper.obtener_streams_para_partido(
            url_promiedos,
            preferir_canales=["ESPN Premium", "Disney+", "TNT Sports", "Fox Sports"]
        )
        
        if fuentes:
            log_partido("sistema", f"✅ {len(fuentes)} fuentes obtenidas")
            return fuentes
        else:
            log_partido("sistema", "❌ No se obtuvieron fuentes")
            return []
            
    except Exception as e:
        log_partido("sistema", f"❌ Error obteniendo fuentes: {str(e)[:80]}")
        return []

def obtener_estado_con_backup(url_promiedos, url_sofascore=None):
    """
    Estado del partido con backup
    """
    try:
        estado = promiedos_client.obtener_estado_partido(url_promiedos)
        if estado != "ERROR":
            return estado, "promiedos"
    except:
        pass
    
    if url_sofascore:
        try:
            estado = sofascore_client.obtener_estado_partido(url_sofascore)
            if estado != "ERROR":
                return estado, "sofascore"
        except:
            pass
    
    return "ERROR", None

# ================= UTILIDADES =================

def obtener_tamanio_archivo(ruta):
    try:
        return os.path.getsize(ruta)
    except:
        return 0

def validar_archivo_video(ruta):
    """
    Valida que el archivo no esté corrupto
    NUEVO: Verificación con ffprobe
    """
    if not os.path.exists(ruta):
        return False
    
    tamaño = obtener_tamanio_archivo(ruta)
    if tamaño < THRESHOLD_TAMAÑO_CORTE:
        return False
    
    try:
        # Verificar con ffprobe
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', ruta
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            
            # Verificar que tenga video y audio
            if 'streams' in data:
                tiene_video = any(s.get('codec_type') == 'video' for s in data['streams'])
                tiene_audio = any(s.get('codec_type') == 'audio' for s in data['streams'])
                
                if tiene_video:
                    return True
        
        return False
        
    except Exception as e:
        # Si ffprobe falla, asumir que está OK si tiene tamaño
        return tamaño > 1024 * 1024  # Al menos 1MB

# ================= MOTOR DE GRABACIÓN MEJORADO =================

def iniciar_grabacion_robusta(stream_obj, ruta_salida, nombre_partido, sufijo=""):
    """
    Grabación con configuración más robusta
    """
    log_partido(nombre_partido, f"🎥 Iniciando REC{sufijo}: {os.path.basename(ruta_salida)}")
    log_partido(nombre_partido, f"   URL: {stream_obj.url[:100]}...")
    
    headers_str = ""
    headers_str += f"User-Agent: {stream_obj.ua}\\r\\n"
    headers_str += f"Referer: {stream_obj.referer}\\r\\n"
    headers_str += f"Origin: {urlparse(stream_obj.referer).scheme}://{urlparse(stream_obj.referer).netloc}\\r\\n"
    headers_str += "Accept: */*\\r\\n"
    
    if hasattr(stream_obj, 'cookies') and stream_obj.cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in stream_obj.cookies.items()])
        headers_str += f"Cookie: {cookie_str}\\r\\n"
    
    cmd = [
        "ffmpeg",
        "-headers", headers_str,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "3",  # Reducido de 5 a 3
        "-reconnect_at_eof", "1",  # NUEVO: Reconectar en EOF
        "-timeout", "20000000",  # Reducido timeout
        "-i", stream_obj.url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "2048",  # Reducido de 4096
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts+discardcorrupt+igndts",  # NUEVO: Ignorar DTS
        "-loglevel", "error",  # Solo errores
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
        
        time.sleep(5)
        
        if proceso.poll() is not None:
            stderr = proceso.stderr.read().decode('utf-8', errors='ignore')
            log_partido(nombre_partido, f"   ❌ FFMPEG murió: {stderr[:200]}")
            return None
        
        time.sleep(3)
        if validar_archivo_video(ruta_salida):
            size = obtener_tamanio_archivo(ruta_salida)
            log_partido(nombre_partido, f"   ✅ Grabación iniciada ({size} bytes)")
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
            proceso.wait(timeout=10)
        except:
            try:
                proceso.send_signal(signal.SIGINT)
                proceso.wait(timeout=10)
            except:
                proceso.kill()

# ================= GRABACIÓN CON ROTACIÓN PREVENTIVA =================

def grabar_con_rotacion_preventiva(fuentes_canal, ruta_base, nombre_partido,
                                   url_promiedos, url_sofascore, estados_fin):
    """
    Graba con rotación preventiva cada 10 minutos
    Evita que streams se congelen por tokens expirados
    """
    log_partido(nombre_partido, f"🚀 GRABACIÓN CON ROTACIÓN PREVENTIVA")
    log_partido(nombre_partido, f"   • Streams paralelos: {MAX_STREAMS_PARALELOS}")
    log_partido(nombre_partido, f"   • Rotación cada: {ROTACION_PREVENTIVA_MINUTOS}min")
    log_partido(nombre_partido, f"   • Detección congelamiento: {UMBRAL_SIN_CRECIMIENTO}s")
    
    procesos = []
    cambios_stream = 0
    rescates_consecutivos = 0
    ultimo_rescate_time = 0
    ultima_rotacion_time = time.time()
    
    # Obtener streams
    candidatos = smart_selector.obtener_mejores_streams(fuentes_canal)
    
    if not candidatos:
        log_partido(nombre_partido, "❌ No hay streams disponibles")
        return []
    
    # Filtrar duplicados
    urls_usadas = set()
    streams_unicos = []
    for s in candidatos:
        if s.url not in urls_usadas:
            streams_unicos.append(s)
            urls_usadas.add(s.url)
    
    max_streams = min(len(streams_unicos), MAX_STREAMS_PARALELOS)
    streams_respaldo = streams_unicos[max_streams:]
    
    log_partido(nombre_partido, f"📊 {max_streams} streams primarios + {len(streams_respaldo)} respaldo")
    
    # Iniciar streams
    for i in range(max_streams):
        stream = streams_unicos[i]
        ruta = f"{ruta_base}_p{cambios_stream}_s{i}.mp4"
        p = iniciar_grabacion_robusta(stream, ruta, nombre_partido, f" [S{i}]")
        
        if p:
            procesos.append({
                "proc": p,
                "ruta": ruta,
                "stream": stream,
                "idx": i,
                "estado": "ok",
                "last_check": time.time(),
                "last_size": 0,
                "stream_id": i,
                "tiempo_inicio": time.time()
            })
    
    log_partido(nombre_partido, f"✅ {len([p for p in procesos if p['estado']=='ok'])} streams activos")
    
    # BUCLE DE MONITOREO
    ultimo_check_metadata = time.time()
    fase_actual = "1T"
    tiempo_inicio_fase = datetime.now()
    
    while True:
        time.sleep(INTERVALO_HEALTH_CHECK)
        now = time.time()
        
        # A) ROTACIÓN PREVENTIVA cada 10 minutos
        if now - ultima_rotacion_time >= (ROTACION_PREVENTIVA_MINUTOS * 60):
            log_partido(nombre_partido, "🔄 ROTACIÓN PREVENTIVA (evitar expiración de tokens)")
            
            # Obtener nuevos streams
            nuevos_streams = smart_selector.obtener_mejores_streams(fuentes_canal)
            
            if nuevos_streams and len(nuevos_streams) >= 2:
                # Reemplazar todos los streams con overlap
                nuevos_procesos = []
                
                for i, nuevo_s in enumerate(nuevos_streams[:MAX_STREAMS_PARALELOS]):
                    cambios_stream += 1
                    ruta_nuevo = f"{ruta_base}_rot{cambios_stream}.mp4"
                    
                    proc_nuevo = iniciar_grabacion_robusta(
                        nuevo_s, ruta_nuevo, nombre_partido, f" [ROT-{i}]"
                    )
                    
                    if proc_nuevo:
                        nuevos_procesos.append({
                            "proc": proc_nuevo,
                            "ruta": ruta_nuevo,
                            "stream": nuevo_s,
                            "idx": 100 + i,
                            "estado": "ok",
                            "last_check": now,
                            "last_size": 0,
                            "stream_id": 100 + i,
                            "tiempo_inicio": now
                        })
                
                if nuevos_procesos:
                    log_partido(nombre_partido, f"   ⏳ Overlap {OVERLAP_SEGUNDOS}s...")
                    time.sleep(OVERLAP_SEGUNDOS)
                    
                    # Detener viejos
                    for p_obj in procesos:
                        if p_obj["estado"] == "ok":
                            detener_grabacion_suave(p_obj["proc"], nombre_partido, f"S{p_obj['idx']}")
                            p_obj["estado"] = "dead"
                    
                    procesos = nuevos_procesos
                    ultima_rotacion_time = now
                    log_partido(nombre_partido, "   ✅ Rotación completada")
        
        # B) VERIFICAR ESTADO DEL PARTIDO
        if now - ultimo_check_metadata >= 20:
            estado, fuente = obtener_estado_con_backup(url_promiedos, url_sofascore)
            log_partido(nombre_partido, f"📡 Estado ({fuente}): {estado}")
            
            tiempo_fase = (datetime.now() - tiempo_inicio_fase).total_seconds() / 60
            
            if estado in estados_fin:
                if fase_actual == "1T" and tiempo_fase >= 35:
                    log_partido(nombre_partido, "🏁 Fin 1T confirmado")
                    break
                elif fase_actual == "2T" and tiempo_fase >= 35:
                    log_partido(nombre_partido, "🏁 Fin 2T confirmado")
                    break
                elif estado == "FINAL":
                    log_partido(nombre_partido, "🏁 FINAL")
                    break
            
            if estado == "JUGANDO_2T" and fase_actual == "1T":
                fase_actual = "2T"
                tiempo_inicio_fase = datetime.now()
                log_partido(nombre_partido, "⚽ INICIO 2T")
            
            ultimo_check_metadata = now
        
        # C) HEALTH CHECK AGRESIVO
        procesos_vivos = 0
        streams_congelados = []
        
        for p_obj in procesos:
            if p_obj["estado"] == "dead":
                continue
            
            if p_obj["proc"].poll() is None:
                try:
                    tamaño_actual = obtener_tamanio_archivo(p_obj["ruta"])
                    
                    if tamaño_actual > p_obj["last_size"]:
                        p_obj["last_size"] = tamaño_actual
                        p_obj["last_check"] = now
                        procesos_vivos += 1
                    else:
                        # CRÍTICO: 15s en lugar de 30s
                        if now - p_obj["last_check"] > UMBRAL_SIN_CRECIMIENTO:
                            log_partido(nombre_partido, f"   ❄️ S{p_obj['idx']} congelado {int(now - p_obj['last_check'])}s")
                            streams_congelados.append(p_obj['idx'])
                            p_obj["estado"] = "dead"
                except:
                    pass
            else:
                p_obj["estado"] = "dead"
                log_partido(nombre_partido, f"   ☠️ S{p_obj['idx']} murió")
        
        # D) RESCATE INMEDIATO si hay congelados
        if streams_congelados and procesos_vivos < MAX_STREAMS_PARALELOS:
            # Prevenir rescates infinitos
            if now - ultimo_rescate_time < 60:  # Mínimo 1min entre rescates
                continue
            
            if rescates_consecutivos >= MAX_RESCATES_CONSECUTIVOS:
                log_partido(nombre_partido, "⚠️ Límite de rescates alcanzado - esperando rotación preventiva")
                continue
            
            log_partido(nombre_partido, "🚨 RESCATE INMEDIATO")
            
            nuevos = smart_selector.obtener_mejores_streams(fuentes_canal)
            
            if nuevos:
                # Reemplazar solo los congelados
                for i, nuevo_s in enumerate(nuevos[:len(streams_congelados)]):
                    cambios_stream += 1
                    ruta_res = f"{ruta_base}_rescue{cambios_stream}.mp4"
                    
                    proc_res = iniciar_grabacion_robusta(
                        nuevo_s, ruta_res, nombre_partido, f" [RESCUE-{i}]"
                    )
                    
                    if proc_res:
                        procesos.append({
                            "proc": proc_res,
                            "ruta": ruta_res,
                            "stream": nuevo_s,
                            "idx": 200 + cambios_stream,
                            "estado": "ok",
                            "last_check": now,
                            "last_size": 0,
                            "stream_id": 200 + cambios_stream,
                            "tiempo_inicio": now
                        })
                        procesos_vivos += 1
                
                ultimo_rescate_time = now
                rescates_consecutivos += 1
                log_partido(nombre_partido, f"✅ Rescate {rescates_consecutivos}/{MAX_RESCATES_CONSECUTIVOS}")
        
        # Resetear contador si hay streams vivos
        if procesos_vivos >= 2:
            rescates_consecutivos = 0
        
        # E) Log periódico
        if int(now) % 30 == 0:
            log_partido(nombre_partido, f"📊 {procesos_vivos} streams vivos, fase: {fase_actual}")
    
    # Buffer final
    log_partido(nombre_partido, f"⏳ Buffer final {BUFFER_FIN_PARTIDO}s...")
    time.sleep(BUFFER_FIN_PARTIDO)
    
    # Detener todos
    for p_obj in procesos:
        if p_obj["estado"] == "ok" and p_obj["proc"].poll() is None:
            detener_grabacion_suave(p_obj["proc"], nombre_partido, "final")
    
    time.sleep(5)
    
    # Validar archivos
    rutas_validas = []
    for p in procesos:
        if validar_archivo_video(p["ruta"]):
            rutas_validas.append(p["ruta"])
        else:
            log_partido(nombre_partido, f"   ⚠️ {os.path.basename(p['ruta'])} corrupto/inválido")
    
    log_partido(nombre_partido, f"📦 {len(rutas_validas)} archivos válidos de {len(procesos)} total")
    
    return rutas_validas

# ================= UNIÓN =================

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

# ================= GESTOR PRINCIPAL =================

def gestionar_partido_v9(url_promiedos, url_sofascore, nombre_archivo, hora_inicio):
    """
    Gestor v9 con scraper dinámico y rotación preventiva
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
        log_partido(nombre_archivo, f"📅 INICIANDO GESTIÓN v9.0")
        log_partido(nombre_archivo, f"   • Scraper dinámico de AngulismoTV")
        log_partido(nombre_archivo, f"   • Rotación preventiva cada {ROTACION_PREVENTIVA_MINUTOS}min")
        log_partido(nombre_archivo, f"   • Detección congelamiento: {UMBRAL_SIN_CRECIMIENTO}s")
        
        # Metadata
        meta, fuente = obtener_metadata_con_scraper(url_promiedos, url_sofascore)
        if not meta:
            log_partido(nombre_archivo, "❌ No se pudo obtener metadata")
            return
        
        # Obtener fuentes dinámicamente
        fuentes_canal = obtener_fuentes_dinamicas(url_promiedos)
        
        if not fuentes_canal:
            log_partido(nombre_archivo, "❌ No se obtuvieron fuentes de AngulismoTV")
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
        
        # Esperar
        sec_wait = (hora_inicio_real - datetime.now()).total_seconds()
        if sec_wait > 0:
            log_partido(nombre_archivo, f"⏳ Esperando {int(sec_wait/60)}m hasta inicio...")
            time.sleep(max(0, sec_wait))
        
        with _lock_partidos:
            _partidos_activos[nombre_archivo]['estado'] = 'grabando'
        
        # GRABACIÓN
        ruta_base = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL"
        ruta_final = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
        
        log_partido(nombre_archivo, "🎬 INICIANDO GRABACIÓN")
        
        rutas_generadas = grabar_con_rotacion_preventiva(
            fuentes_canal, ruta_base, nombre_archivo,
            url_promiedos, url_sofascore, ["NO_JUGANDO", "FINAL", "ENTRETIEMPO"]
        )
        
        # Procesar
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
    print("🚀 SISTEMA MAESTRO v9.0 - CORREGIDO")
    print("="*70)
    print("\n🎯 MEJORAS v9:")
    print("   • Scraper dinámico de AngulismoTV (sin config_tv.py)")
    print("   • Rotación preventiva cada 10min")
    print("   • Detección congelamiento en 15s (antes 30s)")
    print("   • Validación de archivos antes de usar")
    print("   • Límite de rescates consecutivos")
    print("="*70 + "\n")
    
    # CONFIGURACIÓN
    PARTIDOS = [
        {
            'promiedos': "https://www.promiedos.com.ar/game/metz-vs-psg/eegdjhd",
            'sofascore': "https://www.sofascore.com/es-la/football/match/metz-paris-saint-germain/UHsbI#id:14064442"  # Opcional
        }
    ]
    
    hilos = []
    
    for partido in PARTIDOS:
        meta, fuente = obtener_metadata_con_scraper(
            partido['promiedos'],
            partido.get('sofascore')
        )
        
        if meta:
            t = threading.Thread(
                target=gestionar_partido_v9,
                args=(partido['promiedos'], partido.get('sofascore'),
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