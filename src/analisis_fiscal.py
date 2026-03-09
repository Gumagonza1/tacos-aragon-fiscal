# -*- coding: utf-8 -*-
"""
analisis_fiscal.py - Contador IA especialista en Plataformas Tecnológicas y Negocios de Alimentos

Usa Gemini 2.0 Flash (Google) para:
  1. Clasificar cada CFDI en la hoja correcta del Excel
  2. Detectar CFDIs fuera del mes en curso
  3. Definir estrategia fiscal para deducción máxima (RMF 2026)
  4. Generar reportes XLSX y PDF y enviarlos por correo

Instalación:
    pip install google-genai pandas openpyxl fpdf2 python-dotenv httpx
"""

import json
import os
import httpx
import pandas as pd
from fpdf import FPDF
import smtplib
from email.message import EmailMessage
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

# Importaciones del nuevo SDK de Gemini
from google import genai
from google.genai import types

# Cargar variables de entorno desde el archivo .env
load_dotenv()

def _gemini_disponible():
    """Detecta qué versión de la librería de Gemini está instalada."""
    try:
        import google.genai
        return True, "genai", google.genai
    except ImportError:
        return False, None, None

def _resumir_datos(datos: list, rfc: str, anio: int, mes: int) -> str:
    """Convierte la lista de CFDIs a un resumen JSON compacto para el prompt."""
    mi_rfc = rfc.upper()
    resumen = []
    for d in datos:
        resumen.append({
            "uuid":           d.get("uuid", "")[:8] + "...",
            "fecha":          d.get("fecha", "")[:10],
            "tipo":           d.get("tipo_comprobante", "I"),
            "emisor_rfc":     d.get("emisor_rfc", ""),
            "emisor_nombre":  d.get("emisor_nombre", "")[:40],
            "receptor_rfc":   d.get("receptor_rfc", ""),
            "subtotal":       round(d.get("subtotal", 0), 2),
            "total":          round(d.get("total", 0), 2),
            "iva_trasladado": round(d.get("iva_trasladado", 0), 2),
            "isr_retenido":   round(d.get("isr_retenido", 0), 2),
            "iva_retenido":   round(d.get("iva_retenido", 0), 2),
            "soy_emisor":     d.get("emisor_rfc", "").upper() == mi_rfc,
            "fuente":         d.get("fuente", "xml"),
        })

    totales = {
        "total_registros":    len(datos),
        "ingresos_count":     sum(1 for d in resumen if d["soy_emisor"]),
        "gastos_count":       sum(1 for d in resumen if not d["soy_emisor"] and d["tipo"] != "RET"),
        "retenciones_count":  sum(1 for d in resumen if d["tipo"] == "RET"),
        "suma_ingresos":      round(sum(d["total"] for d in resumen if d["soy_emisor"]), 2),
        "suma_gastos":        round(sum(d["total"] for d in resumen if not d["soy_emisor"] and d["tipo"] != "RET"), 2),
        "iva_cobrado":        round(sum(d["iva_trasladado"] for d in resumen if d["soy_emisor"]), 2),
        "iva_acreditable":    round(sum(d["iva_trasladado"] for d in resumen if not d["soy_emisor"]), 2),
        "isr_retenido_total": round(sum(d["isr_retenido"] for d in resumen), 2),
        "iva_retenido_total": round(sum(d["iva_retenido"] for d in resumen), 2),
    }

    return json.dumps({
        "rfc_contribuyente": mi_rfc,
        "periodo":           f"{anio}-{mes:02d}",
        "totales":           totales,
        "cfdis":             resumen,
    }, ensure_ascii=False, indent=2)


