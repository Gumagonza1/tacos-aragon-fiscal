# 🌮 Bot Fiscal Tacos Aragón

Bot automático para descargar CFDIs del SAT, parsear XMLs y generar Excel contable.

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
tax_aragon_bot/
├── .env                    # Credenciales (NO subir a git)
├── main.py                 # Orquestador principal
├── requirements.txt
├── keys/                   # e.firma del SAT (NO subir a git)
│   ├── firma.cer
│   └── firma.key
├── src/
│   ├── sat_client.py       # Conexión Web Service SAT
│   ├── cfdi_parser.py      # Parser de XMLs CFDI 4.0/3.3
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
