"""Endpoints de /captura (captura_productos.api) y la página www/captura.py."""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import frappe
from PIL import Image

from captura_productos import api
from captura_productos.tests.utils import (
	USUARIO_CAPTURADOR,
	USUARIO_CAPTURADOR_2,
	USUARIO_SUPERVISOR,
	CapturaTestCase,
	crear_usuario,
)
from captura_productos.validation import digito_verificador_gtin

USUARIO_SIN_ROL = "sin.rol.test@example.com"
UUID_1 = "6f1c2a9e-0b7d-4c1e-9a55-1f0e2d3c4b5a"
UUID_2 = "0d9a8b7c-6e5f-4a3b-8c2d-1e0f9a8b7c6d"


def modulo_extractor() -> str:
	"""El controlador importa el extractor de `captura_productos.ai` (antes, `captura_productos`)."""
	try:
		import captura_productos.ai.extractor

		return "captura_productos.ai.extractor"
	except ImportError:
		return "captura_productos.extractor"


def gtin(cuerpo: str) -> str:
	return cuerpo + str(digito_verificador_gtin(cuerpo))


EAN_A = gtin("779000000001")
EAN_B = gtin("779000000002")
EAN_C = gtin("779000000003")


def imagen(formato: str = "JPEG", lado: int = 32) -> bytes:
	"""Imagen de ruido: contenido único, así nunca coincide (por hash) con un archivo real del sitio."""
	img = Image.frombytes("RGB", (lado, lado), os.urandom(lado * lado * 3))
	buf = io.BytesIO()
	img.save(buf, formato)
	return buf.getvalue()