PROMPT_SISTEMA = """Eres un contador público certificado especialista en:
- Régimen de Plataformas Tecnológicas (Art. 113-A LISR) para restaurantes en Uber Eats, Didi Food, Rappi.
- ISR e IVA para negocios de preparación de alimentos al carbón en plataformas digitales mexicanas.
- RESICO (Régimen Simplificado de Confianza, Art. 113-E LISR).
- Estrategias de deducción máxima para contribuyentes en modalidad de pagos provisionales.

═══════════════════════════════════════════════════════════
MODALIDAD DEL CONTRIBUYENTE: PAGOS PROVISIONALES
═══════════════════════════════════════════════════════════
ATENCIÓN IA: El contribuyente tributa bajo la modalidad de PAGOS PROVISIONALES (NO son pagos definitivos). 
Esto significa que:
1. Las retenciones que le hacen las plataformas (ISR e IVA) NO son el impuesto final, sino un "anticipo". Son 100% acreditables.
2. Está OBLIGADO a presentar declaración mensual.
3. Para el IVA, los GASTOS DEDUCIBLES son cruciales para bajar el IVA a cargo mensual.
4. El cálculo final mensual es: Impuesto Determinado - Retenciones de la Plataforma = Cargo Real a Pagar.

═══════════════════════════════════════════════════════════
REGLAS DE CLASIFICACIÓN DE CFDIs
═══════════════════════════════════════════════════════════

TIPOS DE CFDI que recibirás en el campo "tipo":
  "I"   = Ingreso (factura normal)
  "E"   = Egreso (nota de crédito)
  "P"   = Pago (complemento de pago)
  "RET" = CFDI de Retenciones e Información de Pagos (especial de plataformas)

CÓMO IDENTIFICAR cada tipo de documento:

1. RETENCIONES_APP → tipo="RET" O (tipo="I" con isr_retenido>0 Y emisor es plataforma)
   - Emisores conocidos: UBER*, DIDI*, RAPPI*, BEAT*, INDRIVER*, DME*, RTP*
   - Contienen ISR retenido e IVA retenido.
   - ACCIÓN: Van a hoja RETENCIONES_APP.

2. INGRESOS_PLATAFORMAS → tipo="I" donde soy_emisor=true
   - Facturas de ingresos propios (mostrador, público en general, facturas a clientes).
   - ACCIÓN: Van a hoja INGRESOS_PLATAFORMAS.

3. NOMINA_EMITIDA → tipo="N" (TipoDeComprobante=N)
   - soy_emisor=true y tipo="N"
   - Gasto deducible del negocio (sueldos pagados a trabajadores).
   - ACCIÓN: Van a hoja NOMINA_EMITIDA.

4. GASTOS_DEDUCIBLES → tipo="I" donde soy_emisor=false Y el gasto aplica como deducción estricta:
   - Insumos de alimentos (carne asada, pollo, verduras, tortillas de maíz/harina, carbón, salsas).
   - Empaques, bolsas y desechables para envíos.
   - Gasolina, mantenimiento y seguro de vehículos de reparto.
   - Teléfono/internet, hosting web y comisiones bancarias o de software de punto de venta.
   - ACCIÓN: Van a hoja GASTOS_DEDUCIBLES. ¡Vitales para el acreditamiento de IVA en pago provisional!

5. GASTOS_NO_DEDUCIBLES → tipo="I" donde soy_emisor=false Y no aplica deducción:
   - Supermercado personal (ropa, despensa no relacionada al restaurante).
   - Gastos médicos (van en la declaración anual, no en el pago provisional).
   - ACCIÓN: Van a hoja GASTOS_NO_DEDUCIBLES.

6. IGNORAR → CFDIs sin relevancia fiscal inmediata:
   - Complementos de pago (tipo="P") sin montos, o cancelados.
   - ACCIÓN: Ignorar.

═══════════════════════════════════════════════════════════
NORMATIVA SAT ESTRICTA — RMF 2026 / LISR
═══════════════════════════════════════════════════════════

BASE LEGAL: Art. 113-A, 113-E de la LISR. Art. 18-J del LIVA.

1. RETENCIONES QUE HACEN LAS PLATAFORMAS AL RESTAURANTE (PREPARACIÓN DE ALIMENTOS):
  - ISR retenido (Art. 113-A, fracción II LISR): Para restaurantes y negocios de preparación de alimentos la retención obligatoria de la app es del 2.1%.
  - IVA retenido: 50% del IVA trasladado (8% si la tasa es del 16%).
  *ALERTA PARA LA IA: La tasa correcta es 2.1% (fracción II, preparación de alimentos), NO 1% (fracción III, enajenación simple de bienes). Verifica que los XML tipo "RET" usen 2.1%.

2. CÁLCULO DE IMPUESTOS EN PAGOS PROVISIONALES:
  - IVA a pagar = IVA Cobrado - IVA Acreditable (de Gastos Deducibles) - IVA Retenido por Apps.
  - ISR a pagar (Si recomiendas RESICO): Aplicar tabla mensual (1% a 2.5% sobre ingreso bruto) MENOS el ISR Retenido por Apps (2.1%).
  - ISR a pagar (Si recomiendas Plataformas/Actividad Empresarial): (Ingresos - Deducciones = Base). Aplicar tarifa mensual Art. 96 MENOS ISR Retenido por Apps.

═══════════════════════════════════════════════════════════
FORMATO DE RESPUESTA
═══════════════════════════════════════════════════════════
Responde ÚNICAMENTE con JSON válido, sin markdown ni texto extra.
{
  "clasificacion": [
    {
      "uuid_corto": "primeros 8 chars del UUID",
      "hoja_excel": "INGRESOS_PLATAFORMAS | GASTOS_DEDUCIBLES | GASTOS_NO_DEDUCIBLES | RETENCIONES_APP | NOMINA_EMITIDA | IGNORAR",
      "razon": "explicacion",
      "mes_correcto": true,
      "alerta": null
    }
  ],
  "estrategia_fiscal": {
    "regimen_recomendado": "Plataformas Tecnologicas (Provisional) | RESICO",
    "razon_regimen": "",
    "ingreso_bruto_plataformas": 0.00,
    "nomina_total_emitida": 0.00,
    "iva_a_pagar": 0.00,
    "isr_cargo_real": 0.00,
    "pago_definitivo": false,
    "deducciones_clave": [],
    "gastos_faltantes": [],
    "alertas_criticas": []
  },
  "mejoras_excel": [],
  "conciliacion_plataformas": [],
  "resumen_ejecutivo": ""
}"""




