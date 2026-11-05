"""Extractor de Claude (spec secciones 6, 13 y HU-13).

Flujo del job `procesar_captura` (cola `long`), **un solo intento por ejecución**:
1. Lee la captura (debe estar en Procesando) y la Configuración. Sin API key → Error.
2. Arma el system prompt con el contexto del sitio (cacheado) y el mensaje con el dictado, los códigos
   y hasta `max_fotos_ia` fotos en base64 (reducidas a `LADO_FOTO_IA` px). Solo se leen archivos
   adjuntos a la misma captura.
3. Cierra la transacción de lectura y llama a la Messages API con salida estructurada (esquema
   `registrar_producto`), sin transacción abierta ni bloqueos mientras espera a Claude.
4. Si la respuesta es buena, la aplica y llama a `marcar_procesada()` (nombre, código y validación).
   Un error transitorio (429, 5xx, 529, timeout, red, JSON inválido) deja la captura en Procesando
   con `reintentar_ia_en` = ahora + 30 s (1.er reintento) o + 120 s (2.º): el programador
   (`reencolar_reintentos`, cada minuto) vuelve a encolar el job a esa hora. Así el worker no queda
   dormido 150 s por captura. Tras 2 reintentos, o con un error definitivo (401, 400, 403, 404,
   negativa), pasa a Error.

`rescatar_atascadas` (cada 15 min) pasa a Error las capturas que quedaron en Procesando sin job (worker
caído, Redis sin el job, carrera de `deduplicate`), para que no queden así para siempre.

La API key nunca se escribe en logs, `error_log` ni mensajes: `_sin_secretos` la enmascara por si
algún mensaje de error la incluyera.
"""

from __future__ import annotations

import base64
import io
import json
import traceback
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anthropic
import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from captura_productos import naming
from captura_productos.ai import prompts

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

DOCTYPE = "Captura de Producto"
METODO_JOB = "captura_productos.ai.extractor.procesar_captura"
MODELO_DEFECTO = "claude-sonnet-5-5"
# Incluye el pensamiento adaptativo (cuenta para max_tokens) y la salida JSON con hasta 20 variantes.
MAX_TOKENS = 4096
ESFUERZO = "low"  # extracción simple: menos latencia y costo que el "high" por defecto
TIMEOUT_DEFECTO = 60
MAX_FOTOS_DEFECTO = 4
ESPERAS_REINTENTO = (30, 120)  # HU-13: segundos antes del 1.er y del 2.º reintento
TIMEOUT_JOB = 300  # un solo intento de hasta `timeout_ia_seg` (60 s) más lectura de fotos, con margen
MINUTOS_ATASCADA = 20  # Procesando sin job durante más que esto → Error (rescatar_atascadas)
LADO_FOTO_IA = 1280  # lado mayor de la foto que se manda a Claude
CALIDAD_JPEG_IA = 85
CONFIANZA_BAJA = 0.6

# Modelos que aceptan el fallback del servidor ante una negativa de los clasificadores de seguridad.
MODELOS_CON_FALLBACK = frozenset({"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"})
BETA_FALLBACK = "server-side-fallback-2026-07-01"

SAVEPOINT = "captura_ia"
MENSAJE_CAPTURADOR = "No se pudo procesar. El supervisor lo revisará."

_FIRMAS_IMAGEN = (
	(b"\xff\xd8\xff", "image/jpeg"),
	(b"\x89PNG\r\n\x1a\n", "image/png"),
	(b"GIF87a", "image/gif"),
	(b"GIF89a", "image/gif"),
)


class ErrorIA(Exception):
	"""Fallo al obtener o interpretar la respuesta de Claude. `transitorio` indica si se reintenta."""

	def __init__(self, detalle: str, transitorio: bool = False):
		super().__init__(detalle)
		self.detalle = detalle
		self.transitorio = transitorio


# ---------------------------------------------------------------------------------------------------
# Encolado
# ---------------------------------------------------------------------------------------------------


def id_job(captura: str) -> str:
	return f"captura-ia-{captura}"


def encolar_procesamiento(captura: str) -> None:
	"""Encola el job después del commit (la captura ya quedó en Procesando)."""
	frappe.enqueue(
		METODO_JOB,
		queue="long",
		timeout=TIMEOUT_JOB,
		enqueue_after_commit=True,
		job_id=id_job(captura),
		deduplicate=True,
		captura=captura,
	)


