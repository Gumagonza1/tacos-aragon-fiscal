"""
main.py - 🌮 BOT FISCAL TACOS ARAGÓN 🌮

Orquestador principal del bot contador automático.
Descarga CFDIs del SAT, parsea los XMLs y genera Excel.

Uso:
    python main.py                  # Menú interactivo
    python main.py --mes 2025-01    # Descargar mes específico
    python main.py --parsear        # Solo parsear XMLs ya descargados
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.sat_client import SatClient
from src.cfdi_parser import parsear_carpeta
from src.excel_export import generar_excel
from src.metadata_parser import parsear_carpeta_metadata, mostrar_resumen_fiscal
from src.analisis_fiscal import (
    analizar_con_ia, 
    imprimir_analisis, 
    aplicar_clasificacion_ia, 
    guardar_analisis_json,
    generar_archivos,
    enviar_correo_reporte
)


def cargar_configuracion() -> dict:
    """Carga y valida las variables de entorno."""
    load_dotenv()

    config = {
        "rfc": os.getenv("SAT_RFC", "").strip().upper(),
        "key_password": os.getenv("SAT_KEY_PASSWORD", ""),
        "cer_path": os.getenv("SAT_CER_PATH", "keys/firma.cer"),
        "key_path": os.getenv("SAT_KEY_PATH", "keys/firma.key"),
        "email_user": os.getenv("EMAIL_USER", ""),
        "email_pass": os.getenv("EMAIL_PASS", ""),
        "imap_server": os.getenv("IMAP_SERVER", "imap.gmail.com"),
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    }

    # Validaciones críticas
    errores = []
    if not config["rfc"] or config["rfc"] == "TU_RFC_AQUI":
        errores.append("SAT_RFC no configurado en .env")
    if not config["key_password"] or "contraseña" in config["key_password"]:
        errores.append("SAT_KEY_PASSWORD no configurado en .env")
    if not Path(config["cer_path"]).exists():
        errores.append(f"Archivo .cer no encontrado: {config['cer_path']}")
    if not Path(config["key_path"]).exists():
        errores.append(f"Archivo .key no encontrado: {config['key_path']}")

    if errores:
        print("\n❌ ERRORES DE CONFIGURACIÓN:")
        for e in errores:
            print(f"   • {e}")
        print("\n💡 Configura el archivo .env y copia tu e.firma a /keys/")
        sys.exit(1)

    return config


def _flujo_descarga_retenciones(config: dict):
    """Descarga CFDIs de Retenciones e Información de Pagos del SAT."""
    anio, mes = _pedir_mes()

    print(f"\n  🧾 Descargando RETENCIONES {anio}-{mes:02d}...")
    print("  (Retenciones de Uber, Didi, Rappi, plataformas digitales)")

    sat = SatClient(
        rfc=config["rfc"],
        cer_path=config["cer_path"],
        key_path=config["key_path"],
        key_password=config["key_password"],
    )

    # Retenciones emitidas (las que tú emites, poco común para conductores)
    print("\n  ¿Qué retenciones descargar?")
    print("    1. Recibidas (las que te hacen a ti — Uber, Didi, Rappi) ← recomendado")
    print("    2. Emitidas (las que tú haces a terceros)")
    print("    3. Ambas")
    op = input("  Opción [1]: ").strip() or "1"

    if op in ("1", "3"):
        carpeta = sat.descargar_mes(anio, mes, tipo="recibidos", tipo_solicitud="Retenciones")
        if carpeta:
            print("\n  ¿Generar Excel con las retenciones? (s/n)")
            if input("  > ").strip().lower() in ("s", "si", "sí", "y", "yes", ""):
                from src.cfdi_parser import parsear_carpeta
                datos = parsear_carpeta(str(carpeta))
                if datos:
                    nombre = f"Retenciones_{config['rfc']}_{anio}-{mes:02d}_recibidas.xlsx"
                    from src.excel_export import generar_excel
                    generar_excel(datos, config["rfc"], nombre=nombre)
                else:
                    print("  ⚠️  No se encontraron retenciones en la carpeta descargada.")

    if op in ("2", "3"):
        carpeta = sat.descargar_mes(anio, mes, tipo="emitidos", tipo_solicitud="Retenciones")
        if carpeta:
            print("\n  ¿Generar Excel con las retenciones emitidas? (s/n)")
            if input("  > ").strip().lower() in ("s", "si", "sí", "y", "yes", ""):
                from src.cfdi_parser import parsear_carpeta
                datos = parsear_carpeta(str(carpeta))
                if datos:
                    nombre = f"Retenciones_{config['rfc']}_{anio}-{mes:02d}_emitidas.xlsx"
                    from src.excel_export import generar_excel
                    generar_excel(datos, config["rfc"], nombre=nombre)



def _flujo_analisis_ia(config: dict):
    """Análisis fiscal con IA: clasifica CFDIs, concilia facturas vs certificados."""
    from calendar import monthrange

    print("\n  🤖 ANÁLISIS FISCAL CON IA (Gemini)")
    print("  ─────────────────────────────────────────")
    print("  NOTA: El certificado de retención se busca en el mes SIGUIENTE")
    print("        al período analizado (así lo expide el SAT).")
    print()

    # Pedir el mes a analizar
    print("  ¿Qué mes deseas analizar?")
    anio, mes = _pedir_mes()

    # Calcular mes siguiente (donde vive el certificado de retención)
    if mes == 12:
        anio_sig, mes_sig = anio + 0, 0
    else:
        anio_sig, mes_sig = anio, mes + 0

    # Raíz de descargas
    base = Path("downloads/sat")
    base2= Path("downloads/email")

    # ── Carpetas del mes a analizar ──────────────────────────────────────────
    carpeta_rec  = base / f"{anio}-{mes:02d}"  / "recibidos" / "cfdi"
    carpeta_emi  = base / f"{anio}-{mes:02d}"  / "emitidos"  / "cfdi"

    # ── Carpeta del mes SIGUIENTE (certificados de retención) ────────────────
    carpeta_sig  = base2 / f"{anio_sig}-{mes_sig:02d}"

    print(f"\n  📂 Carpetas del mes {anio}-{mes:02d} (facturas):")
    print(f"     Recibidos: {carpeta_rec}  {'✅' if carpeta_rec.exists() else '⚠️ no existe'}")
    print(f"     Emitidos:  {carpeta_emi}  {'✅' if carpeta_emi.exists() else '⚠️ no existe'}")
    print(f"\n  📂 Carpeta del mes {anio_sig}-{mes_sig:02d} (certificados de retención):")
    print(f"     Recibidos: {carpeta_sig}  {'✅' if carpeta_sig.exists() else '⚠️ no existe (descárgalo primero)'}")

    # Preguntar si quiere usar carpetas diferentes
    print("\n  ¿Usar estas carpetas? (s/n) [s]: ", end="")
    if input().strip().lower() in ("n", "no"):
        print("  📂 Carpeta mes a analizar (facturas): ", end="")
        carpeta_rec = Path(input().strip() or str(carpeta_rec))
        print("  📂 Carpeta mes siguiente (certificados): ", end="")
        carpeta_sig = Path(input().strip() or str(carpeta_sig))

    # ── Parsear XMLs del mes a analizar ─────────────────────────────────────
    print(f"\n  🔍 Parseando XMLs del mes {anio}-{mes:02d}...")
    datos = []
    for carpeta in [carpeta_rec, carpeta_emi]:
        if carpeta.exists():
            datos += parsear_carpeta(str(carpeta))

    if not datos:
        print("  ⚠️  No se encontraron CFDIs del mes a analizar.")
        print(f"  Descarga primero los CFDIs de {anio}-{mes:02d} (opciones 1, 2 o 3 del menú).")
        return

    # ── Parsear XMLs del mes SIGUIENTE (certificados de retención) ───────────
    datos_sig = []
    if carpeta_sig.exists():
        print(f"  🔍 Parseando certificados de retención de {anio_sig}-{mes_sig:02d}...")
        datos_sig = parsear_carpeta(str(carpeta_sig))
        # Filtrar solo los RET (certificados de retención)
        certs = [d for d in datos_sig if d.get("tipo_comprobante") == "RET"]
        print(f"     {len(certs)} certificado(s) de retención encontrado(s)")
        if not certs:
            print(f"  ⚠️  No hay certificados RET en {carpeta_sig}")
            print(f"     (Descarga los recibidos de {anio_sig}-{mes_sig:02d} para incluirlos)")
    else:
        print(f"  ⚠️  Carpeta {carpeta_sig} no existe.")
        print(f"     Sin certificados de retención — la conciliación será parcial.")

    print(f"\n  📊 Facturas del mes:          {len(datos)} registros")
    print(f"  📊 Certificados mes siguiente: {len([d for d in datos_sig if d.get('tipo_comprobante')=='RET'])} registros")

    # Verificar API key
    api_key = config.get("gemini_api_key", "")
    if not api_key:
        print("\n  ⚠️  Falta GEMINI_API_KEY en el .env")
        print("  Obtén tu clave en: https://aistudio.google.com")
        print("  Agrega al .env:  GEMINI_API_KEY=AIza...")
        return

    # ── Llamar al análisis IA ─────────────────────────────────────────────────
    analisis = analizar_con_ia(
        datos, config["rfc"], anio, mes,
        api_key=api_key,
        datos_mes_siguiente=datos_sig,
    )

    if analisis:
        imprimir_analisis(analisis, config["rfc"])
        
        # Guarda el JSON crudo por si lo necesitas después
        try:
            guardar_analisis_json(analisis, config["rfc"], anio, mes)
        except Exception as e:
            print(f"  ⚠️ No se pudo guardar el JSON crudo: {e}")

        print("\n  ¿Generar Reportes (Excel + PDF) y Enviar por Correo? (s/n)")
        if input("  > ").strip().lower() in ("s", "si", "sí", "y", "yes", ""):
            todos = datos + datos_sig
            datos_clasificados = aplicar_clasificacion_ia(
                todos, analisis.get("clasificacion", []), config["rfc"]
            )
            
            periodo = f"{anio}-{mes:02d}"
            
            # 1. Generar los archivos (PDF y XLSX)
            xlsx = generar_archivos(analisis, datos_clasificados, config["rfc"], periodo)
            
            # 2. Enviar por correo si está configurado en tu .env
            correo_destino = config.get("email_user")
            if correo_destino and config.get("email_pass"):
                print(f"\n  ¿Enviar reportes a {correo_destino}? (s/n)")
                if input("  > ").strip().lower() in ("s", "si", "sí", "y", "yes", ""):
                    enviar_correo_reporte(correo_destino, xlsx, periodo)
            else:
                print("\n  ⚠️ Correo no configurado en .env. Se omitirá el envío.")
    else:
        print("  ❌ No se pudo obtener el análisis. Revisa tu API key de Gemini.")



def menu_interactivo(config: dict):
    """Menú principal del bot."""
    print("""
