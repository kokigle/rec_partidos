#!/usr/bin/env python3
"""
DIAGNÓSTICO DETALLADO - Encuentra el problema exacto
"""

import subprocess
import os
import time

print("\n" + "="*70)
print("🔍 DIAGNÓSTICO DETALLADO DE FFMPEG")
print("="*70 + "\n")

# ============ TEST 1: FFMPEG INSTALADO ============
print("1️⃣  Verificando instalación de FFmpeg...")

try:
    result = subprocess.run(
        ['ffmpeg', '-version'],
        capture_output=True,
        text=True,
        timeout=5
    )
    
    if result.returncode == 0:
        version = result.stdout.split('\n')[0]
        print(f"   ✅ {version}")
    else:
        print(f"   ❌ FFmpeg respondió con error")
        print(f"   STDERR: {result.stderr[:200]}")
        exit(1)
        
except FileNotFoundError:
    print("   ❌ FFmpeg no encontrado en PATH")
    exit(1)
except Exception as e:
    print(f"   ❌ Error: {e}")
    exit(1)

# ============ TEST 2: CONECTIVIDAD ============
print("\n2️⃣  Verificando conectividad a Internet...")

import requests

test_urls = [
    "https://www.google.com",
    "https://ntv1.akamaized.net",
]

for url in test_urls:
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            print(f"   ✅ Conectividad OK: {url}")
        else:
            print(f"   ⚠️  {url} respondió con código {resp.status_code}")
    except Exception as e:
        print(f"   ❌ Error conectando a {url}: {str(e)[:50]}")

# ============ TEST 3: STREAM PÚBLICO ACCESIBLE ============
print("\n3️⃣  Verificando stream de prueba...")

test_stream = "https://ntv1.akamaized.net/hls/live/2014075/NASA-NTV1-HLS/master.m3u8"

try:
    resp = requests.get(test_stream, timeout=10)
    
    if resp.status_code == 200:
        print(f"   ✅ Stream accesible")
        print(f"   📊 Tamaño playlist: {len(resp.text)} bytes")
        
        # Verificar que es un m3u8 válido
        if '#EXTM3U' in resp.text:
            print(f"   ✅ Playlist válido (m3u8)")
        else:
            print(f"   ⚠️  Respuesta no parece ser m3u8")
            print(f"   Primeras 200 chars: {resp.text[:200]}")
    else:
        print(f"   ❌ Stream no accesible: código {resp.status_code}")
        
except Exception as e:
    print(f"   ❌ Error: {e}")

# ============ TEST 4: FFMPEG CON VERBOSE ============
print("\n4️⃣  Probando FFmpeg con output detallado...")

output_file = "./test_diagnostico.mp4"

# Limpiar archivo previo
if os.path.exists(output_file):
    os.remove(output_file)

cmd = [
    'ffmpeg',
    '-v', 'verbose',  # Modo verbose
    '-i', test_stream,
    '-t', '5',  # Solo 5 segundos
    '-c', 'copy',
    '-y',
    output_file
]

print(f"   📝 Comando: {' '.join(cmd)}")
print(f"   ⏳ Ejecutando (esto puede tardar 10-15 segundos)...\n")

try:
    proceso = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Esperar con timeout
    stdout, stderr = proceso.communicate(timeout=30)
    
    print("   📋 OUTPUT DE FFMPEG:")
    print("   " + "-"*66)
    
    # Mostrar últimas 30 líneas del stderr (donde FFmpeg escribe)
    lineas_stderr = stderr.split('\n')
    for linea in lineas_stderr[-30:]:
        if linea.strip():
            print(f"   {linea}")
    
    print("   " + "-"*66)
    
    # Verificar resultado
    if proceso.returncode == 0:
        print(f"\n   ✅ FFmpeg terminó exitosamente (código {proceso.returncode})")
    else:
        print(f"\n   ⚠️  FFmpeg terminó con código {proceso.returncode}")
    
    # Verificar archivo
    time.sleep(2)
    
    if os.path.exists(output_file):
        tamaño = os.path.getsize(output_file)
        print(f"   ✅ Archivo creado: {tamaño} bytes ({tamaño/1024:.1f} KB)")
        
        if tamaño > 10000:  # Al menos 10KB
            print(f"   ✅ Tamaño válido - TEST EXITOSO")
            
            # Limpiar
            os.remove(output_file)
            
            print("\n" + "="*70)
            print("🎉 DIAGNÓSTICO: FFmpeg funciona correctamente")
            print("="*70)
            print("\nEl problema puede estar en:")
            print("  • Timeout muy corto en test_unitarios.py")
            print("  • Stream de prueba temporalmente caído")
            print("\n💡 Solución: Ejecutar test_unitarios.py de nuevo")
            
        else:
            print(f"   ⚠️  Archivo muy pequeño")
            print("\n❌ DIAGNÓSTICO: FFmpeg inicia pero no captura datos")
            print("   Posibles causas:")
            print("   • Stream requiere más tiempo para arrancar")
            print("   • Problemas de red/firewall")
            print("   • Stream temporalmente caído")
    else:
        print(f"   ❌ Archivo NO creado")
        print("\n❌ DIAGNÓSTICO: FFmpeg no puede crear archivo")
        print("   Posibles causas:")
        print("   • Permisos de escritura")
        print("   • Espacio en disco")
        print("   • Error en comando FFmpeg")
        