def job_en_cola(captura: str) -> bool:
	"""True si el job de la captura está en cola o corriendo."""
	from frappe.utils.background_jobs import is_job_enqueued

	return is_job_enqueued(id_job(captura))


# ---------------------------------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------------------------------


def procesar_captura(captura: str) -> None:
	"""Un intento de extracción (ver el docstring del módulo)."""
	doc = _captura_en_proceso(captura)
	if not doc:
		return
	if doc.reintentar_ia_en and doc.reintentar_ia_en > now_datetime():
		return  # todavía no es la hora del reintento: lo encola `reencolar_reintentos`

	conf = frappe.get_single("Configuracion Captura")
	api_key = conf.get_password("anthropic_api_key", raise_exception=False)
	modelo = (conf.modelo_ia or MODELO_DEFECTO).strip()
	if not api_key:
		_registrar_error(
			captura,
			_("Falta la API key de Anthropic en Configuración Captura: cárguela y use Reprocesar IA."),
			modelo,
		)
		return

	try:
		ctx_nombre = naming.cargar_contexto()
		contexto = cargar_contexto_prompt(conf, ctx_nombre)
		contenido, avisos_fotos = construir_contenido_usuario(doc, cint(conf.max_fotos_ia) or MAX_FOTOS_DEFECTO)
		cliente = crear_cliente(api_key, cint(conf.timeout_ia_seg) or TIMEOUT_DEFECTO)
	except Exception:
		_registrar_error(captura, _sin_secretos(traceback.format_exc(), api_key), modelo)
		return

	# Hasta acá solo hubo lecturas: se cierra la transacción para no tenerla abierta (con su foto de
	# lectura y sus bloqueos) durante la llamada a Claude, que puede durar hasta `timeout_ia_seg`.
	_confirmar()
	try:
		datos, modelo_usado = llamar_claude(cliente, modelo, prompts.construir_system(contexto), contenido)
	except ErrorIA as e:
		_despues_de_un_fallo(captura, _sin_secretos(e.detalle, api_key), e.transitorio, modelo)
		return
	except Exception:
		_despues_de_un_fallo(captura, _sin_secretos(traceback.format_exc(), api_key), False, modelo)
		return

	doc = _captura_en_proceso(captura)
	if not doc:
		return  # la rescataron o la cambiaron mientras se esperaba a Claude
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc.intentos_ia = cint(doc.intentos_ia) + 1
		doc.modelo_ia = modelo_usado or modelo
		doc.respuesta_ia = json.dumps(datos, ensure_ascii=False, indent=1)
		advertencias = avisos_fotos + aplicar_respuesta(doc, datos, conf, ctx_nombre)
		doc.advertencias_ia = "\n".join(dict.fromkeys(a for a in advertencias if a)) or None
		doc.error_log = None
		doc.marcar_procesada()
	except Exception:
		respuesta = doc.respuesta_ia
		_deshacer(SAVEPOINT)
		_registrar_error(captura, _sin_secretos(traceback.format_exc(), api_key), modelo_usado or modelo, respuesta)
		return
	_confirmar()


def _captura_en_proceso(captura: str) -> CapturadeProducto | None:
	if not frappe.db.exists(DOCTYPE, captura):
		return None
	doc: CapturadeProducto = frappe.get_doc(DOCTYPE, captura)
	if doc.estado != "Procesando":
		frappe.logger("captura_productos").info(f"{captura}: estado {doc.estado}, no se procesa")
		return None
	doc.flags.ignore_permissions = True
	return doc


def _despues_de_un_fallo(captura: str, detalle: str, transitorio: bool, modelo: str) -> None:
	"""Programa un reintento si el error es transitorio y quedan reintentos; si no, pasa a Error."""
	doc = _captura_en_proceso(captura)
	if not doc:
		return
	reintentos = cint(doc.reintentos_ia)
	if not transitorio or reintentos >= len(ESPERAS_REINTENTO):
		if reintentos:
			detalle = _("{0} (tras {1} reintentos)").format(detalle, reintentos)
		_registrar_error(captura, detalle, modelo)
		return
	espera = ESPERAS_REINTENTO[reintentos]
	frappe.logger("captura_productos").warning(f"{captura}: error transitorio de IA, reintento en {espera} s")
	doc.intentos_ia = cint(doc.intentos_ia) + 1
	doc.modelo_ia = modelo
	doc.reintentos_ia = reintentos + 1
	doc.reintentar_ia_en = add_to_date(now_datetime(), seconds=espera)
	doc.error_log = _("Error transitorio; reintento {0} de {1} a partir de {2}.\n{3}").format(
		reintentos + 1, len(ESPERAS_REINTENTO), doc.reintentar_ia_en, detalle
	)
	doc._guardar(validar=False)
	_confirmar()


