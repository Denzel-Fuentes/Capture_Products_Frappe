"""Tests de QA: criterios de aceptación sin cobertura en la suite de los programadores.

Cubre permisos (Guest, sin rol, capturador A/B, capturador vs. supervisor), máquina de estados
(transiciones inválidas), idempotencia, validación de fotos, códigos repetidos entre producto y
variantes, variantes multi-atributo, exportación excluyendo capturas no aprobadas y rollback de
Crear Item.

Los tests que fallan documentan bugs o desvíos de la spec (ver docs/QA.md); no se debe bajar la
aserción para que pasen.
"""

from __future__ import annotations

import io
import json
import os
from datetime import timedelta
from unittest.mock import patch

import frappe
import frappe.client
from frappe.utils import now_datetime
from PIL import Image

from captura_productos import api, validation
from captura_productos.ai import extractor
from captura_productos.export import erpnext_item, sheets
from captura_productos.tests.test_api import UUID_1, UUID_2, gtin, imagen
from captura_productos.tests.test_sheets import (
	CREDENCIALES,
	EAN_SIMPLE,
	SpreadsheetFalso,
	aprobar,
	captura_simple,
	captura_variantes,
)
from captura_productos.tests.utils import (
	FOTO,
	USUARIO_CAPTURADOR,
	USUARIO_CAPTURADOR_2,
	USUARIO_SUPERVISOR,
	CapturaTestCase,
	configurar,
	crear_usuario,
	insertar_de,
	insertar_item,
	nueva_captura,
	pasar_a_por_revisar,
)

USUARIO_SIN_ROL = "sin.rol.qa@example.com"
EAN_1 = gtin("779100000001")
EAN_2 = gtin("779100000002")
EAN_3 = gtin("779100000003")
UPC_A = gtin("03600029145")  # 12 dígitos

ESTADOS = ("Borrador", "Procesando", "Por revisar", "Aprobado", "Exportado", "Creado", "Error")


def _limpiar_limites():
	for user in (USUARIO_CAPTURADOR, USUARIO_CAPTURADOR_2, USUARIO_SUPERVISOR, USUARIO_SIN_ROL):
		for accion in api.LIMITES_POR_MINUTO:
			frappe.cache.delete(api.clave_limite(accion, user))


