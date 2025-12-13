#!/bin/bash
# Script automatizado de testing para el sistema de grabación
# Ejecuta todos los tests necesarios y genera reporte

set -e  # Salir si hay error

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Banner
echo -e "${BLUE}"
echo "========================================================================"
echo "🧪 SISTEMA DE TESTING AUTOMATIZADO"
echo "========================================================================"
echo -e "${NC}"

# Función para log con timestamp
log() {
    echo -e "[$(date +'%H:%M:%S')] $1"
}

# Función para error
error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

# Función para éxito
success() {
    echo -e "${GREEN}[OK] $1${NC}"
}

# Función para warning
warning() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

# Variables
TEST_DIR="./test_results"
TIMESTAMP=$(date +'%Y%m%d_%H%M%S')
REPORT_FILE="$TEST_DIR/report_$TIMESTAMP.txt"
EXIT_CODE=0

# Crear directorio de resultados
mkdir -p "$TEST_DIR"

# Iniciar reporte
{
    echo "========================================================================"
    echo "REPORTE DE TESTING - $(date)"
    echo "========================================================================"
    echo ""
} > "$REPORT_FILE"

# Función para registrar resultado
registrar() {
    echo "$1" | tee -a "$REPORT_FILE"
}

# ============ FASE 1: VERIFICACIÓN DE ENTORNO ============
log "Fase 1: Verificando entorno..."

# Python
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version)
    success "Python instalado: $PY_VERSION"
    registrar "✅ Python: $PY_VERSION"
else
    error "Python3 no encontrado"
    registrar "❌ Python: NO INSTALADO"
    exit 1
fi

# FFmpeg
if command -v ffmpeg &> /dev/null; then
    FF_VERSION=$(ffmpeg -version | head -n1)
    success "FFmpeg instalado: $FF_VERSION"
    registrar "✅ FFmpeg: $FF_VERSION"
else
    error "FFmpeg no encontrado - Instalar con: sudo apt install ffmpeg"
    registrar "❌ FFmpeg: NO INSTALADO"
    exit 1
fi

# Pip
if command -v pip3 &> /dev/null; then
    success "Pip instalado"
    registrar "✅ Pip: Instalado"
else
    error "Pip no encontrado"
    registrar "❌ Pip: NO INSTALADO"
    exit 1
fi

# ============ FASE 2: VERIFICACIÓN DE DEPENDENCIAS ============
log "Fase 2: Verificando dependencias Python..."

# Lista de paquetes críticos
PACKAGES=("requests" "beautifulsoup4" "selenium" "dateutil")
MISSING=()

for pkg in "${PACKAGES[@]}"; do
    if python3 -c "import ${pkg//-/_}" 2>/dev/null; then
        success "$pkg instalado"
        registrar "  ✅ $pkg"
    else
        warning "$pkg NO instalado"
        registrar "  ❌ $pkg"
        MISSING+=("$pkg")
    fi
done