class TestApiCaptura(CapturaTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		crear_usuario(USUARIO_SIN_ROL, [])

	def setUp(self):
		super().setUp()
		self.archivos: list[str] = []
		for user in (USUARIO_CAPTURADOR, USUARIO_CAPTURADOR_2, USUARIO_SUPERVISOR):
			for accion in api.LIMITES_POR_MINUTO:
				frappe.cache.delete(api.clave_limite(accion, user))
		encolar = patch(f"{modulo_extractor()}.encolar_procesamiento")
		self.encolar = encolar.start()
		self.addCleanup(encolar.stop)

	def tearDown(self):
		# Los File se deshacen con el rollback, pero los archivos en disco no.
		for url in self.archivos:
			ruta = frappe.get_site_path("public", url.lstrip("/"))
			if os.path.exists(ruta):
				os.remove(ruta)
		super().tearDown()

	# --- ayudantes -------------------------------------------------------------------------------

	def subir(self, captura: str, contenido: bytes | None = None) -> dict:
		with patch.object(api, "_contenido_del_request", return_value=contenido or imagen()):
			r = api.subir_foto(captura)
		self.archivos.append(r["file_url"])
		return r

	def nueva(self, user: str = USUARIO_CAPTURADOR, client_uuid: str = UUID_1) -> str:
		frappe.set_user(user)
		return api.crear_captura(client_uuid)["name"]

	# --- crear_captura ---------------------------------------------------------------------------

	def test_crear_es_idempotente_por_uuid(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		r1 = api.crear_captura(UUID_1)
		r2 = api.crear_captura(UUID_1)
		self.assertTrue(r1["creada"])
		self.assertFalse(r2["creada"])
		self.assertEqual(r1["name"], r2["name"])
		self.assertEqual(r2["estado"], "Borrador")
		doc = frappe.get_doc("Captura de Producto", r1["name"])
		self.assertEqual(doc.capturado_por, USUARIO_CAPTURADOR)
		self.assertEqual(doc.client_uuid, UUID_1)
		self.assertNotEqual(api.crear_captura(UUID_2)["name"], r1["name"])

	def test_uuid_de_otro_usuario_no_se_revela(self):
		self.nueva(USUARIO_CAPTURADOR)
		frappe.set_user(USUARIO_CAPTURADOR_2)
		with self.assertRaises(frappe.PermissionError):
			api.crear_captura(UUID_1)

	def test_uuid_con_formato_invalido(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		with self.assertRaises(frappe.ValidationError):
			api.crear_captura("no es un uuid")

	def test_exige_sesion_y_rol(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			api.crear_captura(UUID_1)
		frappe.set_user(USUARIO_SIN_ROL)
		with self.assertRaises(frappe.PermissionError):
			api.crear_captura(UUID_1)
		with self.assertRaises(frappe.PermissionError):
			api.mis_capturas()

	# --- subir_foto ------------------------------------------------------------------------------

	def test_subir_foto_publica_adjunta_y_sin_duplicar(self):
		nombre = self.nueva()
		contenido = imagen()
		r1 = self.subir(nombre, contenido)
		r2 = self.subir(nombre, contenido)
		self.assertFalse(r1["repetida"])
		self.assertTrue(r2["repetida"])
		self.assertEqual(r1["file_url"], r2["file_url"])
		self.assertTrue(r1["file_url"].startswith("/files/"))
		archivos = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Captura de Producto", "attached_to_name": nombre},
			fields=["is_private", "file_url"],
		)
		self.assertEqual(len(archivos), 1)
		self.assertEqual(archivos[0].is_private, 0)
		self.assertTrue(os.path.exists(frappe.get_site_path("public", r1["file_url"].lstrip("/"))))
		self.assertTrue(self.subir(nombre, imagen("PNG"))["file_url"].endswith(".png"))

	def test_foto_con_exif_no_se_duplica(self):
		# Con "quitar EXIF" activo Frappe reescribe el JPEG y su content_hash cambia.
		frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", 1)
		img = Image.frombytes("RGB", (32, 32), os.urandom(32 * 32 * 3))
		exif = Image.Exif()
		exif[0x010F] = "Celular de prueba"  # Make
		buf = io.BytesIO()
		img.save(buf, "JPEG", exif=exif)
		nombre = self.nueva()
		r1 = self.subir(nombre, buf.getvalue())
		r2 = self.subir(nombre, buf.getvalue())
		self.assertTrue(r2["repetida"])
		self.assertEqual(r1["file_url"], r2["file_url"])

	def test_subir_foto_rechaza_tipo_y_tamano(self):
		nombre = self.nueva()
		casos = (
			b"no soy una imagen",
			imagen("GIF"),
			b"\xff\xd8\xff" + b"0" * api.MAX_BYTES_FOTO,  # más de 2 MB
		)
		for contenido in casos:
			with self.subTest(largo=len(contenido)), self.assertRaises(frappe.ValidationError):
				self.subir(nombre, contenido)
		self.assertFalse(
			frappe.db.exists(
				"File", {"attached_to_doctype": "Captura de Producto", "attached_to_name": nombre}
			)
		)

	# --- permisos entre capturadores -------------------------------------------------------------

	def test_un_capturador_no_toca_capturas_de_otro(self):
		nombre = self.nueva(USUARIO_CAPTURADOR)
		url = self.subir(nombre)["file_url"]
		frappe.set_user(USUARIO_CAPTURADOR_2)
		with self.assertRaises(frappe.PermissionError):
			self.subir(nombre)
		with self.assertRaises(frappe.PermissionError):
			api.guardar_paso(nombre, {"transcripcion": "otra cosa"})
		with self.assertRaises(frappe.PermissionError):
			api.enviar_captura(nombre, {"transcripcion": "otra cosa", "fotos": [{"file_url": url}]})
		self.assertNotIn(nombre, [c["name"] for c in api.mis_capturas()])

		# El supervisor sí puede (spec sección 1: revisa y corrige).
		frappe.set_user(USUARIO_SUPERVISOR)
		api.guardar_paso(nombre, {"transcripcion": "corregido por el supervisor"})
		self.assertEqual(
			frappe.db.get_value("Captura de Producto", nombre, "transcripcion"), "corregido por el supervisor"
		)

		frappe.set_user(USUARIO_CAPTURADOR)
		self.assertIn(nombre, [c["name"] for c in api.mis_capturas()])

	def test_no_acepta_fotos_de_otra_captura(self):
		ajena = self.nueva(USUARIO_CAPTURADOR_2, UUID_2)
		url_ajena = self.subir(ajena)["file_url"]
		propia = self.nueva(USUARIO_CAPTURADOR, UUID_1)
		with self.assertRaises(frappe.ValidationError):
			api.enviar_captura(propia, {"transcripcion": "coca cola", "fotos": [{"file_url": url_ajena}]})

	# --- enviar_captura --------------------------------------------------------------------------

	def test_enviar_producto_simple(self):
		nombre = self.nueva()
		f1 = self.subir(nombre)["file_url"]
		f2 = self.subir(nombre)["file_url"]
		datos = {
			"transcripcion": "  coca cola sin azúcar   tres litros ",
			"metodo_entrada": "Voz",
			"fotos": [{"file_url": f1, "es_principal": 0}, {"file_url": f2, "es_principal": 1}],
			"codigos": [{"barcode": EAN_A, "origen": "Escaneado"}, {"barcode": EAN_B, "origen": "Manual"}],
			"sin_codigo": 0,
			"tiene_variantes": 0,
		}
		r = api.enviar_captura(nombre, frappe.as_json(datos))
		self.assertEqual(r["estado"], "Procesando")
		self.assertFalse(r["ya_enviada"])
		self.encolar.assert_called_once_with(nombre)

		doc = frappe.get_doc("Captura de Producto", nombre)
		self.assertEqual(doc.transcripcion, "coca cola sin azúcar tres litros")
		self.assertEqual(doc.metodo_entrada, "Voz")
		self.assertEqual([f.foto for f in doc.fotos], [f1, f2])
		self.assertEqual(doc.imagen_principal, f2)
		self.assertEqual(
			[(c.barcode, c.origen, c.barcode_type) for c in doc.codigos_barras],
			[(EAN_A, "Escaneado", "EAN-13"), (EAN_B, "Manual", "EAN-13")],
		)
		self.assertEqual((doc.tiene_variantes, doc.sin_codigo, len(doc.variantes)), (0, 0, 0))

		# Reintento de un envío que sí llegó: no cambia nada ni vuelve a encolar.
		r2 = api.enviar_captura(nombre, frappe.as_json(datos))
		self.assertEqual((r2["estado"], r2["ya_enviada"]), ("Procesando", True))
		self.encolar.assert_called_once()
		self.assertEqual(api.crear_captura(UUID_1)["estado"], "Procesando")

		with self.assertRaises(frappe.PermissionError):
			api.guardar_paso(nombre, {"transcripcion": "cambio tarde"})
		with self.assertRaises(frappe.PermissionError):
			self.subir(nombre)

		fila = next(c for c in api.mis_capturas() if c["name"] == nombre)
		self.assertEqual((fila["estado"], fila["foto_principal"]), ("Procesando", f2))

	def test_enviar_sin_codigo(self):
		nombre = self.nueva()
		foto = self.subir(nombre)["file_url"]
		api.enviar_captura(
			nombre, {"transcripcion": "pan casero", "fotos": [{"file_url": foto}], "sin_codigo": 1}
		)
		doc = frappe.get_doc("Captura de Producto", nombre)
		self.assertEqual((doc.sin_codigo, len(doc.codigos_barras)), (1, 0))
		self.assertEqual(doc.fotos[0].es_principal, 1)

	def test_enviar_con_variantes(self):
		nombre = self.nueva()
		principal = self.subir(nombre)["file_url"]
		otra = self.subir(nombre)["file_url"]
		foto_v2 = self.subir(nombre)["file_url"]
		foto_v3 = self.subir(nombre)["file_url"]
		datos = {
			"transcripcion": "jugo del valle durazno un litro",
			"metodo_entrada": "Texto",
			"fotos": [{"file_url": otra}, {"file_url": principal, "es_principal": 1}],
			"codigos": [{"barcode": EAN_A}],
			"tiene_variantes": 1,
			"variantes": [
				{"transcripcion": "sabor manzana", "foto": foto_v2, "barcode": EAN_B},
				{"transcripcion": "sabor naranja", "foto": foto_v3, "barcode": None},
			],
		}
		self.assertEqual(api.enviar_captura(nombre, datos)["estado"], "Procesando")
		doc = frappe.get_doc("Captura de Producto", nombre)
		self.assertEqual(doc.tiene_variantes, 1)
		self.assertEqual(len(doc.codigos_barras), 0)
		self.assertEqual(
			[(v.transcripcion, v.foto, v.barcode) for v in doc.variantes],
			[
				("jugo del valle durazno un litro", principal, EAN_A),
				("sabor manzana", foto_v2, EAN_B),
				("sabor naranja", foto_v3, None),
			],
		)
		self.assertEqual(doc.variantes[1].barcode_type, "EAN-13")
		fila = next(c for c in api.mis_capturas() if c["name"] == nombre)
		self.assertEqual(fila["n_variantes"], 3)

	def test_enviar_exige_foto_y_transcripcion(self):
		nombre = self.nueva()
		with self.assertRaises(frappe.ValidationError) as ctx:
			api.enviar_captura(nombre, {"transcripcion": "ab"})
		self.assertIn("al menos 1 foto", str(ctx.exception))
		self.assertEqual(frappe.db.get_value("Captura de Producto", nombre, "estado"), "Borrador")
		self.encolar.assert_not_called()

	def test_enviar_rechaza_codigo_mal_leido_y_listas_largas(self):
		nombre = self.nueva()
		foto = self.subir(nombre)["file_url"]
		mal_leido = EAN_A[:-1] + str((int(EAN_A[-1]) + 1) % 10)
		with self.assertRaises(frappe.ValidationError):
			api.enviar_captura(
				nombre,
				{
					"transcripcion": "galletas",
					"fotos": [{"file_url": foto}],
					"codigos": [{"barcode": mal_leido}],
				},
			)
		muchos = [{"barcode": gtin(f"77900000010{i}")} for i in range(api.MAX_CODIGOS + 1)]
		with self.assertRaises(frappe.ValidationError):
			api.guardar_paso(nombre, {"transcripcion": "galletas", "codigos": muchos})

	def test_guardar_paso_no_envia(self):
		nombre = self.nueva()
		foto = self.subir(nombre)["file_url"]
		api.guardar_paso(
			nombre,
			{"transcripcion": "agua vital", "fotos": [{"file_url": foto}], "codigos": [{"barcode": EAN_C}]},
		)
		doc = frappe.get_doc("Captura de Producto", nombre)
		self.assertEqual(
			(doc.estado, doc.transcripcion, doc.codigos_barras[0].barcode), ("Borrador", "agua vital", EAN_C)
		)
		self.assertEqual(api.enviar_captura(nombre)["estado"], "Procesando")

	def test_reintentar_captura_en_error(self):
		nombre = self.nueva()
		foto = self.subir(nombre)["file_url"]
		api.enviar_captura(nombre, {"transcripcion": "galletas mabel", "fotos": [{"file_url": foto}]})
		frappe.set_user("Administrator")
		frappe.get_doc("Captura de Producto", nombre).marcar_error(
			"detalle técnico", "No se entendió el tamaño."
		)

		frappe.set_user(USUARIO_CAPTURADOR)
		fila = next(c for c in api.mis_capturas() if c["name"] == nombre)
		self.assertEqual(
			(fila["estado"], fila["mensaje_error_simple"]), ("Error", "No se entendió el tamaño.")
		)

		frappe.set_user(USUARIO_CAPTURADOR_2)
		with self.assertRaises(frappe.PermissionError):
			api.reintentar_captura(nombre)

		frappe.set_user(USUARIO_CAPTURADOR)
		self.encolar.reset_mock()
		self.assertEqual(api.reintentar_captura(nombre)["estado"], "Procesando")
		self.encolar.assert_called_once_with(nombre)
		with self.assertRaises(frappe.ValidationError):
			api.reintentar_captura(nombre)  # ya no está en Error

	def test_limite_de_envios_por_minuto(self):
		nombre = self.nueva()
		with patch.dict(api.LIMITES_POR_MINUTO, {"enviar": 2}):
			for _ in range(2):
				with self.assertRaises(frappe.ValidationError):
					api.enviar_captura(nombre, {"transcripcion": "sin fotos"})
			with self.assertRaises(frappe.RateLimitExceededError):
				api.enviar_captura(nombre, {"transcripcion": "sin fotos"})


class TestPaginaCaptura(CapturaTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		crear_usuario(USUARIO_SIN_ROL, [])

	def contexto(self):
		from captura_productos.www import captura as pagina

		return pagina.get_context(frappe._dict())

	def test_invitado_va_al_login(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect) as ctx:
			self.contexto()
		self.assertEqual(ctx.exception.http_status_code, 302)
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/captura")

	def test_solo_roles_de_la_app(self):
		frappe.set_user(USUARIO_SIN_ROL)
		with self.assertRaises(frappe.PermissionError):
			self.contexto()
		frappe.set_user(USUARIO_CAPTURADOR)
		ctx = self.contexto()
		boot = frappe.parse_json(ctx.boot_json)
		self.assertEqual(boot["user"], USUARIO_CAPTURADOR)
		self.assertTrue(boot["csrf_token"])
		self.assertIn("zxing-browser.min.js", boot["zxing_url"])
		self.assertEqual(ctx.no_cache, 1)
