"""Extractor de Claude con la API mockeada (spec secciones 6, 13, HU-09 y HU-13).

Proveedor "Anthropic directo"; la API local está en test_cliente_local.py."""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import frappe
import httpx2
from frappe.utils import add_to_date, now_datetime

from captura_productos.ai import extractor, prompts
from captura_productos.tests.utils import (
	GRUPO_BEBIDAS,
	UOM,
	CapturaTestCase,
	configurar,
	nueva_captura,
)

API_KEY = "sk-ant-test-NO-ES-REAL-123"
EAN_DURAZNO = "4006381333931"
EAN_MANZANA = "7501031311309"


def respuesta_ia(**cambios) -> dict:
	datos = {
		"tipo_producto": "Gaseosa",
		"marca": "pepsi",
		"variante": None,
		"medida": {"valor": 1, "unidad": "L", "texto_original": "de litro"},
		"item_group": GRUPO_BEBIDAS,
		"stock_uom": UOM,
		"descripcion": "Gaseosa Pepsi de 1 litro.",
		"tiene_variantes": False,
		"atributos": [],
		"variantes": [],
		"texto_en_fotos": "Pepsi 1L",
		"confianza": 0.92,
		"advertencias": [],
	}
	datos.update(cambios)
	return datos


def mensaje(datos: dict | None = None, stop_reason="end_turn", texto=None, modelo="claude-sonnet-5-5"):
	bloques = [SimpleNamespace(type="thinking", thinking="")]
	bloques.append(SimpleNamespace(type="text", text=texto if texto is not None else json.dumps(datos)))
	return SimpleNamespace(content=bloques, stop_reason=stop_reason, stop_details=None, model=modelo)


def error_http(codigo: int) -> anthropic.APIStatusError:
	solicitud = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
	clase = {401: anthropic.AuthenticationError, 429: anthropic.RateLimitError}.get(codigo, anthropic.APIStatusError)
	return clase(f"Error code: {codigo}", response=httpx2.Response(codigo, request=solicitud), body=None)


class ClienteFalso:
	"""Imita `anthropic.Anthropic`: devuelve (o lanza) los resultados en orden y guarda los params."""

	def __init__(self, *resultados):
		self.resultados = list(resultados)
		self.llamadas: list[dict] = []
		self.messages = SimpleNamespace(create=self._create)
		self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

	def _create(self, **params):
		self.llamadas.append(params)
		resultado = self.resultados.pop(0)
		if isinstance(resultado, Exception):
			raise resultado
		return resultado


class TestPrompt(CapturaTestCase):
	def test_system_incluye_contexto_y_es_determinista(self):
		conf = frappe.get_single("Configuracion Captura")
		from captura_productos import naming

		ctx = extractor.cargar_contexto_prompt(conf, naming.cargar_contexto())
		system = prompts.construir_system(ctx)
		self.assertIn(GRUPO_BEBIDAS, system)
		self.assertIn("coca cola, cocacola, coca → Coca-Cola", system)
		self.assertIn("- Sabor: Durazno (DUR)", system)
		self.assertIn("Paquete: numérico de 6 a 48", system)
		self.assertEqual(system, prompts.construir_system(extractor.cargar_contexto_prompt(conf, naming.cargar_contexto())))

	def test_esquema_es_estricto(self):
		def revisar(nodo):
			if isinstance(nodo, dict):
				if nodo.get("type") == "object":
					self.assertIs(nodo.get("additionalProperties"), False)
					self.assertEqual(set(nodo["required"]), set(nodo["properties"]))
				for valor in nodo.values():
					revisar(valor)
			elif isinstance(nodo, list):
				for valor in nodo:
					revisar(valor)

		revisar(prompts.ESQUEMA_PRODUCTO)
		self.assertIn("atributos", prompts.ESQUEMA_PRODUCTO["required"])

	def test_texto_usuario_con_variantes(self):
		texto = prompts.construir_texto_usuario(
			prompts.DatosCaptura(
				transcripcion="jugo frut all de litro",
				tiene_variantes=True,
				variantes=[prompts.VarianteCapturada(1, "sabor durazno", EAN_DURAZNO, True)],
			)
		)
		self.assertIn("Variante 1: dictado: sabor durazno; código 4006381333931; tiene foto propia", texto)

	def test_contenido_con_fotos(self):
		doc = nueva_captura(transcripcion="pepsi de litro", fotos=[{"foto": "/files/a.jpg", "es_principal": 1}])
		doc.imagen_principal = "/files/a.jpg"
		jpeg = b"\xff\xd8\xff\xe0" + b"0" * 20
		with patch.object(extractor, "leer_archivo", return_value=jpeg):
			contenido, avisos = extractor.construir_contenido_usuario(doc, 4)
		self.assertEqual(avisos, [])
		self.assertEqual([b["type"] for b in contenido], ["text", "image", "text"])
		self.assertEqual(contenido[1]["source"]["media_type"], "image/jpeg")
		with patch.object(extractor, "leer_archivo", return_value=None):
			contenido, avisos = extractor.construir_contenido_usuario(doc, 4)
		self.assertEqual(len(contenido), 1)
		self.assertIn("No se pudo leer la foto /files/a.jpg", avisos[0])