if [ ${#MISSING[@]} -ne 0 ]; then
    error "Faltan dependencias: ${MISSING[*]}"
    log "Instalando dependencias faltantes..."
    
    pip3 install -q "${MISSING[@]}"
    
    if [ $? -eq 0 ]; then
        success "Dependencias instaladas"
        registrar "✅ Dependencias faltantes instaladas"
    else
        error "Falló instalación de dependencias"
        registrar "❌ Error instalando dependencias"
        exit 1
    fi
fi

# ============ FASE 3: TESTS UNITARIOS ============
log "Fase 3: Ejecutando tests unitarios..."

registrar ""
registrar "TESTS UNITARIOS"
registrar "----------------------------------------"

if [ -f "test_unitarios.py" ]; then
    log "Ejecutando test_unitarios.py..."
    
    # Ejecutar y capturar salida
    if python3 test_unitarios.py > "$TEST_DIR/unitarios_$TIMESTAMP.log" 2>&1; then
        success "Tests unitarios PASARON"
        registrar "✅ Tests unitarios: PASARON"
        
        # Extraer resumen
        SUMMARY=$(grep "Resultado:" "$TEST_DIR/unitarios_$TIMESTAMP.log" || echo "N/A")
        registrar "   $SUMMARY"
    else
        error "Tests unitarios FALLARON"
        registrar "❌ Tests unitarios: FALLARON"
        registrar "   Ver: $TEST_DIR/unitarios_$TIMESTAMP.log"
        EXIT_CODE=1
    fi
else
    warning "test_unitarios.py no encontrado"
    registrar "⚠️  test_unitarios.py: NO ENCONTRADO"
fi

# ============ FASE 4: TEST DE INTEGRACIÓN (OPCIONAL) ============
log "Fase 4: Test de integración (opcional)..."

registrar ""
registrar "TEST DE INTEGRACIÓN"
registrar "----------------------------------------"

echo -e "${YELLOW}"
echo "¿Ejecutar test completo de simulación? (tarda 5-10 minutos)"
echo "Presiona Enter para SÍ, o 'n' para NO"
echo -e "${NC}"

read -r -t 10 respuesta || respuesta=""

if [[ ! "$respuesta" =~ ^[Nn]$ ]]; then
    if [ -f "test_simulador_partido.py" ]; then
        log "Ejecutando test_simulador_partido.py..."
        
        if timeout 600 python3 test_simulador_partido.py > "$TEST_DIR/simulador_$TIMESTAMP.log" 2>&1; then
            success "Test de simulación PASÓ"
            registrar "✅ Test de simulación: PASÓ"
            
            # Verificar video generado
            if [ -f "./test_partido/TEST_PARTIDO_COMPLETO.mp4" ]; then
                VIDEO_SIZE=$(du -h "./test_partido/TEST_PARTIDO_COMPLETO.mp4" | cut -f1)
                success "Video generado: $VIDEO_SIZE"
                registrar "   📹 Video: $VIDEO_SIZE"
            fi
        else
            error "Test de simulación FALLÓ"
            registrar "❌ Test de simulación: FALLÓ"
            registrar "   Ver: $TEST_DIR/simulador_$TIMESTAMP.log"
            EXIT_CODE=1
        fi
    else
        warning "test_simulador_partido.py no encontrado"
        registrar "⚠️  test_simulador_partido.py: NO ENCONTRADO"
    fi
else
    log "Test de simulación omitido"
    registrar "⚠️  Test de simulación: OMITIDO"
fi

# ============ FASE 5: VERIFICACIÓN DE ARCHIVOS DEL PROYECTO ============
log "Fase 5: Verificando archivos del proyecto..."

registrar ""
registrar "ARCHIVOS DEL PROYECTO"
registrar "----------------------------------------"

# Archivos críticos
FILES=(
    "sistema_maestro.py:Sistema principal"
    "sync_manager.py:Gestor de sincronización"
    "smart_selector.py:Selector de streams"
    "promiedos_client.py:Cliente de Promiedos"
    "config_tv.py:Configuración de canales"
    "uploader.py:Subida de videos"
)

for file_info in "${FILES[@]}"; do
    IFS=':' read -r file desc <<< "$file_info"
    
    if [ -f "$file" ]; then
        # Verificar sintaxis Python
        if python3 -m py_compile "$file" 2>/dev/null; then
            success "$desc ($file) - OK"
            registrar "  ✅ $file"
        else
            error "$desc ($file) - ERROR DE SINTAXIS"
            registrar "  ❌ $file (error de sintaxis)"
            EXIT_CODE=1
        fi
    else
        warning "$desc ($file) - NO ENCONTRADO"
        registrar "  ⚠️  $file (no encontrado)"
    fi
done

# ============ FASE 6: VERIFICACIÓN DE DIRECTORIOS ============
log "Fase 6: Verificando estructura de directorios..."

registrar ""
registrar "ESTRUCTURA DE DIRECTORIOS"
registrar "----------------------------------------"

DIRS=("./partidos_grabados" "./logs" "./temp_segments")

for dir in "${DIRS[@]}"; do
    if [ -d "$dir" ]; then
        success "$dir existe"
        registrar "  ✅ $dir"
    else
        log "Creando $dir..."
        mkdir -p "$dir"
        if [ -d "$dir" ]; then
            success "$dir creado"
            registrar "  ✅ $dir (creado)"
        else
            error "No se pudo crear $dir"
            registrar "  ❌ $dir (error)"
            EXIT_CODE=1
        fi
    fi
done

# ============ RESUMEN FINAL ============
registrar ""
registrar "========================================================================"
registrar "RESUMEN FINAL"
registrar "========================================================================"

if [ $EXIT_CODE -eq 0 ]; then
    registrar "✅ TODOS LOS TESTS PASARON"
    registrar ""
    registrar "🎉 Sistema listo para usar en producción"
    registrar ""
    registrar "Próximos pasos:"
    registrar "  1. Configurar partido en sistema_maestro_v5.py"
    registrar "  2. Ajustar fuentes en config_tv.py si es necesario"
    registrar "  3. Ejecutar: python sistema_maestro_v5.py"
else
    registrar "❌ ALGUNOS TESTS FALLARON"
    registrar ""
    registrar "Revisar logs en:"
    registrar "  - $TEST_DIR/unitarios_$TIMESTAMP.log"
    registrar "  - $TEST_DIR/simulador_$TIMESTAMP.log"
    registrar ""
    registrar "Resolver problemas antes de usar en producción"
fi

registrar ""
registrar "Reporte guardado en: $REPORT_FILE"
registrar "Timestamp: $(date)"

# Mostrar reporte en pantalla
echo ""
echo "========================================================================"
echo "📊 REPORTE COMPLETO"
echo "========================================================================"
cat "$REPORT_FILE"

# Resumen visual final
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}"
    echo "╔════════════════════════════════════════╗"
    echo "║  ✅ SISTEMA LISTO PARA PRODUCCIÓN    ║"
    echo "╚════════════════════════════════════════╝"
    echo -e "${NC}"
else
    echo -e "${RED}"
    echo "╔════════════════════════════════════════╗"
    echo "║  ❌ PROBLEMAS DETECTADOS              ║"
    echo "╚════════════════════════════════════════╝"
    echo -e "${NC}"
fi

# Sugerencias finales
if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "💡 Sugerencias:"
    echo "  • Ejecutar este test antes de cada partido importante"
    echo "  • Mantener logs en $TEST_DIR para histórico"
    echo "  • Actualizar config_tv.py con fuentes nuevas regularmente"
fi

exit $EXIT_CODE