# QA: `captura_productos` (ERPNext v16)

| Dato | Valor |
|---|---|
| Fecha | 2026-11-05 |
| Entorno | Docker `erpnext-dev-frappe-1`, sitio `dev.localhost` (Frappe/ERPNext 16.52), setup wizard ya completado (Company `Estacion Tradicion Dev`, zona America/La_Paz) |
| Base | `docs/spec.md` (secciones 12 y 13 prevalecen), `docs/ui/ui-spec.md`, `docs/DEV.md` |
| Tests nuevos | `captura_productos/tests/test_qa.py` (58 tests). No se modificó código de producción. |
| Prueba manual | Navegador integrado, `/captura` a 375×812 como `capturador.demo@example.com` (origen `127.0.0.1:8000`) y Desk como Administrator (origen `localhost:8000`) |

---

## 1. Resultado de la suite

### 1.1 Suite de los programadores (antes de la prueba manual)

`bench --site dev.localhost run-tests --app captura_productos`

```
Running 25 unit tests ...           Ran 25 tests in 0.104s   OK
Running 100 integration tests ...   Ran 100 tests in 10.922s OK
```

**125/125 en verde**, sin tests omitidos. Coincide con lo que reportaron los programadores.

### 1.2 Tests nuevos de QA (`test_qa.py`)

`bench --site dev.localhost run-tests --app captura_productos --module captura_productos.tests.test_qa`

```
Ran 58 tests in 11.987s
FAILED (failures=3)
```

55 pasan y 3 fallan a propósito: cada uno documenta un bug (B4, B5 y B9 en la sección 4). No se bajó ninguna aserción.

### 1.3 Suite completa después de la prueba manual

```
Ran 25 unit tests  OK
Ran 132 integration tests  FAILED (failures=5)
```

- 3 fallas son los tests de QA anteriores.
- 2 fallas nuevas en tests de los programadores: `test_naming.TestEjemplosSpec.test_ejemplos_simples` y `test_multi_atributo_seccion_13`. **No son bugs de la app.** La prueba manual creó en `dev.localhost` los Items reales `BEB-COCA-SINAZUCAR-3L`, `BEB-FRUTALL-2L`, `BEB-FRUTALL-DUR-2L` y `BEB-FRUTALL-MAN-2L`. La app hace lo correcto y agrega `-2`, pero esos tests esperan que esos códigos no existan en la base. Es un problema de aislamiento de los tests (ver B12).

---

## 2. Matriz de trazabilidad HU → tests

Leyenda: **P** = test de los programadores, **QA** = test nuevo en `test_qa.py`, **M** = caso manual (sección 3).

