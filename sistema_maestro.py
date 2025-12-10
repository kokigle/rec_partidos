import time
import os
import threading
import signal
import subprocess
from datetime import datetime, timedelta
from config_tv import GRILLA_CANALES
# Importamos nuestros modulos
import promiedos_client
import smart_selector
import uploader

# ================= CONFIGURACIÓN =================
CARPETA_LOCAL = "./partidos_grabados"
MARGEN_SEGURIDAD = 90 # Segundos extra a grabar post-final (Buffer + Delay)
MINUTOS_PREVIA = 5    # Iniciar antes
TIMEOUT_ENTRETIEMPO = 5 # 20 min max de espera en ET
# =================================================

def iniciar_grabacion_ffmpeg(stream_obj, ruta_salida):
    """
    Usa yt-dlp en modo nativo con los headers correctos para evitar Error 234.
    """
    print(f"🎥 REC: {os.path.basename(ruta_salida)} (Delay real: {stream_obj.delay:.1f}s)")
    
    cmd = [
        "yt-dlp", stream_obj.url,
        "-o", ruta_salida,
        "--hls-prefer-native",
        "--add-header", f"Referer:{stream_obj.referer}",
        "--add-header", f"User-Agent:{stream_obj.ua}",
        "--quiet", "--no-warnings",
        "--retries", "10",
        "--fragment-retries", "10"
    ]
    return subprocess.Popen(cmd)

def detener_grabacion(proceso):
    if proceso and proceso.poll() is None:
        print("🛑 Deteniendo grabación...")
        proceso.send_signal(signal.SIGINT) # Ctrl+C suave
        try:
            proceso.wait(timeout=15) # Esperar cierre limpio MP4
        except:
            proceso.kill() # Forzar si se traba

def unir_videos(v1, v2, salida):
    print("🎬 Uniendo partes...")
    lista = f"{salida}.txt"
    with open(lista, "w") as f:
        f.write(f"file '{os.path.abspath(v1)}'\nfile '{os.path.abspath(v2)}'\n")
    
    # Concatenación rápida sin recodificar
    subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", "-y", salida],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(lista)
    print(f"✅ VIDEO COMPLETO: {salida}")

def gestionar_partido(fuentes_video, url_promiedos, nombre_archivo, hora_inicio):
    print(f"📅 Agenda: {nombre_archivo} ({hora_inicio})")
    
    # --- ESPERA INICIAL ---
    ahora = datetime.now()
    h_match = datetime.strptime(hora_inicio, "%H:%M").replace(year=ahora.year, month=ahora.month, day=ahora.day)
    h_start = h_match - timedelta(minutes=MINUTOS_PREVIA)
    espera = (h_start - ahora).total_seconds()
    
    if espera > 0:
        print(f"⏳ {nombre_archivo}: Esperando {int(espera/60)} min para la previa...")
        time.sleep(espera)

    # Rutas de archivo
    ruta_1t = f"{CARPETA_LOCAL}/{nombre_archivo}_1T.mp4"
    ruta_2t = f"{CARPETA_LOCAL}/{nombre_archivo}_2T.mp4"
    ruta_full = f"{CARPETA_LOCAL}/{nombre_archivo}_FULL.mp4"
    
    tenemos_1t = False
    
    # --- FASE 1: PRIMER TIEMPO ---
    print(f"📡 Consultando estado inicial...")
    estado = promiedos_client.obtener_estado_partido(url_promiedos)
    
    if estado == "PREVIA" or estado == "JUGANDO_1T":
        print("🔍 Buscando mejor stream para 1T...")
        mejor_stream = smart_selector.obtener_mejor_stream(fuentes_video)
        
        if mejor_stream:
            proc_1t = iniciar_grabacion_ffmpeg(mejor_stream, ruta_1t)
            tenemos_1t = True
            
            # Calculamos el delay de corte
            tiempo_extra = mejor_stream.delay + MARGEN_SEGURIDAD
            
            while True:
                est = promiedos_client.obtener_estado_partido(url_promiedos)
                if est == "ENTRETIEMPO" or est == "JUGANDO_2T":
                    print(f"⏸️ Fin 1T detectado. Esperando {int(tiempo_extra)}s de delay...")
                    time.sleep(tiempo_extra)
                    detener_grabacion(proc_1t)
                    break
                if est == "FINAL":
                    detener_grabacion(proc_1t)
                    return
                time.sleep(30)
    else:
        print("⏩ El 1T ya pasó.")

    # --- FASE 2: ENTRETIEMPO ---
    if estado != "FINAL" and estado != "JUGANDO_2T":
        print(f"zzZ Esperando 2T (Max {int(TIMEOUT_ENTRETIEMPO/60)} min)...")
        inicio_et = time.time()
        
        while True:
            est = promiedos_client.obtener_estado_partido(url_promiedos)
            if est == "JUGANDO_2T":
                print("🚀 ¡Arrancó el 2T!")
                break
            
            # Timeout de seguridad
            if (time.time() - inicio_et) > TIMEOUT_ENTRETIEMPO:
                print("⚠️ Timeout de Entretiempo. Arrancando por seguridad.")
                break
                
            time.sleep(15)

    # --- FASE 3: SEGUNDO TIEMPO ---
    print("🔍 Buscando mejor stream para 2T (Refresco)...")
    mejor_stream = smart_selector.obtener_mejor_stream(fuentes_video) # Link fresco
    
    if mejor_stream:
        proc_2t = iniciar_grabacion_ffmpeg(mejor_stream, ruta_2t)
        
        tiempo_extra = mejor_stream.delay + MARGEN_SEGURIDAD + 60 # Un poco más para festejos
        
        while True:
            est = promiedos_client.obtener_estado_partido(url_promiedos)
            if est == "FINAL":
                print(f"🏁 Partido Finalizado. Esperando {int(tiempo_extra)}s...")
                time.sleep(tiempo_extra)
                detener_grabacion(proc_2t)
                break
            time.sleep(30)
            
        # --- FASE 4: POST-PRODUCCION ---
        if tenemos_1t and os.path.exists(ruta_2t):
            unir_videos(ruta_1t, ruta_2t, ruta_full)
            # Subir (Descomentar si tienes las keys puestas)
            # uploader.subir_video(ruta_full)
            
            # Limpieza opcional
            # os.remove(ruta_1t)
            # os.remove(ruta_2t)
        elif os.path.exists(ruta_2t):
            print(f"✅ Solo se grabó el 2T: {ruta_2t}")

