# Especificación de UI: pantalla móvil `/captura`

Versión 1 · Prototipo de referencia: `docs/ui/prototipo.html` (ábrelo en Chrome y usa el modo de dispositivo a 360–412 px; el panel amarillo de arriba simula permisos, voz, códigos e internet).

---

## 1. Para quién y principios

**Usuarios:** personal de un negocio en Bolivia, con conocimiento muy básico de celulares. Usan **Chrome en Android**. Hoy escriben mal los nombres de los productos.

**Principios que guían cada pantalla:**

1. **Un paso por pantalla.** Cada paso tiene un título que dice qué hacer ("Toma fotos del producto").
2. **Botones grandes con ícono y texto corto.** Mínimo 56 px de alto; la acción principal de cada paso mide 72 px.
3. **No hace falta escribir.** El teclado solo aparece en los respaldos (sin voz, código ilegible).
4. **Siempre se sabe dónde está:** indicador "Paso 2 de 5: Dictar" con íconos.
5. **Los errores dicen qué pasó y qué hacer**, con un botón para hacerlo.
6. **Alto contraste y nunca solo color:** cada estado lleva ícono y texto.
7. **Nada se pierde:** el borrador se guarda en el celular en cada cambio.
8. **Español simple, tuteo neutro boliviano**: "Toca", "Saca", "Escanea", "Di". Sin voseo ni tecnicismos ("Procesando", no "En cola").

---

## 2. Mapa de pantallas

| ID | Ruta (hash) | Pantalla | Llega desde | Sale a |
|---|---|---|---|---|
| S0 | `#/inicio` | Inicio | abrir app, éxito, salir | S1, S8 |
| S1 | `#/fotos` | Paso 1: Fotos | S0 "Nuevo producto" / "Seguir…" | S2, S0 (salir) |
| S2 | `#/dictar` | Paso 2: Dictar | S1 | S3, S1 |
| S3 | `#/codigo` | Paso 3: Código de barras | S2 | S4, S2, S0 (código ya registrado) |
| S4 | `#/variantes` | Paso 4: ¿Tiene variantes? / Lista de variantes | S3 | S4a, S5 |
| S4a | `#/var-foto` | Variante: foto (1 de 3) | S4 "Agregar variante N" | S4b, S4 (salir) |
| S4b | `#/var-dictar` | Variante: dictado corto (2 de 3) | S4a | S4c |
| S4c | `#/var-codigo` | Variante: código (3 de 3) | S4b | S4 ("Guardar variante") |
| S5 | `#/resumen` | Paso 5: Revisa y envía | S4 | S6, S1–S4 ("Cambiar") |
| S6 | `#/exito` | ¡Listo! | S5 | S1 ("Siguiente producto"), S8 |
| S8 | `#/capturas` | Mis capturas | S0, S6 | S0, S1 |
| M1 | (hoja) | ¿Quieres salir? | "Salir" en S1–S5, "Atrás" en S1 | S0 o se queda |
| M2 | (hoja) | Escribe el código | S3/S4c "Escribir el código a mano" | vuelve al paso |
| M3 | (hoja) | ¿Dejar esta variante? | "Salir" en S4a–S4c | S4 o se queda |
| M4 | (hoja) | Tienes un producto sin terminar | S0 "Nuevo producto" con borrador | S1 nuevo o paso guardado |
| M5 | (hoja) | ¿Dejar este producto? | S3 "Dejar este producto" (duplicado) | S0 |

```
S0 Inicio ──Nuevo producto──> S1 Fotos ─> S2 Dictar ─> S3 Código ─> S4 ¿Variantes?
   │                                                                  │ No        │ Sí
   │                                                                  v           v
   │                                                               S5 Resumen <─ Lista variantes
   │                                                                  │            │ Agregar
   │                                                                  │            v
   │                                                                  │      S4a Foto ─> S4b Dictar ─> S4c Código ─┐
   │                                                                  │            ^────────── Guardar variante ───┘
   │                                                                  v
   └──Mis capturas──> S8 <──Ver mis capturas── S6 ¡Listo! ──Siguiente producto──> S1
```

**Navegación:** el botón Atrás de Android vuelve al paso anterior (usar `history.pushState` por paso). En el paso 1, "Atrás" abre la hoja M1.

---

## 3. Flujos de usuario

### F1. Producto simple (el más frecuente, ~60–90 s)
1. Inicio → **Nuevo producto**.
2. **Tomar foto** → se abre la cámara del celular → foto → vuelve con la miniatura ("Preparando…" ≈1 s) → (opcional) más fotos, **Principal**, **Borrar** → **Siguiente**.
3. Toca el **micrófono** → habla → ve el texto en vivo → termina solo o toca el botón rojo → ve "Lo que dijiste" → **Siguiente** (o **Repetir** / **Corregir**).
4. La cámara ya está abierta → el código se lee solo (vibración + pitido + recuadro verde + "¡Código leído!") → **Siguiente**.
5. **No, es uno solo** → pasa directo al resumen.
6. Revisa → **Enviar** → "Enviando…" → **¡Listo!** → **Siguiente producto**.

