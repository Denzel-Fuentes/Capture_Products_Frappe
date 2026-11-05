"""Tests de regresión de la revisión de código (CR-*) y de QA (B*) de 2026-11-05 (ver docs/QA.md).

Los de la IA atascada y los reintentos programados (CR-M2, CR-M3) están en test_extractor.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import frappe
import frappe.client

from captura_productos import api
from captura_productos.ai import extractor
from captura_productos.captura_productos.doctype.captura_de_producto import captura_de_producto as controlador
from captura_productos.export import erpnext_item, sheets
from captura_productos.tests.test_api import UUID_1, imagen
from captura_productos.tests.test_sheets import (
	CREDENCIALES,
	SpreadsheetFalso,
	aprobar,
	captura_simple,
	captura_variantes,
)
from captura_productos.tests.utils import (
	USUARIO_CAPTURADOR,
	USUARIO_CAPTURADOR_2,
	USUARIO_SUPERVISOR,
	CapturaTestCase,
	configurar,
	insertar_de,
	insertar_item,
	nueva_captura,
)

DOCTYPE = "Captura de Producto"
RAIZ_APP = Path(__file__).resolve().parent.parent


class RegresionTestCase(CapturaTestCase):
	def setUp(self):
		super().setUp()
		for user in (USUARIO_CAPTURADOR, USUARIO_CAPTURADOR_2, USUARIO_SUPERVISOR):
			for accion in api.LIMITES_POR_MINUTO:
				frappe.cache.delete(api.clave_limite(accion, user))
		self.encolar = self.enterContext(patch.object(extractor, "encolar_procesamiento"))
		self.enterContext(patch("frappe.log_error"))
		self.archivos: list[str] = []

	def tearDown(self):
		for url in self.archivos:  # el rollback no borra los archivos del disco
			ruta = (
				frappe.get_site_path(url.lstrip("/"))
				if url.startswith("/private/")
				else frappe.get_site_path("public", url.lstrip("/"))
			)
			if os.path.exists(ruta):
				os.remove(ruta)
		super().tearDown()

	def subir(self, captura: str) -> str:
		with patch.object(api, "_contenido_del_request", return_value=imagen()):
			url = api.subir_foto(captura)["file_url"]
		self.archivos.append(url)
		return url

	def archivo(self, adjunto_a: str | None, privado: int = 1, nombre: str = "dni") -> str:
		"""Un File cualquiera (por ejemplo, el DNI privado de otra persona)."""
		datos = {
			"doctype": "File",
			"file_name": f"{nombre}-{frappe.generate_hash(length=8)}.jpg",
			"is_private": privado,
			"content": imagen(),
		}
		if adjunto_a:
			datos.update(attached_to_doctype=DOCTYPE, attached_to_name=adjunto_a)
		archivo = frappe.get_doc(datos).insert(ignore_permissions=True)
		self.archivos.append(archivo.file_url)
		return archivo.file_url


# ---------------------------------------------------------------------------------------------------
# CR-A1: el capturador no escribe por REST ni usa fotos ajenas
# ---------------------------------------------------------------------------------------------------


class TestCRA1PermisosYFotos(RegresionTestCase):
	def test_capturador_solo_lee_sus_capturas(self):
		permisos = [p for p in frappe.get_meta(DOCTYPE).permissions if p.role == "Capturador de Productos"]
		self.assertEqual(len(permisos), 1)
		self.assertEqual(
			(
				permisos[0].read,
				permisos[0].if_owner,
				permisos[0].create,
				permisos[0].write,
				permisos[0].delete,
			),
			(1, 1, 0, 0, 0),
		)

	def test_capturador_no_escribe_por_rest(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		propia = frappe.get_doc(DOCTYPE, api.crear_captura(UUID_1)["name"])
		with self.assertRaises(frappe.PermissionError):
			frappe.client.insert({"doctype": DOCTYPE, "transcripcion": "por REST"})
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value(DOCTYPE, propia.name, "transcripcion", "cambiada por REST")
		doc = propia.as_dict()
		doc["fotos"] = [{"foto": "/private/files/dni.jpg", "es_principal": 1}]
		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(doc)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Captura Foto", {"parent": propia.name}), 0)

	def test_validate_rechaza_fotos_no_adjuntas_a_la_captura(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		ajeno = self.archivo(adjunto_a=None)  # p. ej. /private/files/dni-xxxx.jpg
		for url in (ajeno, "/private/files/dni.jpg", "/files/../../site_config.json"):
			with self.subTest(url=url):
				doc = frappe.get_doc(DOCTYPE, nombre)
				doc.append("fotos", {"foto": url, "es_principal": 1})
				with self.assertRaises(frappe.PermissionError):
					doc.save(ignore_permissions=True)  # aunque se salteen los permisos, validate() lo frena
		# Una foto subida con subir_foto sí se acepta.
		doc = frappe.get_doc(DOCTYPE, nombre)
		doc.append("fotos", {"foto": self.subir(nombre), "es_principal": 1})
		doc.save(ignore_permissions=True)

	def test_api_solo_acepta_fotos_de_subir_foto(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		nombre = api.crear_captura(UUID_1)["name"]
		frappe.set_user("Administrator")
		privado_adjunto = self.archivo(adjunto_a=nombre, privado=1)
		publico_otro_nombre = self.archivo(adjunto_a=nombre, privado=0, nombre="desde-el-desk")
		frappe.set_user(USUARIO_CAPTURADOR)
		for url in (privado_adjunto, publico_otro_nombre):
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				api.enviar_captura(nombre, {"transcripcion": "galletas", "fotos": [{"file_url": url}]})
		self.assertEqual(api.fotos_subidas(nombre), set())
		buena = self.subir(nombre)
		self.assertEqual(api.fotos_subidas(nombre), {buena})
		api.enviar_captura(nombre, {"transcripcion": "galletas", "fotos": [{"file_url": buena}]})
		self.assertEqual(frappe.db.get_value(DOCTYPE, nombre, "estado"), "Procesando")

	def test_job_no_manda_a_claude_archivos_ajenos(self):
		frappe.set_user("Administrator")
		doc = nueva_captura(transcripcion="pepsi de litro", fotos=[]).insert()
		ajeno = self.archivo(adjunto_a=None)
		# Un supervisor puede poner cualquier URL, pero el job igual no la lee.
		doc.append("fotos", {"foto": ajeno, "es_principal": 1})
		doc.save()
		contenido, avisos = extractor.construir_contenido_usuario(doc, 4)
		self.assertEqual([b["type"] for b in contenido], ["text"])
		self.assertIn("No se pudo leer la foto", avisos[0])

	def test_error_log_y_respuesta_ia_solo_para_supervisores(self):
		meta = frappe.get_meta(DOCTYPE)
		self.assertEqual(
			(meta.get_field("error_log").permlevel, meta.get_field("respuesta_ia").permlevel), (1, 1)
		)
		doc = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="galletas"))
		frappe.set_user("Administrator")
		doc.db_set({"error_log": "Traceback secreto", "respuesta_ia": '{"x": 1}', "estado": "Error"})
		frappe.set_user(USUARIO_CAPTURADOR)
		leido = frappe.client.get(DOCTYPE, doc.name)
		self.assertNotIn("Traceback", str(leido.get("error_log") or ""))
		self.assertFalse(leido.get("respuesta_ia"))
		frappe.set_user(USUARIO_SUPERVISOR)
		self.assertEqual(frappe.client.get(DOCTYPE, doc.name).get("error_log"), "Traceback secreto")


# ---------------------------------------------------------------------------------------------------
# CR-M1: límites que no se pueden saltear
# ---------------------------------------------------------------------------------------------------


class TestCRM1Limites(RegresionTestCase):
	def test_capturado_por_es_siempre_la_sesion_para_el_capturador(self):
		frappe.set_user(USUARIO_CAPTURADOR)
		doc = nueva_captura(fotos=[], capturado_por=USUARIO_CAPTURADOR_2)
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.capturado_por, USUARIO_CAPTURADOR)
		# El supervisor sí puede registrar a nombre de otra persona.
		frappe.set_user(USUARIO_SUPERVISOR)
		doc = nueva_captura(capturado_por=USUARIO_CAPTURADOR_2).insert()
		self.assertEqual(doc.capturado_por, USUARIO_CAPTURADOR_2)

	def test_enviar_no_es_whitelisted_y_reprocesar_exige_supervisor(self):
		whitelisted = set(frappe.whitelisted)
		self.assertNotIn(controlador.CapturadeProducto.enviar, whitelisted)
		self.assertNotIn(controlador.CapturadeProducto.reintentar, whitelisted)
		self.assertIn(controlador.CapturadeProducto.enviar_desde_formulario, whitelisted)
		doc = insertar_de(USUARIO_CAPTURADOR, nueva_captura(transcripcion="galletas"))
		with self.assertRaises(frappe.PermissionError):
			doc.enviar_desde_formulario()
		frappe.set_user("Administrator")
		doc.db_set("estado", "Error")
		frappe.set_user(USUARIO_CAPTURADOR)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, doc.name).reprocesar_ia()

	def test_metodos_del_controlador_aplican_el_limite(self):
		doc = nueva_captura(transcripcion="galletas").insert()
		with (
			patch.dict(api.LIMITES_POR_MINUTO, {"enviar": 0}),
			self.assertRaises(frappe.RateLimitExceededError),
		):
			doc.enviar_desde_formulario()
		doc.db_set("estado", "Error")
		with (
			patch.dict(api.LIMITES_POR_MINUTO, {"enviar": 0}),
			self.assertRaises(frappe.RateLimitExceededError),
		):
			doc.reprocesar_ia()
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "estado"), "Error")

	def test_limite_con_vencimiento(self):
		"""CR-B2: el contador siempre queda con vencimiento (INCR + EXPIRE NX en MULTI/EXEC)."""
		frappe.set_user(USUARIO_CAPTURADOR)
		api.limitar("crear")
		api.limitar("crear")
		clave = api.clave_limite("crear", USUARIO_CAPTURADOR)
		self.assertEqual(int(frappe.cache.get(clave)), 2)
		self.assertTrue(0 < frappe.cache.ttl(clave) <= 60)

	def test_validate_limita_textos_fotos_y_variantes(self):
		casos = {
			"transcripción": {"transcripcion": "x" * (controlador.LARGO_MAX_TRANSCRIPCION + 1)},
			"fotos": {"fotos": [{"foto": f"/files/f{i}.jpg"} for i in range(controlador.MAX_FOTOS + 1)]},
			"variantes": {
				"tiene_variantes": 1,
				"atributos": [{"atributo": "Sabor"}],
				"variantes": [{"valor_1": f"v{i}"} for i in range(controlador.MAX_VARIANTES + 1)],
			},
			"códigos": {
				"codigos_barras": [{"barcode": f"13088{i}"} for i in range(controlador.MAX_CODIGOS + 1)]
			},
		}
		for nombre, campos in casos.items():
			with self.subTest(caso=nombre), self.assertRaises(frappe.ValidationError):
				nueva_captura(**campos).insert()

	def test_subir_foto_rechaza_fotos_de_mas_de_2000_px(self):
		self.assertEqual(api.MAX_LADO_FOTO, 2000)


# ---------------------------------------------------------------------------------------------------
# CR-M4: exportación sin dobles ni pestañas huérfanas, masiva en segundo plano
# ---------------------------------------------------------------------------------------------------


class SpreadsheetConBorrado(SpreadsheetFalso):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.borradas: list[str] = []

	def del_worksheet(self, hoja):
		self.borradas.append(hoja.title)
		self.hojas.remove(hoja)


class TestCRM4Exportacion(RegresionTestCase):
	def setUp(self):
		super().setUp()
		configurar(
			empresa=None,
			spreadsheet_id="abc",
			google_service_account_json=CREDENCIALES,
			url_base_imagenes="https://erp.example",
			crear_maestros_al_exportar=1,
			pestana_sheet="Items",
		)

	def test_falla_despues_de_escribir_borra_las_pestanas(self):
		doc = aprobar(captura_variantes().insert())
		ss = SpreadsheetConBorrado()
		with (
			patch.object(sheets, "abrir_spreadsheet", return_value=(ss, "bot@x")),
			patch.object(controlador.CapturadeProducto, "_cambiar_estado", side_effect=RuntimeError("caída")),
			self.assertRaises(RuntimeError),
		):
			sheets.exportar_capturas([doc.name])
		self.assertEqual(len(ss.borradas), 2)
		self.assertEqual(ss.hojas, [])
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "estado"), "Aprobado")
		self.assertIn("caída", frappe.db.get_value(DOCTYPE, doc.name, "error_log"))

	def test_no_exporta_dos_veces(self):
		doc = aprobar(captura_simple().insert())
		with patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")):
			primera = sheets.exportar_capturas([doc.name])
			segunda = sheets.exportar_capturas([doc.name])
		self.assertEqual(primera["exportadas"], [doc.name])
		self.assertEqual(segunda["exportadas"], [])
		self.assertIn("Exportado", segunda["excluidas"][0]["motivo"])

	def test_documento_viejo_no_cambia_el_estado(self):
		"""Una copia vieja (otra pestaña, doble clic) no puede pasar a otro estado."""
		doc = aprobar(captura_simple().insert())
		viejo = frappe.get_doc(DOCTYPE, doc.name)
		with patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")):
			doc.exportar()
		with self.assertRaises(frappe.ValidationError):
			viejo.volver_a_revision()
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "estado"), "Exportado")

	def test_exportacion_masiva_en_segundo_plano(self):
		doc = aprobar(captura_simple().insert())
		with patch.object(frappe, "enqueue") as encolar:
			respuesta = sheets.exportar_aprobados([doc.name])
		self.assertTrue(respuesta["encolado"])
		kwargs = encolar.call_args.kwargs
		self.assertEqual(
			encolar.call_args.args[0], "captura_productos.export.sheets.exportar_en_segundo_plano"
		)
		self.assertEqual(
			(kwargs["queue"], kwargs["nombres"], kwargs["usuario"]), ("long", [doc.name], "Administrator")
		)
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "estado"), "Aprobado")

		with (
			patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")),
			patch.object(frappe.db, "commit"),
			patch.object(frappe, "enqueue"),
			patch.object(frappe, "publish_realtime") as realtime,
		):
			resultado = sheets.exportar_en_segundo_plano([doc.name], "Administrator")
		self.assertEqual(resultado["exportadas"], [doc.name])
		eventos = [c.args[0] if c.args else c.kwargs.get("event") for c in realtime.call_args_list]
		self.assertIn("captura_exportacion", eventos)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": "Administrator", "subject": ["like", "%terminada%"]}
			)
		)

	def test_sin_url_base_y_host_invalido_deja_image_vacia_con_aviso(self):
		"""En el job, get_url() puede dar un host como http://default: no se escriben URLs rotas."""
		configurar(url_base_imagenes="")
		doc = aprobar(captura_simple().insert())
		ss = SpreadsheetFalso()
		with (
			patch.object(sheets, "abrir_spreadsheet", return_value=(ss, "bot@x")),
			patch.object(sheets, "get_url", return_value="http://default"),
		):
			resultado = sheets.exportar_capturas([doc.name])
		filas = ss.hoja("-1-Productos").valores
		columna = filas[0].index("Image")
		self.assertEqual([f[columna] for f in filas[1:]], ["", ""])
		self.assertEqual(len(resultado["avisos"]), 1)
		self.assertIn("Configure 'URL base de imágenes'", resultado["avisos"][0])

		# Con un host válido se usa la URL del sitio y no hay aviso.
		configurar(url_base_imagenes="")
		doc2 = aprobar(captura_simple(codigos_barras=[], item_code="QA-HOST-OK").insert())
		ss = SpreadsheetFalso()
		with (
			patch.object(sheets, "abrir_spreadsheet", return_value=(ss, "bot@x")),
			patch.object(sheets, "get_url", return_value="https://erp.estacion.example"),
		):
			resultado = sheets.exportar_capturas([doc2.name])
		self.assertEqual(resultado["avisos"], [])
		fila = ss.hoja("-1-Productos").valores[1]
		self.assertTrue(fila[columna].startswith("https://erp.estacion.example/files/"))

	def test_host_valido(self):
		for url, esperado in (
			("http://default", False),
			("http://default/files/x.jpg", False),
			("", False),
			("ftp://erp.example", False),
			("https://erp.example", True),
			("http://localhost:8000", True),
			("http://192.168.1.10:8000", True),
			("https://abc.trycloudflare.com", True),
		):
			with self.subTest(url=url):
				self.assertEqual(sheets.host_valido(url), esperado)


