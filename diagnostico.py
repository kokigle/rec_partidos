#!/usr/bin/env python3
"""
Script de diagnóstico para verificar el sistema de grabación
"""
import os
import subprocess
import sys

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def check_dependencies():
    """Verifica dependencias instaladas"""
    print_header("1. VERIFICANDO DEPENDENCIAS")
    
    deps = [
        "requests",
        "beautifulsoup4",
        "selenium",
        "selenium-wire",
        "webdriver-manager",
        "yt-dlp",
        "python-dateutil"
    ]
    
    missing = []
    for dep in deps:
        try:
            __import__(dep.replace("-", "_"))
            print(f"✅ {dep}")
        except ImportError:
            print(f"❌ {dep} - FALTA")
            missing.append(dep)
    
    if missing:
        print(f"\n⚠️  Instalar faltantes: pip install {' '.join(missing)}")
        return False
    return True

def check_system_limits():
    """Verifica límites del sistema"""
    print_header("2. VERIFICANDO LÍMITES DEL SISTEMA")
    
    # File descriptors
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        print(f"📁 File Descriptors:")
        print(f"   Soft limit: {soft}")
        print(f"   Hard limit: {hard}")
        
        if soft < 1024:
            print(f"   ⚠️  Límite bajo, recomendado: 4096+")
            print(f"   Ejecutar: ulimit -n 4096")
        else:
            print(f"   ✅ Límite adecuado")
    except:
        print("⚠️  No se pudo verificar límites (Windows?)")

def check_ffmpeg():
    """Verifica ffmpeg y yt-dlp"""
    print_header("3. VERIFICANDO HERRAMIENTAS DE VIDEO")
    
    # ffmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], 
                              capture_output=True, 
                              timeout=5)
        if result.returncode == 0:
            version = result.stdout.decode().split('\n')[0]
            print(f"✅ ffmpeg: {version}")
        else:
            print("❌ ffmpeg: No funciona correctamente")
    except FileNotFoundError:
        print("❌ ffmpeg: NO INSTALADO")
        print("   Instalar: sudo apt install ffmpeg  (Linux)")
        print("            brew install ffmpeg     (Mac)")
    except Exception as e:
        print(f"⚠️  ffmpeg: Error verificando - {e}")
    
    # yt-dlp
    try:
        result = subprocess.run(["yt-dlp", "--version"], 
                              capture_output=True, 
                              timeout=5)
        if result.returncode == 0:
            version = result.stdout.decode().strip()
            print(f"✅ yt-dlp: v{version}")
        else:
            print("❌ yt-dlp: No funciona")
    except FileNotFoundError:
        print("❌ yt-dlp: NO INSTALADO")
        print("   Instalar: pip install yt-dlp")
    except Exception as e:
        print(f"⚠️  yt-dlp: Error verificando - {e}")

def check_chrome():
    """Verifica Chrome/Chromium"""
    print_header("4. VERIFICANDO CHROME/CHROMIUM")
    
    chrome_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    ]
    
    found = False
    for path in chrome_paths:
        if os.path.exists(path):
            print(f"✅ Chrome encontrado: {path}")
            found = True
            break
    
    if not found:
        print("⚠️  Chrome no encontrado en rutas estándar")
        print("   Instalar Google Chrome o Chromium")

def check_config_files():
    """Verifica archivos de configuración"""
    print_header("5. VERIFICANDO ARCHIVOS DEL PROYECTO")
    
    required_files = {
        "sistema_maestro.py": "Script principal",
        "smart_selector.py": "Selector de streams",
        "promiedos_client.py": "Cliente de Promiedos",
        "config_tv.py": "Configuración de canales",
        "uploader.py": "Subida de videos"
    }
    
    all_ok = True
    for filename, desc in required_files.items():
        if os.path.exists(filename):
            size = os.path.getsize(filename) / 1024
            print(f"✅ {filename} ({desc}) - {size:.1f} KB")
        else:
            print(f"❌ {filename} ({desc}) - FALTA")
            all_ok = False
    
    return all_ok

def check_directories():
    """Verifica y crea directorios necesarios"""
    print_header("6. VERIFICANDO DIRECTORIOS")
    
    dirs = ["./partidos_grabados", "./logs"]
    
    for d in dirs:
        if os.path.exists(d):
            print(f"✅ {d} existe")
        else:
            try:
                os.makedirs(d)
                print(f"✅ {d} creado")
            except Exception as e:
                print(f"❌ {d} - Error creando: {e}")