| HU / sección | Criterio | Tests | Estado |
|---|---|---|---|
| HU-01 | 1–6 fotos, la 1.ª es principal, foto pública | P `test_api.test_enviar_producto_simple`, `test_subir_foto_publica_adjunta_y_sin_duplicar`; QA `TestQAFotos.test_mas_de_6_fotos_en_el_envio`, `test_foto_publica_y_principal_unica` | Cubierto |
| HU-01 | Reducción a 1280 px en el navegador | M1 | Solo manual (OK) |
| HU-01 | Texto en vivo, Repetir, Siguiente | M2 (voz simulada) | Solo manual (OK) |
| HU-01 | Hasta 5 códigos / "No tiene código" | P `test_enviar_rechaza_codigo_mal_leido_y_listas_largas`, `test_enviar_sin_codigo`; M10 | Cubierto |
| HU-01 | Enviar → "Enviado" en < 2 s y aparece Procesando | P `test_enviar_producto_simple`; M4 | Cubierto |
| HU-01 | 0 fotos o transcripción < 3 caracteres no avanza | P `test_enviar_exige_foto_y_transcripcion` (x2); M1 | Cubierto |
| HU-02 | Variante 1 = producto de los pasos 1–3 | P `test_enviar_con_variantes`; QA `test_envio_con_dos_codigos_y_variantes_deja_el_segundo_en_el_padre`; M6 | Cubierto |
| HU-02 | De 1 a 20 variantes | QA `TestQACodigos.test_maximo_20_variantes_en_el_envio` | Cubierto (nuevo) |
| HU-03 | Código en Item Barcode: bloquea | P `test_verificar_codigo_en_item`, `test_barcode_en_item_bloquea`; QA `test_codigo_de_variante_ya_en_item_bloquea_aprobar`, `test_verificar_codigo_upc_contra_ean13_en_item`; M7 | Cubierto |
| HU-03 | Código en otra captura: aviso | P `test_verificar_codigo_en_otra_captura`; QA `test_verificar_codigo_encuentra_codigo_de_variante_de_otra_captura` | Cubierto |
| HU-03 | Mismo código dos veces: "Ya escaneado" | P `test_barcode_repetido_en_la_misma_captura`; QA `test_codigo_del_producto_repetido_en_una_variante`, `test_upc_a_y_su_ean13_en_dos_variantes`; M3, M6 | Cubierto |
| HU-03 | `item_code` repetido → sufijo `-2` | P `test_naming.test_unicidad_con_sufijo`, `test_unicidad_entre_variantes_de_la_misma_captura` | Cubierto |
| HU-04 | Verificador inválido → "Código mal leído" | P `TestCodigosDeBarras`, `test_verificar_codigo_invalido`; M3 | Cubierto |
| HU-04 | Otra longitud (`130885`) se acepta con aviso | P `test_codigo_interno_se_acepta_con_aviso` (servidor); M3 | **FALLA en el celular** (B3) |
| HU-04 | 3 fallos de escaneo → "Escribir número" | — | Sin cubrir (sin cámara en la PC) |
| HU-05 | Sin voz o sin permiso → cuadro de texto, `metodo_entrada = Texto` | M2 (permiso denegado) | Parcial: no se probó un navegador sin `SpeechRecognition` |
| HU-05 | Corte de red conserva el texto parcial | — | Sin cubrir (lógica en `voz.js:86`, solo revisada) |
| HU-06 | Lo no enviado se reintenta | QA `TestQAIdempotencia.*`; M9 | Cubierto |
| HU-06 | Borrador sobrevive a cerrar el navegador | M8 | Solo manual (OK, con borrador local en vez de Borrador del servidor, ver D1) |
| HU-06 | Mis capturas: últimos 30 días, colores, mensaje simple | P `test_mis_capturas`, `test_reintentar_captura_en_error`; QA `TestQAMisCapturas.*`; M5 | Cubierto |
| HU-07 | Lista con filtro `Por revisar` y columnas | M11 | **Parcial**: falta la columna de foto (B7) |
| HU-07 | Galería con todas las fotos, advertencias en amarillo, banner rojo < 60 | M12 | **FALLA**: no hay galería (B6) |
| HU-07 | Editar campos no cambia `item_name`/`item_code` | P `test_recalcular_nombre`; M13 | Cubierto |
| HU-08 | Recalcular con confirmación y plantilla vigente | P `test_recalcular_nombre`, `test_recalcular_usa_plantilla_vigente`; QA `TestQAEstados.test_acciones_rechazadas_fuera_de_su_estado`; M13 | Cubierto |
| HU-09 | Reprocesar / Reintentar, `intentos_ia += 1` | P `test_error_de_ia_y_reprocesar`, `test_reprocesar_permisos`, `test_variantes_con_atributos`; QA `test_reprocesar_sube_intentos`; M5 | Cubierto |
| HU-10 | Aprobar sin bloqueantes, registra `aprobado_por/en` | P `test_flujo_completo_hasta_aprobado`, `TestValidarCaptura.*`; QA `TestQAMultiAtributo.*`; M14 | Cubierto |
| HU-11 | Revalida y excluye las que fallan | P `test_exporta_simple_y_variantes`, `test_sin_crear_maestros_excluye`; QA `test_solo_exporta_aprobadas`, `test_aprobada_que_ahora_falla_se_excluye`, `test_maximo_200_y_duplicados_en_la_seleccion` | Cubierto; **falla** con `item_code` repetido entre capturas (B5) |
| HU-11 | Hoja no compartida / error: no cambia estado, `error_log` | P `test_hoja_no_compartida_no_cambia_estado`; QA `test_sin_credenciales_mensaje_claro`, `test_credenciales_invalidas_no_filtran_la_clave`, `test_fallo_al_escribir_deshace_maestros`; M15 | Cubierto en el servidor; **botón del formulario roto** (B1) |
| HU-11 | Data Import termina en Success | — | No re-verificado (DEV/spec dicen que se verificó en dev) |
| HU-12 | Crear Item simple / plantilla + variantes en una transacción | P `test_item_simple`, `test_plantilla_y_variantes`; QA `test_crear_item_multi_atributo_con_numerico`, `test_crear_item_desde_exportado`; M16 | Cubierto en el servidor; **botón roto** (B1) |
| HU-12 | Enlaza Item ya importado | P `test_enlaza_item_importado` | Cubierto |
| HU-12 | Rollback total y `error_log` | P `test_rollback_total_si_falla`; QA `test_rollback_si_falla_create_variant_a_mitad`, `test_rollback_si_el_codigo_de_la_plantilla_es_un_item_simple`, `test_rollback_si_el_barcode_aparece_en_otro_item_despues_de_aprobar` | Cubierto |
| HU-13 | Reintentos, Error definitivo, sin API key, sin filtrar la key | P `test_extractor.*`; M4/M5 (sin API key) | Cubierto (Claude mockeado) |
| HU-14 | Placeholder no válido | P `test_placeholder_invalido` | Cubierto |
| HU-14 | Glosario | P `test_glosario_y_brand`, `test_glosario_de_marcas` | Cubierto |
| HU-14 | "Probar conexión IA" / "Probar Google Sheet" | M17 | **FALLA**: botones rotos (B2) |
| Sec. 1 | Permisos por rol y endpoints | P `TestPermisosCapturador`, `test_exige_sesion_y_rol`, `test_un_capturador_no_toca_capturas_de_otro`; QA `TestQAPermisos.*` (9 tests) | Cubierto |
| Sec. 3 | Máquina de estados | P `TestEstados.*`; QA `TestQAEstados.*` (6 tests, 57 combinaciones acción × estado no permitidas) | Cubierto |
| Sec. 8 | Fotos JPEG/PNG ≤ 2 MB | P `test_subir_foto_rechaza_tipo_y_tamano`; QA `TestQAFotos.*` (BMP, TIFF, PDF, SVG, vacío, límite exacto de 2 MB, > 6000 px) | Cubierto |
| Sec. 13 | Multi-atributo, valor nuevo, numérico fuera de rango | P `test_multi_atributo_seccion_13`, `test_atributo_numerico`; QA `TestQAMultiAtributo.*` | Cubierto; **falla** con `crear_maestros_al_exportar = 0` (B9) |

