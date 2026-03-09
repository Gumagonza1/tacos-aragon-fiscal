"""
email_retention.py - Busca constancias de retención en el correo.

Maneja dos casos:
  1. Adjuntos PDF/XML directos (Uber, SAT)
  2. Links en el cuerpo del correo (Didi, Rappi) → descarga directo de S3
"""

import os
import re
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta
from imap_tools import MailBox, AND


class EmailRetentionHunter:
    """Busca y descarga constancias de retención del correo."""

    KEYWORDS = [
        "constancia",
        "retencion",
        "certificado de retencion",
        "ISR",
        "IVA retenido",
        "signatario",
        "didi",
        "uber",
        "rappi",
    ]

    # Dominios conocidos de plataformas que mandan XMLs por link
    DOMINIOS_PLATAFORMAS = [
        "didiglobal.com",
        "s3-us01.didiglobal.com",
        "uber.com",
        "rappi.com",
        "s3.amazonaws.com",
        "storage.googleapis.com",
        "cornershopapp.com",
    ]

    def __init__(self, user: str, password: str, server: str = "imap.gmail.com",
                 download_dir: str = "downloads/email"):
        self.user = user
        self.password = password
        self.server = server
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def buscar_retenciones(self, dias_atras: int = 90, carpeta: str = "INBOX") -> list[str]:
        """
        Busca correos con constancias de retención.
        Descarga tanto adjuntos como XMLs/PDFs enlazados en el cuerpo.

        Args:
            dias_atras: Buscar correos de los últimos N días
            carpeta:    Carpeta IMAP (INBOX, [Gmail]/Todos, etc.)
        Returns:
            Lista de rutas a los archivos descargados
        """
        fecha_desde = datetime.now() - timedelta(days=dias_atras)
        archivos_descargados = []
        correos_procesados   = set()

        print(f"\n📧 Conectando a {self.server}...")
        print(f"   📅 Buscando desde: {fecha_desde.strftime('%Y-%m-%d')}")

        try:
            with MailBox(self.server).login(self.user, self.password) as mailbox:
                print(f"   ✅ Conectado como: {self.user}")

                for keyword in self.KEYWORDS:
                    criteria = AND(subject=keyword, date_gte=fecha_desde.date())

                    for msg in mailbox.fetch(criteria, limit=100):
                        if msg.uid in correos_procesados:
                            continue
                        correos_procesados.add(msg.uid)

                        fecha_str = msg.date.strftime("%Y-%m-%d") if msg.date else "?"
                        print(f"\n   📨 [{fecha_str}] {msg.subject[:70]}")
                        print(f"      De: {msg.from_}")

                        # ── 1. Adjuntos directos ────────────────────────────
                        for att in msg.attachments:
                            nombre = att.filename or ""
                            if nombre.lower().endswith((".pdf", ".xml", ".zip")):
                                ruta = self._guardar_adjunto(att, msg)
                                if ruta:
                                    print(f"      📎 Adjunto: {os.path.basename(ruta)}")
                                    archivos_descargados.append(ruta)

                        # ── 2. Links en el cuerpo (Didi, Rappi, etc.) ───────
                        cuerpo = msg.text or msg.html or ""
                        links = self._extraer_links_xml(cuerpo)
                        for url in links:
                            ruta = self._descargar_link(url, msg)
                            if ruta:
                                print(f"      🔗 Link descargado: {os.path.basename(ruta)}")
                                archivos_descargados.append(ruta)

                archivos_descargados = list(set(archivos_descargados))

                print(f"\n{'='*50}")
                if archivos_descargados:
                    print(f"✅ {len(archivos_descargados)} archivo(s) descargado(s):")
                    for a in archivos_descargados:
                        print(f"   📄 {a}")
                else:
                    print("⚠️  No se encontraron constancias de retención.")
                    print("   Intenta aumentar dias_atras o revisa las palabras clave.")

        except Exception as e:
            print(f"   ❌ Error de correo: {e}")
            if "authentication" in str(e).lower() or "login" in str(e).lower():
                print("   💡 Para Gmail necesitas contraseña de aplicación:")
                print("      https://myaccount.google.com/apppasswords")

        return archivos_descargados

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _extraer_links_xml(self, cuerpo: str) -> list[str]:
        """
        Extrae URLs de XMLs y PDFs del cuerpo del correo.
        Detecta tanto HTML (<a href="...">) como texto plano (https://...).
        """
        urls = set()

        # Patrón 1: URLs directas en texto plano que terminan en .xml o .pdf
        patron_directo = re.compile(
            r'https?://[^\s\)\]>"\'\|]+\.(?:xml|pdf)',
            re.IGNORECASE
        )
        urls.update(patron_directo.findall(cuerpo))

        # Patrón 2: href en HTML
        patron_href = re.compile(
            r'href=["\']([^"\']+\.(?:xml|pdf))["\']',
            re.IGNORECASE
        )
        urls.update(patron_href.findall(cuerpo))

        # Patrón 3: URLs largas cortadas por salto de línea (correos de Didi)
        # Une líneas que terminan con fragmento de URL sin extensión todavía
        cuerpo_unido = re.sub(r'\s+', ' ', cuerpo)
        urls.update(patron_directo.findall(cuerpo_unido))

        # Filtrar solo dominios de plataformas conocidas (evitar falsos positivos)
        filtradas = []
        for url in urls:
            # Limpiar caracteres extra al final
            url = re.sub(r'[>\)\]\'\"]+$', '', url).strip()
            if any(d in url for d in self.DOMINIOS_PLATAFORMAS):
                filtradas.append(url)
            else:
                # Si no es dominio conocido pero es .xml, igual intentarlo
                if url.endswith('.xml'):
                    filtradas.append(url)

        return list(set(filtradas))

    def _descargar_link(self, url: str, mensaje) -> str | None:
        """Descarga un archivo desde una URL y lo guarda en disco."""
        try:
            # Nombre de archivo desde la URL
            nombre = url.split('/')[-1].split('?')[0]
            if not nombre:
                nombre = f"ret_{int(time.time())}.xml"

            # Subcarpeta por mes del correo
            fecha_msg  = mensaje.date if mensaje.date else datetime.now()
            subcarpeta = self.download_dir / fecha_msg.strftime("%Y-%m")
            subcarpeta.mkdir(parents=True, exist_ok=True)

            ruta = subcarpeta / nombre
            if ruta.exists():
                return str(ruta)  # ya descargado

            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/xml,text/xml,application/pdf,*/*',
            }
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

            with open(ruta, 'wb') as f:
                f.write(resp.content)

            return str(ruta)

        except Exception as e:
            print(f"      ⚠️  Error descargando {url[:80]}: {e}")
            return None

    def _guardar_adjunto(self, attachment, mensaje) -> str | None:
        """Guarda un adjunto en disco evitando duplicados."""
        nombre = attachment.filename
        if not nombre:
            return None

        fecha_msg  = mensaje.date if mensaje.date else datetime.now()
        subcarpeta = self.download_dir / fecha_msg.strftime("%Y-%m")
        subcarpeta.mkdir(parents=True, exist_ok=True)

        ruta = subcarpeta / nombre
        if ruta.exists():
            return str(ruta)

        with open(ruta, 'wb') as f:
            f.write(attachment.payload)

        return str(ruta)