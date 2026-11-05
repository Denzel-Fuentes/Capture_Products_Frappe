# Especificación funcional: `captura_productos` (ERPNext v16)

| Dato | Valor |
|---|---|
| Versión | 1.0, 2026-11-05 |
| Negocio | Estación Tradición (Bolivia), Company `Estacion Tradicion`, moneda BOB, zona horaria America/La_Paz |
| Base | Plan aprobado `usando-nuestrso-agentes-vamos-silly-phoenix.md`. Este documento lo concreta y no lo reemplaza. |
| Módulo | Captura Productos |

---

## 0. Problema, objetivo y métricas

**Problema:** el personal que arma el Excel de ítems escribe mal los nombres, no sigue la convención `PREFIJO-MARCA-VARIANTE-MEDIDA` y tarda mucho.

**Objetivo:** capturar desde el celular con foto, voz y código de barras. La IA estructura los datos, Python compone el nombre y el código con una plantilla fija y el supervisor aprueba y exporta.

| Métrica (medida en las primeras 4 semanas de uso) | Meta |
|---|---|
| Tiempo de captura de un producto simple (de abrir `/captura` a "Enviado") | ≤ 2 min (mediana) |
| Tiempo de Enviar a "Por revisar" | ≤ 90 s (p95) |
| Capturas aprobadas sin editar `item_name` ni `item_code` | ≥ 80 % |
| Filas del Sheet importadas por Data Import sin error | 100 % |
| Códigos de barras duplicados que llegan a ERPNext | 0 |

---

## 1. Usuarios, roles y permisos

| Rol | Quién | Acceso |
|---|---|---|
| **Capturador de Productos** (rol nuevo, va en `fixtures/role.json`) | Personal de depósito o caja | Solo `/captura`. No tiene acceso al Desk (`desk_access = 0`). |
| **Supervisor** = `Item Manager` o `Stock Manager` (roles existentes) | Encargado de compras o propietario | Desk: revisa, corrige, aprueba, exporta y crea Items. También puede usar `/captura`. |
| **System Manager** | Administrador | Todo lo anterior, más `Configuracion Captura`. |

### Matriz de permisos por DocType

| DocType | Capturador | Item Manager / Stock Manager | System Manager |
|---|---|---|---|
| Captura de Producto | Solo **Read** con **If Owner** (desde 2026-11-05, revisión CR-A1). Crea, guarda y envía únicamente por los endpoints de `api.py`, que validan dueño y estado Borrador y guardan con `ignore_permissions`. Sin Create/Write por REST ni Delete. No ve `error_log` ni `respuesta_ia` (permlevel 1). | Read, Write, Delete, Report, Export; Read en permlevel 1 (`error_log`, `respuesta_ia`) | Todos (Read en permlevel 1) |
| Captura Foto / Codigo Barras / Variante (tablas hijas) | Heredan del padre | Heredan del padre | Heredan del padre |
| Configuracion Captura (Single) | Sin acceso | Sin acceso (contiene secretos) | Read, Write |
| Item, Brand, Item Attribute | Sin acceso. La validación de duplicados corre en el servidor. | Según sus permisos estándar. "Crear Item" exige `has_permission("Item", "create")`. | Todos |

**Reglas:**
- Quien no es supervisor solo puede usar como foto (`fotos.foto`, `variantes.foto`) archivos adjuntos a la misma captura y subidos con `subir_foto` (`{captura}-{hash}.jpg|png`, públicos, ≤ 2 MB); lo valida `validate()`. El job de IA solo lee fotos públicas adjuntas a la captura, nunca por ruta.
- `capturado_por` es siempre el usuario de la sesión, salvo que lo fije un supervisor.
- Los métodos del controlador en la whitelist que cambian estado son solo de supervisores y aplican el límite por minuto (`enviar_desde_formulario`, `reprocesar_ia`); `enviar()` y `reintentar()` no están en la whitelist (los usa `api.py`).
- Los permisos se definen en el JSON de los DocTypes nuevos. **No se crean Custom DocPerm** en DocTypes existentes, porque en esta instancia un Custom DocPerm anula los permisos de fábrica (ver hallazgo 4 de `CONTEXTO_SESION.md`).
- Los nombres de los DocTypes van **sin tildes** (`Captura Codigo Barras`, `Configuracion Captura`) para coincidir con las carpetas del plan. Las etiquetas visibles sí llevan tildes.

### Endpoints whitelisted (`api.py`)

Todos exigen login y uno de los roles anteriores.

| Endpoint | Uso | Regla de permiso |
|---|---|---|
| `crear_captura()` | Crea una captura en estado Borrador al abrir el paso 1 | Cualquier rol de la app |
| `subir_foto(captura, es_principal, variante_idx)` | Adjunta una imagen JPEG o PNG de 2 MB como máximo, ya reducida en el navegador | Dueño de la captura y estado Borrador |
| `verificar_codigo(barcode, captura)` | Valida el dígito verificador y busca duplicados en menos de 1 s | Cualquier rol de la app |
| `guardar_paso(captura, datos)` | Guarda la transcripción, los códigos y las variantes | Dueño y estado Borrador |
| `enviar_captura(captura)` | Pasa de Borrador a Procesando y encola la IA | Dueño y estado Borrador. Límite: 30 llamadas por minuto por usuario. |
| `mis_capturas(limit=50)` | Lista las capturas propias de los últimos 30 días | Capturador: solo las propias |

---

## 2. Historias de usuario

**C** = Capturador, **S** = Supervisor, **A** = System Manager.

### HU-01 (C): Captura de un producto simple
**Historia:** Como capturador quiero tomar fotos, dictar y escanear un producto para registrarlo sin escribir.

- **Dado** que abro `/captura` en Chrome Android por HTTPS, **cuando** toco "Fotos", **entonces** se abre la cámara trasera, cada foto se reduce a 1280 px de lado mayor antes de subir y veo su miniatura. Puedo tomar entre 1 y 6 fotos y la primera queda como principal.
- **Dado** el paso "Dictar", **cuando** toco el micrófono y digo "coca cola sin azúcar de tres litros", **entonces** veo el texto en vivo y puedo usar "Repetir" o "Siguiente".
- **Dado** el paso "Código", **cuando** apunto a un EAN-13 válido, **entonces** el código se lee en menos de 3 s, suena un pitido y lo veo en la lista. Puedo agregar hasta 5 códigos o tocar "No tiene código".
- **Dado** que respondo "No" en "¿Tiene variantes?" y toco "Enviar", **entonces** veo "Enviado ✓" en menos de 2 s y la captura aparece en "Mis capturas" como **Procesando**.
- **Dado** que tengo 0 fotos o una transcripción de menos de 3 caracteres, **cuando** toco "Enviar", **entonces** el botón no avanza y se resalta el paso que falta.

### HU-02 (C): Captura con variantes
**Historia:** Como capturador quiero registrar todos los sabores, tamaños o colores de un producto en una sola captura para no repetir el producto base.

- **Dado** que respondo "Sí" en "¿Tiene variantes?", **cuando** elijo el tipo (Sabor, Tamaño, Color u Otro), **entonces** se crea la variante 1 con la foto principal y el código del paso 3, y solo me pide dictar su valor ("¿Qué sabor es este?").
- **Dado** que toco "Agregar otra variante", **entonces** se repite la secuencia foto, dictado corto ("sabor durazno") y código. Se permiten de 1 a 20 variantes y todas comparten el mismo tipo.
- **Dado** que envío con 3 variantes, **entonces** la captura tiene 3 filas en `variantes`, cada una con su código de barras y su foto.

### HU-03 (C/S): Código de barras o código de ítem duplicado
- **Dado** que escaneo un código que ya está en `Item Barcode`, **entonces** veo en menos de 1 s "Este producto ya existe: Coca-Cola 500 ml (BEB-COCA-500ML)" y el código **no se agrega**. Puedo cancelar la captura.
- **Dado** que escaneo un código que está en otra Captura de Producto (en cualquier estado salvo Creado), **entonces** veo el aviso "Ya fue capturado en CAP-2026-00012" y puedo agregarlo igual. La captura queda con la advertencia.
- **Dado** que escaneo el mismo código dos veces en la misma captura, **entonces** se ignora el segundo y veo "Ya escaneado".
- **Dado** que el `item_code` compuesto ya existe como Item o en otra captura activa, **entonces** se le agrega un sufijo (`-2`, `-3`, …) y aparece la advertencia "Ya existe BEB-COCA-2L 'Coca-Cola 2 L': verifique si es el mismo producto".

