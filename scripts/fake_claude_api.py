#!/usr/bin/env python3
"""API local de Claude FALSA, solo para desarrollo (imita el envoltorio HTTP de Claude Code).

No llama a ninguna IA: devuelve en `result` un JSON plausible de `registrar_producto` (a veces envuelto
en ```json o con texto alrededor, para probar el parseo). Usa werkzeug, que ya está en el env del
bench. Uso (dentro del contenedor, ver docs/DEV.md):

    FAKE_CLAUDE_API_TOKEN=<token> ./env/bin/python apps/captura_productos/scripts/fake_claude_api.py

Rutas: POST /v1/query (multipart), POST /v1/query/json, DELETE /v1/sessions/{id}, GET /openapi.json,
GET /docs y, solo en el falso, GET /v1/sessions (sesiones vivas y borradas, para verificar).

Simular fallos: variable de entorno FAKE_CLAUDE_MODO, o una palabra clave en el prompt (por ejemplo en
el dictado de la captura): FAKE_401, FAKE_404, FAKE_413, FAKE_502, FAKE_504, FAKE_JSON_INVALIDO,
FAKE_ESQUEMA (JSON sin campos obligatorios), FAKE_IS_ERROR, FAKE_LENTO (duerme FAKE_CLAUDE_LENTO_S s).
"""

from __future__ import annotations

import argparse
import base64
import hmac
import itertools
import json
import os
import re
import sys
import threading
import time
import uuid

from werkzeug.exceptions import HTTPException
from werkzeug.routing import Map, Rule
from werkzeug.serving import run_simple
from werkzeug.wrappers import Request, Response

MAX_ARCHIVO = 50 * 1024 * 1024
CONCURRENTES = 3
MODOS = ("401", "404", "413", "502", "504", "JSON_INVALIDO", "ESQUEMA", "IS_ERROR", "LENTO")

TOKEN = os.environ.get("FAKE_CLAUDE_API_TOKEN", "")
MODO_ENTORNO = os.environ.get("FAKE_CLAUDE_MODO", "").strip().upper()
LENTO_S = float(os.environ.get("FAKE_CLAUDE_LENTO_S", "20"))

_cupos = threading.BoundedSemaphore(CONCURRENTES)
_lock = threading.Lock()
_sesiones: dict[str, dict] = {}
_borradas: list[str] = []
_contador = itertools.count(1)

OPENAPI = {
	"openapi": "3.1.0",
	"info": {"title": "Claude Code API (FALSA, desarrollo)", "version": "0.0-fake"},
	"paths": {
		"/v1/query": {"post": {"summary": "Consulta (multipart)"}},
		"/v1/query/json": {"post": {"summary": "Consulta (JSON, archivos en base64)"}},
		"/v1/sessions/{session_id}": {"delete": {"summary": "Borra la sesión y sus archivos"}},
	},
}


def log(mensaje: str) -> None:
	print(time.strftime("%H:%M:%S"), mensaje, flush=True)


def json_respuesta(datos, status: int = 200) -> Response:
	return Response(json.dumps(datos, ensure_ascii=False), status=status, mimetype="application/json")


def error(status: int, detalle: str) -> Response:
	return json_respuesta({"detail": detalle}, status)


def autorizado(request: Request) -> bool:
	cabecera = request.headers.get("Authorization", "")
	if not TOKEN or not cabecera.startswith("Bearer "):
		return False
	return hmac.compare_digest(cabecera[len("Bearer ") :].encode(), TOKEN.encode())


def modo_de(prompt: str) -> str:
	for modo in MODOS:
		if f"FAKE_{modo}" in prompt:
			return modo
	return MODO_ENTORNO


def lista_de_seccion(system: str, titulo: str) -> list[str]:
	"""Valores '- x' bajo un título '## ...' del system prompt (grupos, unidades)."""
	m = re.search(rf"^## {re.escape(titulo)}[^\n]*\n((?:- [^\n]*\n?)+)", system, re.MULTILINE)
	return [linea[2:].strip() for linea in m.group(1).splitlines()] if m else []


def producto_plausible(prompt: str, system: str, n_fotos: int) -> dict:
	grupos = lista_de_seccion(system, "Grupos de producto")
	uoms = lista_de_seccion(system, "Unidades de stock")
	dictado = re.search(r"Dictado principal: ([^\n]*)", prompt)
	dictado = dictado.group(1).strip() if dictado else ""
	return {
		"tipo_producto": "Gaseosa",
		"marca": "Coca-Cola",
		"variante": "Sin Azúcar"
		if "sin azucar" in dictado.lower() or "sin azúcar" in dictado.lower()
		else None,
		"medida": {"valor": 2, "unidad": "L", "texto_original": "2 litros"},
		"item_group": grupos[0] if grupos else "Bebidas",
		"stock_uom": "Unidad" if "Unidad" in uoms or not uoms else uoms[0],
		"descripcion": "Gaseosa Coca-Cola de 2 litros (respuesta de la API FALSA de desarrollo).",
		"tiene_variantes": False,
		"atributos": [],
		"variantes": [],
		"texto_en_fotos": f"Coca-Cola 2L ({n_fotos} foto(s) recibida(s))",
		"confianza": 0.87,
		"advertencias": ["Respuesta generada por el servidor falso de desarrollo."],
	}


