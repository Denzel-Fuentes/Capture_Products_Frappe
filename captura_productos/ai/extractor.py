"""Extractor de Claude (spec secciones 6, 13 y HU-13).

Dos proveedores, según `proveedor_ia` de la Configuración:
- **API local** (por defecto): el envoltorio HTTP de Claude Code que corre en el mismo servidor
  (`cliente_local`). URL y token en site_config (`claude_api_url`, `claude_api_token`). Sin salida
  estructurada: el JSON se pide por prompt y se extrae del texto.
- **Anthropic directo**: la Messages API con API key y salida estructurada.

Flujo del job `procesar_captura` (cola `long`), **un solo intento por ejecución**:
1. Lee la captura (debe estar en Procesando) y la Configuración. Sin credencial (API key o token) →
   Error.
2. Arma el system prompt con el contexto del sitio y el mensaje con el dictado, los códigos y hasta
   `max_fotos_ia` fotos (reducidas a `LADO_FOTO_IA` px). Solo se leen archivos adjuntos a la misma
   captura.
3. `obtener_datos_ia` cierra la transacción de lectura y llama al proveedor, sin transacción abierta
   ni bloqueos mientras espera a Claude. Devuelve los datos del esquema `registrar_producto` y los
   metadatos (modelo, costo, duración).
4. Si la respuesta es buena, la aplica y llama a `marcar_procesada()` (nombre, código y validación).
   Un error transitorio (429, 5xx, 529, timeout, red, JSON inválido o fuera de esquema) deja la
   captura en Procesando con `reintentar_ia_en` = ahora + 30 s (1.er reintento) o + 120 s (2.º): el
   programador (`reencolar_reintentos`, cada minuto) vuelve a encolar el job a esa hora. Así el worker
   no queda dormido 150 s por captura. Tras 2 reintentos, o con un error definitivo (401, 400, 403,
   404, 413, negativa), pasa a Error.

`rescatar_atascadas` (cada 15 min) pasa a Error las capturas que quedaron en Procesando sin job (worker
caído, Redis sin el job, carrera de `deduplicate`), para que no queden así para siempre.

La API key y el token nunca se escriben en logs, `error_log` ni mensajes: `_sin_secretos` los
enmascara por si algún mensaje de error los incluyera.
"""

from __future__ import annotations

import base64
import io
import json
import traceback
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anthropic
import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime

from captura_productos import naming
from captura_productos.ai import prompts

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

DOCTYPE = "Captura de Producto"
PROVEEDOR_LOCAL = "API local"
PROVEEDOR_ANTHROPIC = "Anthropic directo"
METODO_JOB = "captura_productos.ai.extractor.procesar_captura"
MODELO_DEFECTO = "claude-sonnet-5-5"
# Incluye el pensamiento adaptativo (cuenta para max_tokens) y la salida JSON con hasta 20 variantes.
MAX_TOKENS = 4096
ESFUERZO = "low"  # extracción simple: menos latencia y costo que el "high" por defecto
TIMEOUT_DEFECTO = 60
MAX_FOTOS_DEFECTO = 4
ESPERAS_REINTENTO = (30, 120)  # HU-13: segundos antes del 1.er y del 2.º reintento
TIMEOUT_JOB = 300  # un solo intento de hasta `timeout_ia_seg` (60 s) más lectura de fotos, con margen
MARGEN_JOB_LOCAL = 90  # API local: `timeout_ia` + margen de requests (30 s) + fotos y borrado de sesión
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
EXTENSIONES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}


class ErrorIA(Exception):
	"""Fallo al obtener o interpretar la respuesta de Claude. `transitorio` indica si se reintenta.

	`respuesta` (texto crudo), `costo_usd` y `duracion_ms` se conocen cuando el proveedor llegó a
	responder (por ejemplo, JSON inválido de la API local): se guardan igual en la captura."""

	def __init__(
		self,
		detalle: str,
		transitorio: bool = False,
		*,
		respuesta: str | None = None,
		costo_usd: float = 0.0,
		duracion_ms: int | None = None,
	):
		super().__init__(detalle)
		self.detalle = detalle
		self.transitorio = transitorio
		self.respuesta = respuesta
		self.costo_usd = costo_usd
		self.duracion_ms = duracion_ms

	def enmascarar(self, *secretos: str | None) -> ErrorIA:
		self.detalle = _sin_secretos(self.detalle, *secretos)
		if self.respuesta:
			self.respuesta = _sin_secretos(self.respuesta, *secretos)
		self.args = (self.detalle,)
		return self