### F2. Producto con variantes (ej. jugo en 3 sabores)
Pasos 1–3 igual con la primera variante (ej. durazno). En el paso 4: **Sí, tiene** → **Agregar variante 2** → foto → "sabor manzana" → código → **Guardar variante** → repetir → **Siguiente** → resumen → enviar.

### F3. Errores y caminos alternos
| Situación | Dónde | Qué ve la persona | Salida |
|---|---|---|---|
| Sin permiso de cámara (fotos) | S1 | Aviso rojo con 4 pasos para dar permiso | "Intentar de nuevo" / "Elegir de la galería" |
| Sin permiso de cámara (escáner) | S3, S4c | Aviso rojo con 4 pasos | "Intentar de nuevo" / "Escribir el código" |
| Sin permiso de micrófono | S2, S4b | Aviso rojo con 4 pasos | "Intentar de nuevo" / "Prefiero escribir" |
| Navegador sin reconocimiento de voz | S2, S4b | Campo de texto + consejo: usar el micrófono del teclado | escribir |
| No se escuchó nada | S2, S4b | Aviso amarillo "No te escuché" | micrófono otra vez |
| Sin internet al dictar | S2, S4b | Aviso amarillo "Para dictar necesitas internet" | escribir o esperar |
| Código ya registrado en ERPNext u otra captura | S3, S4c | Aviso rojo "Este código ya está registrado: Coca-Cola 500 ml" | "Escanear otro código" / "Dejar este producto" |
| Código ya usado en otra variante de esta captura | S4c | "Ya usaste este código en: Variante 2" | "Escanear de nuevo" |
| Dígito verificador inválido | S3, S4c | Aviso amarillo "El código no se leyó bien" | "Escanear de nuevo" / "Escribir el código" |
| Producto sin código de barras | S3, S4c | Casilla "Este producto no tiene código" | Siguiente |
| Sin internet al enviar | S5 | Aviso rojo + producto guardado en el celular | "Intentar de nuevo" / "Enviar después" → S6 "Guardado" |
| Error al cargar Mis capturas | S8 | Aviso rojo | "Intentar de nuevo" |
| Sale a mitad | S1–S5 | Hoja M1; el borrador queda | Inicio muestra "Seguir con el producto sin terminar" |

---

## 4. Pantallas: contenido, estados y textos exactos

Notación: **[Botón]**, «texto en pantalla». Textos entre `{}` son variables.

### Estructura común de un paso (S1–S5, S4a–S4c)
```
┌ cp-header ───────────────────────────┐  sticky arriba, 64 px
│ [✕ Salir]        Nuevo producto      │  (en variantes: «Variante {n}»)
├ cp-progress ─────────────────────────┤  se desplaza con la página
│  (◉)──( )──( )──( )──( )             │  ✓ hecho · ◉ actual · ○ pendiente
│      Paso {i} de {n}: {Nombre}       │
├ cp-main ─────────────────────────────┤
│ H1 título del paso                   │
│ contenido                            │
├ cp-footer ───────────────────────────┤  sticky abajo
│ «pista si Siguiente no está listo»   │
│ [← Atrás]       [Siguiente →]        │  2fr / 3fr
└──────────────────────────────────────┘
```
- Nombres de pasos: Fotos · Dictar · Código · Variantes · Revisar y enviar. En variantes: Foto · Dictar · Código.
- **Siguiente no se desactiva del todo:** se ve gris (`aria-disabled="true"`) y, si lo tocan, la pista de arriba tiembla en rojo y se anuncia. Así la persona entiende *por qué* no avanza.
- Si se llegó desde el resumen con "Cambiar", **Siguiente** dice «Volver al resumen».

### S0 · Inicio
- Barra azul: «Captura de productos» / «Hola, {nombre}».
- Si no hay internet: banda oscura «Sin internet. Puedes capturar igual; enviaremos cuando vuelva la señal.»
- H1 «¿Qué quieres hacer?»
- Si hay borrador: [✎ Seguir con el producto sin terminar] (amarillo, 64 px) → al paso donde se quedó.
- **[📷 Nuevo producto]** botón héroe (≥200 px de alto), subtítulo «Fotos, voz y código de barras». Con borrador abre M4.
- **[≡ Mis capturas →]** fila de 88 px con resumen: «{n} procesando · {n} por revisar · {n} con error · {n} sin enviar» (solo los que existan) o «Todavía no enviaste productos».