def _preconciliar_plataformas(datos: list, rfc: str, datos_mes_siguiente: list = None) -> dict:
    todos = list(datos)
    if datos_mes_siguiente:
        todos += list(datos_mes_siguiente)

    mi_rfc = rfc.upper()

    PLATAFORMAS = {
        "Uber Eats": ["UBER", "UBR", "CORNERSHOP"],
        "Didi Food": ["DIDI", "DIDIF"],
        "Rappi":     ["RAPPI"],
    }

    def detectar_plataforma(emisor_nombre: str, emisor_rfc: str) -> str:
        nombre = (emisor_nombre or "").upper()
        rfc_em = (emisor_rfc or "").upper()
        for plat, keywords in PLATAFORMAS.items():
            if any(k in nombre or k in rfc_em for k in keywords):
                return plat
        return None

    grupos = {}

    for d in todos:
        plat = detectar_plataforma(d.get("emisor_nombre",""), d.get("emisor_rfc",""))
        if not plat:
            continue

        if plat not in grupos:
            grupos[plat] = {"facturas": [], "certificados": []}

        tipo = d.get("tipo_comprobante", "")
        if tipo == "RET":
            grupos[plat]["certificados"].append(d)
        elif tipo == "I" and d.get("emisor_rfc","").upper() != mi_rfc:
            grupos[plat]["facturas"].append(d)

    resumen = {}
    for plat, docs in grupos.items():
        suma_facturas = round(sum(d.get("subtotal", 0) for d in docs["facturas"]), 2)
        suma_cert     = round(sum(d.get("total", 0)    for d in docs["certificados"]), 2)
        isr_cert      = round(sum(d.get("isr_retenido", 0) for d in docs["certificados"]), 2)
        iva_cert      = round(sum(d.get("iva_retenido", 0) for d in docs["certificados"]), 2)
        diferencia    = round(suma_cert - suma_facturas, 2)
        pct           = round(abs(diferencia) / suma_cert * 100, 2) if suma_cert else 0

        resumen[plat] = {
            "facturas_count":       len(docs["facturas"]),
            "certificados_count":   len(docs["certificados"]),
            "suma_facturas":        suma_facturas,
            "suma_certificados":    suma_cert,
            "diferencia":           diferencia,
            "diferencia_pct":       pct,
            "alerta_previa":        f"Diferencia {pct}% entre facturas y certificado" if pct > 2 else None,
            "isr_retenido_cert":    isr_cert,
            "iva_retenido_cert":    iva_cert,
            "base_gravable_oficial": suma_cert,
        }

    return resumen