╔══════════════════════════════════════════╗
║    🌮 BOT FISCAL TACOS ARAGÓN 🌮       ║
║    Sistema Automático de Contabilidad    ║
╚══════════════════════════════════════════╝
    """)
    print(f"   RFC: {config['rfc']}")
    print(f"   CER: {config['cer_path']}")
    print(f"   KEY: {config['key_path']}")
    print()

    while True:
        print("─" * 45)
        print("  ── DESCARGAS ─────────────────────────────")
        print("  1. 📥 Descargar CFDIs RECIBIDOS (incluye retenciones Uber/Didi)")
        print("  2. 📤 Descargar CFDIs EMITIDOS")
        print("  3. 📥📤 Descargar AMBOS")
        print("  ── HERRAMIENTAS ──────────────────────────")
        print("  4. 📄 Parsear XMLs ya descargados → Excel")
        print("  5. 🔐 Probar autenticación SAT")
        print("  6. 📧 Buscar retenciones en correo")
        print("  7. 🤖 Análisis fiscal con IA (Gemini)")
        print("  0. 🚪 Salir")
        print("─" * 45)

        opcion = input("\n  Elige opción: ").strip()

        if opcion == "0":
            print("\n👋 ¡Hasta luego!")
            break
        elif opcion == "1":
            _flujo_descarga(config, tipo="recibidos")
        elif opcion == "2":
            _flujo_descarga(config, tipo="emitidos")
        elif opcion == "3":
            _flujo_descarga(config, tipo="recibidos")
            _flujo_descarga(config, tipo="emitidos")
        elif opcion == "4":
            _flujo_parsear(config)
        elif opcion == "5":
            _test_auth(config)
        elif opcion == "6":
            _flujo_email(config)
        elif opcion == "7":
            _flujo_analisis_ia(config)
        else:
            print("  ⚠️ Opción no válida")


def _pedir_mes() -> tuple[int, int]:
    """Pide año y mes al usuario."""
    ahora = datetime.now()
    default = f"{ahora.year}-{ahora.month:02d}"
    entrada = input(f"  📅 Mes (YYYY-MM) [{default}]: ").strip()

    if not entrada:
        return ahora.year, ahora.month

    try:
        partes = entrada.split("-")
        return int(partes[0]), int(partes[1])
    except (ValueError, IndexError):
        print("  ⚠️ Formato inválido, usando mes actual")
        return ahora.year, ahora.month


def _pedir_fecha_fin(anio: int, mes: int) -> datetime:
    """
    Pregunta si se quiere usar una fecha final personalizada.
    Útil para meses en curso que aún no han terminado.
    """
    from calendar import monthrange
    ahora = datetime.now()
    mes_en_curso = (anio == ahora.year and mes == ahora.month)

    # Fecha fin por defecto = último segundo del mes
    if mes == 12:
        fecha_fin_default = datetime(anio + 1, 1, 1, 0, 0, 0) - timedelta(seconds=1)
    else:
        fecha_fin_default = datetime(anio, mes + 1, 1, 0, 0, 0) - timedelta(seconds=1)

    if mes_en_curso:
        # Sugerir ayer como fecha fin segura para el mes en curso
        ayer = ahora - timedelta(days=1)
        sugerida = ayer.replace(hour=23, minute=59, second=59)
        print(f"\n  ⚠️  {anio}-{mes:02d} es el mes en curso.")
        print(f"  Fecha fin completa:  {fecha_fin_default.strftime('%Y-%m-%d')} (puede dar error 301)")
        print(f"  Fecha fin sugerida:  {sugerida.strftime('%Y-%m-%d')} (hasta ayer)")
        print(f"  Fecha fin manual:    escribe YYYY-MM-DD")
        entrada = input(f"  📅 Fecha fin [{sugerida.strftime('%Y-%m-%d')}]: ").strip()

        if not entrada:
            return sugerida
        try:
            partes = entrada.split("-")
            return datetime(int(partes[0]), int(partes[1]), int(partes[2]), 23, 59, 59)
        except (ValueError, IndexError):
            print("  ⚠️ Formato inválido, usando fecha sugerida")
            return sugerida
    else:
        # Mes pasado — pregunta rápida por si quiere personalizar
        entrada = input(f"  📅 Fecha fin [{fecha_fin_default.strftime('%Y-%m-%d')}] (Enter = fin de mes): ").strip()
        if not entrada:
            return fecha_fin_default
        try:
            partes = entrada.split("-")
            return datetime(int(partes[0]), int(partes[1]), int(partes[2]), 23, 59, 59)
        except (ValueError, IndexError):
            print("  ⚠️ Formato inválido, usando fin de mes")
            return fecha_fin_default


def _flujo_descarga(config: dict, tipo: str):
    """Flujo completo de descarga de un mes."""
    anio, mes = _pedir_mes()
    fecha_fin = _pedir_fecha_fin(anio, mes)

    print(f"\n🚀 Descargando {tipo} de {anio}-{mes:02d} hasta {fecha_fin.strftime('%Y-%m-%d')}...")

    # Preguntar tipo de solicitud
    print("  ¿Qué descargar?")
    print("    1. XMLs completos (CFDI)")
    print("    2. Solo metadata (resumen CSV)")
    tipo_sol_input = input("  Opción [1]: ").strip()
    tipo_solicitud = "Metadata" if tipo_sol_input == "2" else "CFDI"

    sat = SatClient(
        rfc=config["rfc"],
        cer_path=config["cer_path"],
        key_path=config["key_path"],
        key_password=config["key_password"],
    )

    carpeta = sat.descargar_mes(anio, mes, tipo=tipo,
                                tipo_solicitud=tipo_solicitud,
                                fecha_fin_override=fecha_fin)

    if carpeta and tipo_solicitud == "CFDI":
        print("\n  ¿Generar Excel ahora? (s/n)")
        if input("  > ").strip().lower() in ("s", "si", "sí", "y", "yes", ""):
            datos = parsear_carpeta(str(carpeta))
            if datos:
                nombre = f"CFDIs_{config['rfc']}_{anio}-{mes:02d}_{tipo}.xlsx"
                generar_excel(datos, config["rfc"], nombre=nombre)


def _flujo_parsear(config: dict):
    """Parsea XMLs y Metadata del SAT de forma masiva."""
    default_dir = "downloads"
    carpeta = input(f"  📂 Carpeta raíz de búsqueda [{default_dir}]: ").strip()
    if not carpeta:
        carpeta = default_dir

    print(f"\n  🔍 Buscando XMLs en {carpeta}...")
    datos_xml = parsear_carpeta(carpeta)

    print(f"\n  🔍 Buscando Metadata ZIP en {carpeta}...")
    datos_meta = parsear_carpeta_metadata(carpeta, config["rfc"])

    datos = datos_xml + datos_meta

    if datos:
        print(f"\n  📊 Total: {len(datos)} registros  (XMLs: {len(datos_xml)} | Metadata: {len(datos_meta)})")
        if datos_meta:
            mostrar_resumen_fiscal(datos, config["rfc"])
        generar_excel(datos, config["rfc"])
    else:
        print("  ⚠️ No se encontraron XMLs ni Metadata en downloads")


def _test_auth(config: dict):
    """Prueba la autenticación con el SAT."""
    print("\n🔐 Probando conexión con el SAT...")
    sat = SatClient(
        rfc=config["rfc"],
        cer_path=config["cer_path"],
        key_path=config["key_path"],
        key_password=config["key_password"],
    )
    if sat.autenticar():
        print("\n🎉 ¡Autenticación exitosa! Tu e.firma funciona correctamente.")
    else:
        print("\n❌ Falló la autenticación. Revisa la configuración.")


def _flujo_email(config: dict):
    """Busca retenciones en el correo."""
    if not config["email_user"] or "tucorreo" in config["email_user"]:
        print("  ⚠️ Configura EMAIL_USER y EMAIL_PASS en .env primero")
        return

    from src.email_retention import EmailRetentionHunter
    hunter = EmailRetentionHunter(
        user=config["email_user"],
        password=config["email_pass"],
        server=config["imap_server"],
    )
    hunter.buscar_retenciones()


def main():
    """Punto de entrada principal."""
    parser = argparse.ArgumentParser(description="🌮 Bot Fiscal Tacos Aragón")
    parser.add_argument("--mes", help="Mes a descargar (YYYY-MM)", default=None)
    parser.add_argument("--tipo", choices=["recibidos", "emitidos", "ambos"],
                        default="recibidos", help="Tipo de CFDIs")
    parser.add_argument("--parsear", help="Carpeta de XMLs a parsear", default=None)
    parser.add_argument("--metadata", action="store_true",
                        help="Descargar metadata en vez de XMLs")
    parser.add_argument("--test-auth", action="store_true",
                        help="Solo probar autenticación")

    args = parser.parse_args()
    config = cargar_configuracion()

    # Modo CLI directo
    if args.test_auth:
        _test_auth(config)
        return

    if args.parsear:
        datos = parsear_carpeta(args.parsear)
        if datos:
            generar_excel(datos, config["rfc"])
        return

    if args.mes:
        try:
            partes = args.mes.split("-")
            anio, mes = int(partes[0]), int(partes[1])
        except (ValueError, IndexError):
            print(f"❌ Formato de mes inválido: {args.mes} (usa YYYY-MM)")
            sys.exit(1)

        tipo_solicitud = "Metadata" if args.metadata else "CFDI"
        sat = SatClient(
            rfc=config["rfc"],
            cer_path=config["cer_path"],
            key_path=config["key_path"],
            key_password=config["key_password"],
        )

        tipos = ["recibidos", "emitidos"] if args.tipo == "ambos" else [args.tipo]
        for t in tipos:
            carpeta = sat.descargar_mes(anio, mes, tipo=t, tipo_solicitud=tipo_solicitud)
            if carpeta and tipo_solicitud == "CFDI":
                datos = parsear_carpeta(str(carpeta))
                if datos:
                    nombre = f"CFDIs_{config['rfc']}_{anio}-{mes:02d}_{t}.xlsx"
                    generar_excel(datos, config["rfc"], nombre=nombre)
        return

    # Modo interactivo (default)
    menu_interactivo(config)


if __name__ == "__main__":
    main()