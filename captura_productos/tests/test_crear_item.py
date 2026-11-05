"""Crear Item desde una captura (spec HU-12 y T11). Requiere el setup de ERPNext completo en el sitio."""

from unittest.mock import patch

import frappe

from captura_productos.tests.test_sheets import (
	EAN_DURAZNO,
	EAN_MANZANA,
	EAN_SIMPLE,
	INTERNO,
	aprobar,
	captura_simple,
	captura_variantes,
)
from captura_productos.tests.utils import USUARIO_SUPERVISOR, CapturaTestCase, configurar, insertar_item


def barcodes(item_code):
	return sorted(frappe.get_all("Item Barcode", filters={"parent": item_code}, pluck="barcode"))


class TestCrearItem(CapturaTestCase):
	def setUp(self):
		super().setUp()
		self.enterContext(patch("frappe.log_error"))  # Error Log no se deshace con el rollback del test
		empresa = frappe.db.get_value("Company", {}, "name")
		configurar(empresa=empresa, almacen_defecto=None, is_stock_item=1)

	def test_item_simple(self):
		doc = aprobar(captura_simple().insert())
		resultado = doc.crear_item()
		self.assertEqual(resultado["item"], "BEB-AQUARIUS-300ML")
		self.assertEqual(doc.estado, "Creado")
		self.assertEqual(doc.item, "BEB-AQUARIUS-300ML")
		item = frappe.get_doc("Item", "BEB-AQUARIUS-300ML")
		self.assertEqual(item.item_name, "Aquarius 300 ml")
		self.assertEqual(item.brand, "Aquarius")
		self.assertEqual(item.image, "/files/test-captura.jpg")
		self.assertEqual(sorted(b.barcode for b in item.barcodes), sorted([EAN_SIMPLE, INTERNO]))
		self.assertEqual({b.barcode: b.barcode_type for b in item.barcodes}[EAN_SIMPLE], "EAN-13")
		self.assertTrue(item.item_defaults)

	def test_plantilla_y_variantes(self):
		doc = aprobar(captura_variantes().insert())
		resultado = doc.crear_item()
		self.assertEqual(resultado["item"], "BEB-FRUTALL-1L")
		self.assertEqual(len(resultado["creados"]), 3)
		plantilla = frappe.get_doc("Item", "BEB-FRUTALL-1L")
		self.assertEqual(plantilla.has_variants, 1)
		self.assertEqual([a.attribute for a in plantilla.attributes], ["Sabor", "Color"])

		codigos = [v.item_code for v in doc.variantes]
		self.assertEqual([v.item for v in doc.variantes], codigos)
		variante = frappe.get_doc("Item", codigos[1])
		self.assertEqual(variante.variant_of, "BEB-FRUTALL-1L")
		self.assertEqual(variante.item_name, doc.variantes[1].item_name)
		self.assertEqual({a.attribute: a.attribute_value for a in variante.attributes}, {"Sabor": "Manzana", "Color": "Rojo"})
		self.assertEqual(barcodes(codigos[0]), [EAN_DURAZNO])
		self.assertEqual(barcodes(codigos[1]), [EAN_MANZANA])
		self.assertTrue(frappe.db.exists("Item Attribute Value", {"parent": "Color", "attribute_value": "Rojo"}))
		self.assertEqual(doc.estado, "Creado")

	def test_enlaza_item_importado(self):
		doc = aprobar(captura_simple().insert())
		# Simula la importación por Data Import después de aprobar.
		insertar_item(doc.item_code, doc.item_name, barcodes=(EAN_SIMPLE, INTERNO))
		resultado = doc.crear_item()
		self.assertEqual(resultado["enlazados"], [doc.item_code])
		self.assertEqual(resultado["creados"], [])
		self.assertEqual(doc.estado, "Creado")

	def test_rollback_total_si_falla(self):
		doc = aprobar(captura_variantes().insert())
		# La segunda variante ya existe con otro código: no debe quedar nada creado.
		insertar_item(doc.variantes[1].item_code, "Otro", barcodes=("5000112637922",))
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.crear_item()
		self.assertIn("otros códigos de barras", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Item", "BEB-FRUTALL-1L"))
		self.assertFalse(frappe.db.exists("Item", doc.variantes[0].item_code))
		self.assertFalse(frappe.db.exists("Item Attribute Value", {"parent": "Color", "attribute_value": "Rojo"}))
		doc.reload()
		self.assertEqual(doc.estado, "Aprobado")
		self.assertIn("otros códigos de barras", doc.error_log)

	def test_permisos(self):
		doc = aprobar(captura_simple().insert())
		frappe.set_user(USUARIO_SUPERVISOR)  # Item Manager: puede crear Item
		frappe.get_doc("Captura de Producto", doc.name).crear_item()
		self.assertEqual(frappe.db.get_value("Captura de Producto", doc.name, "estado"), "Creado")

	def test_estado_requerido(self):
		doc = captura_simple().insert()
		with self.assertRaises(frappe.ValidationError):
			doc.crear_item()