class QATestCase(CapturaTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		crear_usuario(USUARIO_SIN_ROL, [])

	def setUp(self):
		super().setUp()
		_limpiar_limites()
		self.archivos: list[str] = []
		self.encolar = self.enterContext(patch.object(extractor, "encolar_procesamiento"))
		self.enterContext(patch("frappe.log_error"))  # Error Log no se deshace con el rollback

	def tearDown(self):
		for url in self.archivos:
			ruta = frappe.get_site_path("public", url.lstrip("/"))
			if os.path.exists(ruta):
				os.remove(ruta)
		super().tearDown()

	def subir(self, captura: str, contenido: bytes | None = None) -> dict:
		with patch.object(api, "_contenido_del_request", return_value=contenido or imagen()):
			r = api.subir_foto(captura)
		self.archivos.append(r["file_url"])
		return r

	def en_estado(self, estado: str, **campos):
		"""Captura (dueño: capturador 1) puesta directamente en `estado` con db_set."""
		datos = {
			"transcripcion": "pepsi de litro",
			"marca": "Pepsi",
			"medida_valor": 1,
			"medida_unidad": "L",
		}
		datos.update(campos)
		doc = insertar_de(USUARIO_CAPTURADOR, nueva_captura(**datos))
		frappe.set_user("Administrator")
		if estado != "Borrador":
			doc.db_set("estado", estado)
		doc.reload()
		return doc


# ---------------------------------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------------------------------


class TestQAPermisos(QATestCase):
	def llamadas_api(self, captura: str) -> dict:
		return {
			"verificar_codigo": lambda: api.verificar_codigo(EAN_1),
			"mis_capturas": lambda: api.mis_capturas(),
			"crear_captura": lambda: api.crear_captura(UUID_2),
			"subir_foto": lambda: self.subir(captura),
			"guardar_paso": lambda: api.guardar_paso(captura, {"transcripcion": "x"}),
			"enviar_captura": lambda: api.enviar_captura(captura, {"transcripcion": "xyz"}),
			"reintentar_captura": lambda: api.reintentar_captura(captura),
		}

	def test_ningun_endpoint_admite_guest(self):
		"""Spec 1: todos los endpoints exigen login. Ninguno está en allow_guest."""
		whitelisted = [
			f"{fn.__module__}.{fn.__name__}"
			for fn in frappe.whitelisted
			if fn.__module__.startswith("captura_productos")
		]
		self.assertIn("captura_productos.api.enviar_captura", whitelisted)
		invitados = [f"{fn.__module__}.{fn.__name__}" for fn in frappe.guest_methods]
		self.assertEqual([m for m in invitados if m.startswith("captura_productos")], [])

	def test_guest_bloqueado_en_todos_los_endpoints(self):
		captura = self.en_estado("Borrador").name
		frappe.set_user("Guest")
		for nombre, llamada in self.llamadas_api(captura).items():
			with self.subTest(endpoint=nombre), self.assertRaises(frappe.AuthenticationError):
				llamada()
		with self.assertRaises(frappe.PermissionError):
			sheets.exportar_aprobados(json.dumps([captura]))
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Captura de Producto", captura, "estado"), "Borrador")

	def test_usuario_sin_rol_bloqueado_en_todos_los_endpoints(self):
		captura = self.en_estado("Borrador").name
		frappe.set_user(USUARIO_SIN_ROL)
		for nombre, llamada in self.llamadas_api(captura).items():
			with self.subTest(endpoint=nombre), self.assertRaises(frappe.PermissionError):
				llamada()
		with self.assertRaises(frappe.PermissionError):
			sheets.exportar_aprobados([captura])

	def test_capturador_a_no_ve_ni_edita_capturas_de_b(self):
		ajena = insertar_de(USUARIO_CAPTURADOR_2, nueva_captura(transcripcion="captura de B"))
		propia = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="captura de A"))

		visibles = frappe.get_list("Captura de Producto", pluck="name")
		self.assertIn(propia.name, visibles)
		self.assertNotIn(ajena.name, visibles)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("Captura de Producto", ajena.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Captura de Producto", ajena.name, "transcripcion", "hackeado")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Captura de Producto", ajena.name).delete()
		with self.assertRaises(frappe.PermissionError):
			api.guardar_paso(ajena.name, {"transcripcion": "hackeado"})
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Captura de Producto", ajena.name, "transcripcion"), "captura de B"
		)

	def test_capturador_no_borra_ni_sus_propias_capturas(self):
		propia = insertar_de(USUARIO_CAPTURADOR, nueva_captura())
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Captura de Producto", propia.name)

	def test_capturador_no_cambia_estado_ni_edita_tras_enviar(self):
		doc = self.en_estado("Borrador")
		frappe.set_user(USUARIO_CAPTURADOR)
		# Sin permiso de escritura (CR-A1): PermissionError, que también es un rechazo.
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			frappe.client.set_value("Captura de Producto", doc.name, "estado", "Aprobado")
		frappe.set_user("Administrator")
		doc.db_set("estado", "Procesando")
		frappe.set_user(USUARIO_CAPTURADOR)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Captura de Producto", doc.name, "item_code", "BEB-HACK")

	def test_capturador_no_aprueba_exporta_ni_crea_item(self):
		doc = self.en_estado("Por revisar", item_name="Pepsi 1 L", item_code="BEB-PEPSI-1L")
		frappe.set_user(USUARIO_CAPTURADOR)
		doc = frappe.get_doc("Captura de Producto", doc.name)
		acciones = {
			"aprobar": doc.aprobar,
			"recalcular_nombre": doc.recalcular_nombre,
			"reprocesar_ia (Por revisar)": doc.reprocesar_ia,
		}
		for nombre, accion in acciones.items():
			with self.subTest(accion=nombre), self.assertRaises(frappe.PermissionError):
				accion()

		frappe.set_user("Administrator")
		doc.db_set("estado", "Aprobado")
		frappe.set_user(USUARIO_CAPTURADOR)
		doc = frappe.get_doc("Captura de Producto", doc.name)
		acciones = {
			"exportar": doc.exportar,
			"crear_item": doc.crear_item,
			"volver_a_revision": doc.volver_a_revision,
			"exportar_aprobados": lambda: sheets.exportar_aprobados([doc.name]),
		}
		for nombre, accion in acciones.items():
			with self.subTest(accion=nombre), self.assertRaises(frappe.PermissionError):
				accion()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Aprobado")

	def test_capturador_no_crea_item_ni_lee_configuracion(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		self.assertFalse(frappe.has_permission("Item", "create"))
		self.assertFalse(frappe.has_permission("Item", "read"))
		with self.assertRaises(frappe.PermissionError):
			frappe.client.insert({"doctype": "Item", "item_code": "QA-HACK", "item_group": "Galletas"})
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("Configuracion Captura", "Configuracion Captura")
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get_value("Configuracion Captura", "modelo_ia", "Configuracion Captura")
		conf = frappe.get_doc("Configuracion Captura")
		with self.assertRaises(frappe.PermissionError):
			conf.probar_conexion_ia()
		with self.assertRaises(frappe.PermissionError):
			conf.probar_google_sheet()

	def test_supervisor_no_lee_configuracion_con_secretos(self):
		frappe.set_user(USUARIO_SUPERVISOR)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("Configuracion Captura", "Configuracion Captura")


# ---------------------------------------------------------------------------------------------------
# Máquina de estados
# ---------------------------------------------------------------------------------------------------


class TestQAEstados(QATestCase):
	# acción → estados desde los que está permitida (spec sección 3)
	PERMITIDOS = {
		"enviar": {"Borrador"},
		"marcar_procesada": {"Procesando"},
		"marcar_error": {"Procesando"},
		"reprocesar_ia": {"Por revisar", "Error", "Procesando"},
		"recalcular_nombre": {"Por revisar", "Aprobado"},
		"aprobar": {"Por revisar"},
		"volver_a_revision": {"Aprobado"},
		"exportar": {"Aprobado"},
		"marcar_para_reexportar": {"Exportado"},
		"crear_item": {"Aprobado", "Exportado"},
	}

	def ejecutar(self, doc, accion: str):
		if accion == "marcar_error":
			return doc.marcar_error("detalle")
		return getattr(doc, accion)()

	def test_acciones_rechazadas_fuera_de_su_estado(self):
		for estado in ESTADOS:
			for accion, permitidos in self.PERMITIDOS.items():
				if estado in permitidos:
					continue
				with self.subTest(estado=estado, accion=accion):
					doc = self.en_estado(estado)
					with self.assertRaises(frappe.ValidationError):
						self.ejecutar(doc, accion)
					self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), estado)
		self.encolar.assert_not_called()

	def test_transiciones_directas_invalidas(self):
		invalidas = [
			("Borrador", "Aprobado"),
			("Borrador", "Creado"),
			("Procesando", "Aprobado"),
			("Error", "Por revisar"),
			("Error", "Aprobado"),
			("Por revisar", "Exportado"),
			("Por revisar", "Creado"),
			("Exportado", "Por revisar"),
			("Creado", "Borrador"),
			("Creado", "Aprobado"),
			("Creado", "Por revisar"),
		]
		for desde, hacia in invalidas:
			with self.subTest(desde=desde, hacia=hacia):
				doc = self.en_estado(desde)
				with self.assertRaises(frappe.ValidationError):
					doc._cambiar_estado(hacia)
				self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), desde)

	def test_estado_no_se_cambia_guardando_desde_ningun_estado(self):
		for estado in ESTADOS:
			with self.subTest(estado=estado):
				doc = self.en_estado(estado)
				doc.estado = "Aprobado" if estado != "Aprobado" else "Creado"
				with self.assertRaises(frappe.ValidationError):
					doc.save()

	def test_captura_creada_no_se_borra_ni_por_supervisor(self):
		doc = self.en_estado("Creado")
		frappe.set_user(USUARIO_SUPERVISOR)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Captura de Producto", doc.name)
		self.assertTrue(frappe.db.exists("Captura de Producto", doc.name))

	def test_supervisor_borra_capturas_no_creadas(self):
		doc = self.en_estado("Error")
		frappe.set_user(USUARIO_SUPERVISOR)
		frappe.delete_doc("Captura de Producto", doc.name)
		self.assertFalse(frappe.db.exists("Captura de Producto", doc.name))

	def test_reprocesar_sube_intentos(self):
		"""T5: intentos_ia += 1 (lo hace el job); reprocesar no lo resetea."""
		doc = self.en_estado("Error")
		doc.db_set("intentos_ia", 2)
		doc.reload()
		doc.reprocesar_ia()
		self.assertEqual(doc.estado, "Procesando")
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "intentos_ia"), 2)
		self.encolar.assert_called_once_with(doc.name)


