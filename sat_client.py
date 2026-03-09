"""
sat_client.py - Cliente funcional para descarga masiva de CFDIs del SAT.

Compatible con cfdiclient v1.6.x (API actual) con fallback a v1.4/1.5.

API de cfdiclient v1.6.x (actual):
  - Fiel(cer_der, key_der, password)
  - Autenticacion(fiel) → .obtener_token()
  - solicitadescargaRecibidos.SolicitaDescargaRecibidos(fiel) → .solicitar_descarga(...)
  - solicitadescargaEmitidos.SolicitaDescargaEmitidos(fiel) → .solicitar_descarga(...)
  - VerificaSolicitudDescarga(fiel) → .verificar_descarga(token, rfc, id_solicitud)
  - DescargaMasiva(fiel) → .descargar_paquete(token, rfc, id_paquete) → {paquete_b64: ...}
"""

import os
import sys
import time
import base64
import zipfile
import io
from datetime import datetime, timedelta
from pathlib import Path

# ====================================================
# IMPORTS COMPATIBLES: v1.6.x (actual) con fallback
# ====================================================
from cfdiclient import Fiel, Autenticacion, VerificaSolicitudDescarga, DescargaMasiva

# v1.6.x separó SolicitaDescarga en Recibidos y Emitidos
try:
    from cfdiclient import solicitadescargaRecibidos
    from cfdiclient import solicitadescargaEmitidos
    _USE_NEW_API = True
except ImportError:
    # Fallback para versiones anteriores (
        print("❌ ERROR: No se pudo importar cfdiclient correctamente.")
        print("   Instala la versión más reciente: pip install cfdiclient --upgrade")
        sys.exit(1)


def _crear_solicitador(fiel, tipo: str):
    """
    Crea el objeto correcto para solicitar descarga según versión de cfdiclient.

    Args:
        fiel: Objeto Fiel
        tipo: 'recibidos' o 'emitidos'

    Returns:
        Objeto con método solicitar_descarga()
    """
    if _USE_NEW_API:
        if tipo == "emitidos":
            return solicitadescargaEmitidos.SolicitaDescargaEmitidos(fiel)
        else:
            return solicitadescargaRecibidos.SolicitaDescargaRecibidos(fiel)
    else:
        # API vieja: una sola clase para ambos
        return SolicitaDescarga(fiel)


