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

# ================= CONFIGURACIÓN MEJORADA =================
CARPETA_LOCAL = "./partidos_grabados"
CARPETA_LOGS = "./logs"
MARGEN_SEGURIDAD = 90
MINUTOS_PREVIA = 5
MINUTOS_PREBUSQUEDA = 15  # Buscar streams 15 min antes
TIMEOUT_ENTRETIEMPO = 1200  # 20 min
MAX_REINTENTOS_STREAM = 3
INTERVALO_REFRESCO_ESTADO = 30  # Consultar estado cada 30s (aumentado)
INTERVALO_CHECK_SALUD = 90  # Verificar salud de grabación cada 90s
# ==========================================================

# Cache global de streams encontrados
cache_streams = {}
lock_cache = threading.Lock()

def setup_directorios():
    """Crea directorios necesarios"""
    for carpeta in [CARPETA_LOCAL, CARPETA_LOGS]:
        os.makedirs(carpeta, exist_ok=True)

def log_partido(nombre_archivo, mensaje):
    """Logger específico por partido"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_msg = f"[{timestamp}] {mensaje}"
    print(log_msg)
    
    with open(f"{CARPETA_LOGS}/{nombre_archivo}.log", "a") as f:
        f.write(log_msg + "\n")

def iniciar_grabacion_ffmpeg(stream_obj, ruta_salida, nombre_partido):
    """Grabación con manejo de errores mejorado"""
    log_partido(nombre_partido, f"🎥 Iniciando grabación: {os.path.basename(ruta_salida)}")
    log_partido(nombre_partido, f"   Stream: {stream_obj.fuente} (Delay: {stream_obj.delay:.1f}s)")
    
    cmd = [
        "yt-dlp", stream_obj.url,
        "-o", ruta_salida,
        "--hls-prefer-native",
        "--add-header", f"Referer:{stream_obj.referer}",
        "--add-header", f"User-Agent:{stream_obj.ua}",
        "--no-warnings",
        "--retries", "15",
        "--fragment-retries", "15",
        "--concurrent-fragments", "3",
        "--buffer-size", "32K",
        "--http-chunk-size", "1M"
    ]
    
    try:
        proceso = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        return proceso
    except Exception as e:
        log_partido(nombre_partido, f"❌ Error iniciando ffmpeg: {e}")
        return None

def verificar_grabacion_activa(proceso, nombre_partido):
    """Verifica que la grabación esté funcionando"""
    if not proceso or proceso.poll() is not None:
        log_partido(nombre_partido, "⚠️ Proceso de grabación no está activo")
        return False
    return True

def detener_grabacion(proceso, nombre_partido, descripcion=""):
    """Detención segura con timeout"""
    if proceso and proceso.poll() is None:
        log_partido(nombre_partido, f"🛑 Deteniendo grabación {descripcion}...")
        
        try:
            proceso.send_signal(signal.SIGINT)
            proceso.wait(timeout=20)
            log_partido(nombre_partido, "✅ Grabación finalizada correctamente")
        except subprocess.TimeoutExpired:
            log_partido(nombre_partido, "⚠️ Timeout en cierre, forzando...")
            proceso.kill()
            proceso.wait()
        except Exception as e:
            log_partido(nombre_partido, f"⚠️ Error deteniendo: {e}")
            try:
                proceso.kill()
            except:
                pass

def unir_videos(v1, v2, salida, nombre_partido):
    """Unión de videos con validación"""
    log_partido(nombre_partido, "🎬 Uniendo partes del partido...")
    
    # Verificar que existan ambos archivos
    if not os.path.exists(v1):
        log_partido(nombre_partido, f"⚠️ No existe {v1}, usando solo 2T")
        if os.path.exists(v2):
            os.rename(v2, salida)
        return False
    
    if not os.path.exists(v2):
        log_partido(nombre_partido, f"⚠️ No existe {v2}, usando solo 1T")
        os.rename(v1, salida)
        return False
    
    lista = f"{salida}.txt"
    try:
        with open(lista, "w") as f:
            f.write(f"file '{os.path.abspath(v1)}'\nfile '{os.path.abspath(v2)}'\n")
        
        resultado = subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", "-y", salida],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300
        )
        
        os.remove(lista)
        
        if resultado.returncode == 0:
            log_partido(nombre_partido, f"✅ Video completo creado: {os.path.basename(salida)}")
            # Limpiar partes individuales
            try:
                os.remove(v1)
                os.remove(v2)
                log_partido(nombre_partido, "🧹 Archivos temporales eliminados")
            except:
                pass
            return True
        else:
            log_partido(nombre_partido, "❌ Error en la unión de videos")
            return False
            
    except Exception as e:
        log_partido(nombre_partido, f"❌ Error crítico uniendo videos: {e}")
        return False

def prebuscar_streams(fuentes_video, nombre_archivo, cache_key):
    """Búsqueda anticipada de streams y guardado en caché"""
    log_partido(nombre_archivo, f"🔍 PREBÚSQUEDA: Analizando {len(fuentes_video)} fuentes...")
    
    mejor_stream = smart_selector.obtener_mejor_stream(fuentes_video)
    
    if mejor_stream:
        with lock_cache:
            cache_streams[cache_key] = {
                'stream': mejor_stream,
                'timestamp': time.time(),
                'fuentes_backup': fuentes_video
            }
        log_partido(nombre_archivo, f"✅ Stream pre-cargado: {mejor_stream.fuente}")
        return True
    else:
        log_partido(nombre_archivo, "⚠️ No se encontraron streams en prebúsqueda")
        return False

def obtener_stream_con_fallback(cache_key, fuentes_video, nombre_archivo, fase):
    """Obtiene stream del caché o busca uno nuevo con sistema de fallback"""
    
    # Intentar usar caché si está fresco (menos de 10 min)
    with lock_cache:
        if cache_key in cache_streams:
            cache_data = cache_streams[cache_key]
            edad = time.time() - cache_data['timestamp']
            if edad < 600:  # 10 minutos
                log_partido(nombre_archivo, f"📦 Usando stream cacheado para {fase} (edad: {int(edad)}s)")
                return cache_data['stream']
    
    # Buscar nuevo stream
    log_partido(nombre_archivo, f"🔍 Buscando stream fresco para {fase}...")
    
    for intento in range(MAX_REINTENTOS_STREAM):
        if intento > 0:
            log_partido(nombre_archivo, f"🔄 Reintento {intento + 1}/{MAX_REINTENTOS_STREAM}")
            time.sleep(5)
        
        stream = smart_selector.obtener_mejor_stream(fuentes_video)
        
        if stream:
            # Actualizar caché
            with lock_cache:
                cache_streams[cache_key] = {
                    'stream': stream,
                    'timestamp': time.time(),
                    'fuentes_backup': fuentes_video
                }
            return stream
    
    log_partido(nombre_archivo, f"❌ No se pudo obtener stream después de {MAX_REINTENTOS_STREAM} intentos")
    return None

def monitorear_grabacion(proceso, stream_obj, nombre_partido, fase):
    """Monitorea la salud de la grabación y reinicia si falla"""
    ultimo_check = time.time()
    
    while proceso and proceso.poll() is None:
        time.sleep(30)
        
        # Verificar cada 90s que el proceso siga vivo
        if time.time() - ultimo_check > INTERVALO_CHECK_SALUD:
            if proceso.poll() is not None:
                log_partido(nombre_partido, f"⚠️ Grabación {fase} falló, requiere reinicio")
                return False
            ultimo_check = time.time()
    
    return True

def gestionar_partido(fuentes_video, url_promiedos, nombre_archivo, hora_inicio):
    """Gestor principal con todas las mejoras"""
    cache_key = nombre_archivo
    
    log_partido(nombre_archivo, "="*60)
    log_partido(nombre_archivo, f"📅 PARTIDO AGENDADO: {nombre_archivo}")
    log_partido(nombre_archivo, f"⏰ Hora de inicio: {hora_inicio}")
    log_partido(nombre_archivo, f"🔗 Promiedos: {url_promiedos}")
    log_partido(nombre_archivo, "="*60)
    
    ahora = datetime.now()
    h_match = datetime.strptime(hora_inicio, "%H:%M").replace(
        year=ahora.year, month=ahora.month, day=ahora.day
    )
    
    # Ajustar si el partido es al día siguiente
    if h_match < ahora:
        h_match += timedelta(days=1)
    
    h_prebusqueda = h_match - timedelta(minutes=MINUTOS_PREBUSQUEDA)
    h_inicio_grabacion = h_match - timedelta(minutes=MINUTOS_PREVIA)
    
    # --- FASE 0: ESPERA HASTA PREBÚSQUEDA ---
    espera_prebusqueda = (h_prebusqueda - datetime.now()).total_seconds()
    if espera_prebusqueda > 0:
        log_partido(nombre_archivo, f"⏳ Esperando {int(espera_prebusqueda/60)} min hasta prebúsqueda...")
        time.sleep(espera_prebusqueda)
    
    # --- PREBÚSQUEDA DE STREAMS (15 min antes) ---
    log_partido(nombre_archivo, "🚀 Iniciando prebúsqueda de streams...")
    prebuscar_streams(fuentes_video, nombre_archivo, cache_key)
    
    # --- ESPERA HASTA INICIO DE GRABACIÓN ---
    espera_inicio = (h_inicio_grabacion - datetime.now()).total_seconds()
    if espera_inicio > 0:
        log_partido(nombre_archivo, f"⏳ Esperando {int(espera_inicio/60)} min para iniciar grabación...")
        time.sleep(espera_inicio)
    
    # Rutas de archivo
    ruta_1t = f"{CARPETA_LOCAL}/{nombre_archivo}_1T.mp4"
    ruta_2t = f"{CARPETA_LOCAL}/{nombre_archivo}_2T.mp4"
    ruta_full = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
    
    tenemos_1t = False
    proceso_1t = None
    
    # --- FASE 1: PRIMER TIEMPO ---
    log_partido(nombre_archivo, "📡 Consultando estado del partido...")
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    log_partido(nombre_archivo, f"   Estado actual: {estado}")
    
    if estado in ["PREVIA", "JUGANDO_1T"]:
        stream_1t = obtener_stream_con_fallback(cache_key, fuentes_video, nombre_archivo, "1T")
        
        if stream_1t:
            proceso_1t = iniciar_grabacion_ffmpeg(stream_1t, ruta_1t, nombre_archivo)
            
            if proceso_1t:
                tenemos_1t = True
                tiempo_extra = stream_1t.delay + MARGEN_SEGURIDAD
                
                log_partido(nombre_archivo, "🎮 Monitoreando 1er tiempo...")
                
                # Contador para evitar spam de logs
                contador_checks = 0
                
                while True:
                    time.sleep(INTERVALO_REFRESCO_ESTADO)
                    contador_checks += 1
                    
                    # Verificar que la grabación siga activa cada 3 checks
                    if contador_checks % 3 == 0:
                        if not verificar_grabacion_activa(proceso_1t, nombre_archivo):
                            log_partido(nombre_archivo, "⚠️ Grabación 1T falló, reintentando...")
                            stream_1t = obtener_stream_con_fallback(cache_key, fuentes_video, nombre_archivo, "1T (Reinicio)")
                            if stream_1t:
                                proceso_1t = iniciar_grabacion_ffmpeg(stream_1t, ruta_1t, nombre_archivo)
                    
                    estado = promiedos_client.obtener_estado_partido(url_promiedos)
                    
                    # Solo loggear si el estado cambió
                    if contador_checks == 1 or estado not in ["PREVIA", "JUGANDO_1T"]:
                        log_partido(nombre_archivo, f"   Estado: {estado}")
                    
                    if estado in ["ENTRETIEMPO", "JUGANDO_2T"]:
                        log_partido(nombre_archivo, f"⏸️ Fin 1T detectado. Esperando {int(tiempo_extra)}s (delay + buffer)...")
                        time.sleep(tiempo_extra)
                        detener_grabacion(proceso_1t, nombre_archivo, "1T")
                        break
                    
                    if estado == "FINAL":
                        log_partido(nombre_archivo, "🏁 Partido terminó en 1T (suspendido/walkover)")
                        detener_grabacion(proceso_1t, nombre_archivo, "1T")
                        return
            else:
                log_partido(nombre_archivo, "❌ No se pudo iniciar grabación del 1T")
        else:
            log_partido(nombre_archivo, "❌ No hay streams disponibles para el 1T")
    else:
        log_partido(nombre_archivo, "⏩ El 1T ya finalizó, esperando 2T...")
    
    # --- FASE 2: ENTRETIEMPO ---
    if estado not in ["FINAL", "JUGANDO_2T"]:
        log_partido(nombre_archivo, f"☕ ENTRETIEMPO - Esperando 2T (máx {int(TIMEOUT_ENTRETIEMPO/60)} min)...")
        inicio_et = time.time()
        
        while True:
            time.sleep(15)
            estado = promiedos_client.obtener_estado_partido(url_promiedos)
            
            if estado == "JUGANDO_2T":
                log_partido(nombre_archivo, "🚀 ¡Arrancó el 2T!")
                break
            
            if estado == "FINAL":
                log_partido(nombre_archivo, "⚠️ Partido terminó durante el entretiempo")
                return
            
            if (time.time() - inicio_et) > TIMEOUT_ENTRETIEMPO:
                log_partido(nombre_archivo, "⚠️ Timeout de entretiempo alcanzado, iniciando 2T por seguridad")
                break
    
    # --- FASE 3: SEGUNDO TIEMPO ---
    log_partido(nombre_archivo, "🔄 Refrescando búsqueda de streams para 2T...")
    stream_2t = obtener_stream_con_fallback(cache_key, fuentes_video, nombre_archivo, "2T")
    
    if stream_2t:
        proceso_2t = iniciar_grabacion_ffmpeg(stream_2t, ruta_2t, nombre_archivo)
        
        if proceso_2t:
            tiempo_extra = stream_2t.delay + MARGEN_SEGURIDAD + 60  # Extra para festejos
            
            log_partido(nombre_archivo, "🎮 Monitoreando 2do tiempo...")
            
            # Contador para evitar spam de logs
            contador_checks = 0
            
            while True:
                time.sleep(INTERVALO_REFRESCO_ESTADO)
                contador_checks += 1
                
                # Verificar salud de grabación cada 3 checks
                if contador_checks % 3 == 0:
                    if not verificar_grabacion_activa(proceso_2t, nombre_archivo):
                        log_partido(nombre_archivo, "⚠️ Grabación 2T falló, reintentando...")
                        stream_2t = obtener_stream_con_fallback(cache_key, fuentes_video, nombre_archivo, "2T (Reinicio)")
                        if stream_2t:
                            proceso_2t = iniciar_grabacion_ffmpeg(stream_2t, ruta_2t, nombre_archivo)
                
                estado = promiedos_client.obtener_estado_partido(url_promiedos)
                
                # Solo loggear si hay cambio de estado
                if contador_checks == 1 or estado == "FINAL":
                    log_partido(nombre_archivo, f"   Estado: {estado}")
                
                if estado == "FINAL":
                    log_partido(nombre_archivo, f"🏁 FINAL - Esperando {int(tiempo_extra)}s adicionales...")
                    time.sleep(tiempo_extra)
                    detener_grabacion(proceso_2t, nombre_archivo, "2T")
                    break
    else:
        log_partido(nombre_archivo, "❌ No hay streams disponibles para el 2T")
        return
    
    # --- FASE 4: POST-PRODUCCIÓN ---
    log_partido(nombre_archivo, "🎬 Iniciando post-producción...")
    
    if os.path.exists(ruta_1t) and os.path.exists(ruta_2t):
        if unir_videos(ruta_1t, ruta_2t, ruta_full, nombre_archivo):
            log_partido(nombre_archivo, "☁️ Subiendo a Streamtape...")
            link = uploader.subir_video(ruta_full)
            if link:
                log_partido(nombre_archivo, f"✅ LINK PÚBLICO: {link}")
                # Guardar link en archivo
                with open(f"{CARPETA_LOCAL}/links.txt", "a") as f:
                    f.write(f"{nombre_archivo}: {link}\n")
    elif os.path.exists(ruta_2t):
        log_partido(nombre_archivo, "✅ Solo se grabó el 2T")
        os.rename(ruta_2t, ruta_full)
    elif os.path.exists(ruta_1t):
        log_partido(nombre_archivo, "✅ Solo se grabó el 1T")
        os.rename(ruta_1t, ruta_full)
    
    log_partido(nombre_archivo, "="*60)
    log_partido(nombre_archivo, "🎉 PROCESO COMPLETADO")
    log_partido(nombre_archivo, "="*60)

def resolver_fuentes_de_tv(canales_partido):
    """Resuelve fuentes con mejor logging"""
    fuentes_totales = []
    canales_encontrados = []
    canales_faltantes = []
    
    for canal_promiedos in canales_partido:
        encontrado = False
        for key_config, links in GRILLA_CANALES.items():
            if key_config.lower() in canal_promiedos.lower():
                fuentes_totales.extend(links)
                canales_encontrados.append(f"{canal_promiedos} → {key_config}")
                encontrado = True
                break
        
        if not encontrado:
            canales_faltantes.append(canal_promiedos)
    
    print(f"\n📺 CANALES DETECTADOS: {', '.join(canales_partido)}")
    if canales_encontrados:
        print(f"✅ Mapeados:")
        for c in canales_encontrados:
            print(f"   • {c}")
    if canales_faltantes:
        print(f"⚠️  Sin configurar:")
        for c in canales_faltantes:
            print(f"   • {c}")
    
    return fuentes_totales

if __name__ == "__main__":
    setup_directorios()
    
    print("="*70)
    print("🚀 SISTEMA DE GRABACIÓN INTELIGENTE v2.0")
    print("="*70)
    
    # URLS DE PROMIEDOS
    URLS_PROMIEDOS = [
        "https://www.promiedos.com.ar/game/villarreal-vs-fc-copenhagen/efdieji",
    ]
    
    hilos = []
    partidos_configurados = []
    
    for idx, url in enumerate(URLS_PROMIEDOS, 1):
        print(f"\n{'='*70}")
        print(f"📋 CONFIGURANDO PARTIDO {idx}/{len(URLS_PROMIEDOS)}")
        print(f"🔗 {url}")
        
        meta = promiedos_client.obtener_metadata_partido(url)
        
        if meta:
            fuentes = resolver_fuentes_de_tv(meta['canales'])
            
            if fuentes:
                print(f"✅ {len(fuentes)} fuentes de video configuradas")
                
                t = threading.Thread(
                    target=gestionar_partido,
                    args=(fuentes, url, meta['nombre'], meta['hora']),
                    daemon=False
                )
                hilos.append(t)
                partidos_configurados.append(meta['nombre'])
                t.start()
                
                # Pequeña pausa entre inicios de threads
                time.sleep(1)
            else:
                print(f"❌ Sin fuentes disponibles para: {meta['nombre']}")
        else:
            print("❌ Error obteniendo metadata de Promiedos")
    
    print(f"\n{'='*70}")
    print(f"✅ {len(partidos_configurados)} partidos en cola de grabación:")
    for partido in partidos_configurados:
        print(f"   • {partido}")
    print(f"{'='*70}\n")
    
    # Esperar todos los hilos
    for t in hilos:
        t.join()
    
    print("\n🎉 TODOS LOS PARTIDOS FINALIZADOS")
    print(f"📁 Videos guardados en: {CARPETA_LOCAL}")
    print(f"📝 Logs disponibles en: {CARPETA_LOGS}")