# ---------------------------------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------------------------------


class TestQAIdempotencia(QATestCase):
	def test_crear_captura_repetida_no_duplica(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombres = {api.crear_captura(UUID_1)["name"] for _ in range(5)}
		self.assertEqual(len(nombres), 1)
		self.assertEqual(frappe.db.count("Captura de Producto", {"client_uuid": UUID_1}), 1)
		# Con espacios alrededor es el mismo uuid.
		self.assertEqual(api.crear_captura(f"  {UUID_1} ")["name"], nombres.pop())

	def test_crear_captura_sin_uuid_crea_cada_vez(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		self.assertNotEqual(api.crear_captura()["name"], api.crear_captura()["name"])

	def test_subir_foto_repetida_varias_veces(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		contenido = imagen()
		urls = {self.subir(nombre, contenido)["file_url"] for _ in range(4)}
		self.assertEqual(len(urls), 1)
		self.assertEqual(
			frappe.db.count("File", {"attached_to_doctype": "Captura de Producto", "attached_to_name": nombre}),
			1,
		)

	def test_misma_foto_en_dos_capturas(self):
		contenido = imagen()
		frappe.set_user(USUARIO_CAPTURADOR)
		a = api.crear_captura(UUID_1)["name"]
		b = api.crear_captura(UUID_2)["name"]
		url_a = self.subir(a, contenido)["file_url"]
		r_b = self.subir(b, contenido)
		self.assertFalse(r_b["repetida"])
		for nombre, url in ((a, url_a), (b, r_b["file_url"])):
			api.enviar_captura(nombre, {"transcripcion": "galletas", "fotos": [{"file_url": url}]})
			self.assertEqual(frappe.db.get_value("Captura de Producto", nombre, "estado"), "Procesando")

	def test_enviar_repetido_no_reencola(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		foto = self.subir(nombre)["file_url"]
		datos = {"transcripcion": "agua vital", "fotos": [{"file_url": foto}]}
		respuestas = [api.enviar_captura(nombre, datos) for _ in range(3)]
		self.assertEqual([r["ya_enviada"] for r in respuestas], [False, True, True])
		self.encolar.assert_called_once_with(nombre)

	def test_enviar_repetido_tras_error_no_reprocesa(self):
		"""Un reintento de envío tardío no debe sacar la captura de Error (eso es Reintentar)."""
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		foto = self.subir(nombre)["file_url"]
		api.enviar_captura(nombre, {"transcripcion": "agua vital", "fotos": [{"file_url": foto}]})
		frappe.set_user("Administrator")
		frappe.get_doc("Captura de Producto", nombre).marcar_error("x")
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.enviar_captura(nombre, {"transcripcion": "agua vital", "fotos": [{"file_url": foto}]})
		self.assertEqual((r["estado"], r["ya_enviada"]), ("Error", True))
		self.encolar.assert_called_once()

	def test_limite_de_archivos_por_captura(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		with patch.object(api, "MAX_ARCHIVOS_POR_CAPTURA", 2):
			self.subir(nombre)
			self.subir(nombre)
			with self.assertRaises(frappe.ValidationError):
				self.subir(nombre)


# ---------------------------------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------------------------------


def _jpeg_de_tamano(total: int) -> bytes:
	"""JPEG válido rellenado con bytes después del marcador EOI hasta `total` bytes."""
	contenido = imagen("JPEG", 16)
	return contenido + b"\0" * (total - len(contenido))


def _imagen_de(formato: str, ancho: int = 16, alto: int = 16) -> bytes:
	buf = io.BytesIO()
	Image.new("RGB", (ancho, alto), (200, 10, 10)).save(buf, formato)
	return buf.getvalue()


class TestQAFotos(QATestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user(USUARIO_CAPTURADOR)
		self.captura = api.crear_captura(UUID_1)["name"]

	def archivos_adjuntos(self) -> int:
		return frappe.db.count(
			"File", {"attached_to_doctype": "Captura de Producto", "attached_to_name": self.captura}
		)

	def test_formatos_no_permitidos(self):
		casos = {
			"BMP": _imagen_de("BMP"),
			"TIFF": _imagen_de("TIFF"),
			"vacío": b"",
			"solo cabecera JPEG": b"\xff\xd8\xff\xe0",
			"PDF": b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\n",
			"SVG": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
		}
		for nombre, contenido in casos.items():
			with self.subTest(formato=nombre), self.assertRaises(frappe.ValidationError):
				with patch.object(api, "_contenido_del_request", return_value=contenido):
					api.subir_foto(self.captura)
		self.assertEqual(self.archivos_adjuntos(), 0)

	def test_png_y_jpeg_permitidos(self):
		self.assertTrue(self.subir(self.captura, _imagen_de("PNG"))["file_url"].endswith(".png"))
		self.assertTrue(self.subir(self.captura, _imagen_de("JPEG"))["file_url"].endswith(".jpg"))
		self.assertEqual(self.archivos_adjuntos(), 2)

	def test_limite_de_2_mb(self):
		exacto = _jpeg_de_tamano(api.MAX_BYTES_FOTO)
		self.assertEqual(len(exacto), 2 * 1024 * 1024)
		self.subir(self.captura, exacto)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.subir(self.captura, _jpeg_de_tamano(api.MAX_BYTES_FOTO + 1))
		self.assertIn("2 MB", str(ctx.exception))
		self.assertEqual(self.archivos_adjuntos(), 1)

	def test_dimensiones_excesivas(self):
		with self.assertRaises(frappe.ValidationError):
			self.subir(self.captura, _imagen_de("JPEG", api.MAX_LADO_FOTO + 1, 2))
		self.subir(self.captura, _imagen_de("JPEG", api.MAX_LADO_FOTO, 2))

	def test_mas_de_6_fotos_en_el_envio(self):
		fotos = [{"file_url": self.subir(self.captura)["file_url"]} for _ in range(api.MAX_FOTOS + 1)]
		with self.assertRaises(frappe.ValidationError):
			api.enviar_captura(self.captura, {"transcripcion": "galletas", "fotos": fotos})
		api.enviar_captura(self.captura, {"transcripcion": "galletas", "fotos": fotos[: api.MAX_FOTOS]})
		self.assertEqual(len(frappe.get_doc("Captura de Producto", self.captura).fotos), api.MAX_FOTOS)

	def test_foto_publica_y_principal_unica(self):
		f1 = self.subir(self.captura)["file_url"]
		f2 = self.subir(self.captura)["file_url"]
		api.guardar_paso(
			self.captura,
			{
				"transcripcion": "galletas",
				"fotos": [{"file_url": f1, "es_principal": 1}, {"file_url": f2, "es_principal": 1}],
			},
		)
		doc = frappe.get_doc("Captura de Producto", self.captura)
		self.assertEqual([f.es_principal for f in doc.fotos], [1, 0])
		self.assertEqual(doc.imagen_principal, f1)
		self.assertFalse(f1.startswith("/private/"))


# ---------------------------------------------------------------------------------------------------
# Códigos de barras repetidos entre producto y variantes
# ---------------------------------------------------------------------------------------------------


class TestQACodigos(QATestCase):
	def variantes(self, **campos):
		datos = {
			"marca": "Frut-All",
			"medida_valor": 1,
			"medida_unidad": "L",
			"tiene_variantes": 1,
			"codigos_barras": [],
			"atributos": [{"atributo": "Sabor"}],
			"variantes": [
				{"valor_1": "Durazno", "barcode": EAN_1},
				{"valor_1": "Manzana", "barcode": EAN_2},
			],
		}
		datos.update(campos)
		return nueva_captura(**datos)

	def bloqueantes(self, doc):
		return validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=True))

	def test_codigo_del_producto_repetido_en_una_variante(self):
		doc = self.variantes(codigos_barras=[{"barcode": EAN_2}])
		bloqueantes = self.bloqueantes(doc)
		self.assertTrue(any("van en cada variante" in b for b in bloqueantes), bloqueantes)
		self.assertTrue(any(f"{EAN_2} está repetido" in b for b in bloqueantes), bloqueantes)

	def test_upc_a_y_su_ean13_en_dos_variantes(self):
		doc = self.variantes(
			variantes=[
				{"valor_1": "Durazno", "barcode": UPC_A},
				{"valor_1": "Manzana", "barcode": "0" + UPC_A},
			]
		)
		self.assertTrue(any("repetido en esta captura" in b for b in self.bloqueantes(doc)))

	def test_envio_con_dos_codigos_y_variantes_deja_el_segundo_en_el_padre(self):
		"""ui-spec 9.1: el 1.er código va a la variante 1; los demás quedan en el padre y bloquean."""
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		foto = self.subir(nombre)["file_url"]
		api.enviar_captura(
			nombre,
			{
				"transcripcion": "jugo frut all un litro",
				"fotos": [{"file_url": foto}],
				"codigos": [{"barcode": EAN_1}, {"barcode": EAN_2}],
				"tiene_variantes": 1,
				"variantes": [{"transcripcion": "sabor manzana", "barcode": EAN_2}],
			},
		)
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Captura de Producto", nombre)
		self.assertEqual([c.barcode for c in doc.codigos_barras], [EAN_2])
		self.assertEqual([v.barcode for v in doc.variantes], [EAN_1, EAN_2])
		self.assertIn("repetido en esta captura", doc.advertencias_ia or "")

	def test_maximo_20_variantes_en_el_envio(self):
		"""HU-02: de 1 a 20 variantes (la 1 es el producto de los pasos 1 a 3)."""
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		foto = self.subir(nombre)["file_url"]
		datos = {"transcripcion": "jugo frut all", "fotos": [{"file_url": foto}], "tiene_variantes": 1}
		with self.assertRaises(frappe.ValidationError):
			api.guardar_paso(nombre, {**datos, "variantes": [{"transcripcion": f"sabor {i}"} for i in range(20)]})
		api.guardar_paso(nombre, {**datos, "variantes": [{"transcripcion": f"sabor {i}"} for i in range(19)]})
		self.assertEqual(len(frappe.get_doc("Captura de Producto", nombre).variantes), 20)

	def test_verificar_codigo_encuentra_codigo_de_variante_de_otra_captura(self):
		otra = insertar_de(USUARIO_CAPTURADOR_2, self.variantes())
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.verificar_codigo(EAN_2)
		self.assertTrue(r["existe"])
		self.assertFalse(r["bloquea"])
		self.assertEqual((r["origen"], r["captura"]), ("captura", otra.name))
		self.assertEqual(r["mensaje"], f"Ya fue capturado en {otra.name}")

	def test_codigo_de_variante_ya_en_item_bloquea_aprobar(self):
		insertar_item("QA-EXISTE", "Producto QA", barcodes=(EAN_2,))
		doc = self.variantes().insert()
		pasar_a_por_revisar(doc)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.aprobar()
		self.assertIn("Producto QA (QA-EXISTE)", str(ctx.exception))
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Por revisar")

	def test_verificar_codigo_upc_contra_ean13_en_item(self):
		insertar_item("QA-UPC", "Producto UPC", barcodes=("0" + UPC_A,))
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.verificar_codigo(UPC_A)
		self.assertTrue(r["bloquea"])
		self.assertEqual(r["item_code"], "QA-UPC")


# ---------------------------------------------------------------------------------------------------
# Variantes multi-atributo
# ---------------------------------------------------------------------------------------------------


class TestQAMultiAtributo(QATestCase):
	def captura(self, valores_v2=("Maracuya", "3 litros", "60")):
		return nueva_captura(
			transcripcion="jugo frut all",
			marca="Frut-All",
			tiene_variantes=1,
			atributos=[
				{"atributo": "Sabor", "orden": 1},
				{"atributo": "Tamaño", "orden": 2},
				{"atributo": "Paquete", "orden": 3},
			],
			variantes=[
				{"valor_1": "durazno", "valor_2": "dos litros", "valor_3": "12", "barcode": EAN_1},
				{"valor_1": valores_v2[0], "valor_2": valores_v2[1], "valor_3": valores_v2[2], "barcode": EAN_2},
			],
		)

	def test_valor_nuevo_es_aviso_y_numerico_fuera_de_rango_bloquea(self):
		doc = self.captura().insert()
		pasar_a_por_revisar(doc)
		self.assertIn("[Aviso] Valor nuevo: Maracuya en atributo Sabor", doc.advertencias_ia)
		self.assertIn("[Bloquea] Variante 2: Paquete = 60 está fuera del rango 6 a 48", doc.advertencias_ia)
		self.assertEqual(doc.variantes[0].valor_1, "Durazno")
		self.assertEqual(doc.variantes[1].valor_2, "3 L")
		self.assertIn("Durazno", doc.variantes[0].item_name)
		self.assertTrue(doc.variantes[0].item_name.endswith("2 L"), doc.variantes[0].item_name)
		self.assertIn("-2L", doc.variantes[0].item_code)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.aprobar()
		self.assertIn("fuera del rango", str(ctx.exception))
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Por revisar")

	def test_corrigiendo_el_numerico_se_aprueba_con_valor_nuevo(self):
		doc = self.captura().insert()
		pasar_a_por_revisar(doc)
		doc.variantes[1].valor_3 = "18"
		doc.save()
		doc.recalcular_nombre()
		doc.reload()
		doc.aprobar()
		self.assertEqual(doc.estado, "Aprobado")

	def test_numerico_que_no_respeta_incremento_y_no_numerico(self):
		for valor, motivo in (("10", "incremento"), ("doce", "no es un número"), ("0", "fuera del rango")):
			with self.subTest(valor=valor):
				doc = self.captura(("Manzana", "3 L", valor))
				bloqueantes = validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=True))
				self.assertTrue(any(motivo in b and "Paquete" in b for b in bloqueantes), bloqueantes)

	def test_combinacion_repetida_con_varios_atributos(self):
		doc = self.captura(("Durazno", "2 L", "12"))
		bloqueantes = validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=True))
		self.assertTrue(any("repite la combinación" in b for b in bloqueantes), bloqueantes)
		# Mismo sabor y tamaño, otro paquete: es otra combinación.
		doc = self.captura(("Durazno", "2 L", "24"))
		bloqueantes = validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=True))
		self.assertFalse(any("repite la combinación" in b for b in bloqueantes), bloqueantes)

	def test_atributo_inexistente_o_repetido_bloquea(self):
		doc = nueva_captura(
			tiene_variantes=1,
			atributos=[{"atributo": "Sabor"}, {"atributo": "Sabor"}],
			variantes=[{"valor_1": "Durazno", "valor_2": "Durazno"}],
		)
		bloqueantes = validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=True))
		self.assertTrue(any("atributos repetidos" in b for b in bloqueantes), bloqueantes)

	def test_crear_item_multi_atributo_con_numerico(self):
		empresa = frappe.db.get_value("Company", {}, "name")
		configurar(empresa=empresa, almacen_defecto=None, crear_maestros_al_exportar=1)
		doc = self.captura(("Maracuya", "3 L", "24")).insert()
		pasar_a_por_revisar(doc)
		doc.aprobar()
		resultado = doc.crear_item()
		self.assertEqual(len(resultado["creados"]), 3)
		variante = frappe.get_doc("Item", doc.variantes[1].item_code)
		self.assertEqual(
			{a.attribute: str(a.attribute_value) for a in variante.attributes},
			{"Sabor": "Maracuya", "Tamaño": "3 L", "Paquete": "24"},
		)
		self.assertTrue(
			frappe.db.exists("Item Attribute Value", {"parent": "Sabor", "attribute_value": "Maracuya"})
		)

	def test_crear_item_no_crea_valores_si_crear_maestros_esta_apagado(self):
		"""Spec 13 (prevalece): un valor nuevo se crea al exportar/crear Item SOLO si
		crear_maestros_al_exportar = 1."""
		empresa = frappe.db.get_value("Company", {}, "name")
		configurar(empresa=empresa, almacen_defecto=None, crear_maestros_al_exportar=0)
		doc = self.captura(("Maracuya", "3 L", "24")).insert()
		pasar_a_por_revisar(doc)
		doc.aprobar()
		try:
			doc.crear_item()
		except frappe.ValidationError:
			pass
		self.assertFalse(
			frappe.db.exists("Item Attribute Value", {"parent": "Sabor", "attribute_value": "Maracuya"}),
			"Crear Item creó el valor Maracuya aunque crear_maestros_al_exportar = 0",
		)