### S1 · Fotos
- H1 «Toma fotos del producto». Texto: «Saca el frente, con la marca bien visible. Puedes sacar hasta 4 fotos.»
- **Vacío:** recuadro punteado con cámara: «Todavía no hay fotos» / «Toca el botón azul para abrir la cámara.» + **[📷 Tomar foto]** (azul, 72 px) + [Elegir de la galería] (enlace).
- **Con fotos:** cuadrícula 2 columnas (4:3). Cada foto: insignia «★ Principal» en la principal (borde azul 3 px); debajo dos botones de 56 px: [☆ Principal] o la etiqueta «Foto principal», y [🗑 Borrar] (rojo). Botón pasa a secundario: **[📷 Tomar otra foto ({n} de 4)]**. Con 4 fotos: «Ya tienes 4 fotos (máximo)» desactivado.
- Ayuda con 2+ fotos: «La foto principal es la que se verá en el sistema. Toca **Principal** en otra foto para cambiarla.»
- **Cargando** (reduciendo la foto): capa oscura sobre la miniatura con spinner y «Preparando…». Siguiente sigue gris hasta que termine.
- **Borrar:** sin confirmación; aviso flotante «Foto borrada» con [Deshacer] (6 s). Si se borra la principal, la primera que queda pasa a ser principal.
- **Sin cámara:** aviso rojo «No se pudo abrir la cámara» / «Tu celular no dejó usar la cámara.» + pasos de permiso (ver §4.10) + [Intentar de nuevo] [Elegir de la galería].
- Pista de Siguiente: «Toma al menos 1 foto para seguir.»

### S2 · Dictar
- H1 «Di qué producto es».
- Guía azul: **«Di: marca, tipo, sabor y tamaño»** / «Ejemplo: «Coca-Cola, gaseosa, sin azúcar, 3 litros»» (el ejemplo se oculta mientras escucha para ganar espacio).
- **Listo para dictar:** micrófono redondo azul de 128 px, «Toca el micrófono y habla», enlace [⌨ Prefiero escribir].
- **Escuchando:** el cuadro «Lo que vas diciendo:» va **arriba** del botón (texto final en negro, parcial en gris), botón rojo con cuadrado y pulso, «Te estoy escuchando…» / «Toca el botón rojo cuando termines». Pista: «Termina de hablar para seguir.»
- **Terminado:** cuadro «Lo que dijiste:» con el texto en 24 px; [↺ Repetir] [✎ Corregir]; nota «No importa si está un poco mal escrito: el sistema lo corrige y tu supervisor lo revisa.»
- **Escribiendo (Corregir / Prefiero escribir):** etiqueta «Escribe el producto», `textarea` (placeholder «Ej.: Coca-Cola gaseosa sin azúcar 3 litros») + [🎤 Mejor dictar con la voz].
- **Sin soporte de voz:** el campo de texto primero y debajo aviso azul «Aquí no se puede dictar» / «Tu navegador no permite dictar en esta página. **Consejo:** toca el micrófono 🎤 del teclado de tu celular para dictar igual.»
- **No te escuché:** aviso amarillo «No te escuché» / «Habla más cerca del celular, sin ruido alrededor, y toca el micrófono otra vez.»
- **Sin permiso:** aviso rojo «No podemos usar el micrófono» / «Para dictar, la página necesita permiso del micrófono.» + pasos + [Intentar de nuevo]; debajo [Prefiero escribir].
- **Sin internet:** aviso amarillo «Para dictar necesitas internet» / «No hay señal ahora. Escribe el producto o espera a tener señal y toca el micrófono.»
- Pista de Siguiente: «Dicta o escribe el producto para seguir.»

### S3 · Código de barras
- H1 «Escanea el código de barras».
- **Abriendo:** vista negra con spinner «Abriendo la cámara…».
- **Buscando:** vista de cámara 4:3, recuadro blanco (resto oscurecido), línea roja que sube y baja, botones arriba a la derecha [⚡ Luz] (solo si la cámara tiene linterna) y [🔊 Sonido / Sin sonido], estado «Buscando código…». Debajo: «Pon el código dentro del recuadro. Se lee solo.»
- **Leído:** recuadro verde grueso, «✓ ¡Código leído!», vibración + pitido; la cámara se pausa. Lista «Códigos leídos ({n})» con tarjeta verde por código (`7 773105 000129`, formato GTIN-13 legible) y [🗑 Quitar]. Botón [+ Escanear otro código] y ayuda «Algunos productos tienen más de un código. Si ves otro, escanéalo también.»
- **En pausa** (tras error): la vista se reduce a una franja de 72 px «Cámara en pausa» para que el aviso se vea sin desplazar.
- **Duplicado:** aviso rojo «Este código ya está registrado: {item_name}» / «No hace falta capturarlo otra vez. Si es otro producto, escanea el código correcto.» + [Escanear otro código] [Dejar este producto]. Si el duplicado es otra captura pendiente: «Este código ya lo capturó {persona}: {texto} (por revisar)».
- **Dígito verificador mal:** aviso amarillo «El código no se leyó bien» / «Los números **{código}** no son un código válido. Vuelve a escanear con más luz o con el código más derecho.» + [Escanear de nuevo] [Escribir el código].
- Siempre: enlace [⌨ Escribir el código a mano]; si no hay códigos, casilla grande «☐ Este producto no tiene código» (`aria-pressed`). Marcarla pausa la cámara.
- Repetido en la misma lista: aviso flotante «Ese código ya está en la lista».
- **Sin permiso:** aviso rojo «No podemos usar la cámara» / «Para leer el código, la página necesita la cámara.» + pasos + [Intentar de nuevo] [Escribir el código].
- Pista: «Escanea un código o marca «no tiene código».»