### HU-04 (C): Código mal leído
- **Dado** un código de 8, 12 o 13 dígitos cuyo dígito verificador es inválido, **entonces** se rechaza con "Código mal leído, escanee de nuevo".
- **Dado** un código de otra longitud (por ejemplo, el código interno `130885`), **entonces** se acepta, se guarda con `barcode_type` vacío y se marca con una advertencia.
- **Dado** que el escaneo falla 3 veces, **entonces** aparece el botón "Escribir número", que abre un teclado numérico grande.

### HU-05 (C): Navegador sin soporte de voz
- **Dado** que `webkitSpeechRecognition` no existe o se negó el permiso del micrófono, **cuando** llego a "Dictar", **entonces** veo directamente un cuadro de texto grande con el ejemplo "Coca Cola Sin Azúcar 3 litros" y la captura se guarda con `metodo_entrada = Texto`.
- **Dado** que el reconocimiento de voz se corta por falta de red, **entonces** se conserva el texto parcial y veo "Sin conexión: toque para reintentar o escriba".

### HU-06 (C): Mis capturas y errores de red
- **Dado** que pierdo la red durante la captura, **entonces** cada foto o paso que no se pudo guardar muestra "No enviado ↻" y se reintenta al tocarlo. Lo que ya se subió queda en el servidor como Borrador.
- **Dado** que cierro el navegador a mitad de la captura, **cuando** vuelvo a `/captura`, **entonces** "Mis capturas" muestra el Borrador con el botón "Continuar".
- **Dado** que abro "Mis capturas", **entonces** veo mis capturas de los últimos 30 días con foto, nombre (o "Procesando…") y estado en colores: Borrador gris, Procesando azul, Por revisar amarillo, Error rojo y Aprobado, Exportado o Creado en verde.

### HU-07 (S): Revisión en el Desk
- **Dado** que abro la lista de Captura de Producto, **entonces** veo por defecto el filtro `estado = Por revisar` y las columnas de foto, `item_name`, `item_code`, `capturado_por`, `confianza_ia` y `estado`.
- **Dado** que abro una captura, **entonces** veo una galería con todas las fotos (de la captura y de las variantes), la transcripción, los campos extraídos y `advertencias_ia` resaltadas en amarillo. Si `confianza_ia` es menor que 60, aparece un banner rojo.
- **Dado** que corrijo `marca`, `medida_valor` o cualquier campo extraído y guardo, **entonces** `item_name` e `item_code` **no cambian** hasta que use "Recalcular nombre".

### HU-08 (S): Recalcular nombre
- **Dado** una captura en Por revisar o Aprobado, **cuando** toco "Recalcular nombre", **entonces** se recomponen `item_name` e `item_code` del padre y de cada variante con la plantilla vigente (sección 5), sin llamar a la IA. Antes se pide confirmación si el valor actual difiere del que se calcularía.
- **Dado** que la plantilla de Configuración cambió, **entonces** Recalcular usa la plantilla nueva.

### HU-09 (S/C): Reprocesar IA
- **Dado** una captura en Por revisar o Error, **cuando** el supervisor toca "Reprocesar IA" y confirma "Se sobrescribirán los campos extraídos", **entonces** el estado pasa a Procesando, `intentos_ia` sube en 1 y, al terminar, se sobrescriben los campos extraídos y compuestos.
- El capturador también puede reprocesar sus propias capturas en Error, desde "Mis capturas", con el botón "Reintentar".

### HU-10 (S): Aprobar
- **Dado** una captura en Por revisar sin errores bloqueantes (ver regla siguiente), **cuando** toco "Aprobar", **entonces** pasa a Aprobado y se registran `aprobado_por` y `aprobado_en`.
- **Errores bloqueantes:**
  1. `item_code`, `item_name`, `item_group` o `stock_uom` vacíos.
  2. `item_code` que no cumple la regex `^[A-Z0-9][A-Z0-9.\-]{1,39}$` o que ya existe en Item.
  3. `item_name` más largo que el máximo configurado.
  4. Un código de barras que ya existe en `Item Barcode`.
  5. Con `tiene_variantes = 1`: 0 variantes, variantes con valor vacío o repetido, `item_code` repetido entre variantes, o códigos de barras en la tabla del padre.
- **Dado** que existe un error bloqueante, **entonces** el botón muestra la lista de errores y el estado no cambia. Las advertencias no bloquean.

### HU-11 (S): Exportar a Google Sheet
- **Dado** que selecciono N capturas Aprobado (N ≤ 200) en la lista, **cuando** ejecuto la acción "Exportar a Google Sheet", **entonces**:
  1. Se revalidan las capturas. Las que fallan se excluyen y se informan.
  2. Si `crear_maestros_al_exportar = 1`, se crean los Brand, Item Attribute e Item Attribute Value (con su `abbr`) que falten.
  3. Se crean las pestañas `{pestana}-{AAAAMMDD-HHMM}-1-Productos` y, si hay variantes, `…-2-Variantes`, con el formato de la sección 7.
  4. Cada captura pasa a **Exportado** con `hoja_exportacion`, `fila_sheet` y `exportado_en`.
- La acción masiva de la lista corre en segundo plano (cola `long`): el supervisor recibe una notificación con el resultado (exportadas, excluidas y motivos). Las capturas se bloquean mientras se exportan (no se exportan dos veces) y, si algo falla después de crear las pestañas, se borran. Dos capturas de la misma selección con el mismo `item_code` no se exportan juntas: la segunda se excluye.
- Si `url_base_imagenes` está vacía y la URL del sitio no tiene un host válido (por ejemplo `http://default` dentro del job), la columna Image queda vacía y se avisa "Configure 'URL base de imágenes' en Configuración Captura".
- **Dado** que la hoja no está compartida con el email de la cuenta de servicio, **entonces** veo "Comparta la hoja con xxx@yyy.iam.gserviceaccount.com como Editor", ninguna captura cambia de estado y el detalle queda en `error_log`.
- **Dado** que descargo la pestaña 1 como XLSX o CSV, la importo con Data Import (Item, *Insert New Records*) y después hago lo mismo con la pestaña 2, **entonces** la importación termina en estado **Success** con 0 filas omitidas.

### HU-12 (S): Crear Item directo
- **Dado** una captura en Aprobado o Exportado y un usuario con permiso de crear Item, **cuando** toco "Crear Item", **entonces** se revalida la captura con las reglas de HU-10 y, en **una sola transacción**, se crea el Item simple o plantilla y las variantes (con `create_variant`, y luego se fijan `item_code`, `item_name`, `barcodes` e `image`). La captura pasa a **Creado** con `item` enlazado y cada variante con su `item`.
- **Maestros (decisión 2026-11-05, prevalece la sección 13):** los Brand e Item Attribute Value que falten se crean solo si `crear_maestros_al_exportar = 1`, igual que al exportar. Si está en 0 y falta alguno, se muestra un error claro con la lista de lo que falta ("Faltan maestros: Falta la marca X en Brand; …"), no se crea nada y el estado no cambia.
- **Dado** que ya existe un Item con ese `item_code` y sus códigos de barras incluyen los de la captura (porque ya se importó por Sheet), **entonces** no se crea nada, se enlaza el Item existente y la captura pasa a Creado. Si la captura (o la variante) no tiene códigos de barras, solo se enlaza si coinciden `item_name`, `item_group` y, en plantillas, los atributos; si no, error claro.
- **Dado** que el `item_code` existe con otros códigos de barras, o que hay cualquier error, **entonces** se hace rollback total, el estado no cambia y el detalle queda en `error_log`.

### HU-13 (sistema): Errores de la IA
- **Dado** un error transitorio de Claude (429, 500, 529 o timeout de 60 s), **entonces** se reintenta 2 veces, a los 30 s y a los 120 s. Cada ejecución del job hace un solo intento (sin dormir en el worker ni mantener la transacción abierta durante la llamada): deja `reintentar_ia_en` y la tarea programada `reencolar_reintentos` (cada minuto) vuelve a encolar el job a esa hora, así la espera real es de 30–90 s y 120–180 s. Requiere el scheduler activo.
- **Dado** que una captura queda en Procesando más de 20 minutos sin job en cola (worker caído, Redis sin el job), **entonces** la tarea `rescatar_atascadas` (cada 15 min) la pasa a Error con el motivo en `error_log`, y el supervisor también puede usar "Reprocesar IA" desde Procesando si no hay job.
- **Dado** un error definitivo (401 API key inválida, 400, JSON que no cumple el esquema tras los reintentos, o falta la API key), **entonces** la captura pasa a **Error**. `error_log` guarda el código y el mensaje, nunca la API key. Se crea un Error Log de Frappe y el capturador ve "No se pudo procesar. El supervisor lo revisará".
- **Dado** que la IA devuelve un `item_group` o `stock_uom` que no está en la lista, **entonces** se usan los valores por defecto de Configuración y se agrega una advertencia (la captura no pasa a Error).

