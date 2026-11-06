# Guía del supervisor: revisar, aprobar y pasar capturas a ERPNext

Para quien tiene el rol **Item Manager** o **Stock Manager** (y para el **System Manager** en la sección 9).
Todo se hace en el Desk de ERPNext, con Chrome o Edge de escritorio.

---

## 1. El recorrido de una captura

```
Celular: Enviar
   │
   ▼
Procesando ──(IA OK)──> Por revisar ──Aprobar──> Aprobado ──Exportar a Google Sheet──> Exportado
   │   ▲                    │   ▲                  │   │                                  │
   ▼   │ Reprocesar IA      │   └─Volver a revisión┘   └────────── Crear Item ─────────────┴──> Creado
 Error ┘                    └── Reprocesar IA (vuelve a Procesando)
```

| Estado | Qué significa | Quién actúa |
|---|---|---|
| Borrador | El celular no terminó de enviarla | Normalmente nadie (ver 8.3) |
| Procesando | La IA la está leyendo (normal: menos de 90 s) | Sistema |
| Por revisar | Lista para revisar | **Tú** |
| Aprobado | Revisada; lista para pasar a ERPNext | **Tú**: exportar o Crear Item |
| Exportado | Ya está en el Google Sheet | **Tú**: importar en ERPNext |
| Creado | El Item ya existe en ERPNext y quedó enlazado | Terminado |
| Error | La IA falló | **Tú** (ver 8.1) |

**Regla general:** los botones del formulario exigen que la captura esté guardada. Si ves
"Guarde los cambios antes de continuar.", guarda (**Ctrl+S**) y vuelve a tocar el botón.

---

## 2. Lista de capturas y filtros

1. En la barra de búsqueda del Desk escribe **Captura de Producto** y abre la lista.
2. Por defecto se ve el filtro **Estado = Por revisar**: es tu bandeja de trabajo.
3. Columnas: foto, nombre del ítem, código del ítem, capturado por, confianza IA y el estado (indicador de color).

**Cambiar el filtro:**

- Usa el campo **Estado** de la barra de filtros: Procesando, Por revisar, Aprobado, Exportado, Creado o Error.
- O haz clic en el **indicador de color** de cualquier fila para filtrar por ese estado.
- Para ver todas, quita el filtro (la **x** del filtro).

| Para... | Filtra por |
|---|---|
| Revisar lo nuevo | Por revisar |
| Exportar en lote | Aprobado |
| Saber qué falta importar | Exportado |
| Atender fallas | Error, y Procesando ordenado por fecha (las viejas pueden estar atascadas) |

Colores: Borrador gris, Procesando azul, Por revisar amarillo, Error rojo, Aprobado/Exportado/Creado verde.

---

## 3. Revisar una captura

Abre la captura. De arriba hacia abajo verás:

1. **Banner rojo** (solo si la confianza es menor que 60): "Confianza de la IA baja (55%): revise con las fotos."
2. **Recuadro amarillo de advertencias** (una por línea).
3. **Galería de fotos**: todas las fotos de la captura y de cada variante. Clic en una foto para verla grande.
4. **Lo que se dictó**: el texto que dijo el capturador.
5. **Datos extraídos** por la IA, **Nombre y código**, **Variantes**, **Resultado** y **Técnica** (plegables).

### 3.1 Cómo leer las advertencias

| La línea empieza con | Significa | Qué haces |
|---|---|---|
| **[Bloquea]** | Error que impide **Aprobar** (ej. "Este producto ya existe…", código de variante repetido, valor fuera de rango) | Corrígelo. Mientras exista, Aprobar no funciona. |
| **[Aviso]** | Algo para revisar, no bloquea (ej. "Valor nuevo: Maracuya en atributo Sabor", código de barras de largo no estándar) | Confirma que está bien y sigue. |
| **[Nombre]** | Nota del armado del nombre/código (ej. "Ya existe BEB-PEPSI-1L…", se agregó el sufijo `-2`) | Verifica que no sea el mismo producto ya cargado. |
| Sin prefijo | Comentario de la IA (ej. "La foto no coincide con el dictado", "Baja confianza: revise con las fotos") | Compara con las fotos. |