### Qué cubren los tests nuevos (`test_qa.py`)

| Clase | Tests | Qué prueba |
|---|---|---|
| `TestQAPermisos` | 9 | Ningún método de la app está en `allow_guest`. Guest y usuario sin rol reciben error en los 7 endpoints de `api.py` y en `exportar_aprobados`. Capturador A no lista, no lee, no edita ni borra capturas de B (incluido `frappe.client.*`). El capturador no borra ni sus propias capturas, no cambia `estado` por `set_value`, no edita después de enviar, no aprueba, recalcula, reprocesa en Por revisar, exporta, crea Item ni vuelve a revisión. No lee `Configuracion Captura` ni usa sus botones. Item Manager tampoco lee la configuración. |
| `TestQAEstados` | 6 | Cada una de las 10 acciones del controlador desde cada estado no permitido (57 combinaciones) es rechazada y el estado no cambia. Transiciones directas inválidas con `_cambiar_estado`. `estado` no se cambia guardando, en ningún estado. Creado no se borra, ni por el supervisor; Error sí. |
| `TestQAIdempotencia` | 7 | `crear_captura` repetido (incluso con espacios en el uuid), `subir_foto` repetido 4 veces, misma foto en dos capturas, `enviar_captura` repetido (no re-encola), reenvío tardío sobre una captura en Error no la reprocesa, tope de archivos por captura. |
| `TestQAFotos` | 6 | Rechaza BMP, TIFF, PDF, SVG, vacío y cabecera JPEG truncada. Acepta PNG y JPEG. Límite exacto: 2 MB pasa, 2 MB + 1 byte falla. Más de 6000 px falla. Más de 6 fotos en el envío falla. Una sola foto principal. |
| `TestQACodigos` | 7 | Código del padre repetido en una variante, UPC-A y su EAN-13 en dos variantes, envío con 2 códigos y variantes, `verificar_codigo` contra variantes de otra captura y UPC contra EAN-13 en Item, código de variante ya en Item bloquea Aprobar, tope de 20 variantes. |
| `TestQAMultiAtributo` | 7 | Sabor + Tamaño + Paquete (numérico): valor nuevo es aviso, fuera de rango bloquea, incremento, no numérico, combinación repetida con 3 atributos, atributo repetido, Crear Item multi-atributo con numérico, flag `crear_maestros_al_exportar = 0`. |
| `TestQAExportacion` | 8 | Solo exporta Aprobado (excluye los otros 6 estados y una inexistente), sin aprobadas no abre el Sheet, aprobada que ahora falla se excluye, `item_code` repetido entre capturas, tope de 200 y duplicados, sin credenciales, credenciales inválidas no filtran la clave privada, fallo al escribir deshace los maestros. |
| `TestQACrearItem` | 6 | Rollback si `create_variant` falla a mitad (no queda plantilla, variante, Brand ni Item Attribute Value), si el código de la plantilla es un Item simple y si un barcode aparece en otro Item después de aprobar. Sin permiso de crear Item. Advertencias después de Creado. Crear Item desde Exportado. |
| `TestQAMisCapturas` | 2 | Solo los últimos 30 días, `limit` inválido o negativo, el capturador ve el mensaje simple y nunca el detalle técnico. |