### HU-14 (A): Configuración
- **Dado** que guardo `Configuracion Captura` con un `{placeholder}` desconocido en una plantilla, **entonces** se rechaza con "Placeholder no válido: {x}".
- **Dado** que agrego "coca cola, cocacola, coca" → "Coca-Cola" (abreviatura COCA) al glosario, **entonces** las capturas siguientes con cualquiera de esas formas salen como "Coca-Cola" y `BEB-COCA-…`.
- **Dado** que toco "Probar conexión IA" o "Probar Google Sheet", **entonces** veo OK o el error concreto en menos de 15 s.

---

## 3. Máquina de estados (`estado`)

```
(nueva) → Borrador → Procesando → Por revisar → Aprobado → Exportado → Creado
                         ↓  ↑           ↑   ↓        ↓   ↑ (re-exportar)
                         Error ─────────┘   └────────┴→ Creado
```

| # | Desde → Hacia | Disparador | Quién | Precondición o efecto |
|---|---|---|---|---|
| T1 | (nueva) → Borrador | Abrir el paso 1 de `/captura` (`crear_captura`) | C, S | `capturado_por = sesión` |
| T2 | Borrador → Procesando | "Enviar" | C (dueño), S | ≥ 1 foto y transcripción de ≥ 3 caracteres. Encola el job en la cola `long`. |
| T3 | Procesando → Por revisar | Job OK | Sistema | Guarda los campos de IA, compone nombre y código y valida (sección 5) |
| T4 | Procesando → Error | Job falla (HU-13) | Sistema | `error_log` |
| T5 | Error → Procesando | "Reprocesar IA" o "Reintentar" | S, C (dueño) | `intentos_ia += 1` |
| T6 | Por revisar → Procesando | "Reprocesar IA" | S | Confirmación previa (sobrescribe) |
| T6b | Procesando → Procesando | "Reprocesar IA" | S | Solo si no hay job en cola ni corriendo (captura atascada). Vuelve a encolar. |
| T4b | Procesando → Error | Tarea `rescatar_atascadas` | Sistema | Más de 20 min sin cambios y sin job |
| T7 | Por revisar → Aprobado | "Aprobar" | S | Sin errores bloqueantes (HU-10) |
| T8 | Aprobado → Por revisar | "Volver a revisión" | S | Ninguna |
| T9 | Aprobado → Exportado | "Exportar a Google Sheet" | S | HU-11. Si el export falla, se queda en Aprobado y se escribe `error_log`. |
| T10 | Exportado → Aprobado | "Marcar para re-exportar" | S | Limpia `fila_sheet` y `hoja_exportacion` |
| T11 | Aprobado o Exportado → Creado | "Crear Item" | S con permiso de crear Item | HU-12. Si falla, no cambia el estado y se escribe `error_log`. |
| — | Cualquier estado salvo Creado → (borrado) | Delete | S, A | Una captura Creado no se borra. |

**Reglas generales:**
- `estado` es de solo lectura en el formulario. Solo cambia mediante los botones y endpoints anteriores (lo valida `validate()` comparando con `doc_before_save`).
- **Error** se reserva para fallos de la IA. Los fallos de exportación o creación no cambian el estado.
- El capturador solo edita en estado Borrador.

---

## 4. DocTypes, campo por campo

En la columna **Llena**: C = Capturador, IA = extractor, Sis = sistema (Python), S = Supervisor y A = System Manager. "S edita" indica que el supervisor puede corregir el valor.

### 4.1 Captura de Producto

Configuración del DocType: `autoname = CAP-.YYYY.-.#####`, no submittable, `title_field = item_name`, `image_field = imagen_principal`, `track_changes = 1`, `sort_field = modified`.

| fieldname | Etiqueta | Tipo Frappe | Opciones / default | Oblig. | Llena |
|---|---|---|---|---|---|
| `estado` | Estado | Select | Borrador\nProcesando\nPor revisar\nAprobado\nExportado\nCreado\nError; default Borrador; read_only, in_list_view, in_standard_filter | Sí | Sis |
| `capturado_por` | Capturado por | Link | User; default `__user`; read_only | Sí | Sis |
| `fecha_captura` | Fecha de captura | Datetime | default Now; read_only | Sí | Sis |
| `metodo_entrada` | Método de entrada | Select | Voz\nTexto; read_only | No | Sis |
| **Sección Entrada** | | | | | |
| `transcripcion` | Lo que se dictó | Small Text | Mínimo 3 caracteres para enviar | Al enviar | C (S edita) |
| `imagen_principal` | Foto principal | Attach Image | read_only. Se copia de la foto con `es_principal = 1`. | No | Sis |
| `fotos` | Fotos | Table | Captura Foto (de 1 a 6 filas) | Al enviar | C |
| `codigos_barras` | Códigos de barras | Table | Captura Codigo Barras (de 0 a 5 filas) | No | C (S edita) |
| `tiene_variantes` | ¿Tiene variantes? | Check | default 0 | No | C (la IA puede sugerir y S edita) |
| `variantes` | Variantes | Table | Captura Variante (de 0 a 20 filas); depends_on `tiene_variantes` | Si `tiene_variantes` | C, IA (S edita) |
| **Sección Datos extraídos** | | | | | |
| `tipo_producto` | Tipo de producto | Data | Ej. Gaseosa, Jugo, Agua | No | IA (S edita) |
| `marca` | Marca | Data | Forma corregida por el glosario o por un Brand existente | No | IA (S edita) |
| `marca_corta` | Marca para el código | Data | 2 a 10 caracteres `[A-Z0-9]` | No | Sis (S edita) |
| `variante_descripcion` | Variante del producto | Data | Ej. "Sin Azucar". Vacío si es plantilla. | No | IA (S edita) |
| `variante_corta` | Variante para el código | Data | 2 a 12 caracteres `[A-Z0-9]` | No | Sis (S edita) |
| `medida_valor` | Medida | Float | precision 2; > 0 | No | IA y Sis (normaliza; S edita) |
| `medida_unidad` | Unidad de medida | Select | \nml\nL\ng\nkg\nu | Si hay valor | IA y Sis (S edita) |
| `item_group` | Grupo de producto | Link | Item Group (solo hojas) | Al aprobar | IA (S edita) |
| `stock_uom` | Unidad de stock | Link | UOM; default desde Configuración ("Unidad") | Al aprobar | IA o Sis (S edita) |
| `descripcion` | Descripción | Small Text | 1 a 2 frases en español | No | IA (S edita) |
| `confianza_ia` | Confianza IA | Percent | 0 a 100; read_only | No | IA |
| `advertencias_ia` | Advertencias | Small Text | Una por línea; read_only para C | No | IA y Sis |
| **Sección Nombre y código** | | | | | |
| `item_name` | Nombre del ítem | Data | length 140; el máximo efectivo se configura | Al aprobar | Sis (S edita) |
| `item_code` | Código del ítem | Data | length 140; regex en HU-10 | Al aprobar | Sis (S edita) |
| **Sección Resultado** (colapsable) | | | | | |
| `aprobado_por` | Aprobado por | Link | User; read_only | No | Sis |
| `aprobado_en` | Aprobado en | Datetime | read_only | No | Sis |
| `hoja_exportacion` | Pestaña del Sheet | Data | read_only | No | Sis |
| `fila_sheet` | Fila del Sheet | Int | Primera fila de la captura en la pestaña 1; read_only | No | Sis |
| `exportado_en` | Exportado en | Datetime | read_only | No | Sis |
| `item` | Item creado | Link | Item; read_only | No | Sis |
| **Sección Técnica** (colapsable, solo S y A) | | | | | |
| `intentos_ia` | Intentos IA | Int | default 0; read_only | No | Sis |
| `modelo_ia` | Modelo usado | Data | read_only | No | Sis |
| `respuesta_ia` | Respuesta IA (JSON) | Code | options JSON; read_only | No | Sis |
| `error_log` | Último error | Long Text | read_only | No | Sis |

### 4.2 Captura Foto (tabla hija, `istable = 1`)

| fieldname | Etiqueta | Tipo | Opciones | Oblig. | Llena |
|---|---|---|---|---|---|
| `foto` | Foto | Attach Image | Archivo **público** (`is_private = 0`) para poder usarlo como imagen del Item; JPEG; ≤ 2 MB | Sí | C |
| `es_principal` | Principal | Check | Exactamente una fila con 1. Si ninguna la tiene, se usa la primera. | No | C o Sis |

### 4.3 Captura Codigo Barras (tabla hija)

