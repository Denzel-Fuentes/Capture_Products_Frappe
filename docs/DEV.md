# Entorno de desarrollo: captura_productos

App Frappe `captura_productos` ("Captura de Productos", módulo **Captura Productos**) sobre ERPNext v16,
corriendo en el stack Docker local `erpnext-dev`.

## Resumen

| Pieza | Valor |
|---|---|
| Frappe / ERPNext | 16.52.0 (rama `version-16`) |
| Python del bench | 3.14.8 (`/workspace/frappe-bench/env`) |
| Node | 24.22.0 |
| Compose | `C:\Users\denze\OneDrive\Documentos\ERPCOCINAESTACIONTRADICION\local-dev\docker-compose.yml` (proyecto `erpnext-dev`) |
| Contenedor del bench | `erpnext-dev-frappe-1` (imagen `frappe/bench`, PID 1 = `sleep infinity` bajo tini) |
| Bench | `/workspace/frappe-bench` dentro del volumen `erpnext-dev-bench` |
| Código de la app | `C:\Users\denze\OneDrive\Documentos\App-Frappe` (este repo), **bind mount** en `/workspace/frappe-bench/apps/captura_productos` |
| Sitio | `dev.localhost` (developer_mode 1, allow_tests 1), servido en http://localhost:8000 |
| Login | `Administrator` / `admin` (solo desarrollo local) |
| Dependencias Python de la app | `anthropic` 2.6.0, `gspread` 6.2.1, `google-auth` 2.48.0 (declaradas en `pyproject.toml`); `requests` y `werkzeug` vienen con Frappe |
| IA en dev | API local **falsa** (`scripts/fake_claude_api.py`, ver "IA con la API local falsa") |

Frappe y ERPNext viven en el volumen (rápido); solo esta app está en bind mount, así lo que se edita
en Windows lo ve el bench al instante y lo que genera el bench (DocTypes exportados por developer_mode)
cae directo en el repo.

## Arrancar y parar

Desde **Git Bash** en la raíz del repo:

```bash
scripts/dev-start.sh            # levanta el stack + "bench start" en segundo plano y espera a que responda :8000
scripts/dev-start.sh status     # contenedores, si bench start corre y si :8000 responde
scripts/dev-start.sh logs       # sigue logs/bench-start.log (web, socketio, watch, schedule, worker)
scripts/dev-start.sh shell      # bash dentro del contenedor, en /workspace/frappe-bench
scripts/dev-start.sh stop       # detiene bench start (contenedores siguen arriba)
scripts/dev-start.sh down       # detiene bench start y los contenedores (no borra volúmenes)
```

Luego abrir http://localhost:8000 → Administrator / admin.

Equivalente manual (PowerShell, sin conversión de rutas que preocupar):

```powershell
docker compose -f "C:\Users\denze\OneDrive\Documentos\ERPCOCINAESTACIONTRADICION\local-dev\docker-compose.yml" up -d
docker exec -d -w /workspace/frappe-bench erpnext-dev-frappe-1 bash -lc "exec bench start >> logs/bench-start.log 2>&1"
curl.exe http://localhost:8000/api/method/ping      # {"message":"pong"}
```

Para ejecutar comandos bench sueltos:

```powershell
docker exec -it -w /workspace/frappe-bench erpnext-dev-frappe-1 bash
# ya dentro:
bench --site dev.localhost list-apps
```

> En Git Bash anteponer `MSYS_NO_PATHCONV=1` a cualquier `docker exec -w /workspace/...`,
> si no, Git Bash convierte `/workspace` en `C:/Program Files/Git/workspace` y falla con
> "Cwd must be an absolute path".

## Ciclo de desarrollo

Todos los comandos, dentro del contenedor en `/workspace/frappe-bench`:

```bash
# Tras crear/modificar DocTypes, patches o hooks
bench --site dev.localhost migrate

# Assets (JS/CSS de captura_productos/public). "bench start" ya corre "bench watch";
# si un cambio no se refleja o se agrega un *.bundle.js nuevo:
bench build --app captura_productos

bench --site dev.localhost clear-cache
bench --site dev.localhost console          # ipython con frappe cargado
```