#### M2 · Hoja «Escribe el código»
«Copia los números que están debajo de las barras.» · etiqueta «Números del código» · campo numérico grande centrado (`inputmode="numeric"`, máx. 14) · [✓ Guardar código] [Cancelar].
Errores bajo el campo (con ícono, `aria-invalid`):
- vacío: «Escribe los números del código.»
- largo: «El código tiene {n} números. Debe tener 8, 12 o 13. Revisa que no falte ninguno.»
- verificador: «Estos números no forman un código válido. Revisa número por número.»
Al guardar se valida igual que un código leído (duplicados incluidos).

### S4 · Variantes
- **Pregunta:** H1 «¿Este producto tiene variantes?» / «Variantes son el mismo producto en otro sabor, tamaño o color. Ejemplo: jugo Del Valle de durazno, de manzana y de naranja.» Dos tarjetas de 96 px:
  - [◈ **Sí, tiene** · Hay otros sabores, tamaños o colores]
  - [✓ **No, es uno solo** · Pasar a revisar y enviar] → va directo a S5.
  - Pie solo con [← Atrás] (la elección es la acción).
- **Lista:** H1 «Variantes del producto» / «Por cada variante: 1 foto, lo que cambia (ej. «sabor durazno») y su código.»
  - **[+ Agregar variante {n}]** arriba (72 px) para no tener que bajar.
  - «Variantes ({total})» y tarjetas, la más nueva primero; al final «Variante 1 · la que ya registraste» (no se borra). Cada tarjeta: miniatura, «{dictado}», código o «Sin código», [🗑 Borrar] con [Deshacer].
  - Sin variantes agregadas: «La variante 1 es la que ya registraste. Toca **Agregar variante 2** para seguir.»
  - Enlace [Cambié de idea: no tiene variantes].
  - Pista: «Agrega al menos 1 variante, o elige «no tiene variantes».»

### S4a–S4c · Sub-flujo de variante (3 pasos)
Mismos componentes que S1–S3 con encabezado «Variante {n}».
- S4a «Toma una foto de esta variante»: 1 sola foto (a lo ancho); al tenerla, [↺ Volver a tomar la foto]. Vacío: «Que se vea bien el sabor, tamaño o color.»
- S4b «¿Qué cambia en esta variante?»: guía «Di solo lo que cambia» / «Ejemplo: «sabor durazno» o «tamaño 2 litros»».
- S4c «Escanea el código de esta variante»: Siguiente se llama **[✓ Guardar variante]** → vuelve a S4 con aviso «Variante guardada».
- **Salir** abre M3: «¿Dejar esta variante?» / «Se borra lo que hiciste en esta variante. Las demás quedan guardadas.» [Seguir con la variante] (principal) [Sí, dejarla] (rojo).

### S5 · Revisa y envía
- H1 «Revisa y envía». Tarjetas, cada una con [✎ Cambiar] (vuelve al paso y luego al resumen):
  «Fotos» (miniaturas 72 px, principal con borde azul) · «Lo que dijiste» («{dictado}») · «Código de barras» (lista o «No tiene código») · «Variantes» (lista o «No tiene variantes»).
- Nota: «ⓘ El nombre final lo arma el sistema y lo revisa tu supervisor.»
- Pie: [← Atrás] **[➤ Enviar]** (verde).
- **Enviando:** pantalla con spinner «Enviando…» / «No cierres la página. Esto tarda unos segundos.» (si hay varias fotos, mostrar «Subiendo foto {i} de {n}»).
- **Error sin internet:** aviso rojo «No se pudo enviar: no hay internet» / «Tu producto quedó guardado en este celular. Toca **Intentar de nuevo** cuando tengas señal.» [↺ Intentar de nuevo] [🕒 Enviar después].
- **Error del servidor** (no previsto en el prototipo): «No se pudo enviar. Tu producto quedó guardado en este celular. Intenta de nuevo en un rato; si sigue fallando, avisa a tu supervisor.»

### S6 · ¡Listo!
- Círculo verde 120 px con ✓, H1 «¡Listo!», «Tu producto se envió. Tu supervisor lo va a revisar.», «{dictado}».
- **[📷 Siguiente producto]** (72 px) y [≡ Ver mis capturas].
- Variante "Enviar después": H1 «Guardado», «Lo enviaremos automáticamente cuando vuelva la señal. Puedes seguir capturando.»
- Opcional: vibración corta de éxito.

### S8 · Mis capturas
- Encabezado [← Inicio] «Mis capturas». Fila: «Aquí ves en qué va cada producto que enviaste.» + [↺ Actualizar].
- Tarjeta por captura: miniatura 72 px, título (`item_name` si ya existe, si no la transcripción), «Hoy, 10:40 · 3 variantes», chip de estado.