---

## 3. Casos manuales ejecutados

Cámara y micrófono no existen en la PC. Para simularlos: las fotos se inyectaron como `File` en el `<input type="file">` después de tocar "Elegir de la galería" (con el diálogo nativo suprimido). La voz se simuló reemplazando `start()` del `SpeechRecognition` nativo y `navigator.permissions.query`. Los códigos se ingresaron con "Escribir el código".

| # | Caso (375×812, capturador) | Resultado |
|---|---|---|
| M1 | Paso Fotos: Siguiente con 0 fotos | **OK**: no avanza, pista "Toma al menos 1 foto para seguir." con `is-shake`, `aria-disabled="true"`. |
| M1b | Foto de 3000×2000 por la galería | **OK**: la miniatura queda en 1280×853 (reducida en el navegador). Una de 800×600 queda igual. |
| M2 | Paso Dictar: micrófono sin permiso | **OK**: aviso "No podemos usar el micrófono" con los 4 pasos, "Intentar de nuevo" y "Prefiero escribir". |
| M2b | Dictado simulado "coca cola sin azúcar de tres litros" | **OK**: "Lo que dijiste" con Repetir y Corregir. `lang = es-BO`. |
| M3 | Código: verificador inválido `7791000000049` | **OK**: "Estos números no forman un código válido. Revisa número por número." con `aria-invalid`. |
| M3b | Código interno `130885` (HU-04) | **FALLA** (B3): "El código tiene 6 números. Debe tener 8, 12 o 13." No se puede agregar. |
| M3c | EAN-13 válido y luego el mismo otra vez | **OK**: "¡Código leído!", después "Ese código ya está en la lista" y queda 1 código. |
| M4 | Variantes "No, es uno solo" → Resumen → Enviar (CAP-2026-00004) | **OK**: "¡Listo!". Requests: `verificar_codigo`, `crear_captura`, 2 × `subir_foto`, `enviar_captura`, todos 200. Pasó a Error en segundos (sin API key) con "No se pudo procesar. El supervisor lo revisará." |
| M5 | Mis capturas → "Reintentar" en CAP-2026-00004 | **OK**: `reintentar_captura` 200, vuelve a Error, `intentos_ia = 2`. |
| M6 | Captura con 2 variantes (CAP-2026-00005): foto, dictado y código de la variante 2; código de la variante 1 repetido en la 2 | **OK**: "Ya usaste este código en: Variante 1 (este producto)"; con otro código, "Variante guardada". Enviar llegó a "¡Listo!" en ~0,3 s. En el servidor quedaron 2 filas de variantes, cada una con su foto y su código. |
| M7 | Código que ya está en un Item (`7794513510035`) → "Dejar este producto" | **OK**: "Este código ya está registrado: Frut-All Manzana 2 L", hoja M5 y vuelta al inicio. Diferencia menor de texto, ver B10. |
| M8 | Producto a medio hacer y recarga de la página | **OK**: el inicio muestra "Seguir con el producto sin terminar" y retoma en el paso guardado. |
| M9 | Enviar sin red (fetch y XHR forzados a fallar) → "Intentar de nuevo" con red | **OK**: "No se pudo enviar: no hay internet", "Intentar de nuevo" y "Enviar después". Al reintentar se creó **una sola** captura (CAP-2026-00006). |
| M10 | "Este producto no tiene código" | **OK**: se envía sin códigos. |
| — | Consola del navegador en `/captura` | **OK**: sin mensajes. |