class TestRespuesta(CapturaTestCase):
	def test_json_valido(self):
		self.assertEqual(extractor.interpretar_respuesta(mensaje(respuesta_ia()))["marca"], "pepsi")

	def test_negativa_es_definitiva(self):
		with self.assertRaises(extractor.ErrorIA) as ctx:
			extractor.interpretar_respuesta(mensaje(stop_reason="refusal", texto=""))
		self.assertFalse(ctx.exception.transitorio)

	def test_json_invalido_o_cortado_se_reintenta(self):
		for respuesta in (mensaje(texto="{no es json"), mensaje(respuesta_ia(), stop_reason="max_tokens")):
			with self.assertRaises(extractor.ErrorIA) as ctx:
				extractor.interpretar_respuesta(respuesta)
			self.assertTrue(ctx.exception.transitorio)
		with self.assertRaises(extractor.ErrorIA):
			extractor.interpretar_respuesta(mensaje(texto=json.dumps({"marca": "x"})))

	def test_llamada_usa_salida_estructurada_cache_y_fallback(self):
		cliente = ClienteFalso(mensaje(respuesta_ia()))
		extractor.llamar_claude(cliente, "claude-sonnet-5-5", "SYSTEM", [{"type": "text", "text": "hola"}])
		params = cliente.llamadas[0]
		self.assertNotIn("tool_choice", params)
		self.assertEqual(params["output_config"]["format"]["schema"], prompts.ESQUEMA_PRODUCTO)
		self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})
		self.assertEqual(params["fallbacks"], "default")
		cliente = ClienteFalso(mensaje(respuesta_ia()))
		extractor.llamar_claude(cliente, "claude-haiku-5-5", "SYSTEM", [])
		self.assertNotIn("fallbacks", cliente.llamadas[0])

	def test_errores_http(self):
		for codigo, transitorio in ((401, False), (400, False), (429, True), (529, True), (500, True)):
			with self.assertRaises(extractor.ErrorIA) as ctx:
				extractor.llamar_claude(ClienteFalso(error_http(codigo)), "claude-sonnet-5-5", "S", [])
			self.assertEqual(ctx.exception.transitorio, transitorio, codigo)
		timeout = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com"))
		with self.assertRaises(extractor.ErrorIA) as ctx:
			extractor.llamar_claude(ClienteFalso(timeout), "claude-sonnet-5-5", "S", [])
		self.assertTrue(ctx.exception.transitorio)

	def test_esperas_de_hu_13(self):
		# CR-M3: ya no se duerme en el worker; las esperas se programan (ver TestJob).
		self.assertEqual(extractor.ESPERAS_REINTENTO, (30, 120))
		self.assertFalse(hasattr(extractor, "llamar_con_reintentos"))


