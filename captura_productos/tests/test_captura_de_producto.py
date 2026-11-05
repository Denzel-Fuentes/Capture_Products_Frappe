from unittest.mock import patch

import frappe

from captura_productos import api
from captura_productos.tests.utils import (
	FOTO,
	USUARIO_CAPTURADOR,
	USUARIO_CAPTURADOR_2,
	USUARIO_SUPERVISOR,
	CapturaTestCase,
	configurar,
	insertar_de,
	insertar_item,
	nueva_captura,
	pasar_a_por_revisar,
)

EAN13 = "4006381333931"


def captura_dictada(**campos):
	datos = {
		"transcripcion": "pepsi de litro",
		"marca": "pepsi",
		"medida_valor": 1,
		"medida_unidad": "L",
		"codigos_barras": [{"barcode": EAN13, "origen": "Escaneado"}],
	}
	datos.update(campos)
	return nueva_captura(**datos)


class TestEstados(CapturaTestCase):
	def test_nueva_empieza_en_borrador(self):
		doc = captura_dictada(estado="Aprobado").insert()
		self.assertEqual(doc.estado, "Borrador")
		self.assertEqual(doc.capturado_por, "Administrator")
		self.assertEqual(doc.imagen_principal, FOTO)
		self.assertTrue(doc.name.startswith("CAP-"))

	def test_estado_no_se_cambia_a_mano(self):
		doc = captura_dictada().insert()
		doc.estado = "Aprobado"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_flujo_completo_hasta_aprobado(self):
		doc = captura_dictada().insert()
		pasar_a_por_revisar(doc)
		self.assertEqual(doc.estado, "Por revisar")
		self.assertEqual((doc.item_name, doc.item_code), ("Pepsi 1 L", "BEB-PEPSI-1L"))
		self.assertEqual(doc.codigos_barras[0].barcode_type, "EAN-13")

		doc.aprobar()
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertEqual(doc.aprobado_por, "Administrator")
		self.assertIsNotNone(doc.aprobado_en)

		doc.volver_a_revision()
		doc.reload()
		self.assertEqual(doc.estado, "Por revisar")
		self.assertIsNone(doc.aprobado_por)

	def test_transicion_invalida(self):
		doc = captura_dictada().insert()
		with self.assertRaises(frappe.ValidationError):
			doc.aprobar()  # Borrador → Aprobado no existe
		with self.assertRaises(frappe.ValidationError):
			doc.marcar_procesada()  # requiere Procesando

	def test_enviar_exige_foto_y_transcripcion(self):
		doc = captura_dictada(fotos=[], transcripcion="ab").insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.enviar()
		self.assertIn("al menos 1 foto", str(ctx.exception))
		self.assertIn("mínimo 3 caracteres", str(ctx.exception))

	def test_aprobar_bloquea_con_errores(self):
		insertar_item("BEB-COCA-500ML", "Coca-Cola 500 ml", barcodes=(EAN13,))
		doc = captura_dictada().insert()
		pasar_a_por_revisar(doc)
		self.assertIn("[Bloquea] Este producto ya existe: Coca-Cola 500 ml", doc.advertencias_ia)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.aprobar()
		self.assertIn("Coca-Cola 500 ml (BEB-COCA-500ML)", str(ctx.exception))
		doc.reload()
		self.assertEqual(doc.estado, "Por revisar")

	def test_error_de_ia_y_reprocesar(self):
		doc = captura_dictada().insert()
		doc.enviar()
		doc.reload()
		doc.marcar_error("HTTP 529 overloaded")
		doc.reload()
		self.assertEqual(doc.estado, "Error")
		self.assertEqual(doc.error_log, "HTTP 529 overloaded")
		self.assertEqual(doc.mensaje_capturador, "No se pudo procesar. El supervisor lo revisará.")
		with patch("captura_productos.ai.extractor.encolar_procesamiento") as encolar:
			doc.reprocesar_ia()
		encolar.assert_called_once_with(doc.name)
		self.assertEqual(doc.estado, "Procesando")
		self.assertIsNone(doc.error_log)

	def test_recalcular_nombre(self):
		doc = captura_dictada().insert()
		pasar_a_por_revisar(doc)
		doc.marca = "coca cola"
		doc.save()
		doc.reload()
		# Editar la marca no cambia el nombre hasta Recalcular (HU-07).
		self.assertEqual(doc.item_name, "Pepsi 1 L")

		vista_previa = doc.recalcular_nombre(guardar=0)
		self.assertTrue(vista_previa["cambia"])
		self.assertEqual(vista_previa["despues"][0], ("Coca-Cola 1 L", "BEB-COCA-1L"))
		doc.reload()
		self.assertEqual(doc.item_name, "Pepsi 1 L")

		doc.recalcular_nombre(guardar=1)
		doc.reload()
		self.assertEqual(
			(doc.marca, doc.item_name, doc.item_code), ("Coca-Cola", "Coca-Cola 1 L", "BEB-COCA-1L")
		)
		self.assertFalse(doc.recalcular_nombre(guardar=0)["cambia"])

	def test_recalcular_usa_plantilla_vigente(self):
		doc = captura_dictada().insert()
		pasar_a_por_revisar(doc)
		configurar(plantilla_codigo="{prefijo}.{marca_corta}.{medida}")
		doc.recalcular_nombre()
		doc.reload()
		self.assertEqual(doc.item_code, "BEB.PEPSI.1L")

	def test_no_se_borra_si_esta_creado(self):
		doc = captura_dictada().insert()
		doc.db_set("estado", "Creado")
		doc.reload()
		with self.assertRaises(frappe.ValidationError):
			doc.delete()

	def test_client_uuid_unico(self):
		captura_dictada(client_uuid="abc-123").insert()
		with self.assertRaises(frappe.UniqueValidationError):
			captura_dictada(client_uuid="abc-123").insert()
		# Vacío no choca.
		captura_dictada(client_uuid="").insert()
		captura_dictada(client_uuid=" ").insert()


