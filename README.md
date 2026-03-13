# tacos-aragon-fiscal — SAT CFDI Fiscal Bot

Automated tool to download CFDIs from the SAT (Mexico's tax authority), parse XMLs, and generate accounting Excel reports. Optionally performs AI-powered fiscal analysis using Gemini.

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
# Edit .env with your RFC and private key password

# 4. Copy your e.firma
# Place your .cer and .key files in the keys/ folder
# Rename them to firma.cer and firma.key
```

## Usage

```bash
# Interactive menu
python main.py

# Download a specific month (received)
python main.py --mes 2025-01

# Download issued invoices
python main.py --mes 2025-01 --tipo emitidos

# Download both
python main.py --mes 2025-01 --tipo ambos

# Test SAT authentication only
python main.py --test-auth

# Parse already-downloaded XMLs
python main.py --parsear downloads/sat/2025-01_recibidos
```

## Project Structure

```
tacos-aragon-fiscal/
├── .env                    # Credentials (do NOT commit)
├── main.py                 # Main orchestrator
├── requirements.txt
├── keys/                   # SAT e.firma (do NOT commit)
│   ├── firma.cer
│   └── firma.key
├── src/
│   ├── sat_client.py       # SAT Web Service connection
│   ├── cfdi_parser.py      # CFDI 4.0/3.3 XML parser
│   ├── analisis_fiscal.py  # Fiscal analysis with Gemini
│   ├── email_retention.py  # Searches retention receipts in email
│   └── excel_export.py     # Generates accounting Excel
├── downloads/sat/          # XMLs downloaded from SAT
├── downloads/email/        # Retention PDFs from email
└── output/                 # Final Excel reports
```

## SAT Web Service Flow

1. **Authentication** → Session token (5-minute lifetime)
2. **Request** → Request a package for a date range
3. **Verification** → Wait for SAT to prepare the package (~30s to ~5min)
4. **Download** → Download ZIP with XMLs
5. **Extraction** → Unzip and parse

## Environment Variables

```env
SAT_RFC=XAXX010101000
SAT_KEY_PASSWORD=your_key_password
SAT_CER_PATH=keys/firma.cer
SAT_KEY_PATH=keys/firma.key

# Optional: AI fiscal analysis
GEMINI_API_KEY=AIza...

# Optional: email retention search
EMAIL_USER=your@gmail.com
EMAIL_PASS=your_app_password
IMAP_SERVER=imap.gmail.com
```

## Notes

- SAT limits to **2 active simultaneous requests** per RFC
- Error 5004 (duplicate) means a previous request is still active — wait for it to expire
- Tokens expire in ~5 minutes; the bot re-authenticates automatically
- For Gmail, use an **app password** (not your regular password)

---

# tacos-aragon-fiscal — Bot Fiscal SAT

Bot automático para descargar CFDIs del SAT, parsear XMLs y generar reportes de Excel contable. Incluye análisis fiscal opcional con Gemini.

## Instalación rápida

```bash
# 1. Crear entorno virtual
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar credenciales
# Edita el archivo .env con tu RFC y contraseña de llave privada

# 4. Copiar e.firma
# Copia tus archivos .cer y .key a la carpeta keys/
# Renómbralos a firma.cer y firma.key
```

## Uso

```bash
# Menú interactivo
python main.py

# Descargar mes específico (recibidos)
python main.py --mes 2025-01

# Descargar emitidos
python main.py --mes 2025-01 --tipo emitidos

# Descargar ambos
python main.py --mes 2025-01 --tipo ambos

# Solo probar autenticación
python main.py --test-auth

# Parsear XMLs ya descargados
python main.py --parsear downloads/sat/2025-01_recibidos
```

## Estructura

```
tacos-aragon-fiscal/
├── .env                    # Credenciales (NO subir a git)
├── main.py                 # Orquestador principal
├── requirements.txt
├── keys/                   # e.firma del SAT (NO subir a git)
│   ├── firma.cer
│   └── firma.key
├── src/
│   ├── sat_client.py       # Conexión Web Service SAT
│   ├── cfdi_parser.py      # Parser de XMLs CFDI 4.0/3.3
│   ├── analisis_fiscal.py  # Análisis fiscal con Gemini
│   ├── email_retention.py  # Busca retenciones en correo
│   └── excel_export.py     # Genera Excel contable
├── downloads/sat/          # XMLs descargados del SAT
├── downloads/email/        # PDFs de retenciones
└── output/                 # Excel final
```

## Flujo del Web Service del SAT

1. **Autenticación** → Token de sesión (5 min de vida)
2. **Solicitud** → Pedir paquete por rango de fechas
3. **Verificación** → Esperar a que el SAT prepare el paquete (~30s a ~5min)
4. **Descarga** → Bajar ZIP con los XMLs
5. **Extracción** → Descomprimir y parsear

## Notas

- El SAT limita a **2 solicitudes** activas simultáneas por RFC
- Si recibes error 5004 (duplicada), espera a que expire la anterior
- Los tokens expiran en ~5 minutos; el bot reautentica automáticamente
- Para Gmail, necesitas una **contraseña de aplicación** (no tu contraseña normal)