Las líneas **[Bloquea]** y **[Aviso]** se recalculan **cada vez que guardas**: si corriges el problema y guardas, desaparecen.

### 3.2 Cómo usar la confianza de la IA

| Confianza IA | Cómo revisar |
|---|---|
| 80 a 100 | Revisión normal: marca, medida, grupo y nombre final. |
| 60 a 79 | Compara **cada** campo extraído con las fotos. |
| Menos de 60 (banner rojo) | No confíes en nada sin verlo en la foto. Si las fotos no alcanzan, pide al capturador que lo capture de nuevo. |

### 3.3 Lista de control (30 segundos por captura)

- [ ] **Marca**: escrita como en ERPNext (ej. "Coca-Cola", "Frut-All").
- [ ] **Variante del producto** (sabor/versión) correcta, o vacía si es plantilla con variantes.
- [ ] **Medida** y **Unidad de medida** iguales a la etiqueta de la foto.
- [ ] **Grupo de producto** correcto (solo grupos hoja) y **Unidad de stock** (normalmente "Unidad").
- [ ] **Códigos de barras** coinciden con la foto, si se ve.
- [ ] Con variantes: **Atributos de las variantes** (Sabor, Tamaño…) y el valor de cada fila de **Variantes**.
- [ ] **Nombre del ítem** y **Código del ítem** finales siguen la convención (`BEB-COCA-SINAZUCAR-3L`).

---

## 4. Corregir campos y "Recalcular nombre"

1. Corrige los campos que estén mal (por ejemplo **Marca** o **Medida**).
2. Guarda con **Ctrl+S**.
   El **Nombre del ítem** y el **Código del ítem** **no cambian** al guardar. Es a propósito.
3. Menú **Acciones → Recalcular nombre**.
4. Si el resultado es distinto, se abre una tabla **Valor actual / Valor nuevo** con el padre y cada variante.
5. Revisa y confirma con **Sí**. Si ya estaba al día, verás "El nombre y el código ya están al día.".

Recalcular usa la plantilla vigente de Configuración, el glosario y los prefijos. No llama a la IA (no tiene costo).

**Otras formas de ajustar:**

- **Marca para el código** y **Variante para el código** controlan las abreviaturas del código (ej. `COCA`, `SINAZUCAR`). Edítalas y luego **Recalcular nombre**.
- También puedes escribir el **Nombre del ítem** o el **Código del ítem** a mano. En ese caso **no** uses Recalcular después, porque lo reemplaza.
- Reglas del código: mayúsculas, letras, números, punto y guion; máximo 40 caracteres; no puede existir ya como Item.
- Reglas del nombre: máximo 80 caracteres (configurable).

Si la IA se equivoca **siempre** con la misma marca, pide al administrador que la agregue al glosario (sección 9.2).

---

## 5. Aprobar

1. Con la captura en **Por revisar** y guardada, toca el botón azul **Aprobar**.
2. Si no hay errores bloqueantes, pasa a **Aprobado** y quedan registrados **Aprobado por** y **Aprobado en**.
3. Si hay errores, aparece la lista y el estado **no cambia**. Corrígelos y vuelve a aprobar.

**Errores que bloquean la aprobación:**

1. Código, nombre, grupo o unidad de stock vacíos.
2. Código con caracteres no permitidos o que ya existe como Item.
3. Nombre más largo que el máximo.
4. Un código de barras que ya está en otro Item.
5. Con variantes: ninguna variante, una variante sin valor, dos variantes con la misma combinación, códigos de ítem repetidos entre variantes, o códigos de barras cargados en el padre en vez de en cada variante.