| # | Caso (Desk, Administrator) | Resultado |
|---|---|---|
| — | Preparación | CAP-2026-00004 y CAP-2026-00005 se llevaron de Error a Por revisar con `reprocesar_ia()` (con `encolar_procesamiento` sustituido) + campos llenados a mano + `marcar_procesada()` (script `bench`, sin IA). |
| M11 | Lista de Captura de Producto | **Parcial**: filtro por defecto `estado = Por revisar` OK. Columnas: Nombre, Estado (dos veces), Capturado por, Confianza IA, Código. Falta la foto (B7). Acción "Exportar aprobados a Google Sheet" presente. |
| M12 | Formulario CAP-2026-00005 (confianza 55) | **Parcial**: banner rojo "Confianza de la IA baja (55%)…" OK y advertencias en amarillo OK. **No hay galería**: solo se ve la foto principal en la barra lateral; la foto de la variante 2 no aparece (B6). |
| M13 | Cambiar `medida_valor` 1 → 2 y guardar; luego "Recalcular nombre" | **OK**: guardar no cambia el nombre. Recalcular muestra la tabla antes/después y, al confirmar, queda `Frut-All 2 L` / `BEB-FRUTALL-2L` y las variantes `…-DUR-2L` y `…-MAN-2L`. Texto confuso: la columna dice "Real" en lugar de "Actual" (B11). |
| M14 | "Aprobar" | **OK**: Aprobado, `aprobado_por = Administrator`, `aprobado_en` lleno. Aparecen los botones Exportar, Volver a revisión y Crear Item. |
| M15 | "Exportar a Google Sheet" (formulario) sin credenciales | **FALLA** (B1): "Fallo al obtener el método para el comando …captura_de_producto.exportar…". Llamando al método del documento correctamente se ve el mensaje esperado "No se pudo exportar: Falta el ID del spreadsheet en Configuración Captura." El estado sigue en Aprobado, hay `error_log` y no se filtran secretos. |
| M15b | Acción de lista "Exportar aprobados a Google Sheet" (CAP-2026-00006) | **OK**: confirmación "Se exportarán 1 capturas." y error claro "Falta el ID del spreadsheet en Configuración Captura." |
| M16 | "Crear Item" (formulario) | **FALLA** (B1): el mismo error de método. Llamando al método del documento: CAP-2026-00005 creó la plantilla `BEB-FRUTALL-2L` (`has_variants = 1`, atributo Sabor) y las variantes `BEB-FRUTALL-DUR-2L` / `BEB-FRUTALL-MAN-2L` con `variant_of`, Sabor = Durazno/Manzana, EAN-13 `7791000000055` / `7791000000062`, imagen propia de cada variante, Brand Frut-All, Item Defaults (Estacion Tradicion Dev / Depósito Bebidas - ETD). CAP-2026-00004 creó `BEB-COCA-SINAZUCAR-3L` con su barcode EAN-13 e imagen. Las dos capturas pasaron a Creado con `item` enlazado. Después de Creado muestran "[Bloquea] Este producto ya existe…" (B4). |
| M17 | Configuración Captura → Probar → "Probar conexión IA" / "Probar Google Sheet" | **FALLA** (B2): el mismo error de método en los dos. Con la llamada correcta: "Falta la API key de Anthropic: cárguela y guarde antes de probar." y "Falta el ID del spreadsheet…". |
| — | Consola del Desk | Sin errores de JS propios. Las respuestas 417 con traceback son las de los `frappe.throw` anteriores (normal en developer_mode). Ningún 500. |

---

## 4. Bugs encontrados (por severidad)

### Alta

**B1. Los botones "Exportar a Google Sheet" y "Crear Item" del formulario no funcionan.** **[Corregido: `frm.call({doc: frm.doc, …})` en todos los botones (`llamar_metodo`); probado en el navegador.]**
- Dónde: `captura_productos/captura_productos/doctype/captura_de_producto/captura_de_producto.js:89` (`frm.call({ method: "exportar", freeze… })`) y `:109` (`frm.call({ method: "crear_item", … })`).
- Causa: `frm.call` con un objeto sin `doc` busca una función a nivel de módulo (`…captura_de_producto.exportar`), no el método del documento (`frappe/public/js/frappe/form/form.js:1943`). Hay que pasar `doc: frm.doc` (o usar la forma `frm.call("exportar")`).
- Pasos: Desk → captura en Aprobado → "Crear Item" → Sí (o Acciones → "Exportar a Google Sheet").
- Esperado: crea el Item (HU-12) o muestra "Falta el ID del spreadsheet…" (HU-11).
- Obtenido: "Fallo al obtener el método para el comando captura_productos…captura_de_producto.crear_item con module … has no attribute 'crear_item'". El estado no cambia.
- Impacto: HU-11 desde el formulario y HU-12 completa quedan inutilizables para el supervisor. Los tests de Python no lo detectan porque llaman a `doc.crear_item()` directamente. Funciona la acción masiva de la lista para exportar.

**B2. Los botones "Probar conexión IA" y "Probar Google Sheet" de Configuración Captura no funcionan.** **[Corregido: `frm.call` con `doc` en `configuracion_captura.js`; probado en el navegador.]**
- Dónde: `captura_productos/captura_productos/doctype/configuracion_captura/configuracion_captura.js:23`. Misma causa que B1.
- Pasos: Desk → Configuracion Captura → Probar → cualquiera de los dos.
- Esperado: OK o el error concreto en < 15 s (HU-14).
- Obtenido: "Fallo al obtener el método para el comando …configuracion_captura.probar_conexion_ia…".