def _registrar_error(captura: str, detalle: str, modelo: str | None = None, respuesta: str | None = None) -> None:
	"""Deja la captura en Error con el detalle técnico y un Error Log de Frappe."""
	frappe.log_error(title=_("Captura {0}: error de IA").format(captura), message=detalle)
	doc = _captura_en_proceso(captura)
	if not doc:
		return
	doc.intentos_ia = cint(doc.intentos_ia) + 1
	if modelo:
		doc.modelo_ia = modelo
	if respuesta:
		doc.respuesta_ia = respuesta
	doc.marcar_error(detalle, _(MENSAJE_CAPTURADOR))
	_confirmar()


def _confirmar() -> None:
	# En los tests no se confirma: cada test deshace lo que hizo.
	if not frappe.in_test:
		frappe.db.commit()


def _deshacer(savepoint: str) -> None:
	"""Fuera de los tests, rollback completo (nada a medias llega a confirmarse con el commit que
	sigue); en los tests, solo hasta el savepoint, porque el test deshace todo al terminar."""
	if frappe.in_test:
		frappe.db.rollback(save_point=savepoint)
	else:
		frappe.db.rollback()


def _sin_secretos(texto: str, *secretos: str | None) -> str:
	for secreto in secretos:
		if secreto:
			texto = texto.replace(secreto, "***")
	return texto


# ---------------------------------------------------------------------------------------------------
# Tareas programadas (hooks.scheduler_events)
# ---------------------------------------------------------------------------------------------------


def reencolar_reintentos() -> None:
	"""Cada minuto: vuelve a encolar las capturas cuyo reintento ya está vencido."""
	for nombre in frappe.get_all(
		DOCTYPE,
		filters={"estado": "Procesando", "reintentar_ia_en": ["<=", now_datetime()]},
		pluck="name",
	):
		frappe.db.set_value(DOCTYPE, nombre, "reintentar_ia_en", None, update_modified=False)
		encolar_procesamiento(nombre)
		_confirmar()


def rescatar_atascadas() -> None:
	"""Cada 15 minutos: pasa a Error las capturas en Procesando sin cambios hace más de
	`MINUTOS_ATASCADA` minutos y sin job en cola ni corriendo (ni reintento pendiente)."""
	limite = now_datetime() - timedelta(minutes=MINUTOS_ATASCADA)
	candidatas = frappe.get_all(
		DOCTYPE,
		filters={"estado": "Procesando", "modified": ["<", limite]},
		fields=["name", "reintentar_ia_en"],
	)
	for fila in candidatas:
		if fila.reintentar_ia_en and fila.reintentar_ia_en > limite:
			continue  # reintento programado y todavía razonable: lo maneja reencolar_reintentos
		if job_en_cola(fila.name):
			continue
		try:
			_registrar_error(
				fila.name,
				_(
					"La captura quedó en Procesando más de {0} minutos sin un trabajo en cola (worker "
					"detenido, Redis caído o el trabajo se perdió). Use Reprocesar IA."
				).format(MINUTOS_ATASCADA),
			)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=_("Rescatar captura {0}").format(fila.name))


# ---------------------------------------------------------------------------------------------------
# Llamada a Claude
# ---------------------------------------------------------------------------------------------------


def crear_cliente(api_key: str, timeout: int) -> anthropic.Anthropic:
	# Sin reintentos del SDK: los reintentos con espera de HU-13 los programa `_despues_de_un_fallo`.
	return anthropic.Anthropic(api_key=api_key, timeout=float(timeout), max_retries=0)