@dataclass
class ResultadoIA:
	"""Datos del esquema `registrar_producto` y metadatos de la llamada, por proveedor."""

	datos: dict
	modelo: str | None = None
	costo_usd: float = 0.0
	duracion_ms: int | None = None
	avisos: list[str] = field(default_factory=list)  # fotos que no se pudieron leer


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
		timeout=timeout_job(),
		enqueue_after_commit=True,
		job_id=id_job(captura),
		deduplicate=True,
		captura=captura,
	)


def timeout_job() -> int:
	"""Con la API local el job debe durar más que `timeout_ia` (configurable hasta 900 s)."""
	timeout_local = cint(frappe.db.get_single_value("Configuracion Captura", "timeout_ia"))
	return max(TIMEOUT_JOB, timeout_local + MARGEN_JOB_LOCAL)


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
	proveedor = proveedor_de(conf)
	modelo = (conf.modelo_ia or MODELO_DEFECTO).strip()
	try:
		secreto = credencial_ia(conf, proveedor)
	except ErrorIA as e:
		_registrar_error(captura, e.detalle, modelo)
		return

	try:
		ctx_nombre = naming.cargar_contexto()
		contexto = cargar_contexto_prompt(conf, ctx_nombre)
	except Exception:
		_registrar_error(captura, _sin_secretos(traceback.format_exc(), secreto), modelo)
		return

	try:
		resultado = obtener_datos_ia(doc, conf, proveedor, modelo, contexto, secreto)
	except ErrorIA as e:
		e.enmascarar(secreto)
		_despues_de_un_fallo(captura, e.detalle, e.transitorio, modelo, e.respuesta, e.costo_usd, e.duracion_ms)
		return
	except Exception:
		_despues_de_un_fallo(captura, _sin_secretos(traceback.format_exc(), secreto), False, modelo)
		return

	modelo_usado = resultado.modelo or modelo
	doc = _captura_en_proceso(captura)
	if not doc:
		return  # la rescataron o la cambiaron mientras se esperaba a Claude
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc.intentos_ia = cint(doc.intentos_ia) + 1
		doc.modelo_ia = modelo_usado
		_anotar_consumo(doc, resultado.costo_usd, resultado.duracion_ms)
		doc.respuesta_ia = json.dumps(resultado.datos, ensure_ascii=False, indent=1)
		advertencias = resultado.avisos + aplicar_respuesta(doc, resultado.datos, conf, ctx_nombre)
		doc.advertencias_ia = "\n".join(dict.fromkeys(a for a in advertencias if a)) or None
		doc.error_log = None
		doc.marcar_procesada()
	except Exception:
		respuesta = doc.respuesta_ia
		_deshacer(SAVEPOINT)
		_registrar_error(
			captura,
			_sin_secretos(traceback.format_exc(), secreto),
			modelo_usado,
			respuesta,
			resultado.costo_usd,
			resultado.duracion_ms,
		)
		return
	_confirmar()


def proveedor_de(conf) -> str:
	return PROVEEDOR_ANTHROPIC if conf.get("proveedor_ia") == PROVEEDOR_ANTHROPIC else PROVEEDOR_LOCAL


def credencial_ia(conf, proveedor: str) -> str:
	"""API key de Anthropic (Configuración) o token de la API local (site_config). ErrorIA si falta."""
	if proveedor == PROVEEDOR_LOCAL:
		from captura_productos.ai import cliente_local

		return cliente_local.token_requerido()
	api_key = conf.get_password("anthropic_api_key", raise_exception=False)
	if not api_key:
		raise ErrorIA(_("Falta la API key de Anthropic en Configuración Captura: cárguela y use Reprocesar IA."))
	return api_key


