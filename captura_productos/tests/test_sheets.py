"""Exportación a Google Sheet con gspread mockeado (spec HU-11, secciones 7 y 13)."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe

from captura_productos.ai import extractor
from captura_productos.export import sheets
from captura_productos.export.sheets import ItemExport, OpcionesExport
from captura_productos.tests.utils import (
	GRUPO_BEBIDAS,
	UOM,
	CapturaTestCase,
	configurar,
	nueva_captura,
	pasar_a_por_revisar,
)

EAN_SIMPLE = "4006381333931"
EAN_DURAZNO = "7501031311309"
EAN_MANZANA = "7790895000997"
INTERNO = "130899"

CREDENCIALES = json.dumps(
	{"type": "service_account", "client_email": "bot@proyecto.iam.gserviceaccount.com", "private_key": "x"}
)


class HojaFalsa:
	def __init__(self, titulo):
		self.title = titulo
		self.valores = None

	def update(self, values, range_name, value_input_option):
		self.valores = values


class SpreadsheetFalso:
	url = "https://docs.google.com/spreadsheets/d/abc"

	def __init__(self, existentes=()):
		self.hojas = [HojaFalsa(t) for t in existentes]

	def worksheets(self):
		return list(self.hojas)

	def add_worksheet(self, title, rows, cols):
		hoja = HojaFalsa(title)
		self.hojas.append(hoja)
		return hoja

	def hoja(self, sufijo):
		return next(h for h in self.hojas if h.title.endswith(sufijo))


def aprobar(doc):
	with patch.object(extractor, "encolar_procesamiento"):
		pasar_a_por_revisar(doc)
	doc.aprobar()
	doc.reload()
	return doc


def captura_simple(**campos):
	datos = {
		"transcripcion": "aquarius de 300 ml",
		"marca": "Aquarius",
		"tipo_producto": "Agua saborizada",
		"medida_valor": 300,
		"medida_unidad": "ml",
		"descripcion": "Agua saborizada.",
		"codigos_barras": [{"barcode": EAN_SIMPLE}, {"barcode": INTERNO}],
	}
	datos.update(campos)
	return nueva_captura(**datos)


def captura_variantes():
	return nueva_captura(
		transcripcion="jugo frut all de un litro",
		marca="Frut-All",
		tipo_producto="Jugo",
		medida_valor=1,
		medida_unidad="L",
		tiene_variantes=1,
		atributos=[{"atributo": "Sabor", "orden": 1}, {"atributo": "Color", "orden": 2}],
		variantes=[
			{"valor_1": "Durazno", "valor_2": "Verde", "barcode": EAN_DURAZNO},
			{"valor_1": "Manzana", "valor_2": "Rojo", "barcode": EAN_MANZANA},
		],
	)


class TestFilasPuras(CapturaTestCase):
	def test_encabezados_opcionales(self):
		base = sheets.encabezados(OpcionesExport())
		self.assertEqual(base[:4], ["Item Code", "Item Name", "Item Group", "Default Unit of Measure"])
		self.assertIn("Maintain Stock", base)
		self.assertNotIn("Company (Item Defaults)", base)
		completos = sheets.encabezados(
			OpcionesExport(empresa="ET", almacen="Dep - ET", campos_custom=("custom_formato_venta",))
		)
		self.assertEqual(
			completos[-3:],
			["Company (Item Defaults)", "Default Warehouse (Item Defaults)", "custom_formato_venta"],
		)

	def test_multifila_codigos_y_atributos(self):
		op = OpcionesExport()
		col = {c: i for i, c in enumerate(sheets.encabezados(op))}
		simple = ItemExport(
			"BEB-A-300ML", "A 300 ml", GRUPO_BEBIDAS, UOM, barcodes=[(EAN_SIMPLE, "EAN-13"), (INTERNO, "")]
		)
		filas = sheets.filas_item(simple, op)
		self.assertEqual(len(filas), 2)
		self.assertEqual(filas[0][col["Item Code"]], "BEB-A-300ML")
		self.assertEqual(filas[0][col["Has Variants"]], "0")
		self.assertEqual(filas[0][col["Maintain Stock"]], "1")
		self.assertEqual(filas[1][col["Barcode (Barcodes)"]], INTERNO)
		self.assertEqual([v for i, v in enumerate(filas[1]) if i != col["Barcode (Barcodes)"]], [""] * (len(col) - 1))

		variante = ItemExport(
			"BEB-F-DUR-1L",
			"F Durazno 1 L",
			GRUPO_BEBIDAS,
			UOM,
			variant_of="BEB-F-1L",
			barcodes=[(EAN_DURAZNO, "EAN-13")],
			atributos=[("Sabor", "Durazno"), ("Tamaño", "1 L")],
		)
		filas = sheets.filas_item(variante, op)
		self.assertEqual(len(filas), 2)
		self.assertEqual(filas[0][col["Variant Of"]], "BEB-F-1L")
		self.assertEqual(filas[0][col["Attribute Value (Variant Attributes)"]], "Durazno")
		self.assertEqual(filas[1][col["Attribute (Variant Attributes)"]], "Tamaño")
		self.assertEqual(filas[1][col["Item Code"]], "")

	def test_construir_filas_y_titulos(self):
		op = OpcionesExport()
		a = ItemExport("A", "A", GRUPO_BEBIDAS, UOM, barcodes=[("1", ""), ("2", "")], captura="CAP-1")
		b = ItemExport("B", "B", GRUPO_BEBIDAS, UOM, has_variants=True, atributos=[("Sabor", "")], captura="CAP-2")
		filas = sheets.construir_filas([a, b], [], op)
		self.assertEqual(filas.fila_por_captura, {"CAP-1": 2, "CAP-2": 4})
		self.assertEqual(filas.variantes, [])
		momento = datetime(2026, 11, 5, 10, 30)
		self.assertEqual(
			sheets.titulos_pestanas("Items", momento),
			("Items-20261105-1030-1-Productos", "Items-20261105-1030-2-Variantes"),
		)
		self.assertEqual(
			sheets.titulos_pestanas("Items", momento, {"Items-20261105-1030-1-Productos"})[0],
			"Items-20261105-1030-2-1-Productos",
		)

	def test_url_imagen(self):
		self.assertEqual(sheets.url_imagen("/files/a.jpg", "https://erp.example/"), "https://erp.example/files/a.jpg")
		self.assertEqual(sheets.url_imagen("/private/files/a.jpg", "https://erp.example"), "")
		self.assertEqual(sheets.url_imagen(None, "https://erp.example"), "")


class TestExportar(CapturaTestCase):
	def setUp(self):
		super().setUp()
		self.enterContext(patch("frappe.log_error"))  # Error Log no se deshace con el rollback del test
		configurar(
			empresa=None,
			almacen_defecto=None,
			spreadsheet_id="abc",
			google_service_account_json=CREDENCIALES,
			url_base_imagenes="https://erp.example",
			crear_maestros_al_exportar=1,
			pestana_sheet="Items",
		)

	def exportar(self, nombres, spreadsheet=None):
		spreadsheet = spreadsheet or SpreadsheetFalso()
		with patch.object(sheets, "abrir_spreadsheet", return_value=(spreadsheet, "bot@x")):
			return sheets.exportar_capturas(nombres), spreadsheet

	def test_exporta_simple_y_variantes(self):
		simple = aprobar(captura_simple().insert())
		con_variantes = aprobar(captura_variantes().insert())
		self.assertFalse(frappe.db.exists("Brand", "Aquarius"))
		borrador = captura_simple(codigos_barras=[]).insert()

		resultado, ss = self.exportar([simple.name, con_variantes.name, borrador.name])
		self.assertEqual(resultado["exportadas"], [simple.name, con_variantes.name])
		self.assertEqual(resultado["excluidas"][0]["captura"], borrador.name)
		self.assertEqual(len(resultado["hojas"]), 2)

		productos = ss.hoja("-1-Productos").valores
		variantes = ss.hoja("-2-Variantes").valores
		col = {c: i for i, c in enumerate(productos[0])}
		self.assertEqual(productos[0], list(sheets.ENCABEZADOS_BASE))
		# simple: 2 filas (2 códigos); plantilla: 2 filas (2 atributos)
		self.assertEqual(len(productos), 5)
		self.assertEqual(productos[1][col["Item Code"]], "BEB-AQUARIUS-300ML")
		self.assertEqual(productos[1][col["Image"]], "https://erp.example/files/test-captura.jpg")
		self.assertEqual(productos[2][col["Barcode (Barcodes)"]], INTERNO)
		self.assertEqual(productos[3][col["Has Variants"]], "1")
		self.assertEqual(
			[productos[3][col["Attribute (Variant Attributes)"]], productos[4][col["Attribute (Variant Attributes)"]]],
			["Sabor", "Color"],
		)
		self.assertEqual(len(variantes), 5)
		self.assertEqual(variantes[1][col["Variant Of"]], "BEB-FRUTALL-1L")
		self.assertEqual(variantes[1][col["Barcode (Barcodes)"]], EAN_DURAZNO)
		self.assertEqual(variantes[2][col["Attribute Value (Variant Attributes)"]], "Verde")
		self.assertEqual(variantes[4][col["Attribute Value (Variant Attributes)"]], "Rojo")

		simple.reload()
		con_variantes.reload()
		self.assertEqual(simple.estado, "Exportado")
		self.assertEqual(simple.fila_sheet, 2)
		self.assertEqual(con_variantes.fila_sheet, 4)
		self.assertIn("-2-Variantes", con_variantes.hoja_exportacion)
		self.assertTrue(frappe.db.exists("Brand", "Aquarius"))
		self.assertTrue(
			frappe.db.exists("Item Attribute Value", {"parent": "Color", "attribute_value": "Rojo", "abbr": "ROJ"})
		)

	def test_sin_crear_maestros_excluye(self):
		configurar(crear_maestros_al_exportar=0)
		doc = aprobar(captura_simple().insert())
		resultado, ss = self.exportar([doc.name])
		self.assertEqual(resultado["exportadas"], [])
		self.assertIn("Falta la marca Aquarius", resultado["excluidas"][0]["motivo"])
		self.assertEqual(ss.hojas, [])

	def test_hoja_no_compartida_no_cambia_estado(self):
		doc = aprobar(captura_simple().insert())
		error = sheets.ErrorExportacion("Comparta la hoja con bot@x como Editor", "APIError 403: forbidden")
		with (
			patch.object(sheets, "abrir_spreadsheet", side_effect=error),
			self.assertRaises(frappe.ValidationError) as ctx,
		):
			sheets.exportar_capturas([doc.name])
		self.assertIn("Comparta la hoja con bot@x como Editor", str(ctx.exception))
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertIn("APIError 403", doc.error_log)
		self.assertFalse(frappe.db.exists("Brand", "Aquarius"))

	def test_abrir_traduce_errores_de_gspread(self):
		import gspread

		cliente = MagicMock()
		cliente.open_by_key.side_effect = gspread.exceptions.SpreadsheetNotFound("no")
		conf = frappe.get_single("Configuracion Captura")
		with patch("gspread.service_account_from_dict", return_value=cliente):
			with self.assertRaises(sheets.ErrorExportacion) as ctx:
				sheets.abrir_spreadsheet(conf)
		self.assertIn("bot@proyecto.iam.gserviceaccount.com como Editor", ctx.exception.mensaje)
		cliente.set_timeout.assert_called_once_with(sheets.TIMEOUT_GOOGLE)

		configurar(google_service_account_json="{no es json")
		with self.assertRaises(sheets.ErrorExportacion) as ctx:
			sheets.abrir_spreadsheet(frappe.get_single("Configuracion Captura"))
		self.assertIn("no es válido", ctx.exception.mensaje)

	def test_credenciales_largas_en_password(self):
		"""Riesgo R6: un JSON de ~2,4 KB se guarda y se recupera entero del campo Password."""
		largo = json.dumps({"client_email": "a@b.iam.gserviceaccount.com", "private_key": "k" * 2400})
		configurar(google_service_account_json=largo)
		conf = frappe.get_single("Configuracion Captura")
		self.assertEqual(conf.get_password("google_service_account_json"), largo)

	def test_boton_exportar_del_formulario(self):
		doc = aprobar(captura_simple().insert())
		with patch.object(sheets, "abrir_spreadsheet", return_value=(SpreadsheetFalso(), "bot@x")):
			doc.exportar()
		self.assertEqual(doc.estado, "Exportado")
		doc.marcar_para_reexportar()
		self.assertEqual(doc.estado, "Aprobado")