def llamar_claude(
	cliente: anthropic.Anthropic, modelo: str, system: str, contenido: list[dict]
) -> tuple[dict, str | None]:
	"""Una llamada a la Messages API. Devuelve (datos del esquema, modelo que respondió)."""
	params: dict[str, Any] = {
		"model": modelo,
		"max_tokens": MAX_TOKENS,
		"system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
		"messages": [{"role": "user", "content": contenido}],
		"output_config": {
			"effort": ESFUERZO,
			"format": {"type": "json_schema", "schema": prompts.ESQUEMA_PRODUCTO},
		},
	}
	try:
		if modelo in MODELOS_CON_FALLBACK:
			respuesta = cliente.beta.messages.create(**params, betas=[BETA_FALLBACK], fallbacks="default")
		else:
			respuesta = cliente.messages.create(**params)
	except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
		raise ErrorIA(_("Sin respuesta de la API de Claude ({0}): {1}").format(type(e).__name__, e), True)
	except anthropic.APIStatusError as e:
		transitorio = e.status_code == 429 or e.status_code >= 500
		raise ErrorIA(
			_("La API de Claude respondió HTTP {0} ({1}): {2}. request_id={3}").format(
				e.status_code, type(e).__name__, e.message, getattr(e, "request_id", None)
			),
			transitorio,
		)
	return interpretar_respuesta(respuesta), getattr(respuesta, "model", None)


def interpretar_respuesta(respuesta) -> dict:
	"""Extrae y valida el JSON de la respuesta (bloques leídos por tipo, no por posición)."""
	motivo = getattr(respuesta, "stop_reason", None)
	if motivo == "refusal":
		detalles = getattr(respuesta, "stop_details", None)
		categoria = getattr(detalles, "category", None) if detalles else None
		raise ErrorIA(_("Claude se negó a procesar la captura (categoría: {0}).").format(categoria))
	if motivo == "max_tokens":
		raise ErrorIA(_("La respuesta de Claude se cortó por max_tokens ({0}).").format(MAX_TOKENS), True)

	texto = next((b.text for b in respuesta.content if getattr(b, "type", None) == "text"), None)
	if not texto:
		raise ErrorIA(_("La respuesta de Claude no trae el JSON esperado (stop_reason={0}).").format(motivo), True)
	try:
		datos = json.loads(texto)
	except json.JSONDecodeError as e:
		raise ErrorIA(_("La respuesta de Claude no es JSON válido: {0}").format(e), True)
	faltan = validar_esquema(datos)
	if faltan:
		raise ErrorIA(_("La respuesta de Claude no cumple el esquema: {0}").format("; ".join(faltan)), True)
	return datos


def validar_esquema(datos: Any) -> list[str]:
	"""Chequeo mínimo del esquema (la salida estructurada ya lo garantiza; esto cubre otros modelos)."""
	if not isinstance(datos, dict):
		return [_("no es un objeto")]
	errores = [
		_("falta {0}").format(campo) for campo in prompts.ESQUEMA_PRODUCTO["required"] if campo not in datos
	]
	for campo, tipo in (("variantes", list), ("atributos", list), ("advertencias", list)):
		if campo in datos and not isinstance(datos[campo], tipo):
			errores.append(_("{0} no es una lista").format(campo))
	return errores


# ---------------------------------------------------------------------------------------------------
# Contexto y mensaje
# ---------------------------------------------------------------------------------------------------


def cargar_contexto_prompt(conf, ctx_nombre: naming.ContextoNombre) -> prompts.ContextoPrompt:
	uom_filtros = {"enabled": 1} if frappe.get_meta("UOM").has_field("enabled") else {}
	valores_por_atributo: dict[str, list[tuple[str, str]]] = {}
	for fila in frappe.get_all(
		"Item Attribute Value",
		filters={"parenttype": "Item Attribute"},
		fields=["parent", "attribute_value", "abbr"],
		order_by="parent asc, idx asc",
	):
		valores_por_atributo.setdefault(fila.parent, []).append((fila.attribute_value, fila.abbr or ""))
	atributos = [
		prompts.AtributoContexto(
			nombre=a.name,
			numerico=bool(a.numeric_values),
			desde=float(a.from_range or 0),
			hasta=float(a.to_range or 0),
			incremento=float(a.increment or 0),
			valores=valores_por_atributo.get(a.name, []),
		)
		for a in frappe.get_all(
			"Item Attribute",
			filters={"disabled": 0},
			fields=["name", "numeric_values", "from_range", "to_range", "increment"],
			order_by="name asc",
		)
	]
	return prompts.ContextoPrompt(
		item_groups=frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", order_by="name asc"),
		uoms=frappe.get_all("UOM", filters=uom_filtros, pluck="name", order_by="name asc"),
		brands=sorted(ctx_nombre.brands),
		glosario=[(g.dicho, g.forma_correcta) for g in conf.glosario_marcas if g.dicho and g.forma_correcta],
		atributos=atributos,
		instrucciones_extra=conf.instrucciones_extra_ia or "",
	)