def resolver_fuentes_de_tv(canales_partido):
    """
    Recibe ["ESPN Premium", "TNT Sports"] y busca en nuestra config
    qué links (Angulismo, LibreFutbol) corresponden.
    """
    fuentes_totales = []
    print(f"📡 Canales anunciados: {canales_partido}")
    
    for canal_promiedos in canales_partido:
        # Buscamos coincidencias parciales. 
        # Si Promiedos dice "TNT Sports Premium" y yo tengo "TNT Sports", sirve.
        encontrado = False
        for key_config, links in GRILLA_CANALES.items():
            if key_config.lower() in canal_promiedos.lower():
                print(f"   -> Mapeado: {canal_promiedos} usando configuración de '{key_config}'")
                fuentes_totales.extend(links)
                encontrado = True
        
        if not encontrado:
            print(f"   ⚠️ Alerta: No tienes configurado links para '{canal_promiedos}' en config_tv.py")
            
    return fuentes_totales

if __name__ == "__main__":
    if not os.path.exists("./partidos_grabados"): os.makedirs("./partidos_grabados")

    # ================= INPUT: SOLO LOS LINKS DE PROMIEDOS =================
    # ¡ESTO ES LO ÚNICO QUE TIENES QUE EDITAR CADA DÍA!
    URLS_PROMIEDOS = [
        "https://www.promiedos.com.ar/game/villarreal-vs-fc-copenhagen/efdieji", #FOX SPORTS
        "https://www.promiedos.com.ar/game/real-madrid-vs-manchester-city/efdieii", #FOX SPORTS
        "https://www.promiedos.com.ar/game/club-brugge-vs-arsenal/efdieif", #ESPN 2
        "https://www.promiedos.com.ar/game/athletic-bilbao-vs-psg/efdieic", #ESPN
        "https://www.promiedos.com.ar/game/benfica-vs-napoli/efdieij", #FOX SPORTS 2
        "https://www.promiedos.com.ar/game/borussia-dortmund-vs-bodo-glimt/efdieie", #ESPN 4
        "https://www.promiedos.com.ar/game/argentino-merlo-vs-deportivo-armenio/egchheb", #TYC SPORTS
        "https://www.promiedos.com.ar/game/real-pilar-vs-acassuso/egchhed", #DSPORTS
        "https://www.promiedos.com.ar/game/bayer-leverkusen-vs-newcastle-united/efdieid", #ESPN 3
    ]
    # ======================================================================

    hilos = []
    print("🚀 INICIANDO SISTEMA DE GRABACIÓN INTELIGENTE")
    
    for url in URLS_PROMIEDOS:
        # 1. Scrapeamos la info del partido
        print(f"\n🔍 Analizando ficha del partido: {url}")
        meta = promiedos_client.obtener_metadata_partido(url)
        
        if meta:
            # 2. Resolvemos los links de video automáticamente
            fuentes = resolver_fuentes_de_tv(meta['canales'])
            
            if fuentes:
                print(f"✅ Configuración automática exitosa para {meta['nombre']}")
                # 3. Lanzamos el hilo
                t = threading.Thread(
                    target=gestionar_partido, 
                    args=(fuentes, url, meta['nombre'], meta['hora'])
                )
                hilos.append(t)
                t.start()
            else:
                print(f"❌ Error: El partido {meta['nombre']} no tiene canales configurados o conocidos.")
        else:
            print("❌ No se pudo obtener información de Promiedos.")

    for t in hilos: t.join()