class TestJob(CapturaTestCase):
	def setUp(self):
		super().setUp()
		configurar(anthropic_api_key=API_KEY, proveedor_ia=extractor.PROVEEDOR_ANTHROPIC)
		# frappe.log_error se guarda aunque el test haga rollback: se mockea para no ensuciar Error Log.
		self.log_error = self.enterContext(patch("frappe.log_error"))

	def procesar(self, doc, *resultados):
		cliente = ClienteFalso(*resultados)
		with (
			patch.object(extractor, "crear_cliente", return_value=cliente),
			patch.object(extractor, "encolar_procesamiento") as encolar,
		):
			if doc.estado == "Borrador":
				doc.enviar()
				encolar.assert_called_once_with(doc.name)
			extractor.procesar_captura(doc.name)
		doc.reload()
		return cliente

	def test_producto_simple(self):
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		self.procesar(doc, mensaje(respuesta_ia()))
		self.assertEqual(doc.estado, "Por revisar")
		self.assertEqual((doc.item_name, doc.item_code), ("Pepsi 1 L", "BEB-PEPSI-1L"))
		self.assertEqual((doc.marca, doc.medida_valor, doc.medida_unidad), ("Pepsi", 1, "L"))
		self.assertEqual(doc.confianza_ia, 92)
		self.assertEqual(doc.intentos_ia, 1)
		self.assertEqual(doc.modelo_ia, "claude-sonnet-5-5")
		self.assertEqual(json.loads(doc.respuesta_ia)["texto_en_fotos"], "Pepsi 1L")
		self.assertIsNone(doc.error_log)

	def test_grupo_uom_invalidos_y_baja_confianza(self):
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		self.procesar(
			doc, mensaje(respuesta_ia(item_group="Bebidas", stock_uom="Botella", confianza=1.7, advertencias=["Foto borrosa"]))
		)
		self.assertEqual(doc.estado, "Por revisar")
		self.assertEqual((doc.item_group, doc.stock_uom), (GRUPO_BEBIDAS, UOM))
		self.assertEqual(doc.confianza_ia, 100)
		self.assertIn("Foto borrosa", doc.advertencias_ia)
		self.assertIn("grupo 'Bebidas'", doc.advertencias_ia)
		self.assertIn("unidad 'Botella'", doc.advertencias_ia)

		doc2 = nueva_captura(transcripcion="pepsi de litro").insert()
		self.procesar(doc2, mensaje(respuesta_ia(confianza=0.3)))
		self.assertIn("Baja confianza: revise con las fotos", doc2.advertencias_ia)

	def test_variantes_con_atributos(self):
		doc = nueva_captura(
			transcripcion="jugo frut all de un litro",
			tiene_variantes=1,
			variantes=[
				{"transcripcion": "sabor durazno", "barcode": EAN_DURAZNO},
				{"transcripcion": "sabor manzana", "barcode": EAN_MANZANA},
			],
		).insert()
		datos = respuesta_ia(
			tipo_producto="Jugo",
			marca="frut all",
			tiene_variantes=True,
			atributos=["sabor", "Inventado"],
			variantes=[
				{"indice": 1, "valores": [{"atributo": "Sabor", "valor": "durazno"}], "confianza": 0.9},
				{"indice": 2, "valores": [{"atributo": "Sabor", "valor": "Manzana"}], "confianza": 0.9},
				{"indice": None, "valores": [{"atributo": "Sabor", "valor": "Naranja"}], "confianza": 0.5},
				{"indice": 9, "valores": [], "confianza": 0.9},
			],
		)
		self.procesar(doc, mensaje(datos))
		self.assertEqual(doc.estado, "Por revisar")
		self.assertEqual([a.atributo for a in doc.atributos], ["Sabor"])
		self.assertEqual([v.valor_1 for v in doc.variantes], ["Durazno", "Manzana", "Naranja"])
		self.assertEqual(
			[v.item_code for v in doc.variantes], ["BEB-FRUTALL-DUR-1L", "BEB-FRUTALL-MAN-1L", "BEB-FRUTALL-NAR-1L"]
		)
		self.assertEqual(doc.item_code, "BEB-FRUTALL-1L")
		self.assertIn("atributo 'Inventado'", doc.advertencias_ia)
		self.assertIn("Variante 3: variante sin código de barras", doc.advertencias_ia)
		self.assertIn("variante 9", doc.advertencias_ia)

		# Reprocesar: la fila que agregó la IA se rehace (no se duplica) e intentos_ia sube.
		with patch.object(extractor, "encolar_procesamiento"):
			doc.reprocesar_ia()
		self.procesar(doc, mensaje(datos))
		self.assertEqual(len(doc.variantes), 3)
		self.assertEqual(doc.intentos_ia, 2)

	def test_sin_api_key(self):
		configurar(anthropic_api_key="", proveedor_ia=extractor.PROVEEDOR_ANTHROPIC)
		frappe.db.delete("__Auth", {"doctype": "Configuracion Captura", "fieldname": "anthropic_api_key"})
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		cliente = self.procesar(doc)
		self.assertEqual(cliente.llamadas, [])
		self.assertEqual(doc.estado, "Error")
		self.assertIn("Falta la API key", doc.error_log)
		self.assertEqual(doc.mensaje_capturador, "No se pudo procesar. El supervisor lo revisará.")
		titulos = [c.kwargs.get("title") for c in self.log_error.call_args_list]
		self.assertIn(f"Captura {doc.name}: error de IA", titulos)

	def test_error_definitivo_no_expone_la_key(self):
		error = error_http(401)
		error.message = f"invalid x-api-key {API_KEY}"
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		cliente = self.procesar(doc, error)
		self.assertEqual(len(cliente.llamadas), 1)
		self.assertEqual(doc.estado, "Error")
		self.assertIn("HTTP 401", doc.error_log)
		self.assertNotIn(API_KEY, doc.error_log)
		self.assertNotIn(API_KEY, doc.mensaje_capturador)

	def vencer_reintento(self, doc):
		"""Simula que pasó la espera y corre la tarea programada que vuelve a encolar."""
		doc.db_set("reintentar_ia_en", add_to_date(now_datetime(), seconds=-1), update_modified=False)
		with patch.object(extractor, "encolar_procesamiento") as encolar:
			extractor.reencolar_reintentos()
		# assert_any_call: el sitio puede tener otras capturas con reintento vencido (pruebas manuales).
		encolar.assert_any_call(doc.name)
		doc.reload()
		self.assertIsNone(doc.reintentar_ia_en)

	def test_transitorio_programa_reintento_y_luego_ok(self):
		"""CR-M3: un intento por ejecución; el reintento se programa (30 s y 120 s) y no se duerme."""
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		inicio = now_datetime()
		cliente = self.procesar(doc, error_http(529))
		self.assertEqual(len(cliente.llamadas), 1)
		self.assertEqual(doc.estado, "Procesando")
		self.assertEqual((doc.reintentos_ia, doc.intentos_ia), (1, 1))
		self.assertAlmostEqual((doc.reintentar_ia_en - inicio).total_seconds(), 30, delta=5)
		self.assertIn("HTTP 529", doc.error_log)

		# Antes de la hora, una ejecución (por ejemplo un job repetido) no llama a Claude.
		self.assertEqual(self.procesar(doc, mensaje(respuesta_ia())).llamadas, [])

		self.vencer_reintento(doc)
		inicio = now_datetime()
		self.procesar(doc, mensaje(texto="{roto"))
		self.assertEqual((doc.estado, doc.reintentos_ia, doc.intentos_ia), ("Procesando", 2, 2))
		self.assertAlmostEqual((doc.reintentar_ia_en - inicio).total_seconds(), 120, delta=5)

		self.vencer_reintento(doc)
		self.procesar(doc, mensaje(respuesta_ia()))
		self.assertEqual(doc.estado, "Por revisar")
		self.assertEqual((doc.intentos_ia, doc.reintentos_ia, doc.reintentar_ia_en), (3, 0, None))
		self.assertIsNone(doc.error_log)

	def test_transitorio_agotado_pasa_a_error(self):
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		self.procesar(doc, error_http(429))
		self.vencer_reintento(doc)
		self.procesar(doc, error_http(429))
		self.vencer_reintento(doc)
		self.procesar(doc, error_http(429))
		self.assertEqual(doc.estado, "Error")
		self.assertEqual(doc.intentos_ia, 3)
		self.assertIn("tras 2 reintentos", doc.error_log)

	def test_no_mantiene_transaccion_abierta_durante_la_llamada(self):
		"""CR-M3: antes de llamar a Claude se cierra la transacción de lectura."""
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		eventos = []
		cliente = ClienteFalso(mensaje(respuesta_ia()))
		original = cliente._create

		def crear(**params):
			eventos.append("claude")
			return original(**params)

		cliente.messages.create = cliente.beta.messages.create = crear
		with (
			patch.object(extractor, "crear_cliente", return_value=cliente),
			patch.object(extractor, "encolar_procesamiento"),
			patch.object(extractor, "_confirmar", side_effect=lambda: eventos.append("commit")),
		):
			doc.enviar()
			extractor.procesar_captura(doc.name)
		self.assertEqual(eventos, ["commit", "claude", "commit"])

	def test_reprocesar_permisos(self):
		from captura_productos.tests.utils import USUARIO_CAPTURADOR, insertar_de

		doc = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="pepsi de litro"))
		frappe.set_user("Administrator")
		self.procesar(doc, mensaje(respuesta_ia()))
		self.assertEqual(doc.estado, "Por revisar")

		frappe.set_user(USUARIO_CAPTURADOR)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Captura de Producto", doc.name).reprocesar_ia()
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Captura de Producto", doc.name).reintentar()

		frappe.set_user("Administrator")
		doc.db_set("estado", "Error")
		frappe.set_user(USUARIO_CAPTURADOR)
		# CR-M1: el método whitelisted es solo de supervisores (el capturador usa "Reintentar", que
		# pasa por api.reintentar_captura y su límite por minuto).
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Captura de Producto", doc.name).reprocesar_ia()
		with patch.object(extractor, "encolar_procesamiento") as encolar:
			frappe.get_doc("Captura de Producto", doc.name).reintentar()
		encolar.assert_called_once_with(doc.name)
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Procesando")