**¿Te equivocaste?** En **Aprobado**: **Acciones → Volver a revisión** (regresa a Por revisar).

---

## 6. Reprocesar IA

Úsalo cuando la IA leyó mal casi todo (por ejemplo, otro producto) y corregir a mano es más trabajo.

1. En **Por revisar** o **Error**: **Acciones → Reprocesar IA**.
2. Confirma "Se sobrescribirán los campos extraídos. ¿Continuar?" con **Sí**.
3. La captura vuelve a **Procesando** y **Intentos IA** sube en 1. En 1 o 2 minutos vuelve a **Por revisar** (o a Error).

**Cuidado:** se pierden tus correcciones de los campos extraídos, del nombre y del código. Cada reproceso es una llamada paga a la IA.

---

## 7. Pasar las capturas a ERPNext

Hay dos caminos. Elige **uno** por captura.

| | Opción A: Google Sheet + Data Import | Opción B: Crear Item directo |
|---|---|---|
| Cuándo | Lotes grandes, o si quieres ver todo en una planilla antes de cargar | Pocas capturas, o para cargar ya mismo |
| Requiere | Configuración de Google Sheets lista | Permiso de crear Item |
| Resultado | Captura en **Exportado**; el Item se crea al importar | Captura en **Creado** con el Item enlazado |

### 7.1 Opción A, paso 1: exportar al Google Sheet

**Una captura (desde el formulario):**

1. Captura en **Aprobado** → **Acciones → Exportar a Google Sheet**.
2. Espera el mensaje **Exportado** con el nombre de las pestañas creadas.
3. La captura pasa a **Exportado** y se llenan **Pestaña del Sheet**, **Fila del Sheet** y **Exportado en** (sección Resultado).

Cada exportación crea pestañas nuevas. Para no llenar el Sheet de pestañas, prefiere la exportación masiva.

**Varias capturas (desde la lista), recomendado:**

1. En la lista, filtra **Estado = Aprobado**.
2. Marca las casillas de las capturas a exportar (máximo **200** por vez).
3. Menú **Acciones → Exportar aprobados a Google Sheet**.
4. Confirma "Se exportarán N capturas." (si marcaste alguna que no está en Aprobado, se omite y te avisa).
5. Verás "La exportación sigue en segundo plano. Le avisaremos al terminar." **Puedes seguir trabajando.**
6. Al terminar aparece la ventana **Exportación a Google Sheet** con:
   - **Exportadas**: cuántas pasaron a Exportado.
   - **Pestañas**: los nombres de las pestañas creadas.
   - **Excluidas**: cada captura que no se exportó y el motivo. Esas siguen en **Aprobado**: corrige el motivo y vuelve a exportarlas.
7. El mismo resultado queda en las **notificaciones** (la campana, arriba a la derecha), por si cerraste la página.

Si sale **No se pudo exportar**, ninguna captura cambió de estado. Mensajes comunes:

| Mensaje | Qué hacer |
|---|---|
| "Comparta la hoja con xxx@yyy.iam.gserviceaccount.com como Editor" | En Google Sheets: **Compartir** → pega ese correo → rol **Editor**. Vuelve a exportar. |
| "Falta el ID del spreadsheet en Configuración Captura." | Pide al administrador que complete la sección 9.5. |
| "Error inesperado. El detalle quedó en el Error Log." | Avisa al administrador (Desk → **Error Log**). |

**Qué contiene el Sheet:** por cada exportación se crean hasta dos pestañas:

- `Items-AAAAMMDD-HHMM-1-Productos`: productos simples y **plantillas** (productos con variantes).
- `Items-AAAAMMDD-HHMM-2-Variantes`: las **variantes** (solo si hay).

Un producto con varios códigos de barras o varios atributos ocupa varias filas: la primera completa y las siguientes solo con las columnas del código o del atributo. **No borres esas filas ni cambies los encabezados.**

### 7.2 Opción A, paso 2: importar el Sheet en ERPNext (Data Import)