| Estado (DocType) | Chip | Ícono | Colores | Texto extra |
|---|---|---|---|---|
| Procesando | **Procesando** | ⟳ (gira) | info: `#DBEAFE` / `#1E3A8A` | — |
| Por revisar | **Por revisar** | 🕒 | warning: `#FEF3C7` / `#78350F` | — |
| Error | **Error** | ⓘ | danger: `#FEE2E2` / `#7F1D1D` | motivo simple, ej. «No se entendió el tamaño. Tu supervisor lo va a corregir.» |
| Aprobado / Exportado / Creado | **Aprobado** | ✓ | success: `#DCFCE7` / `#14532D` | — |
| (local) en cola sin enviar | **Sin enviar** | wifi tachado | neutral: `#E5E7EB` / `#1F2937` | «Se enviará cuando haya señal.» + [➤ Enviar ahora] |

- Plegable «ⓘ ¿Qué significa cada color?» con una línea por chip.
- **Cargando:** 3 tarjetas esqueleto (`aria-busy`). **Vacío:** «Todavía no tienes capturas» / «Cuando envíes un producto, aparecerá aquí.» + [Nuevo producto]. **Error:** «No pudimos traer tus capturas» / «Revisa que tengas internet y toca **Intentar de nuevo**.»
- Pie fijo [📷 Nuevo producto].
- Orden: más reciente primero; mostrar las últimas 50 (o 7 días).

### Hojas M1 y M4
- M1: «¿Quieres salir?» / «Guardamos lo que hiciste en este celular. Puedes seguir después desde el inicio.» **[Seguir capturando]** (principal, recibe el foco) [Salir].
- M4: «Tienes un producto sin terminar» / «Si empiezas uno nuevo, se borra el que dejaste a medias.» **[Seguir con el anterior]** [Empezar uno nuevo] (rojo).
- M5: «¿Dejar este producto?» / «Ya está registrado, no hace falta capturarlo. Se borran las fotos y lo que dictaste.» **[Seguir con el producto]** [Sí, dejarlo] (rojo).
- En todas, la opción segura es la principal y la primera; la destructiva va abajo y en rojo.

### 4.10 Pasos de permiso (texto común)
1. «Arriba, junto a la dirección de la página, toca el candado 🔒.»
2. «Toca **Permisos** y luego **Cámara** / **Micrófono**.»
3. «Elige **Permitir**.»
4. «Vuelve aquí y toca **Intentar de nuevo**.»

(Si la app está instalada como PWA no hay barra de dirección: mostrar «Abre **Ajustes** del celular → **Aplicaciones** → **Chrome** → **Permisos** → **Cámara** → **Permitir**».)

---

## 5. Sistema de diseño (tokens)

Todos definidos en `:root` del prototipo; copiar el bloque tal cual a `public/css/captura.css`. Tema solo claro (`color-scheme: light`) por contraste en exteriores y consistencia.

### Color (contraste verificado WCAG AA)
| Token | Valor | Uso | Contraste |
|---|---|---|---|
| `--cp-color-bg` | `#F1F3F6` | fondo de página | — |
| `--cp-color-surface` | `#FFFFFF` | tarjetas, encabezado, pie | — |
| `--cp-color-text` | `#111827` | texto | 16.9:1 |
| `--cp-color-text-muted` | `#374151` | texto secundario | 10.3:1 (9.3 sobre bg) |
| `--cp-color-border` | `#6B7280` | bordes de controles | 4.8:1 |
| `--cp-color-divider` | `#D1D5DB` | separadores (decorativo) | — |
| `--cp-color-primary` | `#1D4ED8` | botón principal, foco de pasos | 6.7:1 con blanco |
| `--cp-color-primary-pressed` | `#1E3A8A` | presionado | — |
| `--cp-color-primary-soft` | `#DBEAFE` | fondos suaves | — |
| `--cp-color-success` | `#15803D` | Enviar, éxito | 5.0:1 con blanco |
| `--cp-color-warning` / `-soft` / `-text` | `#B45309` / `#FEF3C7` / `#78350F` | avisos, "Por revisar" | 8.9:1 |
| `--cp-color-danger` / `-soft` / `-text` | `#B91C1C` / `#FEE2E2` / `#7F1D1D` | errores, borrar | 6.5:1 / 9.2:1 |
| `--cp-color-info-soft` / `-text` | `#DBEAFE` / `#1E3A8A` | guía, "Procesando" | 8.6:1 |
| `--cp-color-neutral-soft` / `-text` | `#E5E7EB` / `#1F2937` | "Sin enviar", etiquetas | 12.6:1 |
| `--cp-color-disabled-bg` / `-text` | `#E5E7EB` / `#4B5563` | botón no disponible | 6.0:1 |
| `--cp-color-scanner-*` | `#0B1220`, `#FFFFFF`, `#22C55E`, `#F43F5E` | escáner | — |
| `--cp-color-focus` / `-inverse` | `#111827` / `#FDE047` | anillo de foco (claro / oscuro) | — |