| fieldname | Etiqueta | Tipo | Opciones | Oblig. | Llena |
|---|---|---|---|---|---|
| `barcode` | Código | Data | Solo dígitos o `[A-Z0-9-]`, de 4 a 20 caracteres; único dentro de la captura | Sí | C |
| `barcode_type` | Tipo | Select | \nEAN-13\nEAN-8\nUPC-A (son opciones válidas de `Item Barcode`) | No | Sis (13, 8 o 12 dígitos con verificador válido; si no, vacío) |
| `origen` | Origen | Select | Escaneado\nManual | No | Sis |
| `duplicado_en` | Ya capturado en | Data | Nombre de otra captura (solo aviso); read_only | No | Sis |

### 4.4 Captura Variante (tabla hija)

| fieldname | Etiqueta | Tipo | Opciones | Oblig. | Llena |
|---|---|---|---|---|---|
| `transcripcion` | Lo que se dictó | Small Text | Ej. "sabor durazno, dos litros" | No | C |
| `valor_1` | Valor atributo 1 | Data | Valor para el atributo de la fila 1 de `atributos` (sección 13), Title Case, ej. "Durazno" | Sí al aprobar | IA (S edita) |
| `valor_corto_1` | Abrev. atributo 1 | Data | 2 a 12 caracteres `[A-Z0-9.]` | No | Sis (S edita) |
| `valor_2` / `valor_corto_2` | Valor / abrev. atributo 2 | Data | Solo si hay 2.º atributo | Sí al aprobar si existe el atributo | IA / Sis |
| `valor_3` / `valor_corto_3` | Valor / abrev. atributo 3 | Data | Solo si hay 3.er atributo | Sí al aprobar si existe el atributo | IA / Sis |
| `barcode` | Código de barras | Data | Mismas reglas que 4.3; uno por variante en el MVP | No | C |
| `barcode_type` | Tipo | Select | \nEAN-13\nEAN-8\nUPC-A | No | Sis |
| `foto` | Foto | Attach Image | Pública | No | C |
| `item_name` | Nombre del ítem | Data | | Sí al aprobar | Sis (S edita) |
| `item_code` | Código del ítem | Data | | Sí al aprobar | Sis (S edita) |
| `item` | Item creado | Link | Item; read_only | No | Sis |

### 4.5 Configuracion Captura (Single, `issingle = 1`, solo System Manager)

| fieldname | Etiqueta | Tipo | Default / opciones | Oblig. |
|---|---|---|---|---|
| **IA** | | | | |
| `anthropic_api_key` | API key Anthropic | Password | Nunca se envía al cliente | Sí |
| `modelo_ia` | Modelo | Data | `claude-sonnet-5-5` | Sí |
| `max_fotos_ia` | Fotos enviadas a la IA | Int | 4 (la principal más 3) | Sí |
| `timeout_ia_seg` | Timeout IA (s) | Int | 60 | Sí |
| `instrucciones_extra_ia` | Instrucciones extra para la IA | Long Text | Vacío | No |
| `idioma_voz` | Idioma del dictado | Select | es-BO\nes-419\nes-ES; default es-BO | Sí |
| **Google Sheets** | | | | |
| `google_service_account_json` | Credenciales de la cuenta de servicio (JSON) | Password | Unos 2,3 KB. En un Single no aplica el límite de 140 caracteres; verificarlo en dev. | Sí para exportar |
| `spreadsheet_id` | ID del spreadsheet | Data | | Sí para exportar |
| `pestana_sheet` | Prefijo de la pestaña | Data | `Items` | Sí |
| `url_base_imagenes` | URL base de las imágenes | Data | Ej. `https://erpnext-213997-0.cloudclusters.net`. Si está vacío, se usa la ruta relativa `/files/…`. | No |
| `crear_maestros_al_exportar` | Crear Brand y atributos faltantes al exportar | Check | 1 | No |
| **Nombre y código** | | | | |
| `plantilla_nombre` | Plantilla de nombre | Data | `{marca} {variante} {medida}` | Sí |
| `plantilla_codigo` | Plantilla de código | Data | `{prefijo}-{marca_corta}-{variante_corta}-{medida}` | Sí |
| `quitar_tildes_nombre` | Quitar tildes y ñ del nombre | Check | 1 (convención actual: "Sin Azucar", "La Cabana") | No |
| `estilo_mayusculas` | Mayúsculas del nombre | Select | Título\nComo se dictó; default Título | Sí |
| `largo_max_nombre` | Largo máximo del nombre | Int | 80 | Sí |
| `largo_max_codigo` | Largo máximo del código | Int | 40 | Sí |
| `atributos_de_medida` | Atributos que son medida | Data | `Tamaño,Tamano,Presentacion` | No |
| `prefijo_defecto` | Prefijo si el grupo no tiene uno | Data | `PRD` | Sí |
| **Valores por defecto del Item** | | | | |
| `item_group_defecto` | Grupo por defecto | Link Item Group | Cargar "1.9 Bebidas Embotelladas" | Sí |
| `uom_defecto` | UOM por defecto | Link UOM | Cargar "Unidad" | Sí |
| `empresa` | Empresa | Link Company | "Estacion Tradicion" | No |
| `almacen_defecto` | Almacén por defecto | Link Warehouse | "Depósito Bebidas - ET" | No |
| `is_stock_item` | Mantener stock | Check | 1 | No |
| `is_sales_item` | Permitir venta | Check | 1 | No |
| `is_purchase_item` | Permitir compra | Check | 1 | No |
| **Tablas** | | | | |
| `prefijos` | Prefijo por grupo | Table | Prefijo por Grupo | No |
| `glosario_marcas` | Glosario de marcas | Table | Glosario de Marcas | No |

**Botones del Single:** "Probar conexión IA" y "Probar Google Sheet" (HU-14).

### 4.6 Prefijo por Grupo (tabla hija)

| fieldname | Tipo | Reglas | Ejemplo |
|---|---|---|---|
| `item_group` | Link Item Group | Obligatorio y único en la tabla | 1.9 Bebidas Embotelladas |
| `prefijo` | Data | Obligatorio; 2 a 5 caracteres `[A-Z]` | BEB |
| `formato_venta` | Data | Opcional. Valor para `custom_formato_venta` si ese campo existe en Item. | Bebida |

### 4.7 Glosario de Marcas (tabla hija)

| fieldname | Tipo | Reglas | Ejemplo |
|---|---|---|---|
| `dicho` | Small Text | Obligatorio. Formas dictadas o mal escritas, separadas por coma. Se comparan sin tildes ni mayúsculas. | coca cola, cocacola, coca |
| `forma_correcta` | Data | Obligatorio | Coca-Cola |
| `abreviatura` | Data | Opcional; 2 a 10 caracteres `[A-Z0-9]`. Si se define, reemplaza la regla automática de `marca_corta`. | COCA |

**Glosario inicial, derivado de los ítems actuales:**

| Forma correcta | Abreviatura |
|---|---|
| Coca-Cola | COCA |
| Del Valle | VALLE |
| Frut-All | FRUTALL |
| Super Frut | SUPERFRUT |
| Del Tropico | TROPICO |
| La Cabana | CABANA |
| Guarana Antarctica | GUARANA |
| Aquarius | (regla automática) |
| Pepsi | (regla automática) |
| Mendocina | (regla automática) |

---

## 5. Reglas de nombre y código

### 5.1 Placeholders

| Placeholder | En el nombre | En el código |
|---|---|---|
| `{prefijo}` | (no se usa) | Prefijo del `item_group` según la tabla de prefijos; si no hay, `prefijo_defecto` |
| `{tipo}` | `tipo_producto` en Title Case | `tipo_producto` en mayúsculas ASCII, sin espacios, cortado a 10 caracteres |
| `{marca}` | `marca`. Si está vacía, se usa `{tipo}`. | (usar `{marca_corta}`) |
| `{marca_corta}` | n/a | Regla 5.4. Si la marca está vacía, se aplica la regla sobre `tipo_producto`. |
| `{variante}` | `variante_descripcion` (padre) o `valor` (variante) | (usar `{variante_corta}`) |
| `{variante_corta}` | n/a | Regla 5.5 |
| `{medida}` | Forma de nombre: `500 ml`, `1.5 L`, `36 g`, `1 kg`, `6 u` | Forma de código: `500ML`, `1.5L`, `36G`, `1KG`, `6U` |

**Composición:**
- Un placeholder vacío se elimina junto con su separador adyacente. Los espacios y guiones repetidos se colapsan y se recortan los extremos.
- En una **variante**, `{variante}` y `{variante_corta}` toman el valor de la variante. Si el atributo está en `atributos_de_medida`, el valor se interpreta como medida (regla 5.2) y va en `{medida}` en lugar de `{variante}`.
- La **plantilla** (Item con variantes) se compone con `{variante}` vacío.

### 5.2 Normalización de unidades

