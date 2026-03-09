# -*- coding: utf-8 -*-
import xmltodict
import os
import zipfile
from pathlib import Path

def _limpiar_dict(d):
    """ Quita los prefijos 'cfdi:', 'tfd:', 'retenciones:', etc. """
    if isinstance(d, dict):
        return {k.split(':')[-1]: _limpiar_dict(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [_limpiar_dict(x) for x in d]
    else:
        return d

def procesar_contenido_xml(contenido_binario, nombre_archivo):
    """ Detecta si el XML es una factura estándar o una retención de apps. """
    try:
        raw_data = xmltodict.parse(contenido_binario)
        data = _limpiar_dict(raw_data)
        
        # 1. CASO: FACTURA ESTÁNDAR (Ingreso, Egreso, Pago)
        if data.get('Comprobante'):
            c = data['Comprobante']
            tipo = c.get('@TipoDeComprobante', 'I')
            impuestos = c.get('Impuestos', {})
            ret_list = impuestos.get('Retenciones', {}).get('Retencion', [])
            if isinstance(ret_list, dict): ret_list = [ret_list]
            tras_list = impuestos.get('Traslados', {}).get('Traslado', [])
            if isinstance(tras_list, dict): tras_list = [tras_list]

            return {
                "fecha": c.get('@Fecha', ''),
                "tipo_comprobante": tipo,
                "es_pago": True if tipo == 'P' else False,
                "emisor_rfc": c.get('Emisor', {}).get('@Rfc', '').upper(),
                "emisor_nombre": c.get('Emisor', {}).get('@Nombre', 'Desconocido'),
                "receptor_rfc": c.get('Receptor', {}).get('@Rfc', '').upper(),
                "receptor_nombre": c.get('Receptor', {}).get('@Nombre', 'Desconocido'),
                "subtotal": float(c.get('@SubTotal', 0)),
                "total": float(c.get('@Total', 0)),
                "iva_trasladado": sum(float(t.get('@Importe', 0)) for t in tras_list if t.get('@Impuesto') == '002'),
                "isr_retenido": sum(float(r.get('@Importe', 0)) for r in ret_list if r.get('@Impuesto') == '001'),
                "iva_retenido": sum(float(r.get('@Importe', 0)) for r in ret_list if r.get('@Impuesto') == '002'),
                "uuid": c.get('Complemento', {}).get('TimbreFiscalDigital', {}).get('@UUID', ''),
                "archivo": nombre_archivo
            }
        
        # 2. CASO: CFDI DE RETENCIONES v1 y v2 (Uber, Didi, Rappi, Plataformas)
        if data.get('Retenciones'):
            r = data['Retenciones']
            version = r.get('@Version', '1.0')
            es_v2 = version.startswith('2')

            # ── Emisor ──────────────────────────────────────────────────────
            emisor = r.get('Emisor', {})
            if es_v2:
                # v2: <Emisor RfcE="..." NomDenRazSocE="..."/>
                emisor_rfc    = emisor.get('@RfcE', '').upper()
                emisor_nombre = emisor.get('@NomDenRazSocE', 'APP RETENEDORA')
            else:
                # v1: <Emisor RFCEmisor="..." NomDenRazSocE="..." o NomReten="..."/>
                emisor_rfc    = (emisor.get('@RFCEmisor') or emisor.get('@RfcE', '')).upper()
                emisor_nombre = (emisor.get('@NomDenRazSocE') or
                                 emisor.get('@NomReten', 'APP RETENEDORA'))

            # ── Receptor ────────────────────────────────────────────────────
            receptor      = r.get('Receptor', {})
            receptor_nac  = receptor.get('Nacional', {})
            if es_v2:
                # v2: <Nacional RfcR="..." NomDenRazSocR="..."/>
                receptor_rfc    = receptor_nac.get('@RfcR', '').upper()
                receptor_nombre = receptor_nac.get('@NomDenRazSocR', 'Desconocido')
            else:
                # v1: <Nacional RFCReceptor="..." NomRecep="..."/>
                receptor_rfc    = (receptor_nac.get('@RFCReceptor') or
                                   receptor_nac.get('@RfcR', '')).upper()
                receptor_nombre = (receptor_nac.get('@NomRecep') or
                                   receptor_nac.get('@NomDenRazSocR', 'Desconocido'))

            # ── Totales ─────────────────────────────────────────────────────
            totales = r.get('Totales', {})
            # v1 usa @montoTotOperacion (minúscula), v2 usa @MontoTotOperacion (Mayúscula)
            monto_total = float(
                totales.get('@MontoTotOperacion') or
                totales.get('@montoTotOperacion') or 0
            )

            # ── Impuestos retenidos ──────────────────────────────────────────
            imp_ret = totales.get('ImpRetenidos', [])
            if isinstance(imp_ret, dict):
                imp_ret = [imp_ret]

            isr = 0.0
            iva = 0.0
            for i in imp_ret:
                # v1: @Impuesto='01'/'02', @montoRet
                # v2: @ImpuestoRet='001'/'002', @MontoRet
                impuesto = (i.get('@ImpuestoRet') or i.get('@Impuesto') or '').strip()
                monto    = float(i.get('@MontoRet') or i.get('@montoRet') or 0)
                if impuesto in ('001', '01'):   # ISR
                    isr += monto
                elif impuesto in ('002', '02'): # IVA
                    iva += monto

            # ── Complemento PlataformasTecnológicas (v2) ────────────────────
            complemento = r.get('Complemento', {})
            plat = complemento.get('ServiciosPlataformasTecnologicas', {})
            iva_trasladado = float(plat.get('@TotalIVATrasladado', 0)) if plat else 0.0

            # Periodo
            periodo = r.get('Periodo', {})
            mes_ini = periodo.get('@MesIni', '')
            ejercicio = periodo.get('@Ejercicio', '')
            fecha = (r.get('@FechaExp') or r.get('@FechaExpedicion', ''))

            return {
                "fecha":             fecha,
                "tipo_comprobante":  "RET",
                "version_ret":       version,
                "cve_retencion":     r.get('@CveRetenc', ''),
                "es_pago":           False,
                "emisor_rfc":        emisor_rfc,
                "emisor_nombre":     emisor_nombre,
                "receptor_rfc":      receptor_rfc,
                "receptor_nombre":   receptor_nombre,
                "subtotal":          monto_total,
                "total":             monto_total,
                "iva_trasladado":    iva_trasladado,
                "isr_retenido":      isr,
                "iva_retenido":      iva,
                "periodo_mes":       mes_ini,
                "periodo_año":       ejercicio,
                "uuid":              complemento.get('TimbreFiscalDigital', {}).get('@UUID', ''),
                "archivo":           nombre_archivo,
                "fuente":            "xml_ret_v2" if es_v2 else "xml_ret_v1",
            }

        return None
    except Exception as e:
        print(f"  ⚠️ Error en {nombre_archivo}: {e}")
        return None

def parsear_carpeta(carpeta_path):
    """ Función que busca XMLs y ZIPs de forma recursiva. """
    resultados = []
    path_raiz = Path(carpeta_path)
    if not path_raiz.exists():
        return []

    for elemento in path_raiz.rglob("*"):
        # Procesar archivos XML
        if elemento.is_file() and elemento.suffix.lower() == ".xml":
            with open(elemento, 'rb') as f:
                res = procesar_contenido_xml(f.read(), elemento.name)
                if res: resultados.append(res)
        
        # Procesar archivos ZIP (SAT)
        elif elemento.is_file() and elemento.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(elemento, 'r') as z:
                    for n in z.namelist():
                        if n.lower().endswith('.xml'):
                            with z.open(n) as f:
                                res = procesar_contenido_xml(f.read(), f"{elemento.name}/{n}")
                                if res: resultados.append(res)
            except Exception as e:
                print(f"  ❌ Error en ZIP {elemento.name}: {e}")
            
    return resultados