### Media

**B3. El celular no deja escribir códigos internos (6 dígitos u otro largo).** **[Corregido: `codigos.errorManual` acepta lo mismo que el servidor (4 a 20 caracteres; verificador solo en 8/12/13).]**
- Dónde: `captura_productos/public/js/captura/codigos.js:65` (`errorManual` exige 8, 12 o 13 dígitos).
- Pasos: `/captura` → Fotos → Dictar → Código → "Escribir el código" → `130885` → Guardar código.
- Esperado (HU-04): se acepta, `barcode_type` vacío y advertencia. El servidor (`validation.analizar_barcode`) y `codigos.analizar()` ya lo aceptan.
- Obtenido: "El código tiene 6 números. Debe tener 8, 12 o 13. Revisa que no falte ninguno." y no se agrega.
- Nota: el ui-spec (M2) pide ese mensaje, pero contradice HU-04 y el negocio usa códigos internos (`130899`, `133646` en el ejemplo 7.3). También rechaza un GTIN-14 aunque el campo admite 14 dígitos.

**B4. Una captura en Creado (y en Exportado tras importar) muestra como bloqueante que su propio Item ya existe.** **[Corregido: los Items con el código de la propia captura no bloquean en Exportado/Creado (`permitir_items_propios`).]**
- Dónde: `captura_productos/captura_productos/doctype/captura_de_producto/captura_de_producto.py:117-118` llama a `validation.validar_captura` en cada guardado; `captura_productos/validation.py:360-368` (`buscar_en_items`) encuentra los códigos del Item recién creado.
- Pasos: aprobar una captura y crear el Item; abrir el formulario.
- Esperado: sin "[Bloquea]" sobre su propio Item.
- Obtenido: banda amarilla "[Bloquea] Este producto ya existe: Coca-Cola Sin Azucar 3 L (BEB-COCA-SINAZUCAR-3L). Código 7791000000048".
- Test que falla: `test_qa.TestQACrearItem.test_captura_creada_no_queda_con_advertencia_bloqueante_de_si_misma`.

**B5. La exportación no detecta dos capturas aprobadas con el mismo `item_code`.** **[Corregido: `_capturas_exportables` excluye la segunda captura con un `item_code` ya usado en la selección.]**
- Dónde: `captura_productos/export/sheets.py:423-440` (`_capturas_exportables` valida cada captura por separado) y `captura_productos/validation.py:571-577` (un código usado en otra captura es solo `aviso`).
- Pasos: dos capturas en Aprobado con el mismo `item_code` (el supervisor puede editarlo a mano o aprobar dos capturas del mismo producto) → exportar las dos juntas.
- Esperado (HU-11 "se revalidan… las que fallan se excluyen" y 5.6 "la unicidad se recalcula… en Exportar"): una de las dos se excluye, porque Data Import fallaría con el código repetido.
- Obtenido: las dos se exportan y el Sheet tiene `BEB-AQUARIUS-300ML` dos veces.
- Test que falla: `test_qa.TestQAExportacion.test_dos_capturas_con_el_mismo_item_code_no_se_exportan_juntas`.

**B6. El formulario no tiene la galería de fotos de la captura y de las variantes (HU-07).** **[Corregido: campo HTML `galeria` con todas las fotos (captura y variantes).]**
- Dónde: `captura_de_producto.js` / `captura_de_producto.json` (no hay campo HTML ni código de galería).
- Obtenido: solo se ve la foto principal (`image_field`) en la barra lateral; la foto de cada variante es una URL dentro de la tabla. En CAP-2026-00005 la foto de la variante 2 no se ve.

### Baja

**B7. La lista no tiene la columna de foto (HU-07) y muestra "Estado" dos veces** **[Corregido: columna de foto (miniatura) y sin columna Estado duplicada.]** (el indicador de `get_indicator` y el campo `estado` con `in_list_view`). Dónde: `captura_de_producto_list.js:14-19` y `captura_de_producto.json` (`estado.in_list_view = 1`).

**B8. `idioma_voz` de Configuración no se usa.** **[Corregido: `idioma_voz` llega en el boot de `/captura` y `voz.js` lo usa primero.]** El dictado siempre arranca en `es-BO` (`captura_productos/public/js/captura/voz.js:6` y `:46`); el valor configurado no llega al cliente.