Primero se intenta la medida que entrega la IA. Python además analiza `texto_original` y, si los resultados no coinciden, agrega una advertencia y se queda con el valor de Python.

| Entrada (dictada o escrita) | Normalizada |
|---|---|
| "500 ml", "500ml", "500 cc", "500 centímetros cúbicos", "medio litro" | 500 ml |
| "cuarto litro", "cuarto de litro" | 250 ml |
| "litro", "un litro", "1 lt", "1 l", "1000 ml" | 1 L |
| "litro y medio", "1,5 litros", "1.5 lts", "1500 ml" | 1.5 L |
| "dos litros y medio", "2,5 L" | 2.5 L |
| "tres litros", "3 lts" | 3 L |
| "medio kilo", "500 gr", "500 grs", "500 gramos" | 500 g |
| "kilo", "un kilo", "1000 g", "1 kg" | 1 kg |
| "kilo y medio" | 1.5 kg |
| "6 unidades", "media docena", "paquete de 6" | 6 u |
| "docena" | 12 u |
| "onzas", "libras", "galón", "6 x 500 ml" | Sin medida, con la advertencia "Unidad no soportada: revise" |

**Reglas de conversión y formato:**
- A partir de 1000 ml se expresa en L y a partir de 1000 g en kg.
- Por debajo de 1 L se expresa en ml y por debajo de 1 kg en g.
- Máximo 2 decimales, separador `.` (convención actual "1.5L") y sin ceros finales (1.50 se escribe 1.5).
- En la entrada se acepta la coma como separador decimal.
- Para escribir la unidad en el nombre: `ml`, `L`, `g`, `kg` y `u`, con un espacio entre el número y la unidad.

### 5.3 Formato del nombre (`item_name`)

| Regla | Detalle |
|---|---|
| Tildes | Si `quitar_tildes_nombre = 1`: á→a, é→e, í→i, ó→o, ú→u, ü→u, ñ→n (y sus mayúsculas) |
| Mayúsculas (estilo Título) | Cada palabra con inicial mayúscula. Excepciones: las unidades (`ml`, `L`, `g`, `kg`, `u`) y los conectores `de, del, con, y, e, en, a, al, para` en minúscula, salvo si son la primera palabra. "sin" sí se capitaliza ("Sin Azucar"). |
| Marca | Se escribe tal como en el glosario. Si no está en el glosario pero coincide sin distinguir mayúsculas ni tildes con un Brand existente, se usa la grafía del Brand (por ejemplo "Coca-Cola" o "Frut-All"). |
| Caracteres permitidos | Letras, dígitos, espacio y `- . ' & / ( ) %`. Los demás se eliminan. |
| Longitud | ≤ `largo_max_nombre` (80). Si se excede, se agrega una advertencia y la aprobación queda bloqueada. No se trunca automáticamente. |

### 5.4 `marca_corta`
1. Si la marca está en el glosario con `abreviatura`, se usa esa abreviatura.
2. Si no:
   - Se pasa a mayúsculas ASCII.
   - Se eliminan las palabras `DEL, DE, LA, EL, LOS, LAS`.
   - Se unen las palabras restantes quitando los espacios y símbolos.
   - Si el resultado tiene más de 10 caracteres, se usa solo la primera palabra (cortada a 10).

| Marca | Resultado |
|---|---|
| Del Valle | VALLE |
| Frut-All | FRUTALL |
| Super Frut | SUPERFRUT |
| Guarana Antarctica | GUARANA |
| Coca-Cola | COCACOLA por la regla automática, por eso el glosario fija COCA |

### 5.5 `variante_corta` y `valor_corto`
1. Si existe un `Item Attribute Value` del atributo cuyo valor coincide, se usa su `abbr` en mayúsculas.
2. Si el atributo es de medida, se usa la forma de código de la medida (por ejemplo `2L`).
3. Si no, se pasa a mayúsculas ASCII:
   - Valor de **1 palabra**: las 3 primeras letras (Durazno → DUR, Manzana → MAN, Naranja → NAR).
   - Valor de **2 o más palabras**: todas las palabras unidas, cortadas a 12 caracteres (Sin Azucar → SINAZUCAR, Sin Gas → SINGAS).
4. Si dos variantes de la misma captura producen la misma abreviatura (Mango y Manzana dan MAN), se agrega una letra a cada una hasta que se distingan (MANG y MANZ).
5. Al crear el Item Attribute Value, la abreviatura se guarda como `abbr`, así que queda estable para el futuro.

### 5.6 Formato del código y unicidad (`item_code`)

**Formato:**
- Mayúsculas ASCII y solo `[A-Z0-9.-]`.
- Segmentos separados por `-`.
- Regex: `^[A-Z0-9][A-Z0-9.\-]{1,39}$`.

**Longitud** (máximo `largo_max_codigo`, 40). Si se excede, se aplica este orden y se agrega una advertencia:
1. Se corta `variante_corta` a 3 caracteres.
2. Se corta `marca_corta` a 6 caracteres.

**Unicidad:**
- Se compara sin distinguir mayúsculas contra:
  - `tabItem.name`
  - el `item_code` de otras capturas en estado Por revisar, Aprobado o Exportado
  - las demás filas de la misma captura
- Si choca, se agrega `-2`, `-3`, … hasta encontrar uno libre, y se registra la advertencia de HU-03.
- La unicidad se recalcula en Recalcular, en Aprobar y en Exportar.

### 5.7 Ejemplos (con la plantilla por defecto)

| # | Dictado o entrada | Extraído | `item_name` | `item_code` |
|---|---|---|---|---|
| 1 | "coca cola sin azúcar de tres litros" | Coca-Cola / Sin Azucar / 3 L / grupo 1.9 Bebidas Embotelladas | Coca-Cola Sin Azucar 3 L | `BEB-COCA-SINAZUCAR-3L` |
| 2 | "pepsi de litro" | Pepsi / – / 1 L | Pepsi 1 L | `BEB-PEPSI-1L` |
| 3 | "del valle durazno medio litro" | Del Valle / Durazno / 500 ml | Del Valle Durazno 500 ml | `BEB-VALLE-DUR-500ML` |
| 4 | "fanta naranja litro y medio" | Fanta / Naranja / 1.5 L | Fanta Naranja 1.5 L | `BEB-FANTA-NAR-1.5L` |
| 5 | "agua vital sin gas dos litros y medio" | Vital / Sin Gas / 2.5 L | Vital Sin Gas 2.5 L | `BEB-VITAL-SINGAS-2.5L` |
| 6 | **Variantes (Sabor):** "jugo frut all de un litro"; variantes "sabor durazno" y "sabor manzana" | Frut-All / – / 1 L; atributo Sabor | Plantilla: Frut-All 1 L. Variantes: Frut-All Durazno 1 L y Frut-All Manzana 1 L | Plantilla: `BEB-FRUTALL-1L`. Variantes: `BEB-FRUTALL-DUR-1L` y `BEB-FRUTALL-MAN-1L` |
| 7 | **Variantes (Tamaño, atributo de medida):** "pop"; variantes "dos litros" y "tres litros" | Pop / – / sin medida; atributo Tamaño | Plantilla: Pop. Variantes: Pop 2 L y Pop 3 L | Plantilla: `BEB-POP`. Variantes: `BEB-POP-2L` y `BEB-POP-3L` |
| 8 | **Variantes (Color), sin marca:** "vaso descartable paquete de cincuenta"; variantes rojo y azul; grupo hipotético "Descartables" con prefijo DES | marca vacía, tipo "Vaso Descartable" / 50 u | Plantilla: Vaso Descartable 50 u. Variantes: Vaso Descartable Rojo 50 u y Vaso Descartable Azul 50 u | Plantilla: `DES-VASO-50U`. Variantes: `DES-VASO-ROJ-50U` y `DES-VASO-AZU-50U` |

**Nota sobre la unicidad en los ejemplos:** los códigos se muestran tal como salen de la plantilla, antes de verificar la unicidad. Varios ya existen en la instancia: `BEB-COCA-SINAZUCAR-3L`, `BEB-PEPSI-1L`, `BEB-FRUTALL-DUR-1L`, `BEB-FRUTALL-MAN-1L` y `BEB-POP-3L`. Ahí saldrían con el sufijo `-2` y la advertencia de duplicado de HU-03, y lo normal es que ya los frene antes el aviso por código de barras repetido. Que coincidan confirma que la plantilla reproduce la convención actual.

**Diferencias conocidas con la convención actual:**
- "Jumbo Jugo Durazno 2.5 L" saldría como "Jumbo Durazno 2.5 L", porque el tipo no entra en la plantilla. Para conservarlo, el glosario puede mapear "jumbo" a "Jumbo Jugo".
- Los códigos antiguos `-PEQ` no se reproducen.

