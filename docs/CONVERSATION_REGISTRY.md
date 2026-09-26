# Registro de Conversaciones (quién habló con el bot)

Cada mensaje que un contacto le envía al bot y que produce una respuesta se
guarda como un registro con estos campos (todos configurables):

| Campo | Contenido |
|---|---|
| `hora` | Fecha/hora del intercambio (ISO-8601, zona horaria configurable) |
| `numero` | Número de WhatsApp del remitente (`message.from` de Meta) |
| `nombre` | Nombre del contacto (si está en el directorio de contactos) |
| `ciudad` | Ciudad del contacto (ídem) |
| `empresa` | Empresa del contacto (ídem) |
| `pregunta` | Texto que escribió el usuario |
| `respuesta` | Respuesta que generó el bot (texto, redirect o fallback) |

El destino del registro es **completamente parametrizable**: puede apuntar a
**una base de datos (SQLite)** o a **una hoja de Google** (Google Sheets), sin
tocar código. Solo cambias variables de configuración.

> **Datos personales:** a diferencia de los logs (teléfono enmascarado, sin
> texto), este registro **sí** guarda número, nombre y mensajes del cliente por
> diseño. Defina responsable, finalidad y tiempo de retención conforme a la
> Ley 1581 de 2012 antes de activarlo en producción.
>
> Los mensajes no-texto (imágenes, audios, etc.) no se registran.

---

## 1. Activar y elegir el destino

Todo se configura con variables de entorno (`.env`) o en
`config/config.yaml`. Las variables de `.env` tienen prioridad.

| Variable | Valores | Significado |
|---|---|---|
| `CONVERSATION_LOG_ENABLED` | `0` / `1` | Activa o desactiva el registro |
| `CONVERSATION_LOG_BACKEND` | `none` \| `sqlite` \| `google_sheets` | Dónde se guardan los registros |
| `CONVERSATION_LOG_COLUMNS` | lista separada por comas | Orden/campos de las columnas |
| `CONVERSATION_LOG_TIMEZONE` | ej. `America/Bogota` | Zona horaria de `hora` (vacío = UTC) |
| `CONVERSATION_LOG_CONTACTS_FILE` | ruta a un `.json` | Directorio de contactos (nombre/ciudad/empresa) |
| `CONVERSATION_LOG_GOOGLE_AUTH_MODE` | `service_account` \| `oauth_user` | Cómo se autentica con Google (ver sección 3) |

### Ejemplo mínimo (SQLite)

```env
CONVERSATION_LOG_ENABLED=1
CONVERSATION_LOG_BACKEND=sqlite
CONVERSATION_LOG_DB_PATH=data/conversation_log.sqlite3
CONVERSATION_LOG_TIMEZONE=America/Bogota
```

### Ejemplo mínimo (Google Sheets)

```env
CONVERSATION_LOG_ENABLED=1
CONVERSATION_LOG_BACKEND=google_sheets
CONVERSATION_LOG_SPREADSHEET_ID=1GB9LaRtZgtw4JEyK33uDH8IweOVh7aB_fv8ohjIhGcM
CONVERSATION_LOG_WORKSHEET=Sheet1
# elegir UNO de los dos modos de autenticación (ver sección 3.1):
CONVERSATION_LOG_GOOGLE_AUTH_MODE=service_account
CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE=/app/credentials/gspread-sa.json
```

> En Docker los cambios de `.env` requieren recrear el contenedor:
> `docker compose up -d --force-recreate chatbot`.

---

## 2. Backend SQLite (base de datos local)

Es el destino por defecto y no necesita nada extra (usa el módulo estándar
`sqlite3`). Crea un archivo en `./data/conversation_log.sqlite3`, que ya es un
volumen persistente en Docker.

```env
CONVERSATION_LOG_ENABLED=1
CONVERSATION_LOG_BACKEND=sqlite
CONVERSATION_LOG_DB_PATH=data/conversation_log.sqlite3
```

Consultar lo registrado:

```bash
docker compose exec chatbot python scripts/registry_cli.py
docker compose exec chatbot python scripts/registry_cli.py --export report.csv
```