def envolver(datos: dict, n: int) -> str:
	"""Alterna formatos para probar el parseo: JSON puro, con cerco ```json, con texto alrededor."""
	texto = json.dumps(datos, ensure_ascii=False, indent=2)
	if n % 3 == 1:
		return f"```json\n{texto}\n```"
	if n % 3 == 2:
		return f"Aquí está el producto registrado:\n{texto}\nAvísame si necesitas algo más."
	return texto


def atender(prompt: str, system: str, archivos: list[tuple[str, bytes]], modelo: str) -> Response:
	if not prompt:
		return error(422, "Falta el campo prompt")
	for nombre, contenido in archivos:
		if len(contenido) > MAX_ARCHIVO:
			return error(413, f"El archivo {nombre} supera 50 MB")
	modo = modo_de(prompt)
	if modo == "401":
		return error(401, "Token inválido")
	if modo == "404":
		return error(404, "Sesión inexistente")
	if modo == "413":
		return error(413, "Archivo demasiado grande")
	if modo == "502":
		return error(502, "Claude Code terminó con error (simulado)")
	if modo == "504":
		return error(504, "Tiempo agotado (simulado)")

	n = next(_contador)
	session_id = str(uuid.uuid4())
	with _lock:
		_sesiones[session_id] = {"archivos": [nombre for nombre, _c in archivos], "modelo": modelo}
	inicio = time.monotonic()
	with _cupos:  # como el real: 3 consultas a la vez, las demás esperan
		if modo == "LENTO":
			time.sleep(LENTO_S)
		if "Responde solo: OK" in prompt:
			resultado, es_error = "OK", False
		elif modo == "IS_ERROR":
			resultado, es_error = "Claude Code: error interno simulado", True
		elif modo == "JSON_INVALIDO":
			resultado, es_error = '```json\n{"tipo_producto": "Gaseosa", "marca": \n```', False
		elif modo == "ESQUEMA":
			resultado, es_error = json.dumps({"marca": "Coca-Cola"}), False
		else:
			resultado, es_error = envolver(producto_plausible(prompt, system, len(archivos)), n), False
	duracion = int((time.monotonic() - inicio) * 1000) + 1234
	log(
		f"consulta {n}: modelo={modelo or '-'} archivos={len(archivos)} modo={modo or 'normal'} sesion={session_id}"
	)
	return json_respuesta(
		{
			"session_id": session_id,
			"result": resultado,
			"is_error": es_error,
			"num_turns": 1,
			"duration_ms": duracion,
			"total_cost_usd": 0.0123 if "Responde solo: OK" not in prompt else 0.0004,
			"usage": {"input_tokens": 1700, "output_tokens": 350},
		}
	)


def query_multipart(request: Request) -> Response:
	archivos = [(f.filename or "archivo", f.read()) for f in request.files.getlist("files")]
	return atender(
		request.form.get("prompt", ""),
		request.form.get("system_prompt", ""),
		archivos,
		request.form.get("model", ""),
	)


def query_json(request: Request) -> Response:
	datos = request.get_json(silent=True) or {}
	archivos = []
	for archivo in datos.get("files") or []:
		try:
			archivos.append(
				(archivo.get("name", "archivo"), base64.b64decode(archivo.get("content_base64", "")))
			)
		except ValueError:
			return error(422, "content_base64 inválido")
	return atender(datos.get("prompt", ""), datos.get("system_prompt", ""), archivos, datos.get("model", ""))


def borrar_sesion(session_id: str) -> Response:
	with _lock:
		if session_id not in _sesiones:
			return error(404, "Sesión inexistente")
		del _sesiones[session_id]
		_borradas.append(session_id)
	log(f"sesión borrada {session_id}")
	return json_respuesta({"deleted": True, "session_id": session_id})


def listar_sesiones() -> Response:
	with _lock:
		return json_respuesta({"vivas": sorted(_sesiones), "borradas": list(_borradas)})


URLS = Map(
	[
		Rule("/v1/query", endpoint="query", methods=["POST"]),
		Rule("/v1/query/json", endpoint="query_json", methods=["POST"]),
		Rule("/v1/sessions", endpoint="sesiones", methods=["GET"]),
		Rule("/v1/sessions/<session_id>", endpoint="borrar", methods=["DELETE"]),
		Rule("/openapi.json", endpoint="openapi", methods=["GET"]),
		Rule("/docs", endpoint="docs", methods=["GET"]),
	]
)


@Request.application
def app(request: Request) -> Response:
	try:
		endpoint, args = URLS.bind_to_environ(request.environ).match()
	except HTTPException as e:
		return error(e.code or 500, e.description or "")
	if endpoint == "openapi":
		return json_respuesta(OPENAPI)
	if endpoint == "docs":
		return Response("<h1>Claude Code API (FALSA)</h1><p>Ver /openapi.json</p>", mimetype="text/html")
	if not autorizado(request):
		return error(401, "Token inválido o ausente")
	if endpoint == "query":
		return query_multipart(request)
	if endpoint == "query_json":
		return query_json(request)
	if endpoint == "borrar":
		return borrar_sesion(args["session_id"])
	return listar_sesiones()


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("--host", default="127.0.0.1")
	parser.add_argument("--port", type=int, default=8787)
	args = parser.parse_args()
	if not TOKEN:
		sys.exit("Falta FAKE_CLAUDE_API_TOKEN (el mismo valor que claude_api_token en site_config).")
	log(f"API local FALSA en http://{args.host}:{args.port} (modo: {MODO_ENTORNO or 'normal'})")
	run_simple(args.host, args.port, app, threaded=True, use_reloader=False)


if __name__ == "__main__":
	main()
