# -*- coding: utf-8 -*-
"""
metadata_parser.py - Parsea los archivos de Metadata del SAT y calcula impuestos.

Formato real del SAT (separador ~):
  Uuid~RfcEmisor~NombreEmisor~RfcReceptor~NombreReceptor~PacCertifico~
  FechaEmision~FechaCertificacionSat~Monto~EfectoComprobante~Estatus~FechaCancelacion

Tasas para Plataformas Tecnologicas (Tacos Aragon):
  - IVA emitidos:  16%
  - IVA recibidos:  8%
  - ISR plataformas: 2.5%
"""

import zipfile
import csv
import io
from pathlib import Path


TASA_IVA_EMITIDO    = 0.16
TASA_IVA_RECIBIDO   = 0.08
TASA_ISR_PLATAFORMA = 0.021  # 2.1% — restaurantes/preparación de alimentos (Art. 113-A frac. II LISR)


def _safe_float(valor, default=0.0):
    try:
        return float(str(valor).strip().replace(',', '')) if valor else default
    except (ValueError, TypeError):
        return default


def _calcular_impuestos(monto: float, es_emitido: bool) -> dict:
    """Calcula impuestos a partir del Monto total (que incluye IVA)."""
    tasa_iva = TASA_IVA_EMITIDO if es_emitido else TASA_IVA_RECIBIDO
    subtotal = monto / (1 + tasa_iva) if monto else 0.0
    return {
        'subtotal':       round(subtotal, 2),
        'iva_trasladado': round(subtotal * tasa_iva, 2),
        'isr_retenido':   round(subtotal * TASA_ISR_PLATAFORMA, 2),
        'iva_retenido':   round(subtotal * TASA_IVA_RECIBIDO, 2) if not es_emitido else 0.0,
    }


def _parsear_fila(row: dict, mi_rfc: str, nombre_archivo: str) -> dict | None:
    """
    Convierte una fila del TXT de metadata SAT al formato estandar del bot.

    Columnas reales del SAT:
        Uuid, RfcEmisor, NombreEmisor, RfcReceptor, NombreReceptor,
        PacCertifico, FechaEmision, FechaCertificacionSat,
        Monto, EfectoComprobante, Estatus, FechaCancelacion
    """
    uuid = (row.get('Uuid') or row.get('uuid') or '').strip()
    if not uuid:
        return None

    # Estatus: 1=Vigente, 0=Cancelado
    estatus = str(row.get('Estatus') or row.get('EstadoComprobante') or '1').strip()
    if estatus == '0':
        return None

    rfc_emisor  = (row.get('RfcEmisor')  or '').strip().upper()
    rfc_receptor = (row.get('RfcReceptor') or '').strip().upper()
    efecto       = (row.get('EfectoComprobante') or 'I').strip()

    # El SAT llama "Monto" al total de la factura
    monto      = _safe_float(row.get('Monto') or row.get('Total') or 0)
    es_emitido = (rfc_emisor == mi_rfc.upper())
    impuestos  = _calcular_impuestos(monto, es_emitido)

    return {
        'fecha':            (row.get('FechaEmision') or '').strip(),
        'tipo_comprobante': efecto,
        'es_pago':          efecto == 'P',
        'emisor_rfc':       rfc_emisor,
        'emisor_nombre':    (row.get('NombreEmisor')  or 'Desconocido').strip(),
        'receptor_rfc':     rfc_receptor,
        'receptor_nombre':  (row.get('NombreReceptor') or 'Desconocido').strip(),
        'subtotal':         impuestos['subtotal'],
        'total':            monto,
        'iva_trasladado':   impuestos['iva_trasladado'],
        'isr_retenido':     impuestos['isr_retenido'],
        'iva_retenido':     impuestos['iva_retenido'],
        'uuid':             uuid,
        'archivo':          nombre_archivo,
        'fuente':           'metadata',
        'tasa_iva_aplicada': TASA_IVA_EMITIDO if es_emitido else TASA_IVA_RECIBIDO,
    }


def _parsear_txt(contenido: str, mi_rfc: str, nombre_archivo: str) -> list:
    """Parsea el texto del TXT/CSV de metadata del SAT."""
    resultados = []

    # Quitar BOM si existe
    contenido = contenido.lstrip('\ufeff')

    # Detectar separador: el SAT usa ~ por defecto
    primera = contenido.split('\n')[0]
    sep = '~' if '~' in primera else ('|' if '|' in primera else ',')

    reader = csv.DictReader(io.StringIO(contenido), delimiter=sep)
    for row in reader:
        # FIX CRITICO: csv.DictReader pone None como clave cuando hay tildes
        # finales en las filas (campo extra vacio). Filtrar esas claves None.
        row_limpio = {
            k.strip(): (v.strip() if v else v)
            for k, v in row.items()
            if k is not None          # <-- aqui esta el fix
        }
        registro = _parsear_fila(row_limpio, mi_rfc, nombre_archivo)
        if registro:
            resultados.append(registro)

    return resultados