class TestAtascadas(CapturaTestCase):
	"""CR-M2: capturas que quedaron en Procesando sin job."""

	def setUp(self):
		super().setUp()
		self.enterContext(patch("frappe.log_error"))

	def en_procesando(self, minutos_sin_cambios: int):
		doc = nueva_captura(transcripcion="pepsi de litro").insert()
		with patch.object(extractor, "encolar_procesamiento"):
			doc.enviar()
		frappe.db.set_value(
			"Captura de Producto",
			doc.name,
			"modified",
			add_to_date(now_datetime(), minutes=-minutos_sin_cambios),
			update_modified=False,
		)
		return doc

	def test_rescata_las_viejas_sin_job(self):
		vieja = self.en_procesando(25)
		reciente = self.en_procesando(5)
		con_job = self.en_procesando(25)
		con_reintento = self.en_procesando(25)
		con_reintento.db_set("reintentar_ia_en", add_to_date(now_datetime(), seconds=60), update_modified=False)
		with patch.object(extractor, "job_en_cola", side_effect=lambda nombre: nombre == con_job.name):
			extractor.rescatar_atascadas()
		estado = lambda d: frappe.db.get_value("Captura de Producto", d.name, "estado")  # noqa: E731
		self.assertEqual(estado(vieja), "Error")
		self.assertIn("sin un trabajo en cola", frappe.db.get_value("Captura de Producto", vieja.name, "error_log"))
		self.assertEqual(estado(reciente), "Procesando")
		self.assertEqual(estado(con_job), "Procesando")
		self.assertEqual(estado(con_reintento), "Procesando")

	def test_supervisor_reprocesa_desde_procesando_solo_sin_job(self):
		doc = self.en_procesando(25)
		doc.reload()
		with patch.object(extractor, "job_en_cola", return_value=True), self.assertRaises(frappe.ValidationError):
			doc.reprocesar_ia()
		with (
			patch.object(extractor, "job_en_cola", return_value=False),
			patch.object(extractor, "encolar_procesamiento") as encolar,
		):
			self.assertEqual(doc.reprocesar_ia(), "Procesando")
		encolar.assert_called_once_with(doc.name)

	def test_scheduler_registrado(self):
		eventos = frappe.get_hooks("scheduler_events").get("cron", {})
		tareas = [t for lista in eventos.values() for t in lista]
		self.assertIn("captura_productos.ai.extractor.reencolar_reintentos", tareas)
		self.assertIn("captura_productos.ai.extractor.rescatar_atascadas", tareas)