Tabla: `conversations`, una fila por intercambio, con las columnas definidas en
`CONVERSATION_LOG_COLUMNS` (se crea automáticamente al primer registro).

---

## 3. Backend Google Sheets (hoja de cálculo de Google)

Hay **dos formas** de autenticarse para escribir en la hoja. Usa la que te sea
más cómoda; en ambos casos solo se configura una vez.

| Modo | ¿Qué necesitas? | Cuándo usarlo |
|---|---|---|
| `service_account` (por defecto) | Clave JSON de una cuenta de servicio de Google Cloud | Uso corporativo / multi-persona |
| `oauth_user` | Tu cuenta personal de Google (login único OAuth2) | Uso personal, sin crear cuentas de servicio |

> ⚠️ **Las "App passwords" no sirven aquí.** Los App passwords solo aplican a
> Gmail/IMAP/SMTP; la API de Google Sheets **no los acepta**. Con una cuenta
> personal el equivalente es el flujo OAuth2: un `client_secret.json` + un login
> único que guarda un token de acceso renovable.

### 3.1 Elegir y configurar el modo

En `.env` (o en `config/config.yaml`):

```env
CONVERSATION_LOG_GOOGLE_AUTH_MODE=oauth_user   # o service_account
```

- **`service_account`** → usa `CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE`
  (ver sección 3.2).
- **`oauth_user`** → usa `CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE` +
  `CONVERSATION_LOG_GOOGLE_TOKEN_FILE` (ver sección 3.3).

Variables del modo `oauth_user`:

| Variable | Significado |
|---|---|
| `CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE` | `client_secret.json` del cliente OAuth (tipo **Desktop app**), p. ej. `/app/credentials/client_secret.json` |
| `CONVERSATION_LOG_GOOGLE_TOKEN_FILE` | Ruta donde se guarda el token tras el login único. Por defecto `data/gspread_authorized_user.json` |

### 3.2 Modo `service_account` (cuenta de servicio)

1. Ve a https://console.cloud.google.com → crea un proyecto (o usa uno existente).
2. **APIs y servicios → Biblioteca** → busca **Google Sheets API** → **Habilitar**.
3. **APIs y servicios → Credenciales → Crear credenciales → Cuenta de servicio**.
   - Nombre: p. ej. `wa-registry`. Se genera un correo como
     `wa-registry@<proyecto>.iam.gserviceaccount.com`.
4. En la cuenta de servicio creada: **Claves → Agregar clave → Crear nueva clave →
   JSON** → se descarga un archivo `*.json`.
5. Copia ese JSON en `deploy/credentials/gspread-sa.json`
   (carpeta montada en el contenedor como `/app/credentials`).
6. **Comparte tu hoja con el correo de la cuenta de servicio** como **Editor**:
   - Abre `https://docs.google.com/spreadsheets/d/1GB9LaRtZgtw4JEyK33uDH8IweOVh7aB_fv8ohjIhGcM`
   - Botón **Compartir** → pega `wa-registry@<proyecto>.iam.gserviceaccount.com` → *Editor*.

### 3.3 Modo `oauth_user` (tu cuenta personal de Google)

El bot escribe en la hoja **usando tu propia cuenta de Google**, sin cuenta de
servicio. Requiere un login único desde el contenedor:

1. **Google Cloud Console** → https://console.cloud.google.com → crea/usas un
   proyecto → **APIs y servicios → Biblioteca → Google Sheets API → Habilitar**.
2. **Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **Aplicación de escritorio** (Desktop app).
   - En "Usuarios de prueba" de la **pantalla de consentimiento** añade tu correo.
   - **Descargar JSON** → ese archivo es el `client_secret.json`.
3. Cópialo en `deploy/credentials/client_secret.json`
   (carpeta montada en `/app/credentials`).