class SatClient:
    """Cliente completo para el Web Service de Descarga Masiva del SAT."""

    ESTADOS = {
        1: "Aceptada",
        2: "En proceso",
        3: "Terminada (lista para descargar)",
        4: "Error",
        5: "Rechazada",
        6: "Vencida",
    }

    def __init__(self, rfc: str, cer_path: str, key_path: str, key_password: str,
                 download_dir: str = "downloads/sat"):
        self.rfc = rfc.strip().upper()
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # Leer certificados (bytes DER)
        cer_der = self._leer_archivo(cer_path, "Certificado (.cer)")
        key_der = self._leer_archivo(key_path, "Llave privada (.key)")

        # Crear objeto Fiel (envuelve cer + key)
        try:
            self.fiel = Fiel(cer_der, key_der, key_password)
            print(f"   ✅ Objeto FIEL creado correctamente")
        except Exception as e:
            print(f"   ❌ Error creando FIEL: {e}")
            self._diagnosticar_error_fiel(e)
            sys.exit(1)

        self.token = None
        api_ver = "v1.6+ (Recibidos/Emitidos separados)" if _USE_NEW_API else "v1.4/1.5 (SolicitaDescarga unificado)"
        print(f"   📦 API detectada: {api_ver}")

    # ──────────────────────────────────────────────
    # UTILIDADES
    # ──────────────────────────────────────────────

    @staticmethod
    def _leer_archivo(ruta: str, descripcion: str) -> bytes:
        ruta = Path(ruta)
        if not ruta.exists():
            print(f"❌ ERROR: No se encontró {descripcion} en: {ruta.resolve()}")
            print(f"   Asegúrate de copiar tu e.firma a la carpeta /keys/")
            sys.exit(1)
        with open(ruta, "rb") as f:
            data = f.read()
        print(f"   ✅ {descripcion} cargado ({len(data):,} bytes)")
        return data

    @staticmethod
    def _diagnosticar_error_fiel(error: Exception):
        msg = str(error).lower()
        if "password" in msg or "decrypt" in msg or "bad decrypt" in msg:
            print("   💡 La contraseña de la llave privada es incorrecta.")
            print("      Revisa SAT_KEY_PASSWORD en tu archivo .env")
        elif "certificate" in msg or "x509" in msg or "pem" in msg:
            print("   💡 Problema con el certificado (.cer).")
            print("      ¿Es la FIEL (no CSD)? ¿Está vigente?")
        elif "key" in msg or "rsa" in msg:
            print("   💡 Problema con la llave privada (.key).")
        else:
            print(f"   💡 Error: {error}")

    # ──────────────────────────────────────────────
    # PASO 1: AUTENTICACIÓN
    # ──────────────────────────────────────────────

    def autenticar(self) -> bool:
        print(f"\n🔐 Autenticando RFC: {self.rfc}...")
        try:
            auth = Autenticacion(self.fiel)
            self.token = auth.obtener_token()

            if self.token:
                t = self.token
                preview = f"{t[:20]}...{t[-20:]}" if len(t) > 40 else t
                print(f"   ✅ Token obtenido (expira en ~5 min)")
                print(f"   🔑 Token: {preview}")
                return True
            else:
                print("   ❌ No se obtuvo token. Verifica:")
                print("      - Que el RFC coincida con la e.firma")
                print("      - Que uses la FIEL, no el CSD")
                print("      - Que la e.firma esté vigente")
                return False

        except Exception as e:
            print(f"   ❌ Error de autenticación: {e}")
            msg = str(e).lower()
            if "connection" in msg or "timeout" in msg or "urlerror" in msg:
                print("   💡 No se pudo conectar al SAT. Revisa tu internet.")
            return False

    # ──────────────────────────────────────────────
    # PASO 2: SOLICITAR DESCARGA
    # ──────────────────────────────────────────────

    def solicitar_descarga(
        self,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        tipo: str = "recibidos",
        tipo_solicitud: str = "CFDI",
        tipo_comprobante: str = None,
    ) -> str | None:
        """
        Solicita al SAT un paquete de CFDIs.

        Args:
            fecha_inicio/fin: Rango de fechas
            tipo: 'recibidos' o 'emitidos'
            tipo_solicitud: 'CFDI' o 'Metadata'
            tipo_comprobante: None, 'I', 'E', 'T', 'N', 'P'
        """
        if not self.token:
            print("❌ Necesitas autenticarte primero.")
            return None

        print(f"\n📋 Solicitando descarga ({tipo})...")
        print(f"   📅 Desde: {fecha_inicio.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   📅 Hasta: {fecha_fin.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            # Crear solicitador según tipo y versión de API
            solicitador = _crear_solicitador(self.fiel, tipo)

            # Construir kwargs
            kwargs = {}
            if tipo == "emitidos":
                kwargs["rfc_emisor"] = self.rfc
            else:
                kwargs["rfc_receptor"] = self.rfc
            if tipo_solicitud:
                kwargs["tipo_solicitud"] = tipo_solicitud
            if tipo_comprobante:
                kwargs["tipo_comprobante"] = tipo_comprobante

            # solicitar_descarga(token, rfc_solicitante, fecha_ini, fecha_fin, **kwargs)
            resultado = solicitador.solicitar_descarga(
                self.token,
                self.rfc,
                fecha_inicio,
                fecha_fin,
                **kwargs
            )

            print(f"   📊 Respuesta del SAT:")
            print(f"      Código: {resultado.get('cod_estatus', 'N/A')}")
            print(f"      Mensaje: {resultado.get('mensaje', 'N/A')}")

            id_solicitud = resultado.get("id_solicitud")
            cod_estatus = str(resultado.get("cod_estatus", ""))

            if id_solicitud:
                print(f"   ✅ Solicitud registrada: {id_solicitud}")
                return id_solicitud
            elif cod_estatus == "5004":
                print("   ⚠️ Solicitud duplicada. Ya existe una para este rango.")
                print("      Espera ~48h a que expire o usa el ID anterior.")
                return None
            else:
                print(f"   ❌ No se pudo crear la solicitud")
                return None

        except Exception as e:
            print(f"   ❌ Error en solicitud: {e}")
            return None

    # ──────────────────────────────────────────────
    # PASO 3: VERIFICAR SOLICITUD
    # ──────────────────────────────────────────────

    def verificar_descarga(self, id_solicitud: str, intentos: int = 10,
                           espera: int = 30) -> list:
        if not self.token:
            print("❌ Necesitas autenticarte primero.")
            return []

        print(f"\n🔍 Verificando solicitud: {id_solicitud}")
        verifica = VerificaSolicitudDescarga(self.fiel)

        for intento in range(1, intentos + 1):
            try:
                resultado = verifica.verificar_descarga(
                    self.token, self.rfc, id_solicitud
                )

                estado = resultado.get("estado_solicitud", 0)
                num_cfdis = resultado.get("numero_cfdis", 0)
                paquetes = resultado.get("paquetes", [])
                mensaje = resultado.get("mensaje", "")

                estado_int = int(estado) if estado else 0
                estado_texto = self.ESTADOS.get(estado_int, f"Desconocido ({estado})")

                print(f"   [{intento}/{intentos}] Estado: {estado_texto} | "
                      f"CFDIs: {num_cfdis} | Paquetes: {len(paquetes)}")
                if mensaje:
                    print(f"      Mensaje: {mensaje}")

                if estado_int == 3:
                    if paquetes:
                        print(f"   ✅ ¡Listo! {len(paquetes)} paquete(s)")
                        for i, p in enumerate(paquetes, 1):
                            print(f"      📦 Paquete {i}: {p}")
                        return paquetes
                    else:
                        print("   ⚠️ Terminada pero sin paquetes (0 CFDIs en rango)")
                        return []
                elif estado_int >= 4:
                    print(f"   ❌ Estado final: {estado_texto}")
                    return []
                else:
                    if intento < intentos:
                        print(f"      ⏳ Esperando {espera}s...")
                        time.sleep(espera)

            except Exception as e:
                print(f"   ❌ Error verificando (intento {intento}): {e}")
                if intento < intentos:
                    if "token" in str(e).lower():
                        print("   🔄 Reautenticando...")
                        if self.autenticar():
                            verifica = VerificaSolicitudDescarga(self.fiel)
                            continue
                    time.sleep(espera)

        print(f"   ⏰ Se agotaron los {intentos} intentos.")
        return []

    # ──────────────────────────────────────────────
    # PASO 4: DESCARGAR PAQUETES
    # ──────────────────────────────────────────────

    def descargar_paquete(self, id_paquete: str, carpeta_extra: str = "") -> Path | None:
        if not self.token:
            print("❌ Necesitas autenticarte primero.")
            return None

        print(f"\n📥 Descargando paquete: {id_paquete}...")
        try:
            descarga = DescargaMasiva(self.fiel)
            resultado = descarga.descargar_paquete(
                self.token, self.rfc, id_paquete
            )

            # v1.6.x usa 'paquete_b64', versiones anteriores usan 'paquete'
            paquete_b64 = resultado.get("paquete_b64") or resultado.get("paquete", "")

            if not paquete_b64:
                print(f"   ❌ Paquete vacío. {resultado.get('mensaje', '')}")
                return None

            zip_bytes = base64.b64decode(paquete_b64)
            print(f"   📦 Descargado: {len(zip_bytes):,} bytes")

            destino = self.download_dir / (carpeta_extra or id_paquete[:12])
            destino.mkdir(parents=True, exist_ok=True)

            # También guardar el .zip original por si acaso
            zip_path = destino / f"{id_paquete}.zip"
            with open(zip_path, "wb") as f:
                f.write(zip_bytes)
            print(f"   💾 ZIP guardado: {zip_path.name}")

            # Extraer XMLs
            xmls = self._extraer_zip(zip_bytes, destino)
            print(f"   ✅ {xmls} XML(s) extraídos en: {destino}")

            return destino

        except Exception as e:
            print(f"   ❌ Error al descargar: {e}")
            return None

    def _extraer_zip(self, zip_bytes: bytes, destino: Path) -> int:
        contador = 0
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for nombre in zf.namelist():
                if nombre.lower().endswith(".xml"):
                    zf.extract(nombre, destino)
                    contador += 1
        return contador

    # ──────────────────────────────────────────────
    # FLUJO COMPLETO
    # ──────────────────────────────────────────────

    def descargar_periodo(
        self,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        tipo: str = "recibidos",
        tipo_solicitud: str = "CFDI",
        tipo_comprobante: str = None,
    ) -> Path | None:
        """Flujo completo: autenticar → solicitar → verificar → descargar."""
        etiqueta = f"{fecha_inicio.strftime('%Y-%m')}_{tipo}"
        print(f"\n{'='*60}")
        print(f"🏛️  DESCARGA MASIVA SAT - {etiqueta.upper()}")
        print(f"{'='*60}")

        # 1. Autenticar
        if not self.autenticar():
            return None

        # 2. Solicitar
        id_solicitud = self.solicitar_descarga(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            tipo=tipo,
            tipo_solicitud=tipo_solicitud,
            tipo_comprobante=tipo_comprobante,
        )
        if not id_solicitud:
            return None

        # 3. Verificar
        paquetes = self.verificar_descarga(id_solicitud)
        if not paquetes:
            return None

        # 4. Descargar cada paquete
        # Re-autenticar porque la verificación pudo haber tardado minutos
        self.autenticar()

        carpeta_final = None
        for i, id_paquete in enumerate(paquetes, 1):
            sub = f"{etiqueta}/paquete_{i}" if len(paquetes) > 1 else etiqueta
            carpeta = self.descargar_paquete(id_paquete, carpeta_extra=sub)
            if carpeta:
                carpeta_final = carpeta

        if carpeta_final:
            print(f"\n{'='*60}")
            print(f"🎉 ¡DESCARGA COMPLETA!")
            print(f"   📂 XMLs en: {carpeta_final.resolve()}")
            print(f"{'='*60}")

        return carpeta_final

    def descargar_mes(self, anio: int, mes: int, tipo: str = "recibidos",
                      tipo_solicitud: str = "CFDI") -> Path | None:
        """Atajo: descarga un mes completo."""
        fecha_inicio = datetime(anio, mes, 1)
        if mes == 12:
            fecha_fin = datetime(anio + 1, 1, 1) - timedelta(seconds=1)
        else:
            fecha_fin = datetime(anio, mes + 1, 1) - timedelta(seconds=1)
        return self.descargar_periodo(fecha_inicio, fecha_fin, tipo, tipo_solicitud)