def test_promiedos_connection():
    """Prueba conexión a Promiedos"""
    print_header("7. PROBANDO CONEXIÓN A PROMIEDOS")
    
    try:
        import requests
        url = "https://www.promiedos.com.ar"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            print(f"✅ Promiedos accesible (código {response.status_code})")
            return True
        else:
            print(f"⚠️  Promiedos respondió con código {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error conectando a Promiedos: {e}")
        return False

def analyze_logs():
    """Analiza logs existentes"""
    print_header("8. ANALIZANDO LOGS (si existen)")
    
    if not os.path.exists("./logs"):
        print("   No hay logs todavía")
        return
    
    log_files = [f for f in os.listdir("./logs") if f.endswith(".log")]
    
    if not log_files:
        print("   No hay archivos de log")
        return
    
    print(f"📝 Archivos de log encontrados: {len(log_files)}")
    
    # Buscar errores comunes
    error_patterns = {
        "filedescriptor": 0,
        "RuntimeWarning": 0,
        "Timeout": 0,
        "❌": 0,
        "✅": 0
    }
    
    for log_file in log_files:
        with open(f"./logs/{log_file}", "r") as f:
            content = f.read()
            for pattern in error_patterns:
                error_patterns[pattern] += content.count(pattern)
    
    print("\n   Estadísticas de logs:")
    print(f"   • Éxitos (✅): {error_patterns['✅']}")
    print(f"   • Errores (❌): {error_patterns['❌']}")
    print(f"   • Timeouts: {error_patterns['Timeout']}")
    print(f"   • FD errors: {error_patterns['filedescriptor']}")
    print(f"   • Warnings: {error_patterns['RuntimeWarning']}")
    
    if error_patterns['filedescriptor'] > 0:
        print("\n   ⚠️  ALERTA: Errores de file descriptors detectados")
        print("      Solución: Reducir MAX_WORKERS o aumentar ulimit")

def print_recommendations():
    """Imprime recomendaciones finales"""
    print_header("RECOMENDACIONES")
    
    print("📋 Antes de ejecutar sistema_maestro.py:")
    print("   1. Verifica que TODAS las dependencias estén instaladas")
    print("   2. Asegúrate de tener espacio en disco (20GB+ recomendado)")
    print("   3. Configura los URLs de Promiedos en sistema_maestro.py")
    print("   4. Opcional: Aumenta límite de FDs con 'ulimit -n 4096'")
    print("\n📊 Durante la ejecución:")
    print("   • Monitorear FDs: watch -n 1 'lsof -p $(pgrep -f sistema) | wc -l'")
    print("   • Ver logs: tail -f logs/*.log")
    print("\n⚙️  Configuración actual en smart_selector.py:")
    print("   • MAX_WORKERS: 2 (no aumentar)")
    print("   • BATCH_SIZE: 2 (procesamiento lento pero estable)")
    print("   • TIMEOUT_PAGINA: 40s")

def main():
    print("="*70)
    print("  🔍 DIAGNÓSTICO DEL SISTEMA DE GRABACIÓN")
    print("="*70)
    
    checks = [
        ("Dependencias", check_dependencies),
        ("Límites del Sistema", check_system_limits),
        ("FFmpeg/yt-dlp", check_ffmpeg),
        ("Chrome/Chromium", check_chrome),
        ("Archivos Config", check_config_files),
        ("Directorios", check_directories),
        ("Conexión Promiedos", test_promiedos_connection),
        ("Análisis de Logs", analyze_logs)
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result if result is not None else True))
        except Exception as e:
            print(f"❌ Error ejecutando {name}: {e}")
            results.append((name, False))
    
    # Resumen
    print_header("RESUMEN")
    
    total = len(results)
    passed = sum(1 for _, result in results if result)
    
    print(f"Checks pasados: {passed}/{total}\n")
    
    for name, result in results:
        status = "✅" if result else "⚠️ "
        print(f"{status} {name}")
    
    print_recommendations()
    
    if passed == total:
        print("\n" + "="*70)
        print("  🎉 SISTEMA LISTO PARA USAR")
        print("="*70)
        print("\nEjecutar: python3 sistema_maestro.py")
    else:
        print("\n" + "="*70)
        print("  ⚠️  REVISA LOS ERRORES ANTES DE CONTINUAR")
        print("="*70)

if __name__ == "__main__":
    main()