def analizar_con_ia(datos: list, rfc: str, anio: int, mes: int, api_key: Optional[str] = None, datos_mes_siguiente: list = None) -> Optional[dict]:
    disponible, version, modulo_genai = _gemini_disponible()
    if not disponible or version != "genai":
        print("\n  ⚠️  Necesitas el nuevo SDK de Gemini. Ejecuta: pip install google-genai")
        return None

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("\n  ⚠️  No se encontró GEMINI_API_KEY en el entorno.")
        return None

    if not datos:
        print("\n  ⚠️  Sin datos para analizar")
        return None

    print(f"\n{'─'*55}")
    print(f"  🤖 ANÁLISIS FISCAL IA (Gemini 2.0 Flash) — {anio}-{mes:02d}")
    print(f"  Enviando {len(datos)} CFDIs al contador IA...")
    print(f"{'─'*55}")

    resumen_json      = _resumir_datos(datos, rfc, anio, mes)
    conciliacion_prev = _preconciliar_plataformas(datos, rfc, datos_mes_siguiente=datos_mes_siguiente)
    mes_nombre        = datetime(anio, mes, 1).strftime("%B %Y")
    conciliacion_str  = json.dumps(conciliacion_prev, ensure_ascii=False, indent=2)

    prompt = f"""{PROMPT_SISTEMA}

Analiza los siguientes CFDIs del contribuyente {rfc} correspondientes al período {mes_nombre}.

PRE-CONCILIACIÓN CALCULADA POR EL SISTEMA (Python):
{conciliacion_str}

CFDI COMPLETOS:
{resumen_json}

INSTRUCCIÓN FINAL:
1. Los CERTIFICADOS DE RETENCIÓN (tipo=RET) son la BASE GRAVABLE OFICIAL.
2. Usa los montos del certificado para calcular ISR e IVA.
3. Si la diferencia entre facturas y certificado > 2% → ALERTA CRÍTICA.
4. Si no hay certificado → recomendar descargarlo del portal del SAT."""

    texto = ""
    try:
        # Corrección: El SDK nuevo usa HttpOptions para el timeout
        client = genai.Client(
            api_key=key, 
            http_options=types.HttpOptions(timeout=60_000)
        )
        
        print(f"  ⏳ Tamaño del paquete a enviar: {len(prompt)} caracteres.")
        if len(prompt) > 800000:
            print("  ⚠️ ALERTA: Estás enviando demasiados datos. El prompt es gigantesco y podría fallar.")
            
        print("  ⏳ Conectando con los servidores de Google Gemini...")
        print("  ⏳ Analizando deducciones (timeout configurado a 60s)...")
        
        response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
            )
        )
        texto = response.text.strip()

        if "```" in texto:
            for bloque in texto.split("```"):
                bloque = bloque.strip()
                if bloque.startswith("json"):
                    bloque = bloque[4:].strip()
                if bloque.startswith("{"):
                    texto = bloque
                    break

        resultado = json.loads(texto)
        print("  ✅ Análisis recibido y procesado correctamente.")
        return resultado

    except json.JSONDecodeError as e:
        print(f"  ❌ La IA no devolvió JSON válido: {e}")
        if texto:
            print(f"  Respuesta cruda: {texto[:400]}")
        return None
    except Exception as e:
        print(f"  ❌ Error de conexión con Gemini API: {e}")
        return None
def guardar_analisis_json(analisis: dict, rfc: str, anio: int, mes: int, carpeta: str = "downloads") -> str:
    """Guarda el análisis en JSON para referencia futura."""
    os.makedirs(carpeta, exist_ok=True)
    nombre = f"{carpeta}/analisis_fiscal_{rfc}_{anio}-{mes:02d}.json"
    with open(nombre, "w", encoding="utf-8") as f:
        json.dump(analisis, f, ensure_ascii=False, indent=2)
    print(f"  💾 Análisis JSON guardado en: {nombre}")
    return nombre