**Orden obligatorio: primero la pestaña `-1-Productos`, después la `-2-Variantes`.** Las variantes necesitan que su plantilla ya exista.

**a) Descargar la pestaña**

1. Abre el Google Sheet y haz clic en la pestaña **`…-1-Productos`**.
2. **Archivo → Descargar → Valores separados por comas (.csv)**. Baja **solo la pestaña que estás viendo**. Es la opción recomendada.
   - Si prefieres **Microsoft Excel (.xlsx)**: Google baja **todas** las pestañas en un solo archivo. En la vista previa de Data Import (paso c.5) verifica que se vean las filas de la pestaña correcta; si no, usa CSV.

**b) Antes de importar, comprueba**

- Que existan las **Marcas (Brand)**, los **Item Attribute** y sus **valores**. Si la opción "Crear Brand y atributos faltantes al exportar" está activa (por defecto sí), la exportación ya los creó.
- Que ningún código de barras del archivo esté ya en otro Item.

**c) Importar**

1. En el Desk busca **Data Import** y crea uno nuevo (**+ Agregar**).
2. **Document Type** (Tipo de documento): **Item**.
3. **Import Type** (Tipo de importación): **Insert New Records** (Insertar nuevos registros).
4. Guarda (**Ctrl+S**).
5. En **Import File**, adjunta el CSV (o XLSX) de la pestaña 1. Revisa la vista previa y las advertencias de columnas.
6. Toca **Start Import** (Iniciar importación).
7. Espera el estado **Success**. Revisa que el número de Items importados coincida con el número de productos de la pestaña.

**d) Repite a), b) y c) con la pestaña `…-2-Variantes`**, en un **Data Import nuevo**.

**e) Cerrar el ciclo (recomendado):** abre cada captura en **Exportado** y toca **Crear Item**. Como el Item ya existe con los mismos códigos de barras, **no crea nada nuevo**: solo lo enlaza y la captura pasa a **Creado**.

**Si la importación falla o queda en "Partial Success":**

- Abre el registro de importación (Import Log) para ver la fila y el motivo.
- Motivos típicos: falta una marca o un valor de atributo; el código de barras ya existe en otro Item; se importaron las variantes antes que las plantillas; se importó dos veces el mismo archivo (el Item ya existe).
- Corrige en ERPNext y vuelve a importar **solo las filas que fallaron** (Data Import permite exportar las filas con error).

**Exportar de nuevo:** si necesitas volver a exportar una captura (por ejemplo, se borró la pestaña), en **Exportado** usa **Acciones → Marcar para re-exportar**. Vuelve a **Aprobado** y limpia la pestaña y la fila. Si además hay que corregir datos: **Volver a revisión** → corregir → **Aprobar** → exportar.

### 7.3 Opción B: Crear Item directo

1. Captura en **Aprobado** (o **Exportado**) → botón **Crear Item**.
   Solo aparece si tu usuario tiene permiso para crear Items.
2. Confirma "Se creará el Item BEB-… ¿Continuar?" (o "Se creará la plantilla … y N variantes").
3. Espera **Creando Item…**. Verás "Item BEB-…: N creados, M enlazados".
4. La captura pasa a **Creado**. Usa **Ver Item** para abrirlo.

Crea en un solo paso las marcas y valores de atributo que falten, el Item (o la plantilla y sus variantes) con códigos de barras, imagen y valores por defecto.

Es **todo o nada**: si algo falla (por ejemplo, el código ya existe como otro Item con otros códigos de barras), **no se crea nada**, el estado no cambia y el detalle queda en **Técnica → Último error**.

---

## 8. Capturas con problemas

### 8.1 En Error

El capturador ve "No se pudo procesar. El supervisor lo revisará." y puede tocar **Reintentar** una vez.

1. Abre la captura → sección **Técnica** → lee **Último error** e **Intentos IA**.
2. Según la causa:

| Último error menciona | Causa | Qué hacer |
|---|---|---|
| "Token de la API local", "Falta claude_api_token", API key, 401 | La clave de la IA falta o es inválida | Avisa al administrador: la revisa con **Configuración Captura → Probar → Probar conexión IA** (el token de la API local se configura en el servidor, no en esta pantalla). Después **Reprocesar IA**. |
| "El servicio de IA local no responde", 429, 500, 502, 504, 529, timeout | Servicio de IA caído o saturado (ya se reintentó 2 veces solo) | Espera unos minutos y **Reprocesar IA**. Si pasa con todas las capturas, avisa al administrador. |
| Respuesta no válida / esquema | La IA devolvió algo mal formado | **Reprocesar IA** una vez. Si se repite, revisa fotos y dictado. |

3. **Acciones → Reprocesar IA** → **Sí**.
4. Si después de 2 intentos sigue en Error y las fotos o el dictado son malos, **borra la captura** y pide al capturador que la haga de nuevo.

### 8.2 Atascada en Procesando

Lo normal es que pase a Por revisar en menos de 90 segundos. Ante fallas pasajeras el sistema reintenta solo (a los 30 s y a los 120 s).

1. Abre la captura → **Técnica**: mira **Reintentos automáticos** y **Próximo reintento IA**.
2. Si **Próximo reintento IA** tiene una hora futura, **espera**.
3. Si lleva **más de 10 minutos** en Procesando y no hay reintento pendiente: **Acciones → Reprocesar IA** → confirma "Se volverá a mandar a la IA si quedó detenida. ¿Continuar?".
4. Si responde "La IA ya está procesando esta captura: espere a que termine.", espera unos minutos más.
5. Si hay **varias** capturas atascadas a la vez, probablemente el servidor de tareas en segundo plano está caído. Avisa al administrador para reiniciarlo. Con más de 10 capturas en cola, se procesan de a una: es normal que tarden más.

### 8.3 En Borrador

Son capturas que el celular empezó a enviar y no terminó. Normalmente el capturador las completa con **Enviar ahora**.
Si quedó abandonada y tiene al menos 1 foto y dictado: **Acciones → Enviar**. Si no sirve, bórrala.

### 8.4 Borrar

Puedes borrar una captura en cualquier estado **excepto Creado**.

---

## 9. Configuración (solo System Manager)

En el Desk busca **Configuracion Captura**. Siempre **guarda antes de probar**: si no, verás "Guarde la configuración antes de probar.".

### 9.1 Prefijos por grupo

Tabla **Prefijos por grupo**: define el primer segmento del código según el **Grupo de producto**.

| Columna | Regla | Ejemplo |
|---|---|---|
| Grupo de producto | Obligatorio, grupo hoja, sin repetir | 1.9 Bebidas Embotelladas |
| Prefijo | Obligatorio, 2 a 5 letras mayúsculas | BEB |
| Formato de venta | Opcional | Bebida |

- Si un grupo no tiene prefijo, se usan las 3 primeras letras del nombre del grupo y aparece una advertencia. **Carga el prefijo de cada grupo que uses** para que los códigos queden parejos.
- **Prefijo si no hay grupo** (por defecto `PRD`) se usa cuando la captura no tiene grupo.
- Los cambios aplican a capturas nuevas y a **Recalcular nombre**. No cambian Items ya creados.

### 9.2 Glosario de marcas

Tabla **Glosario de marcas**: corrige marcas mal dictadas y fija su abreviatura.

| Columna | Regla | Ejemplo |
|---|---|---|
| Formas dictadas | Formas en que la dicen o la escriben mal, separadas por coma. No importan tildes ni mayúsculas. | coca cola, cocacola, coca |
| Forma correcta | Cómo debe quedar en el nombre | Coca-Cola |
| Abreviatura | Opcional, 2 a 10 letras/números en mayúsculas. Reemplaza la abreviatura automática. | COCA |