except subprocess.TimeoutExpired:
    print("\n   ⏱️  TIMEOUT después de 30 segundos")
    proceso.kill()
    proceso.wait()
    
    # Verificar si creó algo
    if os.path.exists(output_file):
        tamaño = os.path.getsize(output_file)
        print(f"   📁 Archivo parcial creado: {tamaño} bytes")
        
        if tamaño > 10000:
            print("\n✅ DIAGNÓSTICO: FFmpeg funciona pero es LENTO")
            print("   Solución: Aumentar timeouts en test_unitarios.py")
            os.remove(output_file)
        else:
            print("\n⚠️  DIAGNÓSTICO: FFmpeg muy lento o stream problemático")
    else:
        print("\n❌ DIAGNÓSTICO: FFmpeg no responde")
        
except Exception as e:
    print(f"\n   ❌ Error inesperado: {e}")

# ============ TEST 5: PERMISOS Y ESPACIO ============
print("\n5️⃣  Verificando sistema de archivos...")

try:
    # Test de escritura
    test_file = "./test_write.tmp"
    with open(test_file, 'w') as f:
        f.write("test")
    
    if os.path.exists(test_file):
        print("   ✅ Permisos de escritura OK")
        os.remove(test_file)
    else:
        print("   ❌ No se pudo crear archivo de prueba")
        
except Exception as e:
    print(f"   ❌ Error de permisos: {e}")

# Espacio en disco
import shutil

try:
    stat = shutil.disk_usage(".")
    libre_gb = stat.free / (1024**3)
    print(f"   📊 Espacio libre: {libre_gb:.2f} GB")
    
    if libre_gb > 1:
        print("   ✅ Espacio suficiente")
    else:
        print("   ⚠️  Poco espacio en disco")
        
except Exception as e:
    print(f"   ⚠️  No se pudo verificar espacio: {e}")

# ============ TEST 6: ALTERNATIVA CON WGET ============
print("\n6️⃣  Test alternativo con wget/curl...")

# Probar descargar directamente
try:
    print("   🌐 Intentando descargar segmento directo...")
    
    # Primero obtener el master playlist
    resp = requests.get(test_stream, timeout=10)
    
    if resp.status_code == 200:
        # Buscar URL de un variant
        for linea in resp.text.split('\n'):
            if linea.strip() and not linea.startswith('#'):
                # Esta es una URL de variant
                if not linea.startswith('http'):
                    # URL relativa
                    from urllib.parse import urljoin
                    variant_url = urljoin(test_stream, linea)
                else:
                    variant_url = linea
                
                print(f"   📡 Variant encontrado: {variant_url[:60]}...")
                
                # Obtener el variant
                resp2 = requests.get(variant_url, timeout=10)
                
                if resp2.status_code == 200:
                    print(f"   ✅ Variant accesible ({len(resp2.text)} bytes)")
                    
                    # Buscar primer segmento .ts
                    for linea2 in resp2.text.split('\n'):
                        if linea2.strip().endswith('.ts'):
                            if not linea2.startswith('http'):
                                from urllib.parse import urljoin
                                segmento_url = urljoin(variant_url, linea2)
                            else:
                                segmento_url = linea2
                            
                            print(f"   🎬 Intentando descargar segmento...")
                            
                            # Descargar segmento
                            resp3 = requests.get(segmento_url, timeout=10)
                            
                            if resp3.status_code == 200:
                                print(f"   ✅ Segmento descargado: {len(resp3.content)} bytes")
                                print("\n✅ La red funciona - El problema es específico de FFmpeg")
                            else:
                                print(f"   ❌ Segmento no disponible: {resp3.status_code}")
                            
                            break  # Solo probar primer segmento
                    
                    break  # Solo probar primer variant
                    
except Exception as e:
    print(f"   ⚠️  Test alternativo falló: {str(e)[:80]}")

# ============ RESUMEN Y RECOMENDACIONES ============
print("\n" + "="*70)
print("📊 RESUMEN DEL DIAGNÓSTICO")
print("="*70)

print("\n💡 PRÓXIMOS PASOS:")
print("\n1. Si FFmpeg funciona correctamente:")
print("   → Editar test_unitarios.py")
print("   → Buscar: timeout=20")
print("   → Cambiar a: timeout=60")
print("   → Buscar: proceso.wait(timeout=20)")
print("   → Cambiar a: proceso.wait(timeout=60)")

print("\n2. O usar un stream alternativo más rápido:")
print("   → Editar test_unitarios.py")
print("   → Cambiar test_stream a uno local o más rápido")

print("\n3. O ejecutar test manual:")
cmd_manual = f'ffmpeg -i "{test_stream}" -t 10 -c copy test_manual.mp4'
print(f"   {cmd_manual}")
print("   Si esto funciona, el sistema está OK")

print("\n" + "="*70 + "\n")