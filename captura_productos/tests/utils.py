"""Maestros y configuración para los tests (el sitio dev es un ERPNext casi vacío).

Todo es idempotente: si el registro ya existe se reutiliza. IntegrationTestCase hace rollback al
terminar cada clase.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from captura_productos.install import GLOSARIO_INICIAL

GRUPO_BEBIDAS = "1.9 Bebidas Embotelladas"
GRUPO_DESCARTABLES = "Descartables"
UOM = "Unidad"
USUARIO_CAPTURADOR = "capturador.test@example.com"
USUARIO_CAPTURADOR_2 = "capturador2.test@example.com"
USUARIO_SUPERVISOR = "supervisor.test@example.com"

# Fotos de prueba: el campo Attach Image solo guarda la URL.
FOTO = "/files/test-captura.jpg"


def crear_si_no_existe(doctype: str, nombre: str, datos: dict) -> None:
	if not frappe.db.exists(doctype, nombre):
		frappe.get_doc({"doctype": doctype, **datos}).insert(ignore_permissions=True)


def crear_atributo(nombre: str, valores: list[tuple[str, str]], **extra) -> None:
	if frappe.db.exists("Item Attribute", nombre):
		return
	frappe.get_doc(
		{
			"doctype": "Item Attribute",
			"attribute_name": nombre,
			"item_attribute_values": [{"attribute_value": v, "abbr": a} for v, a in valores],
			**extra,
		}
	).insert(ignore_permissions=True)


def crear_usuario(email: str, roles: list[str]) -> None:
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"roles": [{"role": r} for r in roles],
		}
	).insert(ignore_permissions=True)


def crear_maestros() -> None:
	crear_si_no_existe(
		"Item Group",
		GRUPO_BEBIDAS,
		{"item_group_name": GRUPO_BEBIDAS, "parent_item_group": "All Item Groups", "is_group": 0},
	)
	crear_si_no_existe(
		"Item Group",
		GRUPO_DESCARTABLES,
		{"item_group_name": GRUPO_DESCARTABLES, "parent_item_group": "All Item Groups", "is_group": 0},
	)
	crear_si_no_existe(
		"Item Group",
		"Galletas",
		{"item_group_name": "Galletas", "parent_item_group": "All Item Groups", "is_group": 0},
	)
	crear_si_no_existe("UOM", UOM, {"uom_name": UOM, "must_be_whole_number": 1})
	for brand in ("Frut-All", "Fanta"):
		crear_si_no_existe("Brand", brand, {"brand": brand})

	crear_atributo("Sabor", [("Durazno", "DUR"), ("Manzana", "MAN"), ("Naranja", "NAR")])
	crear_atributo("Tamaño", [("2 L", "2L"), ("3 L", "3L"), ("500 ml", "500ML")])
	crear_atributo("Color", [("Verde", "VER")])
	crear_atributo("Paquete", [], numeric_values=1, from_range=6, to_range=48, increment=6)

	crear_usuario(USUARIO_CAPTURADOR, ["Capturador de Productos"])
	crear_usuario(USUARIO_CAPTURADOR_2, ["Capturador de Productos"])
	crear_usuario(USUARIO_SUPERVISOR, ["Item Manager"])


def configurar(**cambios) -> None:
	"""Configuración por defecto de la spec, con BEB y DES como prefijos y el glosario inicial."""
	conf = frappe.get_single("Configuracion Captura")
	conf.update(
		{
			"modelo_ia": "claude-sonnet-5-5",
			"plantilla_nombre": "{marca} {variante} {medida}",
			"plantilla_codigo": "{prefijo}-{marca_corta}-{variante_corta}-{medida}",
			"quitar_tildes_nombre": 1,
			"estilo_mayusculas": "Título",
			"largo_max_nombre": 80,
			"largo_max_codigo": 40,
			"atributos_de_medida": "Tamaño,Tamano,Presentacion",
			"prefijo_defecto": "PRD",
			"item_group_defecto": GRUPO_BEBIDAS,
			"uom_defecto": UOM,
		}
	)
	conf.set("prefijos", [])
	conf.append("prefijos", {"item_group": GRUPO_BEBIDAS, "prefijo": "BEB"})
	conf.append("prefijos", {"item_group": GRUPO_DESCARTABLES, "prefijo": "DES"})
	conf.set("glosario_marcas", [])
	for dicho, forma, abreviatura in GLOSARIO_INICIAL:
		conf.append("glosario_marcas", {"dicho": dicho, "forma_correcta": forma, "abreviatura": abreviatura})
	conf.update(cambios)
	conf.save(ignore_permissions=True)


def nueva_captura(**campos):
	"""Captura de Producto en memoria (sin insertar), con una foto y grupo de bebidas."""
	datos = {
		"doctype": "Captura de Producto",
		"transcripcion": "producto de prueba",
		"item_group": GRUPO_BEBIDAS,
		"stock_uom": UOM,
		"fotos": [{"foto": FOTO, "es_principal": 1}],
	}
	datos.update(campos)
	return frappe.get_doc(datos)


def insertar_de(usuario: str, doc):
	"""Inserta `doc` como captura de `usuario` y deja la sesión en `usuario`.

	El capturador no tiene permiso de crear por el ORM ni por REST (solo por `api.crear_captura`, y
	sus fotos deben subirse con `api.subir_foto`). Para preparar datos de prueba la captura se inserta
	como Administrator, con dueño y `capturado_por` = `usuario`."""
	frappe.set_user("Administrator")
	doc.capturado_por = usuario
	doc.insert()
	doc.db_set("owner", usuario, update_modified=False)  # insert() siempre pone la sesión como dueño
	frappe.set_user(usuario)
	return doc


def aislar_codigos(*patrones: str) -> None:
	"""Oculta, dentro de la transacción del test, los Item y los códigos de capturas que coinciden con
	`patrones` (LIKE), para que los tests de nombres no dependan de lo que haya en el sitio (por
	ejemplo, Items creados en una prueba manual). El rollback del test lo deshace todo."""
	for patron in patrones:
		frappe.db.sql("delete from `tabItem` where name like %s", (patron,))
		frappe.db.sql(
			"update `tabCaptura de Producto` set item_code = null where item_code like %s", (patron,)
		)
		frappe.db.sql("update `tabCaptura Variante` set item_code = null where item_code like %s", (patron,))


def insertar_item(item_code: str, item_name: str, barcodes: tuple[str, ...] = ()) -> None:
	"""Item mínimo sin pasar por las validaciones de ERPNext (el sitio no tiene Company ni almacenes)."""
	if frappe.db.exists("Item", item_code):
		return
	item = frappe.new_doc("Item")
	item.update(
		{
			"name": item_code,
			"item_code": item_code,
			"item_name": item_name,
			"item_group": GRUPO_BEBIDAS,
			"stock_uom": UOM,
		}
	)
	item.db_insert()
	for idx, barcode in enumerate(barcodes, start=1):
		fila = frappe.new_doc("Item Barcode", parent_doc=item, parentfield="barcodes")
		fila.update({"barcode": barcode, "idx": idx, "name": frappe.generate_hash(length=10)})
		fila.db_insert()


class CapturaTestCase(IntegrationTestCase):
	"""Crea los maestros una vez por clase y deshace lo que hace cada test (savepoint)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		crear_maestros()
		configurar()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.savepoint("captura_test")
		from captura_productos import api

		# El límite por minuto vive en Redis (no se deshace con el rollback): cada test empieza de cero.
		for user in ("Administrator", USUARIO_CAPTURADOR, USUARIO_CAPTURADOR_2, USUARIO_SUPERVISOR):
			for accion in api.LIMITES_POR_MINUTO:
				frappe.cache.delete(api.clave_limite(accion, user))

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="captura_test")
		super().tearDown()


def pasar_a_por_revisar(doc) -> None:
	"""Simula T2 + T3 (enviar y job de IA OK) sin el extractor."""
	doc.enviar()
	doc.reload()
	doc.marcar_procesada()
	doc.reload()