**B9. Crear Item crea valores de atributo nuevos aunque `crear_maestros_al_exportar = 0`.** **[Corregido (decisión: prevalece la sección 13): con `crear_maestros_al_exportar = 0`, Crear Item da error claro y no crea nada. HU-12 actualizada.]**
- Dónde: `captura_productos/export/erpnext_item.py:62` (`maestros.asegurar_maestros([doc])` sin mirar el flag).
- Spec 13 (prevalece): un valor nuevo "se crea al exportar/crear Item solo si `crear_maestros_al_exportar = 1`". HU-12 dice que Crear Item crea los maestros que falten, así que puede ser una ambigüedad de la spec; conviene que el usuario la decida.
- Test que falla: `test_qa.TestQAMultiAtributo.test_crear_item_no_crea_valores_si_crear_maestros_esta_apagado`.

**B10. El aviso de código duplicado en el celular no muestra el código del Item.** **[Corregido: "Este producto ya existe: Nombre (ITEM-CODE)".]** HU-03 pide "Este producto ya existe: Coca-Cola 500 ml (BEB-COCA-500ML)"; el celular muestra "Este código ya está registrado: Frut-All Manzana 2 L" (`app.js:966` y `:1076`). Coincide con el ui-spec, pero no con la spec.

**B11. Textos confusos en el Desk.** **[Corregido: "Valor actual"/"Valor nuevo", singular/plural, "Tomar otra foto (2 de 4)" y sin texto técnico de excepciones en `/captura`.]**
- En la confirmación de "Recalcular nombre" la columna dice **"Real"** en vez de "Actual": `__("Actual")` (`captura_de_producto.js:144`) se traduce con la traducción española de Frappe para la palabra inglesa "Actual". Usar otra clave, por ejemplo "Valor actual".
- "Se exportarán 1 capturas." (`captura_de_producto_list.js:37`): falta el singular.
- Botón "Tomar otra foto (1 de 4)" con 1 foto tomada (`app.js:594`): se lee como "foto 1 de 4" cuando sería la 2.ª. Se ajusta al ui-spec, pero confunde.
- Los errores de método de B1/B2 salen con texto técnico ("Fallo al obtener el método para el comando…").

**B12. Los tests de los programadores dependen de los datos de `dev.localhost`.** **[Corregido: `test_naming` aísla los códigos `BEB-%`, `DES-%`, `GAL-%` dentro de su transacción.]** `test_naming.TestEjemplosSpec.test_ejemplos_simples` y `test_multi_atributo_seccion_13` fallan si existen Items reales con esos códigos (aquí, los que creó la prueba manual). DEV.md ya avisa que conviene no depender de datos manuales. Sugerencia: un sitio de test aparte o prefijos de código propios de los tests.

### Desvíos de diseño (documentados, no son bugs)

- **D1.** `crear_captura` se llama al tocar Enviar y no al abrir el paso 1 (decisión del ui-spec 8.5). El borrador vive en el celular (IndexedDB) y HU-06 se cumple con "Seguir con el producto sin terminar" en lugar de un Borrador del servidor con "Continuar". Mientras no se envía, el supervisor no ve nada.
- **D2.** El capturador no elige el tipo de variante (Sabor, Tamaño…): lo propone la IA (sección 13 prevalece sobre HU-02).
- **D3.** El resumen (S5) lista solo las variantes agregadas (2 en adelante); la variante 1 aparece implícita en el producto.

---

## 5. Lo que no se pudo probar

| Qué | Por qué | Cómo quedó cubierto |
|---|---|---|
| Celular real (Chrome Android, HTTPS por cloudflared), cámara trasera, `BarcodeDetector`/ZXing, linterna, vibración y pitido, "3 fallos → Escribir número" | La PC no tiene cámara ni micrófono y no se abrió el túnel | Fotos y códigos inyectados; escáner sin probar |
| Web Speech API real (texto en vivo, corte por red, `language-not-supported`, navegador sin voz) | Sin micrófono | Voz simulada sobre el `SpeechRecognition` nativo; el resto, solo revisión de `voz.js` |
| API de Claude real (extracción, reintentos 30/120 s, 401, 529, negativas) | No hay API key en el sitio, como pedía la tarea | `test_extractor.py` con Claude mockeado; en manual, Error por falta de key con mensaje claro |
| Google Sheets real (hoja compartida o no, Data Import con estado Success) | Sin credenciales de cuenta de servicio | gspread mockeado en los tests; en manual, mensaje de configuración faltante |
| Rendimiento (≤ 2 min por captura, ≤ 90 s hasta Por revisar, 200 capturas < 60 s, 4G) | Entorno local | No medido; envío local en ~0,3–2,5 s |
| Límite de 30 envíos por minuto con usuarios reales concurrentes | — | `test_limite_de_envios_por_minuto` (P) |