- Los cambios en `.py` recargan el servidor web solo (werkzeug, "Restarting with stat"; probado con
  ediciones hechas desde Windows). El worker y el scheduler **no** recargan: tras cambiar código de jobs
  en cola (`frappe.enqueue`) reiniciar con `scripts/dev-start.sh stop && scripts/dev-start.sh`.
- **Scheduler**: la app tiene tareas programadas (`hooks.scheduler_events`): `reencolar_reintentos`
  (cada minuto, reintentos de la IA a los 30 s y 120 s) y `rescatar_atascadas` (cada 15 min, pasa a Error
  las capturas en Procesando sin job). Necesitan el scheduler del sitio activo; en `dev.localhost` se
  activó el 05/11/2026 (`bench --site dev.localhost enable-scheduler`; para volver atrás,
  `disable-scheduler`). Ver estado: `bench --site dev.localhost scheduler status`.
- **Exportación masiva**: la acción de la lista encola un job (cola `long`); sin worker corriendo no
  avanza. El resultado llega como notificación (campana) al supervisor.
- Con `developer_mode 1`, los DocTypes creados desde el Desk con módulo "Captura Productos" se exportan
  a `captura_productos/captura_productos/doctype/...` dentro de este repo.

### Dependencias Python

Se declaran en `pyproject.toml` → `[project].dependencies` y se instalan en el env del bench:

```bash
./env/bin/pip install -e apps/captura_productos
./env/bin/python -c "import anthropic, gspread, google.auth; print('ok')"
```

(`bench setup requirements --python` también sirve, pero reinstala las de todas las apps.)

## Tests

`allow_tests` ya está activado en `dev.localhost` (si se recrea el sitio:
`bench --site dev.localhost set-config allow_tests true`).

```bash
bench --site dev.localhost run-tests --app captura_productos
bench --site dev.localhost run-tests --app captura_productos --module captura_productos.tests.test_naming
bench --site dev.localhost run-tests --doctype "Captura de Producto"
bench --site dev.localhost run-tests --app captura_productos --failfast --coverage
```

Detalle de cada corrida: `logs/frappe.testing.log` (o `--verbose`). Los tests corren contra la base de
`dev.localhost`; Frappe hace rollback de lo que crean, pero conviene no depender de datos manuales.

## IA con la API local falsa (`scripts/fake_claude_api.py`)

