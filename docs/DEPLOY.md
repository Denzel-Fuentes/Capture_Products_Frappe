# Despliegue a producción: captura_productos

Guía para instalar, configurar, actualizar y revertir la app `captura_productos` en la instancia
ERPNext v16 de CloudClusters. El entorno de desarrollo está en [DEV.md](DEV.md), la especificación en
[spec.md](spec.md) y el estado de QA en [QA.md](QA.md).

> **Convenciones de esta guía**
>
> - **[CONFIRMAR]** marca algo que el usuario debe decidir o comprobar antes de seguir.
> - **[DESTRUCTIVO]** marca un paso difícil de revertir. No se ejecuta sin una confirmación explícita.
> - `root#` significa ejecutar como `root` y `erpnext$` como el usuario `erpnext`, dentro del bench.
> - `<ENTRE_ANGULOS>` es un valor que hay que reemplazar.

## Datos de la instancia

| Pieza | Valor |
|---|---|
| URL | https://erpnext-213997-0.cloudclusters.net |
| Acceso | SSH como `root` |
| Bench | `/cloudclusters/erpnext/frappe-bench`, usuario `erpnext` (`su - erpnext`) |
| Sitio | `default` (no `erpnext-213997-0`) |
| Apps ya instaladas | `frappe`, `erpnext`, `pos_prime` (comprobar con `bench version`) |
| Restricciones conocidas | `bench build` sin `--app` muere por falta de memoria (exit 137). `bench restart` falla porque no hay `sudo`; se usa `supervisorctl restart all` como root. Poca RAM: hubo caídas por timeout. |
| Uso en vivo | Se vende con POS Prime. Cualquier corte o reinicio afecta a la caja. |

**Cuándo desplegar.** `supervisorctl restart all` corta el sitio entre 10 y 60 segundos, y `migrate`
puede tardar más. **[CONFIRMAR]** Elegir un horario sin ventas (por ejemplo, después del cierre de caja).

### Bloqueantes antes del primer despliegue