**Agrega una fila cuando:** la IA escribe mal la misma marca dos veces, o la abreviatura automática no sigue la convención (Coca-Cola daría `COCACOLA`; con el glosario queda `COCA`).
Truco: para conservar el tipo en el nombre, mapea "jumbo" a "Jumbo Jugo".

### 9.3 Plantillas de nombre y código

| Campo | Valor por defecto |
|---|---|
| Plantilla de nombre | `{marca} {variante} {medida}` → "Coca-Cola Sin Azucar 3 L" |
| Plantilla de código | `{prefijo}-{marca_corta}-{variante_corta}-{medida}` → `BEB-COCA-SINAZUCAR-3L` |

- Placeholders permitidos: `{prefijo}`, `{tipo}`, `{marca}`, `{marca_corta}`, `{variante}`, `{variante_corta}`, `{medida}`. Uno desconocido se rechaza con "Placeholder no válido: {x}".
- Un placeholder vacío se elimina junto con su separador.
- Otros ajustes: **Quitar tildes y ñ del nombre** (activo), **Mayúsculas del nombre** (Título), **Largo máximo del nombre** (80), **Largo máximo del código** (40), **Atributos que son medida** (`Tamaño,Tamano,Presentacion`).
- **Cambia las plantillas lo menos posible.** Los Items ya creados conservan el formato anterior y la convención quedaría mezclada.

### 9.4 Crear Brand y atributos faltantes al exportar

- **Activado (recomendado):** al exportar se crean las marcas y los valores de atributo que falten, con su abreviatura. Así Data Import no falla.
- **Desactivado:** las capturas con una marca o un valor nuevo quedan **Excluidas** de la exportación. Tienes que crear el Brand o el Item Attribute Value a mano y exportar de nuevo.

### 9.5 Google Sheets e IA

- **ID del spreadsheet**: la parte de la URL de la hoja entre `/d/` y `/edit`.
- **Credenciales de la cuenta de servicio (JSON)**: pega el JSON completo. Comparte la hoja con el correo de esa cuenta como **Editor**.
- **Prefijo de la pestaña** (`Items`) y **URL base de las imágenes** (la dirección del sitio, para que el Item tenga imagen).
- **Proveedor de IA**: **API local** (por defecto; el servicio de IA del propio servidor, su dirección y su token los configura el administrador en el servidor) o **Anthropic directo** (pide la **API key Anthropic**, que solo aparece con esa opción).
- **Modelo**, **Timeout API local (s)** e **Instrucciones extra para la IA** (por ejemplo, reglas propias del negocio).
- En cada captura, **Técnica → Costo IA (USD)** suma lo que costaron todos sus intentos con la API local.
- Valores por defecto del Item: **Grupo por defecto**, **UOM por defecto**, **Empresa**, **Almacén por defecto**, **Mantener stock**, **Permitir venta**, **Permitir compra**.
- Para verificar: menú **Probar → Probar conexión IA** y **Probar → Probar Google Sheet**. Responden OK o el error concreto (la prueba de la API local hace una consulta corta y puede tardar hasta un minuto).

---

## 10. Referencia rápida: qué botón aparece en cada estado

| Estado | Botones (los de "Acciones" están en ese menú) |
|---|---|
| Borrador | Acciones → Enviar |
| Procesando | Acciones → Reprocesar IA (solo si quedó atascada) |
| Por revisar | **Aprobar**; Acciones → Recalcular nombre, Reprocesar IA |
| Aprobado | **Crear Item**; Acciones → Exportar a Google Sheet, Volver a revisión, Recalcular nombre |
| Exportado | **Crear Item** (enlaza si ya se importó); Acciones → Marcar para re-exportar |
| Creado | Ver Item |
| Error | Acciones → Reprocesar IA |

Desde la lista: **Acciones → Exportar aprobados a Google Sheet** (masivo, en segundo plano).