def construir_contenido_usuario(doc: CapturadeProducto, max_fotos: int) -> tuple[list[dict], list[str]]:
	"""Bloques del mensaje: fotos (con su rótulo) y al final el texto. Devuelve también avisos."""
	avisos: list[str] = []
	contenido: list[dict] = []
	for rotulo, url in fotos_para_ia(doc)[: max(1, max_fotos)]:
		imagen = imagen_base64(url, doc.name)
		if not imagen:
			avisos.append(_("No se pudo leer la foto {0}: no se envió a la IA").format(url))
			continue
		media_type, datos = imagen
		contenido.append({"type": "text", "text": rotulo})
		contenido.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": datos}})

	variantes = [
		prompts.VarianteCapturada(
			indice=v.idx, transcripcion=v.transcripcion or "", barcode=v.barcode or "", tiene_foto=bool(v.foto)
		)
		for v in (doc.variantes or [])
	]
	datos = prompts.DatosCaptura(
		transcripcion=doc.transcripcion or "",
		codigos_barras=[c.barcode for c in doc.codigos_barras or [] if c.barcode],
		tiene_variantes=bool(doc.tiene_variantes),
		atributos=naming.atributos_ordenados(doc),
		variantes=variantes,
	)
	contenido.append({"type": "text", "text": prompts.construir_texto_usuario(datos)})
	return contenido, avisos


def fotos_para_ia(doc: CapturadeProducto) -> list[tuple[str, str]]:
	"""(rótulo, url) en orden de prioridad: principal, fotos de variantes, resto de fotos."""
	principal = doc.imagen_principal or next((f.foto for f in doc.fotos or [] if f.foto), None)
	fotos: list[tuple[str, str]] = []
	if principal:
		fotos.append((_("Foto principal del producto:"), principal))
	for v in doc.variantes or []:
		if v.foto:
			fotos.append((_("Foto de la variante {0}:").format(v.idx), v.foto))
	for f in doc.fotos or []:
		if f.foto and f.foto != principal:
			fotos.append((_("Otra foto del producto:"), f.foto))
	unicas: dict[str, str] = {}
	for rotulo, url in fotos:
		unicas.setdefault(url, rotulo)
	return [(rotulo, url) for url, rotulo in unicas.items()]


def tipo_imagen(contenido: bytes) -> str | None:
	for firma, media_type in _FIRMAS_IMAGEN:
		if contenido.startswith(firma):
			return media_type
	if contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
		return "image/webp"
	return None


def leer_archivo(url: str, captura: str) -> bytes | None:
	"""Contenido de una foto pública adjunta a la captura (File con attached_to = captura).

	Nunca se lee por ruta, ni un archivo de otro documento, ni uno privado: una URL puesta a mano en
	`foto` (por ejemplo /private/files/dni.jpg) no llega a Claude. Las fotos de /captura siempre son
	públicas (spec 12.3); se exige además porque Frappe, al guardar un campo Attach Image, adjunta al
	documento cualquier archivo suelto cuya URL se haya escrito ahí."""
	if not url or not captura or url.startswith(("http://", "https://")):
		return None
	nombre = frappe.db.get_value(
		"File",
		{"file_url": url, "attached_to_doctype": DOCTYPE, "attached_to_name": captura, "is_private": 0},
		"name",
	)
	if not nombre:
		return None
	try:
		return frappe.get_doc("File", nombre).get_content()
	except Exception:
		return None