---

## 6. Contrato con Claude (salida forzada por tool use)

**Llamada a la Messages API:**

| Parámetro | Valor |
|---|---|
| `tools` | `[registrar_producto]` con `strict: true` |
| `tool_choice` | `{"type": "tool", "name": "registrar_producto"}` |
| `max_tokens` | 2000 |
| Mensaje de usuario | Transcripción principal, transcripciones de las variantes (con su número de fila 1..n), códigos de barras, la foto principal más hasta 3 fotos en base64 |
| Contexto en el system prompt | Lista de Item Groups hoja, UOMs, Brands existentes, glosario (dicho → forma correcta), atributo de las variantes, `instrucciones_extra_ia` |

La IA **no** compone `item_name` ni `item_code`.

> **Nota de implementación (2026-11-05):** `claude-sonnet-5-5` (y Opus 5.5 / Fable 5.1) rechaza con HTTP 400 el `tool_choice` forzado (`{"type": "tool"}`). Como la llamada forzada solo servía para obtener JSON, se usa **salida estructurada** (`output_config.format` con `type: json_schema`) con el mismo esquema `registrar_producto` (ajustado por la sección 13: `atributos` a nivel de producto y `valores: [{atributo, valor}]` por variante), que garantiza JSON válido. Otros parámetros: `output_config.effort = "low"`, `max_tokens = 4096` (el pensamiento adaptativo cuenta para el límite), system prompt con `cache_control`, fallback del servidor ante negativas (`fallbacks: "default"`) en los modelos que lo admiten, y reintentos propios (el SDK con `max_retries=0`). Código: `captura_productos/ai/`.

**Esquema de la herramienta:**

```json
{
  "name": "registrar_producto",
  "description": "Registra los datos estructurados de un producto capturado (y sus variantes) a partir del dictado y las fotos. No inventes datos: usa null si no se ven ni se dicen.",
  "strict": true,
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["tipo_producto", "marca", "variante", "medida", "item_group", "stock_uom",
                 "descripcion", "tiene_variantes", "variantes", "texto_en_fotos",
                 "confianza", "advertencias"],
    "properties": {
      "tipo_producto": {"type": "string", "description": "Tipo genérico, singular, español. Ej: Gaseosa, Jugo, Agua, Galleta."},
      "marca": {"type": ["string", "null"], "description": "Marca con la grafía del glosario o de un Brand existente si coincide; null si no se identifica."},
      "variante": {"type": ["string", "null"], "description": "Sabor/versión del producto principal (ej. 'Sin Azúcar'). null si no hay o si tiene_variantes=true."},
      "medida": {
        "anyOf": [
          {"type": "object", "additionalProperties": false,
           "required": ["valor", "unidad", "texto_original"],
           "properties": {
             "valor": {"type": "number", "description": "Número > 0 en la unidad indicada."},
             "unidad": {"type": "string", "enum": ["ml", "L", "g", "kg", "u"]},
             "texto_original": {"type": "string", "description": "Fragmento tal como se dictó o se lee en la etiqueta, ej. 'medio litro'."}
           }},
          {"type": "null"}
        ]
      },
      "item_group": {"type": "string", "description": "Exactamente uno de la lista de Item Groups provista."},
      "stock_uom": {"type": "string", "description": "Exactamente una de la lista de UOM provista; normalmente 'Unidad'."},
      "descripcion": {"type": "string", "description": "1-2 frases en español, sin precios."},
      "tiene_variantes": {"type": "boolean"},
      "variantes": {
        "type": "array",
        "description": "Vacío si tiene_variantes=false.",
        "items": {
          "type": "object", "additionalProperties": false,
          "required": ["indice", "valor", "medida", "confianza"],
          "properties": {
            "indice": {"type": ["integer", "null"], "description": "Número de fila (1..n) de la variante capturada; null si solo se mencionó en el dictado principal."},
            "valor": {"type": "string", "description": "Valor del atributo en Title Case, ej. 'Durazno', 'Rojo', '2 litros'."},
            "medida": {
              "anyOf": [
                {"type": "object", "additionalProperties": false,
                 "required": ["valor", "unidad", "texto_original"],
                 "properties": {
                   "valor": {"type": "number"},
                   "unidad": {"type": "string", "enum": ["ml", "L", "g", "kg", "u"]},
                   "texto_original": {"type": "string"}
                 }},
                {"type": "null"}
              ],
              "description": "Solo si el atributo es de medida (Tamaño); si no, null."
            },
            "confianza": {"type": "number", "description": "0 a 1."}
          }
        }
      },
      "texto_en_fotos": {"type": "string", "description": "Texto relevante leído en las etiquetas (marca, sabor, contenido neto); '' si nada."},
      "confianza": {"type": "number", "description": "0 a 1, confianza global."},
      "advertencias": {"type": "array", "items": {"type": "string"}, "description": "Contradicciones dictado/foto, datos dudosos. Español, una idea por ítem."}
    }
  }
}
```

**Post-proceso en Python (`validation.py`):**

| Caso | Acción |
|---|---|
| `confianza` fuera de 0..1 | Se recorta al rango 0..1 y se guarda como `confianza_ia` multiplicada por 100 |
| `item_group` o `stock_uom` no están en la lista | Se usa el valor por defecto y se agrega una advertencia |
| `marca` | Se normaliza con el glosario y luego con los Brand existentes |
| `indice` fuera de rango | Advertencia |
| `indice = null` | Se crea una fila de variante sin código, con la advertencia "variante sin código de barras" |
| Confianza global < 0,6 | Advertencia "Baja confianza: revise con las fotos" |
| Respuesta IA | Se guarda completa en `respuesta_ia` |

---

## 7. Google Sheet compatible con Data Import de ERPNext v16 (Item)

### 7.1 Fuente del formato (verificado en `version-16`)

**Encabezados** (`frappe/core/doctype/data_import/exporter.py` e `importer.py`):
- Un campo del padre se identifica por su **Label**. También se acepta su fieldname.
- Un campo de una tabla hija se identifica como **`{Label del campo} ({Label de la tabla})`**. También se acepta `tabla.campo`.

**Filas múltiples** (`Importer.parse_next_row_for_import`):
- Una fila cuyas columnas del padre están **todas vacías** pertenece al documento anterior y solo aporta filas a las tablas hijas.
- La siguiente fila con algún valor en una columna del padre inicia un documento nuevo.

**Labels** (`erpnext/stock/doctype/item/item.json`, `item_barcode.json`, `item_variant_attribute.json` e `item_default.json`):

| Campo o tabla | Label |
|---|---|
| `item_code` | Item Code |
| `item_name` | Item Name |
| `item_group` | Item Group (oblig.) |
| `stock_uom` | **Default Unit of Measure** (oblig.) |
| `brand` | Brand |
| `description` | Description |
| `is_stock_item` | **Maintain Stock**. El plan decía "Is Stock Item"; el label real es este. |
| `is_sales_item` | **Allow Sales** |
| `is_purchase_item` | **Allow Purchase** |
| `has_variants` | Has Variants |
| `variant_of` | Variant Of |
| `image` | Image |
| Tabla `barcodes` | **Barcodes** (child `Item Barcode`: `barcode` "Barcode", que es oblig. y **único global**, y `barcode_type` "Barcode Type") |
| Tabla `attributes` | **Variant Attributes** (child `Item Variant Attribute`: `attribute` "Attribute", oblig., y `attribute_value` "Attribute Value") |
| Tabla `item_defaults` | **Item Defaults** (child `Item Default`: `company` "Company", oblig., y `default_warehouse` "Default Warehouse") |

**Variantes:** varios usuarios del foro (Frappe Forum, ver fuentes) informan que las plantillas deben importarse **antes** que las variantes y recomiendan archivos separados. Por eso se exporta en **2 pestañas**, cada una importada por separado y en orden.

**Verificado en `dev.localhost` (ERPNext 16.52, 2026-11-05):** con las filas generadas por `captura_productos/export/sheets.py` (1 producto simple con 2 códigos, 1 plantilla con 2 atributos —Sabor y Tamaño— y 2 variantes), Data Import → Item → *Insert New Records* terminó en **Success** importando primero la pestaña 1 y luego la 2, tanto en XLSX como en CSV. Los encabezados de 7.2 (incluidos `Company (Item Defaults)` y `Default Warehouse (Item Defaults)`) funcionaron sin cambios; los Items quedaron con barcodes, tipo, atributos, `variant_of`, Item Defaults e imagen (URL absoluta tal cual).

### 7.2 Columnas, en orden