class TestPermisosCapturador(CapturaTestCase):
	def test_capturador_crea_y_envia_las_suyas(self):
		# CR-A1: el capturador solo lee sus capturas; crea y envía por la API de /captura.
		frappe.set_user(USUARIO_CAPTURADOR)
		with self.assertRaises(frappe.PermissionError):
			captura_dictada(fotos=[]).insert()
		doc = insertar_de(USUARIO_CAPTURADOR, captura_dictada())
		self.assertEqual(doc.capturado_por, USUARIO_CAPTURADOR)
		self.assertTrue(frappe.has_permission("Captura de Producto", "read", doc))
		self.assertFalse(frappe.has_permission("Captura de Producto", "write", doc))
		with patch("captura_productos.ai.extractor.encolar_procesamiento"):
			doc.enviar()
		doc.reload()
		self.assertEqual(doc.estado, "Procesando")

		# Ya enviada: no la puede editar.
		doc.transcripcion = "otra cosa"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

		# No es supervisor: no aprueba ni recalcula.
		frappe.set_user("Administrator")
		doc.reload()
		doc.marcar_procesada()
		frappe.set_user(USUARIO_CAPTURADOR)
		doc.reload()
		with self.assertRaises(frappe.PermissionError):
			doc.aprobar()

	def test_capturador_no_ve_las_de_otros(self):
		ajena = insertar_de(USUARIO_CAPTURADOR_2, captura_dictada())
		frappe.set_user(USUARIO_CAPTURADOR)
		self.assertFalse(frappe.has_permission("Captura de Producto", "read", ajena))
		self.assertFalse(frappe.has_permission("Captura de Producto", "delete", ajena))

	def test_capturador_no_accede_a_configuracion(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		self.assertFalse(frappe.has_permission("Configuracion Captura", "read"))
		frappe.set_user(USUARIO_SUPERVISOR)
		self.assertFalse(frappe.has_permission("Configuracion Captura", "read"))
		self.assertTrue(frappe.has_permission("Captura de Producto", "delete"))


class TestApi(CapturaTestCase):
	def test_verificar_codigo_invalido(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.verificar_codigo("4006381333932")
		self.assertFalse(r["valido"])
		self.assertIn("mal leído", r["mensaje"])
		r = api.verificar_codigo("130885")
		self.assertTrue(r["valido"])
		self.assertEqual(r["barcode_type"], "")
		self.assertFalse(r["existe"])

	def test_verificar_codigo_en_item(self):
		insertar_item("BEB-COCA-500ML", "Coca-Cola 500 ml", barcodes=(EAN13,))
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.verificar_codigo(EAN13)
		self.assertTrue(r["existe"] and r["bloquea"])
		self.assertEqual(
			(r["origen"], r["item_code"], r["item_name"]), ("item", "BEB-COCA-500ML", "Coca-Cola 500 ml")
		)
		self.assertEqual(r["mensaje"], "Este producto ya existe: Coca-Cola 500 ml (BEB-COCA-500ML)")

	def test_verificar_codigo_en_otra_captura(self):
		otra = insertar_de(USUARIO_CAPTURADOR_2, captura_dictada())
		frappe.set_user(USUARIO_CAPTURADOR)
		r = api.verificar_codigo(barcode=EAN13)
		self.assertTrue(r["existe"])
		self.assertFalse(r["bloquea"])
		self.assertEqual((r["origen"], r["captura"], r["estado"]), ("captura", otra.name, "Borrador"))
		# Excluye la captura actual.
		self.assertFalse(api.verificar_codigo(EAN13, captura=otra.name)["existe"])
		# Una captura ya Creado no cuenta.
		otra.db_set("estado", "Creado")
		self.assertFalse(api.verificar_codigo(EAN13)["existe"])

	def test_mis_capturas(self):
		propia = insertar_de(
			USUARIO_CAPTURADOR,
			captura_dictada(
				tiene_variantes=1,
				codigos_barras=[],
				atributos=[{"atributo": "Sabor"}],
				variantes=[{"valor_1": "Durazno"}, {"valor_1": "Manzana"}],
			),
		)
		insertar_de(USUARIO_CAPTURADOR_2, captura_dictada(codigos_barras=[]))
		frappe.set_user(USUARIO_CAPTURADOR)
		filas = api.mis_capturas()
		self.assertEqual([f["name"] for f in filas], [propia.name])
		self.assertEqual(filas[0]["n_variantes"], 2)
		self.assertEqual(filas[0]["estado"], "Borrador")
		self.assertEqual(filas[0]["foto_principal"], FOTO)

	def test_sin_rol_no_accede(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			api.mis_capturas()


class TestConfiguracion(CapturaTestCase):
	def test_placeholder_invalido(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			configurar(plantilla_nombre="{marca} {sabor}")
		self.assertIn("Placeholder no válido: {sabor}", str(ctx.exception))

	def test_prefijo_invalido(self):
		conf = frappe.get_single("Configuracion Captura")
		conf.append("prefijos", {"item_group": "Galletas", "prefijo": "g1"})
		with self.assertRaises(frappe.ValidationError):
			conf.save()

	def test_credenciales_largas_en_password(self):
		"""Riesgo R6: el JSON de la cuenta de servicio (~2,3 KB) cabe en un Password del Single."""
		json_largo = '{"type": "service_account", "private_key": "' + "A" * 2400 + '"}'
		configurar(google_service_account_json=json_largo)
		conf = frappe.get_single("Configuracion Captura")
		self.assertEqual(conf.get_password("google_service_account_json"), json_largo)