### Tipografía
Fuente del sistema (`system-ui, Roboto…`), sin descargas. Unidades `rem` para respetar el tamaño de letra del celular.
| Token | Tamaño | Uso |
|---|---|---|
| `--cp-font-xs` | 14 px | solo etiquetas cortas (botones de foto, insignias) |
| `--cp-font-sm` | 16 px | ayudas, chips |
| `--cp-font-base` | 18 px | texto normal |
| `--cp-font-lg` | 20 px | botones, subtítulos |
| `--cp-font-xl` | 24 px | H1 de cada paso, texto dictado |
| `--cp-font-xxl` | 28 px | "Nuevo producto", "¡Listo!" |
Interlineado 1.4; negrita 700 para títulos y botones.

### Espaciado, forma y tamaños
- Espaciado base 4: `--cp-space-1..7` = 4, 8, 12, 16, 24, 32, 48 px. Margen lateral de pantalla 16 px.
- Radios: 8 / 12 / 16 / 999 px.
- **Toque:** `--cp-touch-min` 56 px (cualquier control), `--cp-touch-lg` 64 px (pie), `--cp-touch-xl` 72 px (acción principal del paso), micrófono 128 px, botón de inicio ≥200 px.
- Íconos 24 px (32 en botones grandes, 56 en estados vacíos), trazo 2.25.

### Componentes (clases `cp-`)
| Componente | Clases | Estados |
|---|---|---|
| Botón | `cp-btn` + `--primary` `--secondary` `--ghost` `--danger` `--success` `--warning`; tamaño `--lg` `--xl`; `--block` | normal, `:active` (escala .98), `aria-disabled`/`is-disabled`, `:disabled`, foco |
| Botón de inicio | `cp-hero-btn` (`__icon` `__label` `__hint`) | — |
| Fila de menú | `cp-menu-btn` | — |
| Opción Sí/No | `cp-choice` | `aria-pressed` |
| Casilla grande | `cp-toggle` (`__box`) | `aria-pressed` |
| Encabezado / pie | `cp-header`, `cp-header-btn`, `cp-footer`, `cp-footer__row`, `cp-footer__hint` | pista `is-shake` |
| Progreso | `cp-progress`, `__step.is-done/.is-current`, `__dot`, `__text` | — |
| Aviso | `cp-alert--danger/--warning/--info/--success` (`__title` `__text` `__steps` `__actions`) | — |
| Chip de estado | `cp-chip--procesando/--revisar/--error/--aprobado/--pendiente` | — |
| Fotos | `cp-photo-grid(--single)`, `cp-photo(.is-principal)`, `__frame` `__badge` `__loading` `__actions` `__action(--danger)` `__note`, `cp-thumbs`, `cp-thumb` | cargando |
| Dictado | `cp-guide`, `cp-mic(.is-listening)`, `cp-mic__caption`, `cp-transcript(.is-live)` `__interim` | — |
| Escáner | `cp-scanner(.is-read/.is-paused)`, `__frame` `__line` `__status` `__tools` `__tool` `__msg`; lista `cp-code-list`, `cp-code(.is-new)` | — |
| Formularios | `cp-field`, `cp-label`, `cp-input(--code)`, `cp-textarea`, `cp-field-error` | `aria-invalid` |
| Listas | `cp-list`, `cp-card`, `cp-item` (`__thumb` `__body` `__title` `__meta` `__note` `__actions`), `cp-tag` | — |
| Estados | `cp-empty`, `cp-state` (`__icon--success`), `cp-skeleton`, `cp-spinner(--light)`, `cp-banner` | — |
| Hojas y avisos | `cp-sheet-backdrop`, `cp-sheet`, `cp-toast-region`, `cp-toast` (`__action`) | — |

Todo lo que empieza con `demo-` (panel amarillo, código de barras falso) es solo del prototipo.

---

## 6. Responsive

- Diseño **mobile-first**, probado a 360, 375 y 412 px. Contenedor `max-width: 480px` centrado; en tablet/escritorio se ve como columna central (no hace falta otro diseño: es una herramienta de celular).
- Encabezado y pie `position: sticky`; el pie respeta `env(safe-area-inset-bottom)` (gestos de Android). Usar `100dvh`.
- Con letra grande del sistema (130 %) los botones crecen en alto, no se cortan: no usar alturas fijas en texto, solo `min-height`.
- Orientación vertical; si gira, todo sigue funcionando (sin bloqueo).
- Verificado en el prototipo: sin desplazamiento horizontal y ningún control menor a 56×56 px en las 15 pantallas/estados.

---

## 7. Accesibilidad