| Col. | Encabezado exacto | Origen | Plantilla | Variante | Simple |
|---|---|---|---|---|---|
| A | `Item Code` | `item_code` | Sí | Sí | Sí |
| B | `Item Name` | `item_name` | Sí | Sí | Sí |
| C | `Item Group` | `item_group` | Sí | Sí (igual que la plantilla) | Sí |
| D | `Default Unit of Measure` | `stock_uom` | Sí | Sí | Sí |
| E | `Brand` | `marca` (el Brand debe existir) | Si hay | Si hay | Si hay |
| F | `Description` | `descripcion` | Sí | Sí | Sí |
| G | `Maintain Stock` | Configuración (1 o 0) | Sí | Sí | Sí |
| H | `Allow Sales` | Configuración | Sí | Sí | Sí |
| I | `Allow Purchase` | Configuración | Sí | Sí | Sí |
| J | `Has Variants` | 1 en la plantilla y 0 en las demás | 1 | 0 | 0 |
| K | `Variant Of` | `item_code` de la plantilla | vacío | Sí | vacío |
| L | `Image` | `url_base_imagenes` + `/files/…` de la foto principal o de la foto de la variante | Sí | Sí | Sí |
| M | `Barcode (Barcodes)` | Un código por fila | vacío | Sí | Sí |
| N | `Barcode Type (Barcodes)` | EAN-13, EAN-8, UPC-A o vacío | vacío | Sí | Sí |
| O | `Attribute (Variant Attributes)` | `atributo` | Sí | Sí | vacío |
| P | `Attribute Value (Variant Attributes)` | `valor` | **vacío** | Sí | vacío |
| Q | `Company (Item Defaults)` | `empresa` (columna omitida si está vacía) | Sí | Sí | Sí |
| R | `Default Warehouse (Item Defaults)` | `almacen_defecto` (columna omitida si está vacía) | Sí | Sí | Sí |
| S | `custom_plato_base` | `item_name`. Solo si el campo existe en Item; se usa el fieldname. | Sí | Sí | Sí |
| T | `custom_formato_venta` | `formato_venta` del prefijo. Solo si el campo existe. | Sí | Sí | Sí |

**Reglas:**
- Fila 1 = encabezados y fila 2 en adelante = datos.
- Los checks se escriben como `1` o `0`.
- No se agrega una columna `ID`: el Item se nombra por `item_code`.
- No se agregan columnas ajenas a Item (si no, Data Import mostraría advertencias). La trazabilidad queda en la captura (`hoja_exportacion` y `fila_sheet`).
- Un producto con varios códigos ocupa varias filas: la primera completa y las siguientes solo con las columnas M y N.

### 7.3 Ejemplo

**Pestaña `Items-20261105-1030-1-Productos`** (productos simples y plantillas). Se omiten las columnas F, G, H, I, L, Q, R, S y T por espacio.

| Item Code | Item Name | Item Group | Default Unit of Measure | Brand | Has Variants | Variant Of | Barcode (Barcodes) | Barcode Type (Barcodes) | Attribute (Variant Attributes) | Attribute Value (Variant Attributes) |
|---|---|---|---|---|---|---|---|---|---|---|
| BEB-AQUARIUS-300ML | Aquarius 300 ml | 1.9 Bebidas Embotelladas | Unidad | Aquarius | 0 | | 7771609002094 | EAN-13 | | |
| | | | | | | | 130899 | | | |
| BEB-FRUTALL-1L | Frut-All 1 L | 1.9 Bebidas Embotelladas | Unidad | Frut-All | 1 | | | | Sabor | |

**Pestaña `Items-20261105-1030-2-Variantes`:**

| Item Code | Item Name | Item Group | Default Unit of Measure | Brand | Has Variants | Variant Of | Barcode (Barcodes) | Barcode Type (Barcodes) | Attribute (Variant Attributes) | Attribute Value (Variant Attributes) |
|---|---|---|---|---|---|---|---|---|---|---|
| BEB-FRUTALL-DUR-1L | Frut-All Durazno 1 L | 1.9 Bebidas Embotelladas | Unidad | Frut-All | 0 | BEB-FRUTALL-1L | 133646 | | Sabor | Durazno |
| BEB-FRUTALL-MAN-1L | Frut-All Manzana 1 L | 1.9 Bebidas Embotelladas | Unidad | Frut-All | 0 | BEB-FRUTALL-1L | 7771620230018 | EAN-13 | Sabor | Manzana |

**Prerrequisitos para que Data Import no falle:**
- Que existan los **Brand**, el **Item Attribute** ("Sabor") y los **Item Attribute Value** ("Durazno", "Manzana"). Si `crear_maestros_al_exportar = 1`, la exportación los crea.
- Que no exista ningún código de barras en otro Item.

**Procedimiento para el supervisor:**
1. En el Sheet: Archivo → Descargar → XLSX de la pestaña 1.
2. En ERPNext: Data Import → Document Type Item → Insert New Records → adjuntar → Start Import.
3. Repetir con la pestaña 2.

Data Import también tiene el campo "Import from Google Sheets", pero exige que la hoja sea pública por enlace, por lo que **no se recomienda**.

---

## 8. Requisitos no funcionales

| Tema | Requisito |
|---|---|
| Seguridad | Las API keys y el JSON de la cuenta de servicio van solo en campos Password del Single; nunca se escriben en logs, `error_log`, respuestas ni código. Los endpoints validan rol y dueño en el servidor. Las subidas solo aceptan `image/jpeg` y `image/png` de 2 MB como máximo. `/captura` exige login y HTTPS. |
| Rendimiento | Primera carga de `/captura` < 3 s en 4G. `verificar_codigo` < 1 s. Subida de una foto (~300 KB) < 5 s en 4G. De Enviar a Por revisar ≤ 90 s (p95). Exportar 200 capturas < 60 s, con 1 a 3 llamadas `batch_update` (por debajo de las 60 escrituras por minuto de la API de Sheets). |
| Compatibilidad | Objetivo: Chrome Android ≥ 120 (voz y `BarcodeDetector`). Safari iOS ≥ 16: escaneo con ZXing y voz si `webkitSpeechRecognition` existe; si no, texto (HU-05). Desk: Chrome o Edge de escritorio. |
| Usabilidad (`/captura`) | Un paso por pantalla. Botones de ≥ 56 px de alto, con texto de ≥ 18 px y contraste AA. Ningún paso exige teclado salvo el respaldo de HU-05. Textos en español simple (detalle a cargo del `ui-designer`). |
| Idioma y moneda | UI y textos de la IA en español. BOB no aplica en el MVP porque no se manejan precios. Fechas en dd-mm-aaaa con la zona America/La_Paz. |
| IA | Cola `long` con 1 job por captura. Si hay más de 10 capturas en Procesando, se encolan sin llamadas en paralelo (por la RAM de CloudClusters). |
| Dependencias | `anthropic`, `gspread`, `google-auth` en `pyproject.toml`; `@zxing/browser` empaquetado en la app (sin CDN). |
| Despliegue | Dev: `erpnext-dev` en Docker y túnel cloudflared para probar desde el celular. Prod: CloudClusters con `bench build --app captura_productos` (no `bench build` completo, por OOM) y `supervisorctl restart all` como root. |
| Auditoría | `track_changes = 1` en Captura de Producto. Los errores se registran en el Error Log de Frappe y en `error_log`. |

---

## 9. Fuera de alcance del MVP

| Excluido | Comentario |
|---|---|
| Modo offline real (cola local, Service Worker con sincronización) | Solo se reintenta la subida mientras la página está abierta (HU-06) |
| Precios (Item Price, BOB) y costos | Siguen en scripts o en el Desk |
| Más de un código de barras por variante | Se agregan después en el Item |
| Agregar un código de barras a un Item **existente** desde la captura | Hoy se bloquea con el aviso de HU-03. Candidato para la versión 2. |
| Packs ("6 x 500 ml"), libras, onzas y galones | Generan una advertencia y se resuelven a mano |
| UOM de compra y factores de conversión, Item Defaults de cuentas y centros de costo | Se usan los Item Group Defaults ya configurados en la instancia |
| Workflow de Frappe (DocType Workflow) | El estado se maneja con un Select y botones |
| Importación automática del Sheet | La hace el supervisor con Data Import |
| Reconocimiento de voz del lado del servidor | Solo se usa Web Speech API |

---

