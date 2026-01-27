#!/usr/bin/env python3
"""
Script para agregar automáticamente cálculo local de extremos a app.py
"""
import sys
import os
from datetime import datetime

def modificar_app():
    """Modifica app.py para agregar cálculo local de extremos"""
    
    # Verificar que estamos en el directorio correcto
    if not os.path.exists('app.py'):
        print("❌ Error: app.py no encontrado")
        print("   Ejecuta este script desde el directorio meteolabx_fixed")
        return False
    
    # Leer app.py
    with open('app.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Hacer backup
    backup_name = f'app.py.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    with open(backup_name, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f"📦 Backup creado: {backup_name}")
    
    # Variables de control
    import_agregado = False
    calculo_agregado = False
    lineas_nuevas = []
    
    for i, line in enumerate(lines):
        # 1. Agregar import después de los otros imports de services
        if 'from services import' in line and not import_agregado:
            lineas_nuevas.append(line)
            # Buscar el final del import (puede ser multilínea)
            if ')' in line:
                lineas_nuevas.append("from extremes_local import update_daily_extremes, get_daily_extremes\n")
                import_agregado = True
                print("✅ Import agregado")
        
        # 2. Agregar cálculo después de fetch_wu_current_session_cached
        elif 'fetch_wu_current_session_cached' in line and not calculo_agregado:
            lineas_nuevas.append(line)
            # Agregar después de esta línea
            lineas_nuevas.append("\n")
            lineas_nuevas.append("    # Calcular extremos diarios localmente\n")
            lineas_nuevas.append("    update_daily_extremes(base[\"Tc\"], base[\"RH\"], base[\"gust\"], base[\"epoch\"])\n")
            lineas_nuevas.append("    local_extremes = get_daily_extremes()\n")
            lineas_nuevas.append("    base.update(local_extremes)\n")
            lineas_nuevas.append("\n")
            calculo_agregado = True
            print("✅ Cálculo de extremos agregado")
        else:
            lineas_nuevas.append(line)
    
    # Escribir archivo modificado
    with open('app.py', 'w', encoding='utf-8') as f:
        f.writelines(lineas_nuevas)
    
    if import_agregado and calculo_agregado:
        print("\n✅ app.py modificado correctamente")
        return True
    else:
        print("\n⚠️  Advertencia: No se encontraron todas las líneas objetivo")
        print(f"   Import agregado: {import_agregado}")
        print(f"   Cálculo agregado: {calculo_agregado}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("  AGREGAR CÁLCULO LOCAL DE EXTREMOS")
    print("=" * 50)
    print()
    
    if modificar_app():
        print()
        print("=" * 50)
        print("  ✅ COMPLETADO")
        print("=" * 50)
        print()
        print("Ahora:")
        print("  1. Para streamlit (Ctrl+C)")
        print("  2. Ejecuta: streamlit run app.py")
        print("  3. Los extremos deberían aparecer")
        print()
    else:
        print()
        print("=" * 50)
        print("  ⚠️  REVISAR MANUALMENTE")
        print("=" * 50)
        print()
        print("El script no pudo modificar automáticamente.")
        print("Edita app.py manualmente siguiendo LEEME_URGENTE.md")
        print()
        sys.exit(1)