- `lang="es"`; cada pantalla tiene un único `h1`; al cambiar de pantalla el foco va al `h1` (`tabindex="-1"`) y la página vuelve arriba.
- Región `aria-live="assertive"` oculta (`#cp-live`) para anunciar: «Foto 2 lista», «Te estoy escuchando», «Código leído: …», errores. Avisos flotantes en región `polite`.
- Progreso: `<nav aria-label="Progreso">` con `aria-current="step"` y etiquetas ocultas «Fotos (hecho)».
- Botones con ícono siempre tienen texto visible; los que solo muestran ícono (micrófono) llevan `aria-label` («Empezar a dictar» / «Terminar de dictar»). Íconos SVG con `aria-hidden="true"`.
- Foco visible: anillo 3 px `--cp-color-focus` con separación 3 px; sobre fondos oscuros (escáner, aviso flotante) se usa `--cp-color-focus-inverse` amarillo.
- Hojas: `role="dialog"`/`alertdialog`, `aria-modal`, foco atrapado, `Esc` cierra, al cerrar el foco vuelve al botón que la abrió.
- Formularios con `<label for>`, errores con `aria-invalid` + `aria-describedby`.
- Estados nunca solo por color: chip = ícono + palabra; códigos válidos con ✓; error con ⓘ.
- `prefers-reduced-motion`: se apagan pulso, línea del escáner, giro y temblor.
- Feedback no solo sonoro: lectura de código = vibración + sonido (desactivable) + recuadro verde + texto.

---

## 8. Notas de implementación para el programador

### 8.1 Estructura sugerida
- `www/captura.html` (página con login requerido, rol **Capturador de Productos**) con el sprite SVG y `<div class="cp-app" id="cp-root">`.
- `public/css/captura.css`: copiar secciones 1–15 del `<style>` del prototipo (no la sección "SOLO PROTOTIPO").
- `public/js/captura/`: `router.js` (hash + `history.pushState`), `store.js` (borrador en IndexedDB), `camera-photo.js`, `speech.js`, `scanner.js`, `api.js`, `views/*.js`. El prototipo usa plantillas de texto; el programador puede reutilizar esas funciones de vista (`views.fotos`, `photosBlock`, `codeBlock`, etc.) casi tal cual.
- El prototipo incluye `checkDigit()` / `isValidGtin()` listos para copiar.

### 8.2 Fotos (paso 1 y variantes)
- `<input type="file" accept="image/*" capture="environment" hidden>` disparado por **Tomar foto**; para **Elegir de la galería**, otro input sin `capture`. No requiere `getUserMedia` (el permiso lo maneja Android); si el `change` llega sin archivo, no mostrar error (la persona canceló). Mostrar el aviso "No se pudo abrir la cámara" solo si falla la lectura del archivo o si `navigator.permissions.query({name:'camera'})` devuelve `denied`.
- **Compresión en el navegador antes de subir:** `createImageBitmap(file, { imageOrientation: 'from-image' })` → `canvas`/`OffscreenCanvas` con el lado mayor a **1280 px** → `canvas.toBlob(cb, 'image/jpeg', 0.8)` (≈150–300 KB). Esto corrige la rotación EXIF y elimina metadatos (GPS). Mientras tanto, estado "Preparando…".
- Guardar el `Blob` en IndexedDB (el borrador sobrevive a recargas y a quedarse sin batería). `localStorage` no sirve para fotos.
- Máximo 4 fotos por producto y 1 por variante; la primera es principal por defecto.

### 8.3 Dictado: Web Speech API
```js
const SR = window.SpeechRecognition || window.webkitSpeechRecognition; // si no existe -> estado "nosupport"
const rec = new SR();
rec.lang = 'es-BO';          // si da error 'language-not-supported', reintentar con 'es-419' y luego 'es-ES'
rec.interimResults = true;   // texto en vivo (parcial en gris)
rec.continuous = true;       // Android puede cortar igual tras un silencio; manejar onend
rec.maxAlternatives = 1;
```
- `onresult`: concatenar los `isFinal` en el texto final y mostrar el resto como parcial.
- `onend`: si hay texto → estado "done"; si no → "nohear".
- `onerror` → estado: `not-allowed` / `service-not-allowed` → "denied"; `no-speech` → "nohear"; `network` → "offline"; `audio-capture` → "denied" con texto «No encontramos el micrófono»; `aborted` → ignorar.
- Chrome Android envía el audio a los servidores de Google: **necesita internet**. Comprobar `navigator.onLine` antes de empezar.
- Botón rojo = `rec.stop()`. Poner un tope de 20 s por seguridad.
- El texto se envía tal cual (`transcripcion`); la corrección (glosario, "tres litros" → "3 L") la hace el servidor.