## 10. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Web Speech API envía el audio a Google, necesita red y entiende mal las marcas | Glosario de marcas, respaldo con texto y fotos como segunda fuente para la IA |
| R2 | Data Import de variantes falla (por orden de importación, atributos inexistentes o campos obligatorios) | 2 pestañas, creación de maestros y todos los obligatorios completos en las filas de variantes. QA importa en `dev.localhost` antes de producción. |
| R3 | Duplicados de productos ya cargados con otra convención (por ejemplo `BEB-VALLE-300ML` agrupa sabores) | Verificación de códigos de barras en `Item Barcode` y advertencia por código parecido |
| R4 | La IA inventa marca o medida | `strict` tool use, `confianza_ia`, advertencias, revisión humana obligatoria antes de Aprobado |
| R5 | Fotos públicas (`/files/`) accesibles por URL | Son fotos de producto, sin datos sensibles. Si se rechaza, la columna Image queda vacía. |
| R6 | El JSON de la cuenta de servicio puede no caber o no guardarse en un Password | Verificar en dev (fase programmer). Alternativa: un Attach privado visible solo para System Manager. |
| R7 | Las políticas de Google Cloud pueden impedir crear claves de cuenta de servicio | Validar antes de la fase de exportación |
| R8 | Poca RAM en CloudClusters | Cola `long`, sin paralelismo, imágenes reducidas en el cliente |
| R9 | Permisos atípicos de la instancia (Custom DocPerm, `Sales User` recortado) | No tocar los permisos de DocTypes existentes. Probar "Crear Item" con el usuario real del supervisor. |
| R10 | Costo de la API de Claude | `max_fotos_ia = 4`, `max_tokens = 2000` y un job por envío. Reprocesar es manual. |

---

## 11. Orden de implementación (resumen; el detalle está en las fases del plan)

| # | Agente | Tarea | Terminado cuando |
|---|---|---|---|
| 1 | devops-engineer | Bench dev con la app instalada y túnel HTTPS | `bench --site dev.localhost list-apps` muestra la app y `/captura` abre desde el celular |
| 2 | ui-designer | Prototipo de las 5 pantallas de `/captura` | HTML estático aprobado que cubre HU-01 a HU-06 |
| 3 | programmer | DocTypes y Configuración según la sección 4, más el rol | Migran sin errores y la matriz de permisos se verifica con 3 usuarios |
| 4 | programmer | `naming.py` y `validation.py` | Los 8 ejemplos de 5.7 y la tabla 5.2 pasan como tests |
| 5 | programmer | `api.py` y `/captura` | HU-01 a HU-06 funcionan en el celular |
| 6 | programmer | Extractor de Claude y job | HU-09 y HU-13; tests con Claude mockeado |
| 7 | programmer | Formulario de revisión, Aprobar y Recalcular | HU-07, HU-08 y HU-10 |
| 8 | programmer | Exportación a Sheet | HU-11; el Sheet de ejemplo se importa con estado Success en dev |
| 9 | programmer | Crear Item | HU-12, incluido el rollback |
| 10 | qa-tester | Tests y prueba E2E del plan | Todo en verde y 3 capturas reales importadas |
| 11 | code-reviewer | Revisión por fase | Sin hallazgos de severidad alta (secretos, permisos, endpoints) |

---

## 12. Decisiones (respondidas por el usuario el 2026-11-05)

1. **Variantes:** "depende de las variantes ya creadas en ERPNext y las que ERPNext permita". → Se soportan **1 a 3 atributos por captura**, tomados de los `Item Attribute` existentes (ver sección 13). Esta decisión **prevalece** sobre cualquier mención de "un atributo por captura" en las secciones 4–7.
2. **Configuración:** solo System Manager (propuesta por defecto; no respondida, se puede cambiar luego).
3. **Fotos públicas:** sí.
4. **Prefijos:** el usuario los carga en Configuración. La app trae solo `1.9 Bebidas Embotelladas → BEB`; si un grupo no tiene prefijo, se usan las 3 primeras letras alfabéticas del nombre del grupo en mayúsculas y se agrega una advertencia.
5. **`custom_plato_base` / `custom_formato_venta`:** no existen en `dev.localhost`. La app solo los incluye (en el Sheet y en "Crear Item") si el campo existe en `Item` (`frappe.get_meta("Item").has_field`); `custom_formato_venta` sale de `Prefijo por Grupo.formato_venta`, `custom_plato_base` queda vacío.
6. **Nombres sin tildes ni ñ:** sí, se mantiene.

---

## 13. Variantes con varios atributos (prevalece sobre 4.4, 5, 6 y 7)

**Principio:** las variantes usan los **`Item Attribute` que ya existen en ERPNext**; la IA no inventa atributos. ERPNext permite varios atributos por plantilla (tabla `attributes` del Item) y valores de lista (`Item Attribute Value` con `abbr`) o numéricos (`numeric_values = 1` con from/to/increment).

**Modelo:**
- Nueva tabla hija en `Captura de Producto`: **`atributos`** → DocType **Captura Atributo** (`istable`): `atributo` (Link a `Item Attribute`, obligatorio, máx. 3 filas, sin repetir), `orden` (Int). El orden define qué valor va en `valor_1`, `valor_2`, `valor_3` de cada `Captura Variante`.
- Al dictar las variantes en el celular, el capturador **no elige el atributo**: la IA recibe la lista de `Item Attribute` existentes con sus valores y abreviaturas y propone qué atributo(s) aplican y el valor de cada variante. El supervisor puede corregirlos en el Desk.

**Validación (`validation.py`):**
- Cada `atributo` debe existir. Para atributos de lista: si el valor coincide (sin distinguir mayúsculas ni tildes) con un `Item Attribute Value`, se usa el valor exacto y su `abbr` como `valor_corto_N`; si no existe → advertencia "Valor nuevo: X en atributo Y" (se crea al exportar/crear Item solo si `crear_maestros_al_exportar = 1`). Para atributos numéricos: el valor debe estar dentro de from/to y respetar increment; si no, advertencia bloqueante para aprobar.
- No puede haber dos variantes con la misma combinación de valores.
- Atributo que sea de medida (`atributos_de_medida`) → su valor se normaliza con 5.2 y va a `{medida}`.

**Nombre/código:** `{variante}` = valores de los atributos que no son de medida, unidos por espacio en el orden de `atributos`; `{variante_corta}` = sus `valor_corto` unidos por `-`; `{medida}` = el valor del atributo de medida si existe. Ej.: atributos Sabor + Tamaño, variante Durazno / 2 L → "Frut-All Durazno 2 L", `BEB-FRUTALL-DUR-2L`.

**Contrato con Claude (ajuste a la sección 6):** el system prompt incluye `Item Attribute` existentes con valores y abreviaturas. La salida agrega `atributos: [nombre_atributo_existente, …]` (máx. 3) a nivel de producto y, por variante, `valores: [{atributo, valor}]` en lugar de `atributo/valor` únicos.

**Google Sheet (ajuste a la sección 7):** en la pestaña de plantillas, el Item plantilla lleva una fila hija por atributo en `Attribute (Variant Attributes)`. En la pestaña de variantes, cada variante lleva una fila hija por atributo con `Attribute (Variant Attributes)` + `Attribute Value (Variant Attributes)` (formato multi-fila: filas siguientes con columnas del padre vacías).

**Celular:** sin cambios de pantalla: el dictado de cada variante puede incluir varios datos ("sabor durazno de dos litros"). La guía en pantalla dice: "Di lo que cambia: sabor, tamaño o color".

---

### Fuentes
- [frappe/frappe `version-16`: data_import/exporter.py](https://github.com/frappe/frappe/blob/version-16/frappe/core/doctype/data_import/exporter.py): formato `Label (Table Label)` de los encabezados
- [frappe/frappe `version-16`: data_import/importer.py](https://github.com/frappe/frappe/blob/version-16/frappe/core/doctype/data_import/importer.py): `build_fields_dict_for_column_matching` y `parse_next_row_for_import` (filas múltiples)
- [frappe/frappe `version-16`: data_import.json](https://github.com/frappe/frappe/blob/version-16/frappe/core/doctype/data_import/data_import.json): Import Type y `google_sheets_url`
- [frappe/erpnext `version-16`: item.json](https://github.com/frappe/erpnext/blob/version-16/erpnext/stock/doctype/item/item.json), [item_barcode.json](https://github.com/frappe/erpnext/blob/version-16/erpnext/stock/doctype/item_barcode/item_barcode.json), [item_variant_attribute.json](https://github.com/frappe/erpnext/blob/version-16/erpnext/stock/doctype/item_variant_attribute/item_variant_attribute.json), [item_default.json](https://github.com/frappe/erpnext/blob/version-16/erpnext/stock/doctype/item_default/item_default.json)
- [Frappe Forum: Import template + variant item together](https://discuss.frappe.io/t/import-template-variant-item-together/127961) y [How to Import New Variants?](https://discuss.frappe.io/t/how-to-import-new-variants/8098): importar las plantillas antes que las variantes
- [ERPNext docs: Item Variants](https://docs.frappe.io/erpnext/user/manual/en/stock/item-variants)