def reducir_imagen(contenido: bytes, lado: int = LADO_FOTO_IA) -> tuple[bytes, str] | None:
	"""JPEG con el lado mayor reducido a `lado` px (menos tokens y menos RAM en el worker). None si
	no se puede leer como imagen."""
	from PIL import Image, ImageOps

	try:
		with Image.open(io.BytesIO(contenido)) as original:
			if max(original.size) <= lado and original.format in ("JPEG", "PNG"):
				return None  # ya es chica: se manda tal cual
			imagen = ImageOps.exif_transpose(original)
			imagen.thumbnail((lado, lado))
			if imagen.mode not in ("RGB", "L"):
				imagen = imagen.convert("RGB")
			salida = io.BytesIO()
			imagen.save(salida, "JPEG", quality=CALIDAD_JPEG_IA, optimize=True)
			return salida.getvalue(), "image/jpeg"
	except Exception:
		return None


def imagen_base64(url: str, captura: str) -> tuple[str, str] | None:
	contenido = leer_archivo(url, captura)
	if not contenido:
		return None
	if isinstance(contenido, str):
		contenido = contenido.encode()
	media_type = tipo_imagen(contenido)
	if not media_type:
		return None
	reducida = reducir_imagen(contenido)
	if reducida:
		contenido, media_type = reducida
	return media_type, base64.standard_b64encode(contenido).decode("ascii")


# ---------------------------------------------------------------------------------------------------
# Aplicar la respuesta a la captura
# ---------------------------------------------------------------------------------------------------


@dataclass
class _Maestros:
	item_groups: set[str]
	uoms: set[str]
	atributos: dict[str, str]  # clave de comparación → nombre exacto