### 8.4 Código de barras: BarcodeDetector con respaldo ZXing
- Cámara: `getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false })` en un `<video playsinline muted>` dentro de `.cp-scanner`. Abrirla al entrar al paso; **detener las pistas** (`track.stop()`) al leer, al salir del paso o al pasar la app a segundo plano (`visibilitychange`).
- Si `'BarcodeDetector' in window` y `BarcodeDetector.getSupportedFormats()` incluye `ean_13`: `new BarcodeDetector({ formats: ['ean_13','ean_8','upc_a','upc_e','code_128'] })` y `detect(video)` cada ~150 ms (`requestVideoFrameCallback` o `setTimeout`).
- Si no: cargar **ZXing** (`@zxing/browser`, `BrowserMultiFormatReader`) empaquetado en la app y solo bajo demanda (import dinámico) para no pesar en celulares sin necesidad.
- **Estabilidad:** aceptar un código solo si se lee igual **2 veces seguidas** (evita lecturas falsas).
- **Validación en el cliente:** solo dígitos; largo 8, 12, 13 o 14; dígito verificador GTIN (función `checkDigit`). UPC-E: expandir a UPC-A antes de validar. Normalizar a GTIN-13 (UPC-A con `0` adelante) para comparar duplicados. `code_128` u otros no-GTIN: aceptar sin verificador pero marcar en `advertencias_ia`.
- **Duplicados:** llamar a un endpoint, ej. `captura_productos.api.verificar_codigo(codigo)` → `{ existe: bool, origen: 'item'|'captura', nombre: 'Coca-Cola 500 ml', capturado_por, estado }`. Buscar en `Item Barcode` y en capturas no rechazadas. Además, comprobar en el cliente contra los códigos del producto y de sus variantes. Si no hay internet, aceptar el código y dejar la verificación al servidor.
- **Linterna:** si `track.getCapabilities().torch`, mostrar [Luz] y usar `track.applyConstraints({ advanced: [{ torch: true }] })`. Si no, ocultar el botón.
- **Feedback:** `navigator.vibrate(80)` en éxito y `[60,60,60]` en error (requiere que la persona ya haya tocado la página; en la práctica siempre ocurre). Pitido con Web Audio (oscilador 1320 Hz, 120 ms, volumen 0.12). Preferencia de sonido en `localStorage` (`cp.sound`), activado por defecto.

### 8.5 Envío, cola sin internet y "Mis capturas"
- Generar un `client_uuid` por captura al empezar (sugerencia: campo único en el DocType) para que reintentar no cree duplicados.
- Orden: subir fotos (`/api/method/upload_file`, `is_private=1`) mostrando «Subiendo foto i de n» → crear la captura con el endpoint whitelisted (transcripción, códigos, `sin_codigo`, variantes con su foto/código/dictado, `client_uuid`) → el servidor la deja en **Procesando** y encola la IA.
- Sin internet o error de red: guardar en IndexedDB con estado local `pendiente` ("Sin enviar"); reintentar en el evento `online`, al abrir la app y con [Enviar ahora].
- `Mis capturas`: endpoint que devuelve las capturas del usuario (`capturado_por = session.user`): `name, item_name, transcripcion, estado, foto_principal (URL miniatura), n_variantes, creado, mensaje_error_simple`. Unir con las pendientes locales. Mientras haya alguna en Procesando y la página esté visible, volver a consultar cada 15 s.
- El texto de error que ve el capturador debe ser simple y no técnico (ej. «No se entendió el tamaño. Tu supervisor lo va a corregir.»). El detalle técnico va a `error_log` para el supervisor.

### 8.6 Otros
- HTTPS obligatorio para cámara y micrófono (túnel cloudflared en dev, HTTPS en CloudClusters).
- PWA: `manifest.json` con `display: standalone`, `theme_color: #1D4ED8`, `lang: es`, íconos 192/512. Con la app instalada no hay barra de dirección: usar el texto alternativo de permisos (§4.10).
- Antes de abrir cámara o micrófono, `navigator.permissions.query()` (cuando exista) para mostrar directamente el estado "sin permiso".
- No usar `alert()`/`confirm()` del navegador: usar las hojas `cp-sheet`.
- Fecha y hora relativas: «Hoy, 10:40», «Ayer, 17:20», luego «12 nov, 09:10».

---

## 9. Decisiones de UX a confirmar con el usuario

1. **Variante 1 = el producto ya capturado.** Las fotos, dictado y código de los pasos 1–3 se toman como la primera variante (ej. durazno), y luego se agregan las demás. Alternativa: tratar los pasos 1–3 como la "plantilla" genérica sin sabor. Afecta cómo el servidor arma el Item plantilla.
2. **Orden del sub-flujo de variante: foto → dictado → código** (igual que el producto, para que sea un solo hábito). El pedido original decía "foto + código + dictado".
3. **Se permite "Este producto no tiene código"** (productos a granel, de elaboración propia). Si siempre deben tener código, se quita la casilla.
4. **Código ya registrado:** se bloquea agregarlo y se ofrece "Dejar este producto". ¿Se debería permitir continuar igual (por ejemplo, para actualizar fotos de un producto existente)?
5. **El capturador ve "Aprobado"** además de Procesando / Por revisar / Error, y un estado local "Sin enviar" para la cola sin internet.
6. **Hasta 4 fotos por producto y 1 por variante.**
7. **Borrar foto sin confirmación** (con "Deshacer" 6 s) en lugar de preguntar "¿Seguro?".
8. **Errores de la IA no requieren acción del capturador** («Tu supervisor lo va a corregir»). Alternativa: permitir que el capturador corrija y reenvíe.