En producción la IA usa por defecto la **API local**: un envoltorio HTTP de Claude Code que corre en el
mismo contenedor que ERPNext (spec sección 6). En dev se reemplaza por un servidor **falso** que imita el
contrato (`POST /v1/query` multipart, `POST /v1/query/json`, `DELETE /v1/sessions/{id}`,
`/openapi.json`, `/docs`), exige `Authorization: Bearer <token>` y devuelve en `result` un JSON plausible
de `registrar_producto` (alternando JSON puro, envuelto en ```json y con texto alrededor). No llama a
ninguna IA ni cuesta nada. Solo usa werkzeug (ya está en el env del bench).

Levantarlo dentro del contenedor, en 127.0.0.1:8787, y configurar `dev.localhost` (el token es un valor
de prueba generado en el momento; no se imprime):

```bash
docker exec -it -w /workspace/frappe-bench erpnext-dev-frappe-1 bash
# ya dentro:
TOKEN=$(./env/bin/python -c "import secrets; print(secrets.token_urlsafe(32))")
bench --site dev.localhost set-config claude_api_url http://127.0.0.1:8787
bench --site dev.localhost set-config claude_api_token "$TOKEN"
FAKE_CLAUDE_API_TOKEN="$TOKEN" nohup ./env/bin/python apps/captura_productos/scripts/fake_claude_api.py \
    > logs/fake-claude-api.log 2>&1 &
curl -s http://127.0.0.1:8787/openapi.json        # responde el falso
```

Si el servidor ya estaba levantado con otro token, apagarlo y volver a levantarlo con el token que
quedó en site_config:
`FAKE_CLAUDE_API_TOKEN=$(./env/bin/python -c "import json; print(json.load(open('sites/dev.localhost/site_config.json'))['claude_api_token'])")`.

Después de cambiar código del extractor, reiniciar worker y scheduler (`scripts/dev-start.sh stop &&
scripts/dev-start.sh`). En Configuración Captura: **Proveedor de IA = API local** y **Probar → Probar
conexión IA** (hace `GET /openapi.json` y una consulta "Responde solo: OK" con `claude-haiku-5-5`).

- **Simular fallos**: con la variable `FAKE_CLAUDE_MODO` al levantarlo (`401`, `404`, `413`, `502`,
  `504`, `JSON_INVALIDO`, `ESQUEMA`, `IS_ERROR`, `LENTO`) o escribiendo una palabra clave en el dictado
  de la captura: `FAKE_502`, `FAKE_401`, `FAKE_504`, `FAKE_JSON_INVALIDO`, `FAKE_ESQUEMA`,
  `FAKE_IS_ERROR`, `FAKE_LENTO` (duerme `FAKE_CLAUDE_LENTO_S`, 20 s por defecto). Un 502 deja la captura
  en Procesando con reintento a los 30 s y 120 s (lo encola el scheduler) y después pasa a Error.
- **Ver sesiones** (solo el falso): `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/v1/sessions`
  → `{"vivas": [...], "borradas": [...]}`. Tras cada consulta la app borra la sesión, así que `vivas`
  debe quedar vacía.
- **Log**: `logs/fake-claude-api.log` (no registra el token).
- **Apagarlo**: `pkill -f fake_claude_api.py` (dentro del contenedor). Las capturas que se envíen con el
  servidor apagado reintentan dos veces ("El servicio de IA local no responde") y terminan en Error.
- **Volver a Anthropic directo** (por ejemplo, para probar con una API key real): elegirlo en
  Configuración Captura; la API key solo se muestra con esa opción.

## Pantalla /captura (usuario de prueba)

- Página: http://localhost:8000/captura (archivo `captura_productos/www/captura.html`). Exige sesión y
  un rol de la app (Capturador de Productos, Item Manager, Stock Manager o System Manager). No depende
  del Desk ni del asistente de configuración de ERPNext.
- Usuario de prueba (solo dev, rol **Capturador de Productos**, Website User, sin Desk):
  `capturador.demo@example.com` (contraseña en `docs/DEV.local.md`, que no se sube a git). Al entrar, `role_home_page` lo manda a `/captura`.
  Para regenerar la contraseña: `bench --site dev.localhost set-password capturador.demo@example.com <nueva>`.
- JS/CSS: `captura_productos/public/js/captura.bundle.js` (+ `public/js/captura/*.js`) y
  `public/css/captura.bundle.css`. Tras cambiarlos: `bench build --app captura_productos` (o dejar
  correr `bench watch`) y `bench --site dev.localhost clear-cache`: el mapa `assets.json` queda en
  caché y, sin limpiar, la página sigue pidiendo el bundle con el hash anterior. ZXing va vendorizado en `public/js/vendor/zxing-browser-0.2.1/` y solo se
  carga si el navegador no tiene `BarcodeDetector`.
- En la PC no hay cámara ni micrófono: para probar el flujo, el campo "Prefiero escribir", "Escribir el
  código a mano" y "Elegir de la galería" cubren los mismos pasos.

## Probar desde el celular (HTTPS con cloudflared)

Cámara, micrófono y `BarcodeDetector` exigen HTTPS. Se usa un **Cloudflare Quick Tunnel** (sin cuenta,
URL aleatoria `*.trycloudflare.com` que cambia cada vez), igual que en
`ERPCOCINAESTACIONTRADICION\local-dev\start-mcp-publico.ps1`. `cloudflared` 2026.10.3 ya está instalado en
`C:\Program Files (x86)\cloudflared\cloudflared.exe`.

> **Antes de abrir el túnel**: el sitio queda accesible desde internet y el Administrator tiene la
> contraseña `admin`. Cambiarla mientras el túnel esté arriba, y preferir probar con un usuario de rol
> limitado (p. ej. "Capturador de Productos"):
> `bench --site dev.localhost set-admin-password <otra-clave>`

1. Con `scripts/dev-start.sh` corriendo, en PowerShell:

   ```powershell
   & "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --no-autoupdate --url http://localhost:8000
   ```

   En la salida aparece `https://<algo>.trycloudflare.com` (puede tardar unos segundos en responder).
   No hace falta `--http-host-header`: `common_site_config.json` tiene `serve_default_site: true` y
   `default_site: dev.localhost`, así que Frappe sirve `dev.localhost` para cualquier Host (verificado).

2. Para que las URLs absolutas que arma Frappe (redirecciones, enlaces en correos, URL pública de las
   fotos que se exporta al Google Sheet) usen el túnel:

   ```bash
   bench --site dev.localhost set-config host_name https://<algo>.trycloudflare.com
   ```

3. En el celular abrir `https://<algo>.trycloudflare.com/login` y luego `/captura`.

4. Al terminar: `Ctrl+C` en la ventana de cloudflared (o `Get-Process cloudflared | Stop-Process`, ojo
   que eso también corta el túnel del MCP si está corriendo), y quitar el host_name:

   ```bash
   bench --site dev.localhost set-config host_name ""
   bench --site dev.localhost set-admin-password admin   # si se cambió
   ```

Limitación: solo se publica el puerto 8000; socketio (9000) no pasa por el túnel, así que no hay
actualizaciones en tiempo real en el celular (no afecta la captura; refrescar la lista "Mis capturas").

## Reconstruir desde cero

Si se pierde el volumen `erpnext-dev-bench`:

1. `docker compose ... up -d` y dentro del contenedor `bash /ruta/setup-bench.sh`
   (`ERPCOCINAESTACIONTRADICION\local-dev\setup-bench.sh`; copiarlo con `docker cp`). **Antes** de eso,
   comentar temporalmente el bind mount de `captura_productos` en el compose: `bench init` falla si
   `frappe-bench/` ya existe (el bind mount lo crea vacío).
2. Reactivar el bind mount, `docker compose ... up -d --force-recreate frappe`, y dentro:

   ```bash
   cd /workspace/frappe-bench
   ./env/bin/pip install -e apps/captura_productos
   grep -qx captura_productos sites/apps.txt || printf '\ncaptura_productos\n' >> sites/apps.txt
   bench --site dev.localhost install-app captura_productos
   bench --site dev.localhost set-config allow_tests true
   ```

## Cambios hechos al compose (05/11/2026)

Backup previo: `local-dev\docker-compose.yml.bak-20261105` (para volver atrás: restaurarlo y
`docker compose up -d --force-recreate frappe`).

- `volumes`: bind mount `C:/Users/denze/OneDrive/Documentos/App-Frappe` → `/workspace/frappe-bench/apps/captura_productos`.
- `environment`: `GIT_CONFIG_COUNT/KEY_0/VALUE_0` = `safe.directory` para el bind mount.
- `init: true` en el servicio `frappe`.

## Problemas encontrados y decisiones

- **Exited (137) de hace 13 días**: no fue falta de memoria (`OOMKilled=false`). `sleep infinity` como
  PID 1 ignora SIGTERM, así que al parar Docker Desktop el contenedor terminaba con SIGKILL (137); además
  no recogía procesos zombie de `bench start` (quedaba un `[esbuild] <defunct>`). Solución: `init: true`
  (tini como PID 1).
- **"detected dubious ownership" de git dentro del contenedor**: el bind mount desde Windows aparece como
  `root:root 777` y el bench corre como `frappe`. Solución persistente con `GIT_CONFIG_*` en el compose
  (sobrevive a recrear el contenedor, a diferencia de `git config --global`).
- **Bind mount desde OneDrive**: funciona (lectura/escritura, recarga de Python por stat). Precauciones:
  - Marcar la carpeta `App-Frappe` como "Mantener siempre en este dispositivo" en OneDrive; un archivo
    solo-en-la-nube no se puede leer desde el contenedor.
  - Python escribe `__pycache__/` dentro del repo (lo ignora `.gitignore`, pero OneDrive los sincroniza).
  - Rendimiento: el acceso a archivos por el bind mount de Windows es más lento que el volumen; para una
    app chica no se nota. Si algún día molesta, alternativa: mover el repo fuera de OneDrive
    (p. ej. `C:\dev\App-Frappe`) y cambiar solo la ruta `source` del compose.
  - Si `bench watch` no detecta un cambio de JS hecho desde Windows, correr
    `bench build --app captura_productos`.
- **Line endings**: `.gitattributes` fuerza LF (el código corre en Linux; un `.sh` con CRLF falla) y el
  repo tiene `core.autocrlf=false`. `.editorconfig` del scaffold también pide LF.
- **`bench new-app` es interactivo**: se le alimentaron las respuestas por stdin. Se generó con título
  "Captura Productos" para que el módulo sea "Captura Productos" (carpeta `captura_productos/captura_productos`)
  y después se cambió `app_title` en `hooks.py` a "Captura de Productos".
- **`app_email` es un placeholder** (`dev@estaciontradicion.example` en `hooks.py` y `pyproject.toml`):
  reemplazar por el correo real del publisher.
- **`sites/apps.txt`** no tenía salto de línea final; `bench new-app` agregó la app correctamente
  (`frappe`, `captura_productos`, `erpnext`).
- **Imagen `frappe/bench:latest`** sin versión fijada: aceptable en dev, pero un `docker pull` puede traer
  otra versión de bench/node/python. Para producción no aplica (CloudClusters no usa este compose).
- **Puertos 8000-8005 y 9000-9005 publicados en 0.0.0.0** (accesibles desde la red local). MariaDB y Redis
  no están publicados (bien). Si no se necesita acceso por LAN, cambiar a `127.0.0.1:8000-8005:8000-8005`
  (cloudflared corre en el host y sigue funcionando).
- **Setup wizard de ERPNext sin completar en `dev.localhost`** (05/11/2026): no hay Company, UOM ni Item
  Groups salvo "All Item Groups", y el Desk redirige todo a `/desk/build/setup-wizard`. Para revisar un
  formulario sin completar el wizard (que crearía Company, cuentas, etc.), en la consola del navegador:
  `frappe.boot.setup_complete = 1; frappe.set_route("Form", "Captura de Producto", "new")` (solo afecta
  a esa pestaña). La zona horaria del sitio quedó en Asia/Kolkata por el mismo motivo; la spec pide
  America/La_Paz (se fija al completar el wizard o en System Settings).
- **Configuracion Captura en dev**: `after_install` no corrió porque la app se instaló antes de existir;
  se aplicó a mano con `captura_productos.install.after_install` (idempotente). Grupo, UOM, empresa,
  almacén y el prefijo BEB quedan vacíos hasta que existan "1.9 Bebidas Embotelladas", "Unidad", etc.;
  volver a correr `captura_productos.install.aplicar_configuracion_por_defecto` para completarlos.
- **Tests**: crean sus propios maestros (Item Group, UOM, Brand, Item Attribute, usuarios) en
  `captura_productos/tests/utils.py` y hacen rollback por test (savepoint) y por clase. Las capturas "de un
  capturador" se preparan con `insertar_de(usuario, doc)` (el rol ya no puede crear por el ORM/REST) y
  los tests de nombres ocultan dentro de su transacción los Items `BEB-%`, `DES-%` y `GAL-%` del sitio
  (`aislar_codigos`), así no fallan por datos de pruebas manuales (B12). Los Item de
  prueba se insertan con `db_insert()` porque sin Company las validaciones de Item fallan.
- **Fotos públicas y System Settings** (CR-B8): las fotos de `/captura` se guardan como archivos
  públicos (spec 12.3). Si en System Settings se activa *Only allow System Managers to upload public
  files* (`only_allow_system_managers_to_upload_public_files`), Frappe rechaza la foto del capturador
  aunque se inserte con `ignore_permissions`. `api.subir_foto` lo detecta: el capturador ve "No se pudo
  guardar la foto por un ajuste del sistema. Avisa al administrador." y queda un Error Log con el
  detalle. Solución: desactivar ese ajuste en el sitio (en `dev.localhost` está en 0).
- **URL de las imágenes en el Sheet**: completar `URL base de imágenes` en Configuración Captura
  (p. ej. el dominio público o el túnel). Si está vacía, se usa la URL del sitio solo si su host parece
  válido; dentro del job de exportación puede salir `http://default` y entonces la columna Image queda
  vacía con un aviso.
- **Ejecutar Python suelto contra el sitio** (sin `bench console` interactivo): copiar un script con
  `frappe.init(site="dev.localhost", sites_path="."); frappe.connect()` al contenedor y correrlo con
  `docker exec -w /workspace/frappe-bench/sites erpnext-dev-frappe-1 ../env/bin/python /tmp/script.py`.
- **Memoria**: Docker Desktop tiene ~6.7 GB; con `bench start` el contenedor frappe usa ~1.9 GB (esbuild
  watch ~700 MB) y hay otros stacks (`legalteach`, `iam`) usando ~2.5 GB. Si hay presión, parar
  `bench start` cuando no se use o los stacks ajenos.