def imprimir_analisis(analisis: dict, rfc: str):
    """Muestra el análisis en consola de forma legible."""
    if not analisis: return
    print(f"\n{'═'*60}")
    print(f"  📊 ANÁLISIS FISCAL — RFC: {rfc}")
    print(f"{'═'*60}")
    print(f"\n  📋 RESUMEN:\n")
    for linea in analisis.get("resumen_ejecutivo", "").split(". "):
        if linea.strip(): print(f"    • {linea.strip()}.")

    ef = analisis.get("estrategia_fiscal", {})
    print(f"\n  💰 ESTRATEGIA FISCAL:")
    print(f"    Régimen: {ef.get('regimen_recomendado', '')}")
    print(f"\n    IVA a pagar:      ${ef.get('iva_a_pagar', 0):>10,.2f}")
    print(f"    ISR cargo real:   ${ef.get('isr_cargo_real', 0):>10,.2f}")

    if ef.get("alertas_criticas", []):
        print(f"\n  ⚠️  ALERTAS CRÍTICAS:")
        for a in ef.get("alertas_criticas", []): print(f"    ⚡ {a}")
    print(f"{'═'*60}\n")

def aplicar_clasificacion_ia(datos: list, clasificacion: list, rfc: str) -> list:
    """Agrega campo 'hoja_ia' y 'alerta_ia' a cada registro según el análisis."""
    lookup = {c.get("uuid_corto", "").replace("...", "").strip(): c for c in clasificacion if c.get("uuid_corto", "").replace("...", "").strip()}
    for d in datos:
        uuid_corto = d.get("uuid", "")[:8]
        info_ia    = lookup.get(uuid_corto, {})
        d["hoja_ia"]  = info_ia.get("hoja_excel", None)
        d["alerta_ia"]= info_ia.get("alerta", None)
        d["mes_ok"]   = info_ia.get("mes_correcto", True)
    return datos

def generar_archivos(analisis_ia: dict, datos_cfdi: list, rfc: str, periodo: str):
    """
    Genera el archivo XLSX con los CFDIs clasificados por IA.
    Usa excel_export.generar_excel() para obtener el formato completo con
    hojas: INGRESOS_PLATAFORMAS, GASTOS_DEDUCIBLES, GASTOS_NO_DEDUCIBLES,
           RETENCIONES_APP, NOMINA_EMITIDA y RESUMEN FISCAL.
    """
    from src.excel_export import generar_excel

    print(f"\n  📄 Generando reporte clasificado por IA para {periodo}...")

    nombre = f"Analisis_IA_{rfc}_{periodo}.xlsx"
    ruta_xlsx = generar_excel(datos_cfdi, rfc, nombre=nombre)

    if not ruta_xlsx:
        # Fallback mínimo si excel_export falla
        import pandas as pd
        ruta_xlsx = f"output/reporte_fiscal_{rfc}_{periodo}.xlsx"
        os.makedirs("output", exist_ok=True)
        df = pd.DataFrame(datos_cfdi)
        df.to_excel(ruta_xlsx, index=False)
        print(f"  ⚠️  Excel básico (fallback): {ruta_xlsx}")

    return ruta_xlsx

def enviar_correo_reporte(correo_destino: str, ruta_xlsx: str, periodo: str):
    """Envía un correo con el Excel y el PDF adjuntos usando las credenciales del .env."""
    remitente = os.environ.get("EMAIL_USER", "")
    password = os.environ.get("EMAIL_PASS", "")
    
    if not remitente or not password:
        print("\n  ⚠️ No se configuraron EMAIL_USER o EMAIL_PASS en el entorno. Se omite el correo.")
        return

    print(f"\n  📧 Preparando envío de correo a {correo_destino}...")
    
    msg = EmailMessage()
    msg['Subject'] = f'📊 Tu Análisis Fiscal y Contable - {periodo}'
    msg['From'] = remitente
    msg['To'] = correo_destino
    
    cuerpo_correo = (
        f"Hola,\n\n"
        f"Adjunto encontrarás la clasificación automatizada de tus facturas en Excel "
        f"y el documento PDF con la explicación detallada de tu estrategia fiscal para el periodo {periodo}.\n\n"
        f"Saludos,\nTu IA Contable."
    )
    msg.set_content(cuerpo_correo)

   
        
    # Adjuntar Excel
    with open(ruta_xlsx, 'rb') as f:
        msg.add_attachment(f.read(), maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=os.path.basename(ruta_xlsx))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(remitente, password)
            smtp.send_message(msg)
        print(f"  ✅ Correo enviado exitosamente a {correo_destino}")
    except Exception as e:
        print(f"  ❌ Error al enviar el correo: {e}")