4. Configura `.env`:

   ```env
   CONVERSATION_LOG_ENABLED=1
   CONVERSATION_LOG_BACKEND=google_sheets
   CONVERSATION_LOG_SPREADSHEET_ID=1GB9LaRtZgtw4JEyK33uDH8IweOVh7aB_fv8ohjIhGcM
   CONVERSATION_LOG_WORKSHEET=Sheet1
   CONVERSATION_LOG_GOOGLE_AUTH_MODE=oauth_user
   CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE=/app/credentials/client_secret.json
   # CONVERSATION_LOG_GOOGLE_TOKEN_FILE=data/gspread_authorized_user.json  (opcional)
   ```
5. Recrea el contenedor y ejecuta el **login único** (abre el navegador y pega
   el código de autorización):

   ```bash
   docker compose up -d --force-recreate chatbot
   docker compose exec -it chatbot python scripts/sheets_oauth_setup.py
   ```

   - Entra con la cuenta que **es dueña de la hoja** (o que está compartida en
     ella como Editor).
   - Copia el **código de autorización** que muestra la página y pégalo en la
     terminal.
   - El script guarda el token en `CONVERSATION_LOG_GOOGLE_TOKEN_FILE` y lo
     verifica abriendo la hoja.
6. Listo: a partir de ahí el contenedor reutiliza el token guardado
   automáticamente (lo renueva solo cuando caduca, sin intervención).

> Si el token se borra o el login expira, repite el paso 5: no cambia nada más.

### 3.4 Configuración final (ambos modos)

```env
CONVERSATION_LOG_ENABLED=1
CONVERSATION_LOG_BACKEND=google_sheets
CONVERSATION_LOG_SPREADSHEET_ID=1GB9LaRtZgtw4JEyK33uDH8IweOVh7aB_fv8ohjIhGcM
CONVERSATION_LOG_WORKSHEET=Sheet1
```

> El **ID de la hoja** es la parte entre `/d/` y `/edit` del URL:
> `1GB9LaRtZgtw4JEyK33uDH8IweOVh7aB_fv8ohjIhGcM`. El **nombre de la pestaña**
> suele ser `Sheet1`; si no existe, el proceso la crea automáticamente.

Comportamiento:
- La **fila de encabezado** se escribe automáticamente al primer registro
  (el contenido de `CONVERSATION_LOG_COLUMNS`).
- Cada intercambio agrega una fila **debajo** de los datos existentes.
- Si Google no está accesible, el error se registra en el log pero **no rompe**
  la conversación con el usuario.

> Las dependencias `gspread` y `google-auth` ya están en `requirements.txt`:
> se instalan al reconstruir el contenedor (`docker compose up -d --build chatbot`).

---

## 4. Directorio de contactos (nombre, ciudad, empresa)

WhatsApp solo entrega el número. Para completar `nombre`, `ciudad` y `empresa`
indica un archivo JSON (opcional) con `CONVERSATION_LOG_CONTACTS_FILE`:

```json
{
  "573001234567": {"nombre": "Juan Pérez", "ciudad": "Bogotá", "empresa": "Fertrac"},
  "573008887766": {"nombre": "Ana Gómez",  "ciudad": "Medellín", "empresa": "Fertrac"}
}
```

- El número se compara tal cual y también normalizado (sin caracteres no
  numéricos), así `+57 300 123 4567` encuentra `573001234567`.
- Los números desconocidos dejan esos tres campos vacíos.
- Sugerencia: guarda el archivo en `config/contacts.json` (carpeta ya montada
  en Docker). Deja el archivo vacío `{}` si aún no tienes datos.

---

## 5. Personalizar columnas

`CONVERSATION_LOG_COLUMNS` define el orden y los campos que se escriben.
Valor por defecto:

```env
CONVERSATION_LOG_COLUMNS=hora,numero,nombre,ciudad,empresa,pregunta,respuesta
```

Puedes reordenarlos u omitir campos. Los campos internos disponibles son
exactamente esos siete.

---

## 6. Verificación

1. Reinicia el contenedor con `--force-recreate` (para leer `.env`).
2. Envía un mensaje al bot.
3. Revisa el log de arranque:
   ```bash
   docker compose logs chatbot | Select-String -Pattern "Conversation registry"
   ```
   Debe decir `sqlite backend` o `google_sheets backend` (y no `disabled`).
