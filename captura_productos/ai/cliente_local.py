"""Cliente de la API local de Claude (spec sección 6): un envoltorio HTTP de Claude Code que corre en
el mismo servidor que ERPNext.

Contrato:
- `POST /v1/query` (multipart): `prompt`, `files` (uno por archivo), `system_prompt`, `model`, `mode`
  (`lean`/`full`), `timeout` (s). Responde `{session_id, result, is_error, num_turns, duration_ms,
  total_cost_usd, usage}`.
- `DELETE /v1/sessions/{session_id}`: borra la sesión y sus archivos. Se llama SIEMPRE al terminar.
- Errores: 401 token inválido, 404 sesión inexistente, 413 archivo > 50 MB, 504 tiempo agotado,
  502 fallo de Claude Code. Atiende 3 consultas a la vez; las demás esperan (cuenta en el timeout).
- Sin salida estructurada: el JSON de `registrar_producto` se pide por prompt
  (`prompts.construir_system_json`) y se extrae del texto (`extraer_json`).

La URL y el token se leen de site_config (`claude_api_url`, `claude_api_token`), nunca de la base de
datos ni del código. El token no se escribe en logs ni en mensajes de error (`ErrorIA.enmascarar`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import frappe
import requests
from frappe import _

from captura_productos.ai.extractor import ErrorIA, ResultadoIA, _sin_secretos, validar_esquema

URL_DEFECTO = "http://127.0.0.1:8787"
MODO = "lean"
TIMEOUT_DEFECTO = 180
MARGEN_TIMEOUT = 30  # el cliente espera un poco más que el timeout que se pide al servicio
TIMEOUT_BORRADO = 5
TIMEOUT_PING = 10
MODELO_PRUEBA = "claude-haiku-5-5"
PROMPT_PRUEBA = "Responde solo: OK"
TIMEOUT_PRUEBA = 60
LARGO_DETALLE = 500

_RE_CERCO = re.compile(r"```[A-Za-z0-9_-]*")

# Textos de un `is_error: true` que no se arreglan reintentando (credenciales, saldo, modelo).
_ERRORES_DEFINITIVOS = (
	"authentication",
	"invalid api key",
	"invalid x-api-key",
	"unauthorized",
	"permission",
	"credit balance",
	"billing",
	"not_found_error",
	"model not found",
	"invalid model",
	"refus",
)


@dataclass
class RespuestaLocal:
	texto: str
	session_id: str | None = None
	costo_usd: float = 0.0
	duracion_ms: int | None = None
	num_turns: int | None = None
	sesion_borrada: bool = False


# ---------------------------------------------------------------------------------------------------
# Configuración (site_config)
# ---------------------------------------------------------------------------------------------------


def configuracion() -> tuple[str, str | None]:
	"""(url base sin barra final, token o None)."""
	url = str(frappe.conf.get("claude_api_url") or URL_DEFECTO).strip().rstrip("/")
	token = str(frappe.conf.get("claude_api_token") or "").strip() or None
	return url, token


def token_requerido() -> str:
	_url, token = configuracion()
	if not token:
		raise ErrorIA(
			_(
				"Falta claude_api_token en site_config: configúrelo en el servidor con "
				"`bench --site <sitio> set-config claude_api_token <token>` (y claude_api_url si no es "
				"{0}) y use Reprocesar IA."
			).format(URL_DEFECTO)
		)
	return token


# ---------------------------------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------------------------------


def consultar_producto(
	system: str,
	prompt: str,
	archivos: list[tuple[str, bytes, str]],
	modelo: str,
	timeout: int,
	token: str | None = None,
) -> ResultadoIA:
	"""Extracción de un producto: consulta, extrae el JSON del texto y valida el esquema.

	JSON inválido o fuera de esquema es un error transitorio que lleva el texto crudo y el costo."""
	url, token_conf = configuracion()
	token = token or token_conf or token_requerido()
	respuesta = consultar(
		url, token, prompt, system_prompt=system, archivos=archivos, modelo=modelo, timeout=timeout
	)
	try:
		datos = interpretar_texto(respuesta.texto)
	except ErrorIA as e:
		e.respuesta, e.costo_usd, e.duracion_ms = respuesta.texto, respuesta.costo_usd, respuesta.duracion_ms
		raise e.enmascarar(token) from None
	return ResultadoIA(
		datos=datos, modelo=modelo, costo_usd=respuesta.costo_usd, duracion_ms=respuesta.duracion_ms
	)


def consultar(
	url: str,
	token: str,
	prompt: str,
	*,
	system_prompt: str | None = None,
	archivos: list[tuple[str, bytes, str]] = (),
	modelo: str | None = None,
	timeout: int = TIMEOUT_DEFECTO,
) -> RespuestaLocal:
	"""Una consulta a `POST /v1/query` (multipart). Borra siempre la sesión al terminar, aunque falle.
	Lanza ErrorIA (sin el token) si el servicio no responde, responde un error o `is_error`."""
	campos: list[tuple[str, Any]] = [
		("prompt", (None, prompt)),
		("mode", (None, MODO)),
		("timeout", (None, str(timeout))),
	]
	if system_prompt:
		campos.append(("system_prompt", (None, system_prompt)))
	if modelo:
		campos.append(("model", (None, modelo)))
	# Todo como partes de `files` para que el cuerpo sea multipart aunque no haya fotos.
	campos += [("files", (nombre, contenido, media_type)) for nombre, contenido, media_type in archivos]

	session_id = None
	try:
		try:
			respuesta = requests.post(
				f"{url}/v1/query", headers=_cabeceras(token), files=campos, timeout=timeout + MARGEN_TIMEOUT
			)
		except requests.Timeout:
			raise ErrorIA(
				_("El servicio de IA local no responde: no contestó en {0} s.").format(
					timeout + MARGEN_TIMEOUT
				),
				True,
			) from None
		except requests.ConnectionError as e:
			raise ErrorIA(
				_("El servicio de IA local no responde: no se pudo conectar con {0} ({1}).").format(
					url, type(e).__name__
				),
				True,
			) from None
		except requests.RequestException as e:
			raise ErrorIA(_("Error al llamar a la API local ({0}).").format(type(e).__name__), True) from None

		cuerpo = _cuerpo_json(respuesta)
		if isinstance(cuerpo, dict) and isinstance(cuerpo.get("session_id"), str):
			session_id = cuerpo["session_id"]
		_revisar_estado(respuesta, cuerpo)
		resultado = _leer_resultado(cuerpo)
	except ErrorIA as e:
		raise e.enmascarar(token) from None
	finally:
		borrada = borrar_sesion(url, token, session_id) if session_id else False
	resultado.sesion_borrada = borrada
	return resultado


def borrar_sesion(url: str, token: str, session_id: str) -> bool:
	"""`DELETE /v1/sessions/{id}`, best effort: nunca lanza. True si el servicio la borró."""
	try:
		respuesta = requests.delete(
			f"{url}/v1/sessions/{quote(session_id, safe='')}",
			headers=_cabeceras(token),
			timeout=TIMEOUT_BORRADO,
		)
	except Exception as e:
		frappe.logger("captura_productos").warning(
			f"API local: no se pudo borrar la sesión {session_id} ({type(e).__name__})"
		)
		return False
	if respuesta.status_code >= 400:
		frappe.logger("captura_productos").warning(
			f"API local: borrar la sesión {session_id} respondió HTTP {respuesta.status_code}"
		)
		return False
	return True


def _cabeceras(token: str) -> dict[str, str]:
	return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _cuerpo_json(respuesta: requests.Response) -> Any:
	try:
		return respuesta.json()
	except ValueError:
		return None


def _detalle(respuesta: requests.Response, cuerpo: Any) -> str:
	"""Texto corto del error que devolvió el servicio (FastAPI usa {"detail": ...})."""
	if isinstance(cuerpo, dict) and "detail" in cuerpo:
		texto = (
			cuerpo["detail"]
			if isinstance(cuerpo["detail"], str)
			else json.dumps(cuerpo["detail"], ensure_ascii=False)
		)
	else:
		texto = respuesta.text or ""
	return texto.strip()[:LARGO_DETALLE]


def _revisar_estado(respuesta: requests.Response, cuerpo: Any) -> None:
	codigo = respuesta.status_code
	if codigo < 400:
		return
	detalle = _detalle(respuesta, cuerpo)
	if codigo == 401:
		raise ErrorIA(
			_(
				"Token de la API local inválido o ausente (HTTP 401): revise claude_api_token en site_config. {0}"
			).format(detalle)
		)
	if codigo == 413:
		raise ErrorIA(
			_("Imagen demasiado grande para la API local (HTTP 413, máximo 50 MB por archivo). {0}").format(
				detalle
			)
		)
	if codigo == 404:
		raise ErrorIA(
			_(
				"La API local respondió HTTP 404 (sesión o ruta inexistente): revise claude_api_url. {0}"
			).format(detalle)
		)
	if codigo == 502:
		raise ErrorIA(
			_("El servicio de IA local no responde (HTTP 502: falló Claude Code). {0}").format(detalle), True
		)
	if codigo == 504:
		raise ErrorIA(
			_("El servicio de IA local no responde (HTTP 504: se agotó el tiempo). {0}").format(detalle), True
		)
	transitorio = codigo == 429 or codigo >= 500
	raise ErrorIA(_("La API local respondió HTTP {0}: {1}").format(codigo, detalle), transitorio)


def _leer_resultado(cuerpo: Any) -> RespuestaLocal:
	if not isinstance(cuerpo, dict):
		raise ErrorIA(_("La API local devolvió una respuesta que no es JSON."), True)
	texto = cuerpo.get("result")
	texto = (
		texto if isinstance(texto, str) else ("" if texto is None else json.dumps(texto, ensure_ascii=False))
	)
	resultado = RespuestaLocal(
		texto=texto,
		session_id=cuerpo.get("session_id"),
		costo_usd=_numero(cuerpo.get("total_cost_usd")),
		duracion_ms=int(_numero(cuerpo.get("duration_ms")))
		if cuerpo.get("duration_ms") is not None
		else None,
		num_turns=cuerpo.get("num_turns"),
	)
	if cuerpo.get("is_error"):
		definitivo = any(clave in texto.lower() for clave in _ERRORES_DEFINITIVOS)
		raise ErrorIA(
			_("Claude Code devolvió un error: {0}").format(
				texto.strip()[:LARGO_DETALLE] or _("(sin detalle)")
			),
			not definitivo,
			respuesta=texto,
			costo_usd=resultado.costo_usd,
			duracion_ms=resultado.duracion_ms,
		)
	return resultado


def _numero(valor: Any) -> float:
	try:
		return float(valor or 0)
	except TypeError, ValueError:
		return 0.0


# ---------------------------------------------------------------------------------------------------
# Parseo del JSON pedido por prompt
# ---------------------------------------------------------------------------------------------------


def extraer_json(texto: str) -> dict | None:
	"""Primer objeto JSON `{...}` del texto, tolerando cercos ```json y texto alrededor. None si no hay."""
	limpio = _RE_CERCO.sub("", texto or "")
	decodificador = json.JSONDecoder()
	inicio = limpio.find("{")
	while inicio != -1:
		try:
			objeto, _fin = decodificador.raw_decode(limpio, inicio)
		except json.JSONDecodeError:
			inicio = limpio.find("{", inicio + 1)
			continue
		if isinstance(objeto, dict):
			return objeto
		inicio = limpio.find("{", inicio + 1)
	return None


def interpretar_texto(texto: str) -> dict:
	"""JSON de `registrar_producto` desde el texto de la API local. ErrorIA transitorio si no hay un
	objeto JSON válido o no cumple el esquema (el modelo suele acertar al reintentar)."""
	if not (texto or "").strip():
		raise ErrorIA(_("La API local devolvió una respuesta vacía."), True)
	datos = extraer_json(texto)
	if datos is None:
		raise ErrorIA(_("La respuesta de la API local no trae un objeto JSON válido."), True)
	faltan = validar_esquema(datos)
	if faltan:
		raise ErrorIA(
			_("La respuesta de la API local no cumple el esquema: {0}").format("; ".join(faltan)), True
		)
	return datos


# ---------------------------------------------------------------------------------------------------
# Prueba de conexión (botón de Configuración)
# ---------------------------------------------------------------------------------------------------


def probar_conexion() -> str:
	"""(a) que el servicio responda (`/openapi.json`, o `/docs`); (b) una consulta mínima con
	`MODELO_PRUEBA` para validar el token. Devuelve un texto para el usuario; ErrorIA si algo falla."""
	url, token = configuracion()
	try:
		ping = requests.get(f"{url}/openapi.json", timeout=TIMEOUT_PING)
		if ping.status_code == 404:
			ping = requests.get(f"{url}/docs", timeout=TIMEOUT_PING)
	except requests.RequestException as e:
		raise ErrorIA(
			_(
				"No se pudo conectar con la API local en {0} ({1}). Verifique que el servicio esté levantado "
				"(supervisorctl status) y que claude_api_url en site_config sea correcta."
			).format(url, type(e).__name__)
		) from None
	if ping.status_code >= 500:
		raise ErrorIA(_("La API local en {0} responde, pero con HTTP {1}.").format(url, ping.status_code))
	if not token:
		raise ErrorIA(
			_(
				"La API local responde en {0}, pero falta claude_api_token en site_config: "
				"`bench --site <sitio> set-config claude_api_token <token>`."
			).format(url)
		)

	try:
		respuesta = consultar(url, token, PROMPT_PRUEBA, modelo=MODELO_PRUEBA, timeout=TIMEOUT_PRUEBA)
	except ErrorIA as e:
		raise ErrorIA(
			_("La API local responde en {0}, pero la consulta de prueba falló: {1}").format(url, e.detalle)
		) from None
	segundos = (respuesta.duracion_ms or 0) / 1000
	mensaje = _('Conexión OK con la API local ({0}). {1} respondió "{2}" en {3} s; costo US$ {4}.').format(
		url, MODELO_PRUEBA, respuesta.texto.strip()[:40], f"{segundos:.1f}", f"{respuesta.costo_usd:.4f}"
	)
	if respuesta.sesion_borrada:
		mensaje += " " + _("La sesión de prueba se borró.")
	else:
		mensaje += " " + _("Aviso: no se pudo borrar la sesión de prueba.")
	return _sin_secretos(mensaje, token)