# ---------------------------------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------------------------------


class TestQAExportacion(QATestCase):
	def setUp(self):
		super().setUp()
		configurar(
			empresa=None,
			almacen_defecto=None,
			spreadsheet_id="abc",
			google_service_account_json=CREDENCIALES,
			url_base_imagenes="https://erp.example",
			crear_maestros_al_exportar=1,
			pestana_sheet="Items",
		)

	def exportar(self, nombres):
		ss = SpreadsheetFalso()
		with patch.object(sheets, "abrir_spreadsheet", return_value=(ss, "bot@x")):
			return sheets.exportar_capturas(nombres), ss

	def test_solo_exporta_aprobadas(self):
		aprobada = aprobar(captura_simple().insert())
		otras = {}
		for estado in ("Borrador", "Procesando", "Por revisar", "Error", "Exportado", "Creado"):
			doc = captura_simple(codigos_barras=[], item_code=f"QA-{estado.replace(' ', '')}").insert()
			doc.db_set("estado", estado)
			otras[estado] = doc.name
		resultado, ss = self.exportar([aprobada.name, *otras.values(), "CAP-NO-EXISTE"])
		self.assertEqual(resultado["exportadas"], [aprobada.name])
		excluidas = {e["captura"]: e["motivo"] for e in resultado["excluidas"]}
		self.assertEqual(set(excluidas), set(otras.values()) | {"CAP-NO-EXISTE"})
		self.assertEqual(excluidas["CAP-NO-EXISTE"], "No existe")
		for estado, nombre in otras.items():
			self.assertEqual(frappe.db.get_value("Captura de Producto", nombre, "estado"), estado)
		codigos = [fila[0] for fila in ss.hoja("-1-Productos").valores[1:] if fila[0]]
		self.assertEqual(codigos, [aprobada.item_code])

	def test_sin_aprobadas_no_toca_el_sheet(self):
		doc = captura_simple().insert()
		with patch.object(sheets, "abrir_spreadsheet") as abrir:
			resultado = sheets.exportar_capturas([doc.name])
		abrir.assert_not_called()
		self.assertEqual(resultado["exportadas"], [])

	def test_aprobada_que_ahora_falla_se_excluye(self):
		doc = aprobar(captura_simple().insert())
		insertar_item("QA-OTRO", "Otro producto", barcodes=(EAN_SIMPLE,))
		resultado, ss = self.exportar([doc.name])
		self.assertEqual(resultado["exportadas"], [])
		self.assertIn("Otro producto (QA-OTRO)", resultado["excluidas"][0]["motivo"])
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Aprobado")
		self.assertEqual(ss.hojas, [])

	def test_dos_capturas_con_el_mismo_item_code_no_se_exportan_juntas(self):
		"""HU-11 (revalidar y excluir) + 5.6 (unicidad en Exportar): dos filas con el mismo Item Code
		hacen fallar el Data Import."""
		a = aprobar(captura_simple().insert())
		b = aprobar(captura_simple(codigos_barras=[{"barcode": EAN_3}]).insert())
		frappe.db.set_value("Captura de Producto", b.name, "item_code", a.item_code)
		resultado, ss = self.exportar([a.name, b.name])
		hojas = [h for h in ss.hojas if h.title.endswith("-1-Productos")]
		codigos = [fila[0] for fila in hojas[0].valores[1:] if fila[0]] if hojas else []
		self.assertEqual(len(codigos), len(set(codigos)), f"Item Code repetido en el Sheet: {codigos}")

	def test_maximo_200_y_duplicados_en_la_seleccion(self):
		with self.assertRaises(frappe.ValidationError):
			sheets.exportar_capturas([f"CAP-{i}" for i in range(sheets.MAX_CAPTURAS + 1)])
		doc = aprobar(captura_simple().insert())
		resultado, _ss = self.exportar([doc.name, doc.name, ""])
		self.assertEqual(resultado["exportadas"], [doc.name])

	def test_sin_credenciales_mensaje_claro(self):
		configurar(google_service_account_json="", spreadsheet_id="abc")
		frappe.db.delete(
			"__Auth", {"doctype": "Configuracion Captura", "fieldname": "google_service_account_json"}
		)
		doc = aprobar(captura_simple().insert())
		with self.assertRaises(frappe.ValidationError) as ctx:
			sheets.exportar_capturas([doc.name])
		self.assertIn("Falta el JSON de la cuenta de servicio", str(ctx.exception))
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertIn("Falta el JSON", doc.error_log)

	def test_credenciales_invalidas_no_filtran_la_clave(self):
		secreto = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCQAxSECRETO"
		credenciales = json.dumps(
			{
				"type": "service_account",
				"client_email": "bot@proyecto.iam.gserviceaccount.com",
				"private_key": f"-----BEGIN PRIVATE KEY-----\n{secreto}\n-----END PRIVATE KEY-----\n",
				"token_uri": "https://oauth2.googleapis.com/token",
			}
		)
		configurar(google_service_account_json=credenciales)
		doc = aprobar(captura_simple().insert())
		with self.assertRaises(frappe.ValidationError) as ctx:
			sheets.exportar_capturas([doc.name])
		self.assertNotIn(secreto, str(ctx.exception))
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertTrue(doc.error_log)
		self.assertNotIn(secreto, doc.error_log)

	def test_fallo_al_escribir_deshace_maestros(self):
		import gspread

		doc = aprobar(captura_variantes().insert())

		class Respuesta:
			status_code = 500
			text = "error"

			def json(self):
				return {"error": {"code": 500, "message": "backend", "status": "INTERNAL"}}

		ss = SpreadsheetFalso()
		with (
			patch.object(sheets, "abrir_spreadsheet", return_value=(ss, "bot@x")),
			patch.object(ss, "add_worksheet", side_effect=gspread.exceptions.APIError(Respuesta())),
			self.assertRaises(frappe.ValidationError) as ctx,
		):
			sheets.exportar_capturas([doc.name])
		self.assertIn("HTTP 500", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Item Attribute Value", {"parent": "Color", "attribute_value": "Rojo"}))
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertIn("APIError 500", doc.error_log)


# ---------------------------------------------------------------------------------------------------
# Crear Item: rollback y efectos
# ---------------------------------------------------------------------------------------------------


class TestQACrearItem(QATestCase):
	def setUp(self):
		super().setUp()
		empresa = frappe.db.get_value("Company", {}, "name")
		configurar(empresa=empresa, almacen_defecto=None, is_stock_item=1, crear_maestros_al_exportar=1)

	def assert_sin_cambios(self, doc, plantilla: str, marca: str | None = None):
		self.assertFalse(frappe.db.exists("Item", plantilla))
		for fila in doc.variantes:
			self.assertFalse(frappe.db.exists("Item", fila.item_code))
		if marca:
			self.assertFalse(frappe.db.exists("Brand", marca))
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Aprobado")

	def test_rollback_si_falla_create_variant_a_mitad(self):
		doc = captura_variantes()
		doc.marca = "Marca QA Nueva"
		doc = aprobar(doc.insert())
		plantilla = doc.item_code
		original = erpnext_item.create_variant
		llamadas = []

		def falla_en_la_segunda(*args, **kwargs):
			llamadas.append(1)
			if len(llamadas) == 2:
				raise RuntimeError("fallo simulado")
			return original(*args, **kwargs)

		with (
			patch.object(erpnext_item, "create_variant", side_effect=falla_en_la_segunda),
			self.assertRaises(frappe.ValidationError) as ctx,
		):
			doc.crear_item()
		self.assertIn("fallo simulado", str(ctx.exception))
		self.assert_sin_cambios(doc, plantilla, marca="Marca QA Nueva")
		self.assertFalse(frappe.db.exists("Item Attribute Value", {"parent": "Color", "attribute_value": "Rojo"}))
		self.assertIn("fallo simulado", frappe.db.get_value("Captura de Producto", doc.name, "error_log"))

	def test_rollback_si_el_codigo_de_la_plantilla_es_un_item_simple(self):
		doc = aprobar(captura_variantes().insert())
		insertar_item(doc.item_code, "Item simple con el mismo código")
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("no es una plantilla", str(ctx.exception))
		for fila in doc.variantes:
			self.assertFalse(frappe.db.exists("Item", fila.item_code))
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Aprobado")

	def test_rollback_si_el_barcode_aparece_en_otro_item_despues_de_aprobar(self):
		doc = aprobar(captura_variantes().insert())
		insertar_item("QA-OTRO", "Otro", barcodes=(doc.variantes[1].barcode,))
		with self.assertRaises(frappe.ValidationError):
			doc.crear_item()
		self.assert_sin_cambios(doc, doc.item_code)
		self.assertTrue(frappe.db.get_value("Captura de Producto", doc.name, "error_log"))

	def test_item_manager_sin_permiso_de_crear_item(self):
		"""T11: 'Crear Item' exige has_permission('Item', 'create')."""
		doc = aprobar(captura_simple().insert())
		frappe.set_user(USUARIO_SUPERVISOR)
		with patch.object(frappe, "has_permission", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("Captura de Producto", doc.name).crear_item()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Aprobado")
		self.assertFalse(frappe.db.exists("Item", doc.item_code))

	def test_captura_creada_no_queda_con_advertencia_bloqueante_de_si_misma(self):
		doc = aprobar(captura_simple().insert())
		doc.crear_item()
		doc.reload()
		self.assertEqual(doc.estado, "Creado")
		self.assertNotIn(
			"[Bloquea]",
			doc.advertencias_ia or "",
			"La captura Creado muestra como bloqueante que su propio Item ya existe",
		)

	def test_crear_item_desde_exportado(self):
		configurar(spreadsheet_id="abc", google_service_account_json=CREDENCIALES, pestana_sheet="Items")
		doc = aprobar(captura_simple().insert())
		with patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")):
			doc.exportar()
		self.assertEqual(doc.estado, "Exportado")
		doc.crear_item()
		self.assertEqual(doc.estado, "Creado")
		self.assertEqual(frappe.db.get_value("Item", doc.item_code, "item_name"), doc.item_name)


# ---------------------------------------------------------------------------------------------------
# Mis capturas
# ---------------------------------------------------------------------------------------------------


class TestQAMisCapturas(QATestCase):
	def test_solo_ultimos_30_dias_y_limite(self):
		reciente = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="reciente"))
		vieja = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="vieja"))
		frappe.set_user("Administrator")
		vieja.db_set("fecha_captura", now_datetime() - timedelta(days=31))
		frappe.set_user(USUARIO_CAPTURADOR)
		nombres = [c["name"] for c in api.mis_capturas()]
		self.assertIn(reciente.name, nombres)
		self.assertNotIn(vieja.name, nombres)
		self.assertEqual(len(api.mis_capturas(limit=1)), 1)
		self.assertEqual(len(api.mis_capturas(limit="no-numero")), 1)  # usa 50 por defecto
		self.assertEqual(len(api.mis_capturas(limit=-5)), 1)

	def test_error_muestra_mensaje_simple_sin_detalle_tecnico(self):
		doc = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="galletas"))
		doc.enviar()
		frappe.set_user("Administrator")
		frappe.get_doc("Captura de Producto", doc.name).marcar_error("Traceback ... sk-ant-xxx HTTP 401")
		frappe.set_user(USUARIO_CAPTURADOR)
		fila = next(c for c in api.mis_capturas() if c["name"] == doc.name)
		self.assertEqual(fila["mensaje_error_simple"], "No se pudo procesar. El supervisor lo revisará.")
		self.assertNotIn("error_log", fila)
		self.assertNotIn("Traceback", json.dumps(fila, default=str))
		self.assertEqual(fila["foto_principal"], FOTO)