def obtener_datos_ia(
	doc: CapturadeProducto, conf, proveedor: str, modelo: str, contexto: prompts.ContextoPrompt, secreto: str
) -> ResultadoIA:
	"""Arma el mensaje (leyendo las fotos), cierra la transacción de lectura y llama al proveedor.

	Hasta la llamada solo hay lecturas: se cierra la transacción para no tenerla abierta (con su foto
	de lectura y sus bloqueos) mientras se espera a Claude, que puede tardar minutos. Lanza ErrorIA si
	la llamada o la respuesta fallan."""
	max_fotos = cint(conf.max_fotos_ia) or MAX_FOTOS_DEFECTO
	if proveedor == PROVEEDOR_LOCAL:
		from captura_productos.ai import cliente_local

		prompt, archivos, avisos = construir_entrada_local(doc, max_fotos)
		system = prompts.construir_system_json(contexto)
		_confirmar()
		resultado = cliente_local.consultar_producto(
			system, prompt, archivos, modelo, cint(conf.timeout_ia) or cliente_local.TIMEOUT_DEFECTO, secreto
		)
	else:
		contenido, avisos = construir_contenido_usuario(doc, max_fotos)
		cliente = crear_cliente(secreto, cint(conf.timeout_ia_seg) or TIMEOUT_DEFECTO)
		_confirmar()
		datos, modelo_usado = llamar_claude(cliente, modelo, prompts.construir_system(contexto), contenido)
		resultado = ResultadoIA(datos=datos, modelo=modelo_usado)
	resultado.avisos = avisos + resultado.avisos
	return resultado


def _anotar_consumo(doc: CapturadeProducto, costo_usd: float = 0.0, duracion_ms: int | None = None) -> None:
	"""El costo se acumula entre intentos y reintentos; la duración es la de la última llamada."""
	if costo_usd:
		doc.costo_ia_usd = flt(doc.costo_ia_usd) + flt(costo_usd)
	if duracion_ms is not None:
		doc.duracion_ia_ms = cint(duracion_ms)


def _captura_en_proceso(captura: str) -> CapturadeProducto | None:
	if not frappe.db.exists(DOCTYPE, captura):
		return None
	doc: CapturadeProducto = frappe.get_doc(DOCTYPE, captura)
	if doc.estado != "Procesando":
		frappe.logger("captura_productos").info(f"{captura}: estado {doc.estado}, no se procesa")
		return None
	doc.flags.ignore_permissions = True
	return doc


def _despues_de_un_fallo(
	captura: str,
	detalle: str,
	transitorio: bool,
	modelo: str,
	respuesta: str | None = None,
	costo_usd: float = 0.0,
	duracion_ms: int | None = None,
) -> None:
	"""Programa un reintento si el error es transitorio y quedan reintentos; si no, pasa a Error.
	`respuesta` es el texto crudo del proveedor, si llegó a responder (se guarda en respuesta_ia)."""
	doc = _captura_en_proceso(captura)
	if not doc:
		return
	reintentos = cint(doc.reintentos_ia)
	if not transitorio or reintentos >= len(ESPERAS_REINTENTO):
		if reintentos:
			detalle = _("{0} (tras {1} reintentos)").format(detalle, reintentos)
		_registrar_error(captura, detalle, modelo, respuesta, costo_usd, duracion_ms)
		return
	espera = ESPERAS_REINTENTO[reintentos]
	frappe.logger("captura_productos").warning(f"{captura}: error transitorio de IA, reintento en {espera} s")
	doc.intentos_ia = cint(doc.intentos_ia) + 1
	doc.modelo_ia = modelo
	_anotar_consumo(doc, costo_usd, duracion_ms)
	if respuesta:
		doc.respuesta_ia = respuesta
	doc.reintentos_ia = reintentos + 1
	doc.reintentar_ia_en = add_to_date(now_datetime(), seconds=espera)
	doc.error_log = _("Error transitorio; reintento {0} de {1} a partir de {2}.\n{3}").format(
		reintentos + 1, len(ESPERAS_REINTENTO), doc.reintentar_ia_en, detalle
	)
	doc._guardar(validar=False)
	_confirmar()