---

## 6. Datos que quedaron en `dev.localhost`

La prueba manual dejó estos registros (sirven de evidencia; no se borraron):

- Capturas: CAP-2026-00002, -00003 (anteriores a esta QA, en Error), **CAP-2026-00004** y **CAP-2026-00005** (Creado), **CAP-2026-00006** (Aprobado).
- Items: `BEB-COCA-SINAZUCAR-3L`, `BEB-FRUTALL-2L`, `BEB-FRUTALL-DUR-2L`, `BEB-FRUTALL-MAN-2L` (con barcodes `7791000000048`, `7791000000055` y `7791000000062`).

Mientras existan, `test_naming` tendrá las 2 fallas de B12. Para volver a 125/125 hay que borrar esos Items (y sus capturas, que en Creado no se pueden borrar sin cambiar antes el estado) o correr los tests en un sitio limpio.

---

## 7. Correcciones (2026-11-05, después de esta QA)

Se corrigieron los 12 bugs (B1–B12) y los hallazgos de la revisión de código (CR-A1, CR-M1–M6,
CR-B1–B8). Los 3 tests de `test_qa.py` que fallaban a propósito (B4, B5, B9) pasan por la corrección,
sin cambiar sus aserciones.

| Bug | Corrección | Verificación |
|---|---|---|
| B1/B2 | `frm.call` siempre con `doc: frm.doc` | `test_regresion.TestB1B2LlamadasDelDesk`; navegador: Exportar → "Falta el ID del spreadsheet…", Probar conexión IA → "Falta la API key…", Probar Google Sheet → "Falta el ID…", Recalcular nombre y Crear Item llegan a `run_doc_method` |
| B3 | `codigos.errorManual` igual que el servidor | Revisado con Node (130885, 1234 aceptados; verificador inválido y < 4 rechazados) |
| B4 | Items propios no bloquean en Exportado/Creado | `test_qa…test_captura_creada_no_queda_con_advertencia_bloqueante_de_si_misma`, `test_regresion.TestB4Creado` |
| B5 | Exportación excluye `item_code` repetido en la selección | `test_qa…test_dos_capturas_con_el_mismo_item_code_no_se_exportan_juntas` |
| B6 | Galería en el formulario | Navegador: CAP-2026-00005 muestra "Foto principal" y "Variante 2: Manzana" |
| B7 | Columna de foto, Estado solo en el indicador | Navegador (lista) |
| B8 | `idioma_voz` en el boot | `test_regresion.TestB8YExtra` |
| B9 | Crear Item respeta `crear_maestros_al_exportar` | `test_qa…test_crear_item_no_crea_valores_si_crear_maestros_esta_apagado`, `test_regresion.TestCRM5CrearItem` |
| B10–B11 | Textos | Revisión del código y navegador |
| B12 | `aislar_codigos` en `test_naming` | Suite verde con los Items de la prueba manual todavía en el sitio |

Cambios de tests justificados (el comportamiento esperado cambió por los hallazgos CR):

- El capturador ya no puede crear ni editar capturas por el ORM/REST (CR-A1): los tests que lo hacían
  ahora preparan los datos con `utils.insertar_de()` y `test_capturador_crea_y_envia_las_suyas` verifica
  que `insert()` directo da PermissionError.
- `reprocesar_ia` desde Procesando ahora es válido para el supervisor si no hay job (CR-M2):
  `TestQAEstados.PERMITIDOS` lo incluye; el rechazo con job en cola está en
  `test_extractor.TestAtascadas`.
- `frappe.client.set_value(estado)` del capturador ahora falla por permiso (PermissionError) antes que
  por validación: el test acepta cualquiera de los dos rechazos.
- Los reintentos de la IA ya no duermen dentro del job (CR-M3): `test_extractor` prueba el reintento
  programado (`reintentar_ia_en`, `reencolar_reintentos`) en lugar de `llamar_con_reintentos`.
- El capturador reintenta con `reintentar()` (api); `reprocesar_ia()` del Desk es solo de supervisores.

Tests nuevos: `test_regresion.py` (CR-A1, CR-M1, CR-M4, CR-M5, B1/B2, B4, B8, CR-B2, CR-B8, URL base de
imágenes) y en `test_extractor.py` `TestAtascadas` (CR-M2), reintentos programados y transacción cerrada
durante la llamada (CR-M3), `TestLecturaDeFotos` (CR-A1 en el job y CR-B3).

Resultado: `bench --site dev.localhost run-tests --app captura_productos` → 25 unit OK + 192 integration
OK (217 en total, 0 fallas).