def _cargar_maestros() -> _Maestros:
	return _Maestros(
		item_groups=set(frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")),
		uoms=set(frappe.get_all("UOM", pluck="name")),
		atributos={
			naming.clave_comparacion(n): n
			for n in frappe.get_all("Item Attribute", filters={"disabled": 0}, pluck="name")
		},
	)


def _texto(valor: Any) -> str:
	return valor.strip() if isinstance(valor, str) else ""


def _confianza(valor: Any) -> float:
	try:
		return min(1.0, max(0.0, float(valor)))
	except TypeError, ValueError:
		return 0.0


def aplicar_respuesta(
	doc: CapturadeProducto, datos: dict, conf, ctx_nombre: naming.ContextoNombre | None = None
) -> list[str]:
	"""Carga los campos extraídos en `doc` (sin guardar). Devuelve las advertencias de la IA y del
	post-proceso de la sección 6, sin prefijo."""
	ctx_nombre = ctx_nombre or naming.cargar_contexto()
	maestros = _cargar_maestros()
	advertencias = [_texto(a) for a in datos.get("advertencias") or [] if _texto(a)]

	doc.tipo_producto = _texto(datos.get("tipo_producto")) or None
	doc.marca = _texto(datos.get("marca")) or None
	doc.descripcion = _texto(datos.get("descripcion")) or None

	grupo = _texto(datos.get("item_group"))
	if grupo not in maestros.item_groups:
		defecto = conf.item_group_defecto or None
		advertencias.append(
			_("La IA propuso el grupo '{0}', que no está en la lista: se usó {1}").format(grupo, defecto or "-")
		)
		grupo = defecto
	doc.item_group = grupo or None

	uom = _texto(datos.get("stock_uom"))
	if uom not in maestros.uoms:
		defecto = conf.uom_defecto or None
		advertencias.append(
			_("La IA propuso la unidad '{0}', que no está en la lista: se usó {1}").format(uom, defecto or "-")
		)
		uom = defecto
	doc.stock_uom = uom or None

	confianza = _confianza(datos.get("confianza"))
	doc.confianza_ia = round(confianza * 100, 1)
	if confianza < CONFIANZA_BAJA:
		advertencias.append(_("Baja confianza: revise con las fotos"))

	advertencias += _aplicar_variantes(doc, datos, maestros)

	medida_ia = datos.get("medida") if isinstance(datos.get("medida"), dict) else {}
	atributos_medida = [a for a in naming.atributos_ordenados(doc) if naming.clave_comparacion(a) in ctx_nombre.atributos_medida]
	if doc.tiene_variantes and atributos_medida:
		medida = None  # el tamaño va en cada variante (spec 5.7, ejemplo 7)
	else:
		medida, avisos_medida = naming.resolver_medida(
			medida_ia.get("valor"), medida_ia.get("unidad"), doc.transcripcion
		)
		advertencias += avisos_medida
	doc.medida_valor = medida.valor if medida else 0
	doc.medida_unidad = medida.unidad if medida else ""

	doc.variante_descripcion = None if doc.tiene_variantes else (_texto(datos.get("variante")) or None)
	return advertencias


def _aplicar_variantes(doc: CapturadeProducto, datos: dict, maestros: _Maestros) -> list[str]:
	advertencias: list[str] = []
	variantes_ia = [v for v in datos.get("variantes") or [] if isinstance(v, dict)]

	# Las filas que creó la IA en un procesamiento anterior (sin dictado, código ni foto) se rehacen.
	filas = [v for v in doc.variantes or [] if v.transcripcion or v.barcode or v.foto]
	if len(filas) != len(doc.variantes or []):
		doc.set("variantes", filas)
	capturadas = len(filas)

	doc.tiene_variantes = 1 if (capturadas or (datos.get("tiene_variantes") and variantes_ia)) else 0
	if not doc.tiene_variantes:
		return advertencias

	# Atributos: los que propone la IA (existentes, sin repetir, máx. 3); si no propone, se conservan.
	atributos: list[str] = []
	for nombre in datos.get("atributos") or []:
		exacto = maestros.atributos.get(naming.clave_comparacion(_texto(nombre)))
		if not exacto:
			advertencias.append(_("La IA propuso el atributo '{0}', que no existe: se ignoró").format(nombre))
		elif exacto not in atributos:
			atributos.append(exacto)
	if len(atributos) > 3:
		advertencias.append(_("La IA propuso más de 3 atributos: se usaron los 3 primeros"))
		atributos = atributos[:3]
	if atributos:
		doc.set("atributos", [])
		for orden, nombre in enumerate(atributos, start=1):
			doc.append("atributos", {"atributo": nombre, "orden": orden})
	atributos = naming.atributos_ordenados(doc)
	posicion = {naming.clave_comparacion(a): n for n, a in enumerate(atributos, start=1)}

	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		MAX_VARIANTES,
	)

	for variante in variantes_ia:
		indice = variante.get("indice")
		if indice is None:
			if len(doc.variantes) >= MAX_VARIANTES:
				advertencias.append(
					_("La IA propuso más de {0} variantes: se ignoraron las demás").format(MAX_VARIANTES)
				)
				continue
			fila = doc.append("variantes", {})
			advertencias.append(_("Variante {0}: variante sin código de barras").format(fila.idx))
		elif isinstance(indice, int) and 1 <= indice <= capturadas:
			fila = doc.variantes[indice - 1]
		else:
			advertencias.append(_("La IA devolvió la variante {0}, que no existe en la captura").format(indice))
			continue
		for n in range(1, 4):
			fila.set(f"valor_{n}", None)
			fila.set(f"valor_corto_{n}", None)
		for par in variante.get("valores") or []:
			if not isinstance(par, dict):
				continue
			n = posicion.get(naming.clave_comparacion(_texto(par.get("atributo"))))
			if not n:
				advertencias.append(
					_("Variante {0}: el atributo '{1}' no está en la lista: se ignoró").format(
						fila.idx, par.get("atributo")
					)
				)
				continue
			fila.set(f"valor_{n}", _texto(par.get("valor")) or None)
		if _confianza(variante.get("confianza")) < CONFIANZA_BAJA:
			advertencias.append(_("Variante {0}: baja confianza, revise con la foto").format(fila.idx))
	return advertencias


# ---------------------------------------------------------------------------------------------------
# Prueba de conexión (botón de Configuración)
# ---------------------------------------------------------------------------------------------------


def probar_conexion(api_key: str, modelo: str, timeout: float = 15) -> str:
	"""Verifica la API key y el modelo sin generar tokens (Models API). Devuelve un texto para el usuario."""
	cliente = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
	try:
		info = cliente.models.retrieve(modelo)
	except anthropic.AuthenticationError:
		raise ErrorIA(_("La API key no es válida (HTTP 401)."))
	except anthropic.NotFoundError:
		raise ErrorIA(_("El modelo {0} no existe o la cuenta no tiene acceso (HTTP 404).").format(modelo))
	except anthropic.APIStatusError as e:
		raise ErrorIA(_("La API de Claude respondió HTTP {0}: {1}").format(e.status_code, e.message))
	except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
		raise ErrorIA(_("No se pudo conectar con la API de Claude: {0}").format(type(e).__name__))
	return _("Conexión OK: modelo {0} ({1}).").format(info.id, getattr(info, "display_name", info.id))