4. Inspecciona el resultado:
   - **SQLite:** `docker compose exec chatbot python scripts/registry_cli.py`
   - **Google Sheets:** abre la hoja → verás la fila nueva con encabezado.

   Las filas se escriben **por lotes** (hasta 50 filas o cada 5 segundos), así
   que pueden tardar unos segundos en aparecer.

---

## 7. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| El registro sigue "disabled" | `CONVERSATION_LOG_ENABLED=0` o no re-creado | Verifica `.env` y usa `--force-recreate chatbot` |
| Sheets: error de permisos (403) | La hoja no se compartió con la cuenta/servicio o de login | `service_account`: comparte la hoja con el correo de la SA como Editor. `oauth_user`: inicia sesión con la cuenta dueña/Editor de la hoja |
| Sheets: `OAuth token file not found` | `oauth_user` sin el login único | Ejecuta `docker compose exec -it chatbot python scripts/sheets_oauth_setup.py` |
| Sheets: `client_secret.json` inválido | El JSON no es un OAuth client de tipo Desktop app | Regenera el ID de cliente OAuth y descarga el JSON correcto |
| Sheets: 404/hoja no encontrada | `CONVERSATION_LOG_SPREADSHEET_ID` o `WORKSHEET` mal | Copia el ID exacto del URL y el nombre de la pestaña |
| Sheets: `gspread`/`google-auth` no instalado | Contenedor no reconstruido | `docker compose up -d --build chatbot` |
| Sheets: `File not found: None` / SA inválida | JSON de credenciales mal ubicado | Verifica `deploy/credentials/gspread-sa.json` y que esté montado |
| No aparecen filas de SQLite | Aún no hubo mensajes procesados | Envía un mensaje; revisa `data/conversation_log.sqlite3` |
| Los errores de registro no frenan el bot | Comportamiento esperado | Los fallos se loguean; revisa `logs/` y la métrica `metabot_registry_dropped_total` |
| Una celda muestra `=...` como texto | Comportamiento esperado | Sheets se escribe en modo `RAW`: un mensaje que empiece por `=` no se ejecuta como fórmula (protección contra inyección) |
| La columna `hora` no se ordena como fecha en Sheets | Modo `RAW` guarda el texto ISO-8601 | Aplica formato de fecha a la columna o conviértela con `=DATEVALUE()` |

---

## 8. Arquitectura / dónde está el código

```
src/registry/
  __init__.py    # exporta ConversationRegistry y build_registry
  models.py      # ConversationRecord + columnas por defecto
  contacts.py    # directorio de contactos (enriquecimiento)
  backends.py    # none | sqlite | google_sheets + create_backend()
  service.py     # ConversationRegistry (enriquece y persiste en segundo plano)
```

El hook está en `src/pipeline.py`: después de responder un mensaje, el worker
llama a `registry.log_turn(numero, pregunta, respuesta)`, que solo encola el
registro en un búfer en memoria (máx. 5 000). **Un único escritor** en segundo
plano lo persiste en lotes (`log_many`: `executemany` en SQLite, una sola
llamada `append_rows` en Sheets). Así:

- no hay accesos concurrentes a SQLite ni al cliente de Google;
- se respeta la cuota de escritura de Google Sheets (~60 solicitudes/min);
- al apagar el bot se vacía el búfer antes de cerrar (hasta 10 s).

Los scripts CLI (sin servidor) escriben directamente, fila por fila.

Scripts auxiliares:
- `scripts/registry_cli.py` — consulta/exporta filas de SQLite.
- `scripts/sheets_oauth_setup.py` — login único OAuth2 para `oauth_user`.

### Agregar otro destino (p. ej. PostgreSQL)

1. Crea una clase que herede `RegistryBackend` implementando `log()`, `close()`
   y, para mejor rendimiento, `log_many()` (inserción por lotes).
2. Regístrala en `create_backend()` (en `src/registry/backends.py`).
3. Agrega la variable de configuración que necesites en
   `ConversationLogConfig` (`src/config.py`) y su override en `.env`.