def _jpeg() -> bytes:
	import io

	from PIL import Image

	buf = io.BytesIO()
	Image.frombytes("RGB", (16, 16), os.urandom(16 * 16 * 3)).save(buf, "JPEG")
	return buf.getvalue()


class TestLecturaDeFotos(CapturaTestCase):
	"""CR-A1: el job solo lee archivos adjuntos a la misma captura, nunca por ruta."""

	def adjuntar(self, captura: str, privado: int):
		archivo = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"foto-{frappe.generate_hash(length=8)}.jpg",
				"is_private": privado,
				"content": _jpeg(),
				"attached_to_doctype": "Captura de Producto",
				"attached_to_name": captura,
			}
		).insert(ignore_permissions=True)
		url = archivo.file_url
		ruta = frappe.get_site_path(url.lstrip("/")) if privado else frappe.get_site_path("public", url.lstrip("/"))
		self.addCleanup(lambda: os.path.exists(ruta) and os.remove(ruta))  # el rollback no borra el disco
		return url

	def test_no_lee_archivos_ajenos_privados_ni_por_ruta(self):
		doc = nueva_captura(transcripcion="pepsi de litro", fotos=[]).insert()
		otra = nueva_captura(transcripcion="otra", fotos=[]).insert()
		publico = self.adjuntar(otra.name, privado=0)
		privado = self.adjuntar(otra.name, privado=1)
		self.assertTrue(extractor.leer_archivo(publico, otra.name))
		self.assertIsNone(extractor.leer_archivo(publico, doc.name))  # de otra captura
		self.assertIsNone(extractor.leer_archivo(privado, otra.name))  # privado: nunca va a Claude
		self.assertIsNone(extractor.leer_archivo("/private/files/../site_config.json", doc.name))
		self.assertIsNone(extractor.leer_archivo("/files/../../site_config.json", doc.name))

	def test_reduce_fotos_grandes(self):
		import io

		from PIL import Image

		buf = io.BytesIO()
		Image.new("RGB", (3000, 1500), (10, 200, 10)).save(buf, "PNG")
		with patch.object(extractor, "leer_archivo", return_value=buf.getvalue()):
			media_type, datos = extractor.imagen_base64("/files/x.png", "CAP-X")
		self.assertEqual(media_type, "image/jpeg")
		import base64

		with Image.open(io.BytesIO(base64.b64decode(datos))) as reducida:
			self.assertEqual(max(reducida.size), extractor.LADO_FOTO_IA)