def parsear_metadata_zip(ruta_zip: str, mi_rfc: str) -> list:
    """Lee un ZIP de metadata del SAT y devuelve lista de registros."""
    resultados = []
    try:
        with zipfile.ZipFile(ruta_zip, 'r') as z:
            for nombre in z.namelist():
                if nombre.lower().endswith(('.txt', '.csv', '.tsv')):
                    with z.open(nombre) as f:
                        contenido = f.read().decode('utf-8-sig', errors='replace')
                        regs = _parsear_txt(contenido, mi_rfc, f"{Path(ruta_zip).name}/{nombre}")
                        resultados.extend(regs)
    except Exception as e:
        print(f"  Error leyendo metadata ZIP {Path(ruta_zip).name}: {e}")

    return resultados


def parsear_carpeta_metadata(carpeta_path: str, mi_rfc: str) -> list:
    """
    Busca ZIPs de metadata recursivamente. Distingue metadata (TXT) de CFDI (XML).
    """
    resultados = []
    path_raiz = Path(carpeta_path)
    if not path_raiz.exists():
        print(f"  Carpeta no encontrada: {carpeta_path}")
        return []

    zips_encontrados = 0
    for elemento in path_raiz.rglob('*.zip'):
        try:
            with zipfile.ZipFile(elemento, 'r') as z:
                nombres = z.namelist()
                tiene_txt = any(n.lower().endswith(('.txt', '.csv', '.tsv')) for n in nombres)
                tiene_xml = any(n.lower().endswith('.xml') for n in nombres)

                if tiene_txt and not tiene_xml:
                    zips_encontrados += 1
                    regs = parsear_metadata_zip(str(elemento), mi_rfc)
                    resultados.extend(regs)
                    if regs:
                        print(f"  Metadata: {elemento.name} -> {len(regs)} registros")
        except Exception as e:
            print(f"  No se pudo leer {elemento.name}: {e}")

    if zips_encontrados == 0:
        print(f"  No se encontraron ZIPs de metadata en {carpeta_path}")
    else:
        print(f"\n  {len(resultados)} registros de metadata ({zips_encontrados} ZIPs)")

    return resultados


def mostrar_resumen_fiscal(datos: list, mi_rfc: str):
    """Imprime resumen de impuestos calculados en consola."""
    mi_rfc = mi_rfc.upper()
    emitidos   = [d for d in datos if d.get('emisor_rfc') == mi_rfc]
    recibidos  = [d for d in datos if d.get('emisor_rfc') != mi_rfc and d.get('tipo_comprobante') != 'RET']

    total_ing        = sum(d['subtotal'] for d in emitidos)
    total_iva_cob    = sum(d['iva_trasladado'] for d in emitidos)
    total_gasto      = sum(d['subtotal'] for d in recibidos)
    total_iva_acred  = sum(d['iva_trasladado'] for d in recibidos)
    total_isr_ret    = sum(d['isr_retenido'] for d in datos)
    total_iva_ret    = sum(d['iva_retenido'] for d in datos)

    iva_neto = total_iva_cob - total_iva_acred - total_iva_ret
    isr_neto = (total_ing - total_gasto) * TASA_ISR_PLATAFORMA - total_isr_ret

    print(f"""
+----------------------------------------------+
|   RESUMEN FISCAL ESTIMADO (Metadata)         |
+----------------------------------------------+
  Ingresos (subtotal):      ${total_ing:>12,.2f}
  Gastos deducibles:        ${total_gasto:>12,.2f}
  -----------------------------------------------
  IVA cobrado (16%):        ${total_iva_cob:>12,.2f}
  IVA acreditable (8%):     ${total_iva_acred:>12,.2f}
  IVA retenido apps:        ${total_iva_ret:>12,.2f}
  IVA NETO A PAGAR:         ${iva_neto:>12,.2f}
  -----------------------------------------------
  ISR retenido (2.5%):      ${total_isr_ret:>12,.2f}
  ISR NETO ESTIMADO:        ${isr_neto:>12,.2f}
+----------------------------------------------+
  * Estimado. Consulta a tu contador.
    """)