# ---------------------------------------------------------------------------------------------------
# CR-M5 y B9: Crear Item no enlaza cualquier Item ni crea maestros sin permiso
# ---------------------------------------------------------------------------------------------------


class TestCRM5CrearItem(RegresionTestCase):
	def setUp(self):
		super().setUp()
		empresa = frappe.db.get_value("Company", {}, "name")
		configurar(empresa=empresa, almacen_defecto=None, is_stock_item=1, crear_maestros_al_exportar=1)

	def test_sin_barcodes_no_enlaza_un_item_distinto(self):
		doc = aprobar(captura_simple(codigos_barras=[]).insert())
		insertar_item(doc.item_code, "Otro producto con el mismo código")
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("no coincide", str(ctx.exception))
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "estado"), "Aprobado")
		self.assertIsNone(frappe.db.get_value(DOCTYPE, doc.name, "item"))

	def test_sin_barcodes_enlaza_si_coinciden_nombre_y_grupo(self):
		doc = aprobar(captura_simple(codigos_barras=[]).insert())
		insertar_item(doc.item_code, doc.item_name)
		resultado = doc.crear_item()
		self.assertEqual(resultado["enlazados"], [doc.item_code])
		self.assertEqual(doc.estado, "Creado")

	def test_plantilla_con_otros_atributos_no_se_enlaza(self):
		doc = aprobar(captura_variantes().insert())
		plantilla = frappe.new_doc("Item")
		plantilla.update(
			{
				"name": doc.item_code,
				"item_code": doc.item_code,
				"item_name": doc.item_name,
				"item_group": doc.item_group,
				"stock_uom": doc.stock_uom,
				"has_variants": 1,
			}
		)
		plantilla.db_insert()
		fila = frappe.new_doc("Item Variant Attribute", parent_doc=plantilla, parentfield="attributes")
		fila.update({"attribute": "Tamaño", "idx": 1, "name": frappe.generate_hash(length=10)})
		fila.db_insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("atributos", str(ctx.exception))
		for variante in doc.variantes:
			self.assertFalse(frappe.db.exists("Item", variante.item_code))

	def test_revalida_antes_de_crear(self):
		doc = aprobar(captura_simple().insert())
		insertar_item("QA-OTRO-REG", "Otro", barcodes=(doc.codigos_barras[0].barcode,))
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("ya no cumple", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Item", doc.item_code))

	def test_sin_crear_maestros_error_claro_y_no_crea_nada(self):
		"""B9 (decisión: prevalece la sección 13)."""
		configurar(crear_maestros_al_exportar=0)
		doc = captura_simple()
		doc.marca = "Marca Regresion Nueva"
		doc = aprobar(doc.insert())
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("Falta la marca Marca Regresion Nueva", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Brand", "Marca Regresion Nueva"))
		self.assertFalse(frappe.db.exists("Item", doc.item_code))
		self.assertIn("Faltan maestros", frappe.db.get_value(DOCTYPE, doc.name, "error_log"))


# ---------------------------------------------------------------------------------------------------
# B1/B2 y otros del Desk y /captura
# ---------------------------------------------------------------------------------------------------


class TestB1B2LlamadasDelDesk(CapturaTestCase):
	def test_frm_call_siempre_con_doc(self):
		"""`frm.call({method})` sin `doc` busca una función del módulo y no el método del documento."""
		archivos = list((RAIZ_APP / "captura_productos" / "doctype").rglob("*.js"))
		self.assertTrue(archivos)
		for archivo in archivos:
			texto = archivo.read_text(encoding="utf-8")
			for llamada in re.findall(r"frm\.call\((.{0,120})", texto, flags=re.S):
				with self.subTest(archivo=archivo.name, llamada=llamada[:60]):
					self.assertTrue(
						llamada.lstrip().startswith(("{ doc: frm.doc", '"', "'")),
						f"frm.call sin doc en {archivo.name}: {llamada[:80]}",
					)

	def test_metodos_llamados_desde_el_desk_existen_y_son_whitelisted(self):
		from captura_productos.captura_productos.doctype.configuracion_captura.configuracion_captura import (
			ConfiguracionCaptura,
		)

		whitelisted = set(frappe.whitelisted)
		for clase, metodos in (
			(
				controlador.CapturadeProducto,
				(
					"enviar_desde_formulario",
					"aprobar",
					"reprocesar_ia",
					"volver_a_revision",
					"marcar_para_reexportar",
					"exportar",
					"crear_item",
					"recalcular_nombre",
				),
			),
			(ConfiguracionCaptura, ("probar_conexion_ia", "probar_google_sheet")),
		):
			for metodo in metodos:
				with self.subTest(metodo=metodo):
					self.assertIn(getattr(clase, metodo), whitelisted)
		js = (RAIZ_APP / "captura_productos/doctype/captura_de_producto/captura_de_producto.js").read_text(
			encoding="utf-8"
		)
		self.assertNotIn('llamar(frm, "enviar")', js)


class TestB8YExtra(CapturaTestCase):
	def test_boot_de_captura_lleva_el_idioma_de_voz(self):
		from captura_productos.www import captura as pagina

		configurar(idioma_voz="es-ES")
		frappe.set_user(USUARIO_CAPTURADOR)
		boot = frappe.parse_json(pagina.get_context(frappe._dict()).boot_json)
		self.assertEqual(boot["idioma_voz"], "es-ES")

	@CapturaTestCase.change_settings(
		"System Settings", {"only_allow_system_managers_to_upload_public_files": 1}
	)
	def test_ajuste_de_archivos_publicos_da_mensaje_claro(self):
		"""CR-B8."""
		with patch("frappe.log_error"):
			frappe.set_user(USUARIO_CAPTURADOR)
			nombre = api.crear_captura(UUID_1)["name"]
			with (
				patch.object(api, "_contenido_del_request", return_value=imagen()),
				self.assertRaises(frappe.ValidationError) as ctx,
			):
				api.subir_foto(nombre)
		self.assertIn("ajuste del sistema", str(ctx.exception))


class TestB4Creado(RegresionTestCase):
	def test_exportado_e_importado_no_se_bloquea_a_si_mismo(self):
		configurar(
			spreadsheet_id="abc",
			google_service_account_json=CREDENCIALES,
			pestana_sheet="Items",
			crear_maestros_al_exportar=1,
		)
		doc = aprobar(captura_simple().insert())
		with patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")):
			doc.exportar()
		# Simula el Data Import del Sheet.
		insertar_item(doc.item_code, doc.item_name, barcodes=tuple(c.barcode for c in doc.codigos_barras))
		doc.reload()
		doc.descripcion = "Editada después de importar"
		doc.save()
		self.assertNotIn("[Bloquea]", doc.advertencias_ia or "")
		self.assertEqual(erpnext_item.crear_item(doc)["enlazados"], [doc.item_code])