1. **[CONFIRMAR] Bugs de severidad alta de QA cerrados.** [QA.md](QA.md#4-bugs-encontrados-por-severidad)
   lista B1 (los botones "Exportar a Google Sheet" y "Crear Item" no funcionan) y B2 (los botones
   "Probar conexión IA" y "Probar Google Sheet" no funcionan). Sin B2 resuelto, el paso 3.4 de esta guía
   no se puede hacer desde el Desk. Desplegar solo una versión con la suite de tests en verde en dev.
2. **[CONFIRMAR] El repositorio no tiene ningún commit ni remoto todavía** (`git status`: "No commits yet
   on main"). Hay que hacer el primer commit, crear el repo privado en GitHub, subirlo y crear un tag de
   versión (paso 1.1).
3. **[CONFIRMAR] `app_email` es un placeholder** (`dev@estaciontradicion.example` en `hooks.py` y
   `pyproject.toml`). No impide instalar, pero conviene poner el correo real antes del tag `v1.0.0`.

---

## 1. Prerrequisitos

### 1.1 Repositorio en GitHub (privado) y versión a desplegar

En la PC de desarrollo (Git Bash, en la raíz del repo), después del commit:

```bash
# Crear el repo privado (con GitHub CLI; o desde github.com → New repository → Private)
gh repo create <GITHUB_USUARIO>/captura_productos --private --source . --remote origin
git push -u origin main

# Marcar la versión que se despliega (subir también __version__ en captura_productos/__init__.py)
git tag -a v1.0.0 -m "Primera versión en producción"
git push origin v1.0.0
```

- Llamar al repo **`captura_productos`** (igual que la app) evita que `bench get-app` tenga que renombrar
  la carpeta.
- Comprobar que **no** se subió ningún secreto: `git ls-files | grep -Ei '\.env|token|credential|\.json$'`
  solo debe mostrar JSON de DocTypes, `fixtures/role.json` y `public/manifest.json`. El `.gitignore`
  ya excluye `.env`, `*.env.ps1` y `*.token`.
- Se despliega siempre un **tag** (`v1.0.0`, `v1.0.1`, ...), no la punta de `main`, para saber qué
  código corre en producción y poder volver al anterior.

### 1.2 Acceso de solo lectura del servidor al repo

El servidor necesita leer el repo privado. Hay dos opciones; se recomienda la A.

**Opción A (recomendada): deploy key SSH de solo lectura.** La clave solo sirve para este repo y no
tiene permiso de escritura.

```bash
root# su - erpnext
erpnext$ ssh-keygen -t ed25519 -C "cloudclusters-captura_productos" -f ~/.ssh/id_ed25519_captura -N ""
erpnext$ cat ~/.ssh/id_ed25519_captura.pub
```

En GitHub: repo → **Settings → Deploy keys → Add deploy key**, pegar la clave pública, título
"CloudClusters producción" y **dejar desmarcado "Allow write access"**.

Indicar a SSH que use esa clave para GitHub y probar:

```bash
erpnext$ cat >> ~/.ssh/config <<'EOF'
Host github-captura
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_captura
    IdentitiesOnly yes
EOF
erpnext$ chmod 600 ~/.ssh/config ~/.ssh/id_ed25519_captura
erpnext$ ssh -T github-captura
# La primera vez pide aceptar la huella de github.com. Comparar con la publicada en
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# Respuesta esperada: "Hi <GITHUB_USUARIO>/captura_productos! You've successfully authenticated..."
```

La URL para `bench get-app` queda: `github-captura:<GITHUB_USUARIO>/captura_productos.git`.

> **[CONFIRMAR]** Comprobar que `~/.ssh` del usuario `erpnext` persiste si CloudClusters recrea el
> contenedor. Si se pierde, basta con repetir este paso y reemplazar la deploy key.

**Opción B: token fine-grained de solo lectura.** GitHub → Settings → Developer settings → Personal
access tokens → **Fine-grained tokens** → Generate: "Only select repositories" → `captura_productos`,
permiso **Contents: Read-only**, con fecha de expiración. Desventaja: el token queda en texto plano en
`apps/captura_productos/.git/config` y hay que renovarlo cuando expira. Si se usa, escribir el token
directamente en la terminal del servidor, nunca en un archivo del repo ni en esta guía.

### 1.3 Versiones de producción frente a dev

Dev usa **Frappe y ERPNext 16.52.0**, Python 3.14.8 y Node 24.22.0. La app declara
`requires-python = ">=3.14"`.

```bash
root# su - erpnext
erpnext$ cd /cloudclusters/erpnext/frappe-bench
erpnext$ bench version                      # frappe, erpnext y pos_prime con versión y rama
erpnext$ ./env/bin/python --version         # debe ser 3.14 o mayor
erpnext$ node --version
erpnext$ bench --site default list-apps
erpnext$ grep -E 'developer_mode|maintenance_mode|pause_scheduler' sites/default/site_config.json sites/common_site_config.json
```

| Resultado | Qué hacer |
|---|---|
| Frappe/ERPNext 16.52.x | Seguir. |
| 16.x anterior a 16.52 | **[CONFIRMAR]** Lo más probable es que funcione, pero no está probado. No actualizar Frappe/ERPNext de producción solo por esta app: probar antes en dev con la misma versión (`git checkout v16.<x>` en el bench de dev) o aceptar el riesgo y hacer la prueba de humo completa. |
| Mayor que 16.52 | Seguir; anotar la versión en la sección "Historial" al final. |
| Python < 3.14 | `pip` rechazará la app ("requires a different Python"). **No** es algo que se arregle en el servidor: avisar al programador (habría que bajar `requires-python` en `pyproject.toml` y probarlo). |
| `developer_mode` = 1 | **[CONFIRMAR]** No debería estar activo en producción. |

### 1.4 Salida a internet y recursos del servidor

```bash
erpnext$ for u in https://github.com https://pypi.org/simple/anthropic/ https://api.anthropic.com https://sheets.googleapis.com https://oauth2.googleapis.com; do printf '%-45s ' "$u"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 "$u"; done
# Cualquier código HTTP (200, 301, 401, 404) sirve: lo que importa es que no dé 000 (sin conexión).
# api.anthropic.com solo hace falta con el proveedor "Anthropic directo"; con la API local (por
# defecto) la IA va a http://127.0.0.1:8787 dentro del contenedor (sección 3.1).

erpnext$ free -m          # RAM libre antes de empezar
erpnext$ df -h /cloudclusters
```

Si la memoria disponible ("available") es menor que unos 500 MB, hacer el despliegue en el horario de
menos uso y no correr nada más en paralelo.

### 1.5 Backup completo antes de tocar nada

```bash
erpnext$ cd /cloudclusters/erpnext/frappe-bench
erpnext$ bench --site default backup --with-files
erpnext$ ls -lh sites/default/private/backups/ | tail -5
```

Quedan en `/cloudclusters/erpnext/frappe-bench/sites/default/private/backups/` cuatro archivos con la
misma marca de tiempo:

| Archivo | Contenido |
|---|---|
| `<fecha>-default-database.sql.gz` | Base de datos completa |
| `<fecha>-default-files.tar` | Archivos públicos (`sites/default/public/files`) |
| `<fecha>-default-private-files.tar` | Archivos privados |
| `<fecha>-default-site_config_backup.json` | `site_config.json` (contiene la contraseña de la base: tratarlo como secreto) |

Anotar también el estado del código, para poder volver atrás:

```bash
erpnext$ bench version > ~/antes-captura-$(date +%Y%m%d).txt
erpnext$ cp sites/apps.txt ~/apps.txt.antes-captura-$(date +%Y%m%d)
```

Copiar el backup **fuera del servidor** (desde PowerShell en la PC; el backup en el mismo disco no sirve
si el servidor se pierde):

```powershell
mkdir "$HOME\Backups\erpnext-prod" -Force
scp "root@<HOST_SSH>:/cloudclusters/erpnext/frappe-bench/sites/default/private/backups/<fecha>-default-*" "$HOME\Backups\erpnext-prod\"
```

> **[CONFIRMAR]** El host SSH y si CloudClusters ya hace backups automáticos propios (y con qué
> frecuencia). Esta guía no los reemplaza.

---

## 2. Instalación

Todos los comandos `erpnext$` se ejecutan en `/cloudclusters/erpnext/frappe-bench`. Si `bench` se ejecuta
como `root`, muestra un `WARN` y no hace nada.

### 2.1 Descargar la app

```bash
root# su - erpnext
erpnext$ cd /cloudclusters/erpnext/frappe-bench
erpnext$ bench get-app --branch v1.0.0 --skip-assets github-captura:<GITHUB_USUARIO>/captura_productos.git
```

- `--branch` acepta una rama o un tag. Con un tag el repo queda en "detached HEAD": es lo esperado.
- `--skip-assets` evita que `get-app` compile. Se compila después solo esta app (2.3).
- `get-app` clona en `apps/captura_productos`, agrega la app a `sites/apps.txt` e instala el paquete
  Python con sus dependencias en `./env`. Todavía **no** la instala en el sitio.

### 2.2 Dependencias Python

`get-app` ya debería haberlas instalado (vienen de `pyproject.toml`: `anthropic`, `gspread`,
`google-auth`). Verificar y, si falta algo, instalar en el env del bench (nunca con el `pip` del
sistema ni como root):

```bash
erpnext$ ./env/bin/python -c "import anthropic, gspread, google.auth; print('ok', anthropic.__version__, gspread.__version__)"
# Si falla:
erpnext$ ./env/bin/pip install -e apps/captura_productos
erpnext$ ./env/bin/python -c "import anthropic, gspread, google.auth; print('ok')"
```

Dev usa `anthropic` 2.6.0, `gspread` 6.2.1 y `google-auth` 2.48.0. `pyproject.toml` acepta
`anthropic>=2.0,<3`, `gspread>=6.1,<7` y `google-auth>=2.30,<3`.

### 2.3 Instalar en el sitio, migrar y compilar

```bash
erpnext$ bench --site default install-app captura_productos
erpnext$ bench --site default migrate
erpnext$ bench build --app captura_productos      # NUNCA "bench build" a secas: OOM (exit 137)
erpnext$ bench --site default clear-cache
erpnext$ bench --site default clear-website-cache
```

Qué hace cada paso:

- `install-app` crea los DocTypes nuevos (Captura de Producto, sus tablas hijas, Configuracion Captura,
  Prefijo por Grupo, Glosario de Marcas, Captura Atributo), el rol **Capturador de Productos** (fixture)
  y corre `after_install`, que completa Configuracion Captura con los valores por defecto: modelo,
  plantillas, prefijo `BEB` para "1.9 Bebidas Embotelladas", el glosario inicial y los enlaces a
  "Estacion Tradicion", "Depósito Bebidas - ET" y "Unidad" (solo los que existan en el sitio).
- La app **no modifica DocTypes existentes**: no crea Custom Field, Property Setter ni Custom DocPerm.
- `migrate` corre los patches pendientes de **todas** las apps. Si Frappe/ERPNext no se actualizaron, no
  debería haber ninguno; si `migrate` empieza a ejecutar patches de ERPNext, es señal de que el código de
  ERPNext cambió sin migrar y conviene revisarlo antes de seguir.
- `clear-cache` es obligatorio después de `build`: el mapa `assets.json` queda en caché y, si no se
  limpia, `/captura` sigue pidiendo un bundle con el hash anterior.

### 2.4 Reiniciar los procesos

```bash
erpnext$ exit                          # volver a root
root# supervisorctl status             # anotar los nombres de los procesos antes de reiniciar
root# supervisorctl restart all
root# supervisorctl status             # todos en RUNNING
```

`bench restart` falla en esta instancia (no hay `sudo`) y `supervisorctl` como `erpnext` da
`PermissionError` en el socket.

### 2.5 Verificar el worker `long` y el scheduler

El procesamiento con IA y la exportación al Sheet corren en la cola **`long`**. Las tareas programadas
(`reencolar_reintentos` cada minuto y `rescatar_atascadas` cada 15 minutos, que pasa a Error las
capturas que llevan más de 20 minutos en Procesando sin trabajo en cola) necesitan el **scheduler**.
Sin ellos, las capturas se quedan en "Procesando" para siempre.

```bash
root# supervisorctl status
# Debe haber, en RUNNING, algo como (los nombres exactos dependen de la instalación):
#   frappe-bench-frappe-schedule
#   frappe-bench-frappe-short-worker-0
#   frappe-bench-frappe-long-worker-0      <- el que atiende la cola "long"
#   frappe-bench-frappe-web, frappe-bench-node-socketio, redis-*

root# su - erpnext -c "cd /cloudclusters/erpnext/frappe-bench && bench --site default scheduler status && bench doctor"
# "Scheduler is enabled for site default"
# bench doctor lista los workers y las colas que atiende cada uno: "long" debe aparecer.
```

Si el scheduler está desactivado o pausado:

```bash
erpnext$ bench --site default scheduler enable
erpnext$ bench --site default scheduler resume
```

> **[CONFIRMAR] Si no hay ningún worker que atienda la cola `long`** (CloudClusters puede haber recortado
> workers por la RAM), hay que agregarlo a la configuración de supervisor del bench. Es un cambio de
> configuración persistente del servidor: no hacerlo sin revisar primero
> `/cloudclusters/erpnext/frappe-bench/config/supervisor.conf` y guardar una copia
> (`cp supervisor.conf supervisor.conf.bak-<fecha>`). Para probar sin tocar la configuración, se puede
> lanzar uno temporal (muere al cerrar la sesión): `erpnext$ bench worker --queue long`.

Comprobación desde el Desk (como System Manager): buscar **RQ Worker** en la barra de búsqueda; debe
haber al menos un worker cuyo tipo de cola incluya `long`. En **Scheduled Job Type**, filtrar por
`captura_productos` y comprobar que las dos tareas existen y no están en "Stopped".

---

## 3. Configuración posterior a la instalación

Casi todo se carga en el Desk en **Configuracion Captura** (solo System Manager). Las claves van en
campos de tipo Password: Frappe las guarda cifradas con la `encryption_key` del sitio y no se envían al
navegador. La excepción es la API local de IA: su URL y su token van en `site_config.json` (3.1).

> **Importante:** la `encryption_key` está en `sites/default/site_config.json`. Si se restaura la base en
> otro sitio sin esa clave, las contraseñas guardadas (API key, JSON de Google) no se pueden descifrar y
> hay que volver a cargarlas.

### 3.1 IA: API local de Claude Code (por defecto)

La IA usa por defecto la **API local**: un envoltorio HTTP de Claude Code que corre en el mismo
contenedor que ERPNext (spec sección 6). No hace falta API key de Anthropic. La URL y el token del
envoltorio **no** van en Configuracion Captura ni en el código: van en el `site_config.json` del sitio
`default`.

1. **[CONFIRMAR] El envoltorio responde** desde el contenedor (no requiere token):

   ```bash
   erpnext$ curl -s http://127.0.0.1:8787/openapi.json | head -c 200; echo
   # Debe imprimir el JSON de OpenAPI (FastAPI). Si no responde: el servicio está caído o en otro puerto.
   ```

2. **[CONFIRMAR] El envoltorio está bajo supervisor** (u otro gestor de procesos) para que sobreviva a
   los reinicios del contenedor y a `supervisorctl restart all`. Comprobar que aparece en
   `root# supervisorctl status`. Si se lanzó a mano (por ejemplo con `nohup`), se pierde en el próximo
   reinicio y las capturas terminan en Error ("El servicio de IA local no responde").

3. Configurar URL y token (el token lo da quien instaló el envoltorio; no copiarlo en chats, correos ni
   archivos del repo):

   ```bash
   erpnext$ bench --site default set-config claude_api_url http://127.0.0.1:8787
   erpnext$ bench --site default set-config claude_api_token '<TOKEN_DEL_ENVOLTORIO>'
   ```

   Para no dejar el token en el historial de la shell, anteponer un espacio al comando (si `HISTCONTROL`
   incluye `ignorespace`) o borrarlo después con `history -d`. `claude_api_url` es opcional si el
   envoltorio usa el puerto por defecto (`http://127.0.0.1:8787`).

4. En Configuracion Captura: **Proveedor de IA = API local** (el patch de la versión lo fija en sitios
   existentes), **Modelo** `claude-sonnet-5-5` (o `claude-haiku-5-5`, más rápido y barato) y **Timeout
   API local (s)** 180 (30 a 900; incluye la espera en la cola del envoltorio). Guardar y **Probar →
   Probar conexión IA**: comprueba que el servicio responde y hace una consulta mínima con
   `claude-haiku-5-5` para validar el token; informa duración y costo y borra la sesión.

**Concurrencia.** El envoltorio atiende **3 consultas a la vez**; las demás esperan su turno (y ese
tiempo cuenta dentro de `timeout_ia`). La app manda cada captura como un trabajo de la cola `long`; con
un solo worker `long` (lo normal en esta instancia) las capturas ya se procesan de a una, así que no se
llega al límite. Si se agregan workers `long`, más de 3 capturas simultáneas esperan en el envoltorio:
subir `timeout_ia` si aparecen errores 504.

**Costo.** Cada captura guarda `costo_ia_usd` (suma de todos los intentos) y `duracion_ia_ms` en la
sección Técnica (solo supervisores). El envoltorio usa la cuenta de Claude Code del servidor: revisar el
consumo ahí.

**Seguridad del envoltorio [CONFIRMAR].** Es Claude Code: puede tener herramientas (Bash, leer y escribir
archivos, red) con acceso al servidor, y el dictado de un capturador llega al modelo. La app le pide no
usar herramientas y tratar dictado e imágenes solo como datos, pero eso es una instrucción, no un
bloqueo. Configurar en el envoltorio: sin herramientas (o solo lectura de los archivos adjuntos de la
sesión), un usuario del sistema sin permiso de lectura sobre `sites/*/site_config.json` ni el bench, un
directorio de trabajo vacío por sesión, escucha solo en `127.0.0.1` y el token obligatorio.

#### Alternativa: Anthropic directo

Solo si se elige **Proveedor de IA = Anthropic directo** (el campo de la API key aparece con esa opción):

1. Entrar a https://console.anthropic.com con la cuenta de la empresa.
2. **[CONFIRMAR]** Que la organización tiene crédito o un método de pago, y definir un límite de gasto
   mensual (Settings → Limits). Costo estimado: un llamado por captura, hasta 4 fotos y
   `max_tokens = 4096` (spec R10).
3. Settings → API Keys → **Create Key**, nombre `erpnext-captura-prod`. Se recomienda un workspace propio
   para esta app, así se puede limitar y revocar por separado.
4. Copiar la clave y pegarla directo en Configuracion Captura → **API key Anthropic**. No guardarla en
   archivos, chats ni correos.
5. **[CONFIRMAR]** El modelo por defecto es `claude-sonnet-5-5` (campo **Modelo**). Si "Probar conexión
   IA" responde que el modelo no existe, poner el identificador de un modelo disponible para la cuenta
   (la consola de Anthropic los lista).

### 3.2 Cuenta de servicio de Google y la hoja

1. https://console.cloud.google.com → crear el proyecto `erpnext-captura` (o usar uno existente de la
   empresa).
2. **APIs & Services → Library**: habilitar **Google Sheets API** y también **Google Drive API** (gspread
   la usa para algunas operaciones de metadatos).
3. **IAM & Admin → Service Accounts → Create service account**: nombre `erpnext-captura`. No necesita
   roles del proyecto (el acceso se da compartiendo la hoja).
4. En la cuenta creada → **Keys → Add key → Create new key → JSON**. Se descarga un archivo `.json`
   (unos 2,3 KB) con `client_email` y `private_key`.
   - Si aparece "Service account key creation is disabled", la organización de Google Workspace tiene la
     política `iam.disableServiceAccountKeyCreation` (spec R7). **[CONFIRMAR]** Pedir una excepción al
     administrador de Google Workspace o crear el proyecto con una cuenta de Google fuera de esa
     organización.
5. Crear la hoja en Google Sheets (por ejemplo "Captura de productos - Items"). Copiar el **ID** de la
   URL: `https://docs.google.com/spreadsheets/d/<ESTE_ES_EL_ID>/edit`.
6. En la hoja → **Compartir** → pegar el `client_email` de la cuenta de servicio
   (`erpnext-captura@<proyecto>.iam.gserviceaccount.com`) con permiso **Editor** y desmarcar "Notificar".
7. En Configuracion Captura:
   - **Credenciales de la cuenta de servicio (JSON)**: abrir el `.json` con el Bloc de notas, copiar
     **todo** el contenido y pegarlo.
   - **ID del spreadsheet**: el ID del punto 5.
   - **Prefijo de la pestaña**: `Items` (cada exportación crea pestañas nuevas con fecha y hora).
8. Guardar y **borrar el `.json` descargado** de la PC (y vaciar la papelera). Si se pierde, se crea otra
   clave y se revoca la anterior.

### 3.3 URL base de las imágenes y `host_name`

La exportación al Sheet pone en la columna Image la URL pública de la foto. Corre en un trabajo en segundo
plano, sin una petición web de la que tomar el dominio: si no se configura nada, Frappe puede armar URLs
como `http://default/files/...`, que no sirven para Data Import.

1. En Configuracion Captura → **URL base de las imágenes**:
   `https://erpnext-213997-0.cloudclusters.net` (sin `/` final). Afecta solo a esta app. **Recomendado.**
2. Opcional, para todo el sitio (enlaces de correos, redirecciones). Primero ver si ya está definido:

   ```bash
   erpnext$ grep host_name sites/default/site_config.json
   # Si no aparece:
   erpnext$ bench --site default set-config host_name https://erpnext-213997-0.cloudclusters.net
   erpnext$ bench --site default clear-cache
   ```

   **[CONFIRMAR]** Cambia la configuración de todo el sitio. Es el valor correcto, pero hay que decidirlo.

### 3.4 Probar las conexiones

En Configuracion Captura, **Guardar** y luego menú **Probar**:

- **Probar conexión IA**: debe responder "Conexión OK con la API local (...)" con duración y costo (o,
  con Anthropic directo, el modelo). Errores típicos en la tabla de la sección 6.
- **Probar Google Sheet**: debe abrir la hoja y confirmar que la puede editar.

(Requiere que el bug B2 de QA esté corregido; ver "Bloqueantes".)

### 3.5 Valores por defecto, prefijos y glosario

`after_install` ya cargó lo que encontró. Revisar:

| Campo | Valor esperado en producción |
|---|---|
| Grupo por defecto | 1.9 Bebidas Embotelladas |
| UOM por defecto | Unidad |
| Empresa | Estacion Tradicion |
| Almacén por defecto | Depósito Bebidas - ET |
| Idioma del dictado | es-BO |

- **Prefijo por grupo**: la app trae solo `1.9 Bebidas Embotelladas → BEB`. **[CONFIRMAR]** El usuario debe
  cargar el prefijo (2 a 5 letras A–Z) de cada Item Group que se vaya a capturar. Los 17 grupos del
  Bloque C están en Stock → Item Group. Si un grupo no tiene prefijo, la app usa las 3 primeras letras
  del nombre del grupo y agrega una advertencia. Si existe el Custom Field `custom_formato_venta` en Item
  (Bloque C lo creó), completar también **Formato venta** en cada fila.
- **Glosario de marcas**: trae 10 marcas (Coca-Cola, Del Valle, Frut-All, ...). Agregar las marcas que se
  dictan mal con frecuencia: en **Dicho**, las formas mal escritas separadas por coma; en **Forma
  correcta**, el nombre oficial; **Abreviatura** (2 a 10 caracteres A–Z y 0–9) solo si la regla
  automática no da el código deseado.
- **Si en el futuro se cambian los grupos** y faltan los enlaces por defecto, se pueden completar sin
  pisar lo cargado:

  ```bash
  erpnext$ bench --site default execute captura_productos.install.aplicar_configuracion_por_defecto
  ```

### 3.6 Usuarios capturadores

Los capturadores son **Website User**: no entran al Desk, después del login van directo a `/captura`.

1. Desk → **User → Add User**: email, nombre y **User Type = Website User**.
2. En **Roles**, marcar solo **Capturador de Productos**.
3. Contraseña: si el sitio tiene correo saliente configurado, marcar "Send Welcome Email". Si no
   (**[CONFIRMAR]**), fijarla desde el servidor y entregarla en persona:

   ```bash
   erpnext$ bench --site default set-password <email_del_capturador> '<contraseña_temporal>'
   ```

4. Supervisores: los usuarios que revisan, aprueban, exportan y crean Items necesitan **Item Manager** o
   **Stock Manager**. **[CONFIRMAR]** Qué usuarios reales son supervisores (por ejemplo `compras@` y
   `propietario@`) y que tengan uno de esos roles.

### 3.7 Entrar desde el celular e instalar como app

1. En el celular (Chrome en Android; Safari en iPhone) abrir
   `https://erpnext-213997-0.cloudclusters.net/login`, entrar con el usuario capturador. Redirige a
   `/captura`.
2. Aceptar los permisos de **cámara** y **micrófono** cuando los pida. Funcionan porque el sitio tiene
   HTTPS.
3. Instalar como app:
   - **Android (Chrome):** menú ⋮ → **Agregar a pantalla principal** (o **Instalar app**).
   - **iPhone (Safari):** botón Compartir → **Agregar a inicio**.
   Queda el ícono "Captura" que abre `/captura` a pantalla completa. Si la sesión expira, muestra el
   login y después vuelve.
4. En iPhone la voz depende de que Safari soporte el dictado; si no, la app ofrece "Prefiero escribir".

---

## 4. Ajustes del sistema a revisar

Revisar en **System Settings** (y en Website Settings) antes de dar acceso a los capturadores. Ninguno
lo cambia la app.

| Ajuste | Dónde | Qué necesita la app | Si no se cumple |
|---|---|---|---|
| **Only allow System Managers to upload public files** (`only_allow_system_managers_to_upload_public_files`) | System Settings → Files | Desactivado. Las fotos se guardan públicas (decisión 12.3 de la spec) | El capturador no puede subir fotos; la app muestra un aviso y registra "Captura de productos: fotos públicas bloqueadas" en Error Log. **[CONFIRMAR]** Desactivarlo permite a cualquier usuario con permiso de adjuntar subir archivos públicos. |
| **Max File Size** (`max_file_size`) | System Settings → Files, o `max_file_size` en `site_config.json` (en bytes) | 2 MB o más (el celular reduce las fotos y la app rechaza más de 2 MB) | Las fotos se rechazan. El valor por defecto de Frappe es mucho mayor. |
| **Allowed File Extensions** | System Settings → Files | Vacío, o que incluya `jpg`, `jpeg` y `png` | Las fotos se rechazan. |
| Tamaño máximo del cuerpo en nginx (`client_max_body_size`) | Proxy de CloudClusters | 3 MB o más | Error 413 al subir. No se puede ver ni cambiar desde el bench; solo si aparece el error. |
| Zona horaria | System Settings | America/La_Paz (Bloque A ya la configuró) | Fechas corridas en las listas. |
| **Disable Signup** | Website Settings | Activado (recomendado) | Cualquiera puede crear un Website User. No obtiene acceso a `/captura` (no tiene el rol), pero es superficie innecesaria. |
| Session Expiry | System Settings | El valor por defecto sirve | Si es muy corto, el capturador tiene que volver a entrar seguido. |

```bash
# Consulta rápida de solo lectura
erpnext$ bench --site default execute frappe.get_system_settings --args "['only_allow_system_managers_to_upload_public_files']"
erpnext$ bench --site default execute frappe.get_system_settings --args "['max_file_size']"
erpnext$ grep max_file_size sites/default/site_config.json sites/common_site_config.json
```

### Permisos atípicos de esta instancia

En esta instancia, **un solo Custom DocPerm en un DocType reemplaza todos los permisos de fábrica** de
ese DocType (hallazgo 4 de `CONTEXTO_SESION.md`), y el rol `Sales User` está recortado.

- La app **no crea Custom DocPerm** ni toca DocTypes existentes: sus permisos van en el JSON de sus
  propios DocTypes.
- **No editar los permisos de "Captura de Producto" ni de "Configuracion Captura" con el Role Permissions
  Manager.** Eso crea Custom DocPerm y deja de usar los de la app (por ejemplo, el capturador podría
  perder el acceso "If Owner"). Si hace falta cambiar un permiso, pedirlo al programador para que lo
  cambie en el JSON del DocType y salga con la siguiente versión.
- "Crear Item" exige que el supervisor tenga permiso de **crear Item** (y la app crea Brand e Item
  Attribute Value si faltan). Revisar si Item, Brand o Item Attribute tienen Custom DocPerm en esta
  instancia:

  ```bash
  erpnext$ bench --site default execute frappe.get_all --kwargs '{"doctype": "Custom DocPerm", "filters": {"parent": ["in", ["Item", "Brand", "Item Attribute", "Item Barcode", "File"]]}, "fields": ["parent", "role", "permlevel", "read", "write", "create"]}'
  ```

  Si la lista no está vacía, comprobar que los roles de los supervisores tengan `create` en Item.
  **Probar "Crear Item" con el usuario real del supervisor, no con Administrator** (spec R9).

---

## 5. Actualizaciones y rollback

### 5.1 Publicar una versión nueva (en la PC)

```bash
# Tests en verde en dev, luego:
git tag -a v1.0.1 -m "Descripción corta"
git push origin main v1.0.1
```

### 5.2 Actualizar en producción (método recomendado)

`bench update` compila los assets de todas las apps (OOM) e intenta `bench restart` (falla sin `sudo`),
así que se hace a mano, paso a paso:

```bash
# 0. Backup (sección 1.5), en horario sin ventas
erpnext$ cd /cloudclusters/erpnext/frappe-bench
erpnext$ bench --site default backup --with-files

# 1. Traer el código de la versión nueva
erpnext$ cd apps/captura_productos
erpnext$ git remote -v                      # bench get-app lo llama "upstream"
erpnext$ git describe --tags                # versión actual: anotarla para el rollback
erpnext$ git fetch upstream --tags
erpnext$ git checkout v1.0.1
erpnext$ cd ../..

# 2. Dependencias (por si cambió pyproject.toml)
erpnext$ ./env/bin/pip install -e apps/captura_productos

# 3. Migrar, compilar solo esta app y limpiar caché
erpnext$ bench --site default migrate
erpnext$ bench build --app captura_productos
erpnext$ bench --site default clear-cache

# 4. Reiniciar (como root) y verificar
erpnext$ exit
root# supervisorctl restart all
root# supervisorctl status
```

**Primera versión con la API local de IA:** el `migrate` corre un patch que cambia **Proveedor de IA** a
"API local". Antes del paso 4, configurar `claude_api_url` y `claude_api_token` (sección 3.1, pasos 1 a
3); si no, las capturas nuevas terminan en Error con "Falta claude_api_token en site_config". Para seguir
con la API de Anthropic, volver a elegir "Anthropic directo" en Configuracion Captura (la API key
guardada se conserva).

Después, la prueba de humo (sección 6). El reinicio es necesario siempre que cambie código Python: el
web, los workers y el scheduler no recargan solos. Si la versión solo cambia JS/CSS, alcanza con `build`
y `clear-cache`.

**Alternativa:** `bench update --apps captura_productos --pull` hace solo el `git pull` de esta app (si
la versión de bench instalada acepta `--apps`). Después seguir desde el paso 2. No usar `bench update`
sin `--pull`.

### 5.3 Rollback

De menor a mayor impacto. Elegir siempre la opción más leve que resuelva el problema.

**A. Volver a la versión anterior del código** (problema introducido por una actualización):

```bash
erpnext$ cd /cloudclusters/erpnext/frappe-bench/apps/captura_productos
erpnext$ git checkout v1.0.0              # la versión anotada con "git describe" antes de actualizar
erpnext$ cd ../.. && ./env/bin/pip install -e apps/captura_productos
erpnext$ bench --site default migrate && bench build --app captura_productos && bench --site default clear-cache
root# supervisorctl restart all
```

`migrate` no borra columnas que la versión nueva haya agregado; los datos de las capturas se conservan.

**B. Desactivar el acceso sin desinstalar:** quitar el rol "Capturador de Productos" a los usuarios (o
deshabilitarlos). La app queda instalada pero nadie captura.

**C. [DESTRUCTIVO] Desinstalar la app del sitio.** Borra los DocTypes de la app **y todas las capturas**
(con sus tablas hijas), Configuracion Captura (claves incluidas) y el rol. **No** borra los Items, Brands
ni atributos que ya se crearon, ni las fotos en `/files`. Hace un backup automático antes.

```bash
erpnext$ bench --site default uninstall-app captura_productos --dry-run   # ver qué borraría
erpnext$ bench --site default uninstall-app captura_productos              # pide confirmación
erpnext$ bench --site default clear-cache
root# supervisorctl restart all
# Opcional, para quitar también el código del bench (otro paso destructivo):
erpnext$ bench remove-app captura_productos
```

**D. [DESTRUCTIVO] Restaurar el backup completo.** Último recurso. **Pierde todo lo que se registró en el
sitio después del backup, incluidas las ventas del POS.** Solo si la base quedó dañada y A–C no alcanzan.
Si el backup es anterior a la instalación, después de restaurar la app sigue en el bench pero no en el
sitio; para quitar también el código: `bench remove-app captura_productos`.

```bash
erpnext$ bench --site default restore sites/default/private/backups/<fecha>-default-database.sql.gz \
           --with-public-files sites/default/private/backups/<fecha>-default-files.tar \
           --with-private-files sites/default/private/backups/<fecha>-default-private-files.tar
erpnext$ bench --site default migrate
root# supervisorctl restart all
```

---

## 6. Prueba de humo y problemas conocidos

### 6.1 Checklist posterior al despliegue

Usar un usuario capturador de prueba y el usuario real de un supervisor. Marcar cada paso.

1. [ ] **Instalación y procesos.** `bench --site default list-apps` muestra `captura_productos`;
   `supervisorctl status` todo en RUNNING; `bench --site default scheduler status` habilitado; hay un
   worker con la cola `long`.
2. [ ] **El resto del sitio sigue bien.** Abrir el Desk y `/pos-prime`; hacer una consulta normal (no
   hace falta vender). Revisar `free -m`.
3. [ ] **Assets.** `curl -sI https://erpnext-213997-0.cloudclusters.net/assets/captura_productos/manifest.json`
   responde `200`. En el celular, `/captura` carga en menos de 3 s con 4G y sin errores.
4. [ ] **Configuración.** "Probar conexión IA" y "Probar Google Sheet" responden OK.
5. [ ] **Captura completa en el celular.** Con un producto real: foto, dictado (o "Prefiero escribir"),
   escanear el código de barras y Enviar. Debe pasar de "Procesando" a **"Por revisar" en 90 s o menos**
   (Mis capturas → refrescar). Si se queda en Procesando, ver 6.2.
6. [ ] **Revisión.** Con el supervisor real, en el Desk: abrir la captura, revisar nombre y código,
   **Aprobar**.
7. [ ] **Exportar.** "Exportar a Google Sheet": aparecen las pestañas nuevas en la hoja. Abrir la URL de
   la columna Image en una ventana de incógnito: debe mostrar la foto y empezar con
   `https://erpnext-213997-0.cloudclusters.net/files/`.
8. [ ] **Errores.** Desk → Error Log: sin errores nuevos de `captura_productos`.

> **[CONFIRMAR]** "Crear Item" y Data Import crean Items **reales** en producción. Probarlos solo con un
> producto que de verdad se vaya a dar de alta, y con el usuario del supervisor (no Administrator). Las
> capturas de prueba que no sirvan se pueden pasar a Descartado o borrar desde el Desk.

### 6.2 Problemas conocidos

| Síntoma | Causa probable | Solución |
|---|---|---|
| `bench build` termina con `Killed` / exit 137 | Falta de memoria al compilar todas las apps | `bench build --app captura_productos` |
| `bench restart`: `sudo: command not found` | El contenedor no tiene `sudo` | `root# supervisorctl restart all` |
| `supervisorctl`: `PermissionError` / `error: <class 'PermissionError'>` | Se ejecutó como `erpnext` | Ejecutarlo como `root` |
| `bench` muestra un `WARN` y no hace nada | Se ejecutó como `root` | `su - erpnext` y `cd /cloudclusters/erpnext/frappe-bench` |
| Capturas en "Procesando" que no avanzan | No hay worker de la cola `long`, el scheduler está apagado o el worker se cayó | Sección 2.5. Logs: `tail -n 100 logs/worker.error.log logs/scheduler.log`. Desk → RQ Job (estado del trabajo `captura-ia-<CAP-...>`). A los 20 minutos `rescatar_atascadas` las pasa a Error y se pueden reprocesar. |
| "El servicio de IA local no responde" (se reintenta y luego Error) | Envoltorio caído, reiniciado sin supervisor, en otro puerto, o 502/504 de Claude Code | `curl -s http://127.0.0.1:8787/openapi.json` desde el contenedor; `supervisorctl status`; revisar `claude_api_url`. Con 504 frecuentes, subir **Timeout API local** |
| "Token de la API local inválido o ausente (HTTP 401)" o "Falta claude_api_token en site_config" | Token mal copiado, cambiado en el envoltorio, o no configurado | `bench --site default set-config claude_api_token '<TOKEN>'`, "Probar conexión IA" y reprocesar |
| "Imagen demasiado grande para la API local (HTTP 413)" | Un archivo de más de 50 MB (no debería pasar: las fotos se reducen a 1280 px) | Revisar las fotos de la captura |
| "La respuesta de la API local no trae un objeto JSON válido" / "no cumple el esquema" | El modelo respondió texto en vez de JSON | Se reintenta solo (30 s y 120 s); el texto crudo queda en **Respuesta IA**. Si se repite, probar con otro **Modelo** |
| Capturas en "Error" con mensaje de autenticación (401) de Anthropic | API key mal copiada o revocada (solo con Anthropic directo) | Cargar la key de nuevo, Guardar, "Probar conexión IA" y reprocesar |
| Error de modelo inexistente (404) | `modelo_ia` no disponible para la cuenta | Cambiar **Modelo** en Configuracion Captura |
| Error de crédito o límite (400/429) | Sin crédito o límite de gasto alcanzado (Anthropic directo) | Consola de Anthropic → Billing / Limits. Los errores transitorios se reintentan solos cada minuto. |
| "Probar Google Sheet" da 403 / "permission denied" | La hoja no está compartida con el `client_email`, o falta habilitar la API | Compartir como Editor (3.2, paso 6); habilitar Sheets API y Drive API |
| "Probar Google Sheet" da 404 | ID del spreadsheet mal copiado | Copiar solo la parte entre `/d/` y `/edit` |
| "Service account key creation is disabled" | Política de la organización de Google (R7) | 3.2, paso 4 |
| URLs de imagen en el Sheet con `http://default/...` o `localhost` | Falta la URL base | 3.3 |
| `/captura` pide un `captura.bundle.*.js` que da 404, o no se ven los cambios nuevos | `assets.json` en caché | `bench --site default clear-cache`; en el celular, recargar o cerrar y abrir la app |
| El capturador no puede subir fotos ("fotos públicas bloqueadas" en Error Log) | System Settings solo permite archivos públicos a System Manager | Sección 4 |
| Error 413 al subir una foto | Límite de cuerpo del proxy | Sección 4 (consultar a CloudClusters) |
| La cámara o el micrófono no se activan | Permiso denegado en el navegador | Ajustes del sitio en el navegador → permitir cámara y micrófono. Siempre por HTTPS. |
| No reconoce la voz en iPhone | Safari sin `webkitSpeechRecognition` | "Prefiero escribir" (HU-05) |
| La lista "Mis capturas" no se actualiza sola | Normal: sin tiempo real si socketio no llega al celular | Refrescar |
| El capturador entra al Desk o ve "Not permitted" en `/captura` | User Type o rol mal configurado | User Type = Website User y rol "Capturador de Productos" (3.6) |
| "Crear Item" dice que no hay permiso | Custom DocPerm en Item sin `create` para el rol del supervisor | Sección 4, "Permisos atípicos". No agregar permisos sin revisar el efecto en los demás roles. |
| `install-app` o `migrate` falla a mitad | Error de la app o falta de memoria | Leer el error completo; reintentar `migrate`. Si no se resuelve, rollback 5.3 A o C. |
| El sitio da timeouts después de reiniciar | Poca RAM: los procesos tardan en arrancar | Esperar 1–2 minutos y revisar `supervisorctl status` y `free -m` |

---

## Historial de despliegues

| Fecha | Versión (tag) | Frappe / ERPNext en prod | Quién | Notas |
|---|---|---|---|---|
| | | | | |