def _registrar_error(
	captura: str,
	detalle: str,
	modelo: str | None = None,
	respuesta: str | None = None,
	costo_usd: float = 0.0,
	duracion_ms: int | None = None,
) -> None:
	"""Deja la captura en Error con el detalle técnico y un Error Log de Frappe."""
	frappe.log_error(title=_("Captura {0}: error de IA").format(captura), message=detalle)
	doc = _captura_en_proceso(captura)
	if not doc:
		return
	doc.intentos_ia = cint(doc.intentos_ia) + 1
	if modelo:
		doc.modelo_ia = modelo
	_anotar_consumo(doc, costo_usd, duracion_ms)
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
	"""Chequeo mínimo del esquema. Con Anthropic directo la salida estructurada ya lo garantiza; con la
	API local (JSON pedido por prompt) es la única garantía, junto con `aplicar_respuesta`, que tolera
	tipos inesperados en el resto de los campos."""
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
	"""Anthropic directo. Bloques del mensaje: fotos (con su rótulo) y al final el texto. Devuelve
	también avisos."""
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
	contenido.append({"type": "text", "text": prompts.construir_texto_usuario(datos_captura(doc))})
	return contenido, avisos


def construir_entrada_local(
	doc: CapturadeProducto, max_fotos: int
) -> tuple[str, list[tuple[str, bytes, str]], list[str]]:
	"""API local. Devuelve (prompt, archivos, avisos); cada archivo es (nombre, bytes, media_type), con
	las mismas fotos que el camino directo, ya reducidas y leídas solo con `leer_archivo`."""
	avisos: list[str] = []
	archivos: list[tuple[str, bytes, str]] = []
	rotulos: list[tuple[str, str]] = []
	for rotulo, url in fotos_para_ia(doc)[: max(1, max_fotos)]:
		imagen = imagen_para_ia(url, doc.name)
		if not imagen:
			avisos.append(_("No se pudo leer la foto {0}: no se envió a la IA").format(url))
			continue
		contenido, media_type = imagen
		nombre = f"foto-{len(archivos) + 1}{EXTENSIONES[media_type]}"
		archivos.append((nombre, contenido, media_type))
		rotulos.append((nombre, rotulo))
	texto = prompts.construir_texto_usuario(datos_captura(doc))
	return prompts.construir_prompt_json(texto, rotulos), archivos, avisos


def datos_captura(doc: CapturadeProducto) -> prompts.DatosCaptura:
	variantes = [
		prompts.VarianteCapturada(
			indice=v.idx, transcripcion=v.transcripcion or "", barcode=v.barcode or "", tiene_foto=bool(v.foto)
		)
		for v in (doc.variantes or [])
	]
	return prompts.DatosCaptura(
		transcripcion=doc.transcripcion or "",
		codigos_barras=[c.barcode for c in doc.codigos_barras or [] if c.barcode],
		tiene_variantes=bool(doc.tiene_variantes),
		atributos=naming.atributos_ordenados(doc),
		variantes=variantes,
	)


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


def imagen_para_ia(url: str, captura: str) -> tuple[bytes, str] | None:
	"""(bytes, media_type) de una foto adjunta a la captura, reducida si es grande. None si no se
	puede leer o no es una imagen."""
	contenido = leer_archivo(url, captura)
	if not contenido:
		return None
	if isinstance(contenido, str):
		contenido = contenido.encode()
	media_type = tipo_imagen(contenido)
	if not media_type:
		return None
	return reducir_imagen(contenido) or (contenido, media_type)


def imagen_base64(url: str, captura: str) -> tuple[str, str] | None:
	imagen = imagen_para_ia(url, captura)
	if not imagen:
		return None
	contenido, media_type = imagen
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
	"""Anthropic directo: verifica la API key y el modelo sin generar tokens (Models API). Devuelve un
	texto para el usuario. La prueba de la API local está en `cliente_local.probar_conexion`."""
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
