"""Crear Item directo desde una captura (spec HU-12 y T11).

Antes de crear se revalida la captura (HU-10) y se bloquea su fila (evita crear dos veces con un
doble clic). Si algo falla se deshace todo (maestros, plantilla y variantes), la captura no cambia de
estado y el detalle queda en `error_log`, escrito después del rollback.

Los Brand y valores de atributo que falten se crean solo si `crear_maestros_al_exportar = 1` (spec
13); si no, error claro sin crear nada.

Si un Item con el mismo código ya existe, se enlaza en lugar de crearlo solo si es el mismo producto:
tiene todos los códigos de barras de la captura (porque se importó desde el Sheet) o, si la captura no
tiene códigos, coinciden nombre, grupo y (en plantillas) atributos. Si no, error claro.
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

import frappe
from erpnext.controllers.item_variant import create_variant
from frappe import _
from frappe.utils import cint

from captura_productos import naming, validation
from captura_productos.export import maestros

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

SAVEPOINT = "captura_crear_item"


class ErrorCrearItem(frappe.ValidationError):
	pass


def crear_item(doc: CapturadeProducto) -> dict:
	"""T11: crea (o enlaza) el Item simple, o la plantilla y sus variantes, y pasa a Creado."""
	frappe.has_permission("Item", ptype="create", throw=True)
	doc.bloquear_y_verificar_estado()
	frappe.db.savepoint(SAVEPOINT)
	try:
		resultado = _crear(doc, frappe.get_single("Configuracion Captura"))
		doc.error_log = None
		doc._cambiar_estado("Creado", validar=True)
	except Exception as e:
		mensaje = str(e) if isinstance(e, frappe.ValidationError) else _("Error inesperado: {0}").format(e)
		detalle = f"{mensaje}\n\n{traceback.format_exc()}"
		deshacer_y_anotar_error(SAVEPOINT, [doc.name], detalle, _("Crear Item desde {0}").format(doc.name))
		frappe.clear_messages()
		frappe.throw(mensaje, title=_("No se pudo crear el Item"))
	return resultado


def deshacer_y_anotar_error(savepoint: str, capturas: list[str], detalle: str, titulo: str) -> None:
	"""Deshace lo hecho y deja `detalle` en `error_log` de las capturas, confirmado aunque el pedido
	termine con error.

	Fuera de los tests se hace un rollback completo antes de escribir y confirmar: con un rollback al
	savepoint, el commit también confirmaría cualquier cambio anterior del mismo pedido. En los tests
	solo se vuelve al savepoint (el test deshace todo al terminar y no se confirma nada)."""
	if frappe.in_test:
		frappe.db.rollback(save_point=savepoint)
	else:
		frappe.db.rollback()
	for nombre in capturas:
		frappe.db.set_value("Captura de Producto", nombre, "error_log", detalle, update_modified=False)
	frappe.log_error(title=titulo, message=detalle)
	if not frappe.in_test:
		frappe.db.commit()


def _crear(doc: CapturadeProducto, conf) -> dict:
	_revalidar(doc)
	if cint(conf.crear_maestros_al_exportar):
		maestros.asegurar_maestros([doc])
	else:
		faltan = maestros.maestros_faltantes(doc)
		if faltan:
			raise ErrorCrearItem(
				_(
					"Faltan maestros: {0}. Créelos a mano o active «Crear Brand y atributos faltantes "
					"al exportar» en Configuración Captura."
				).format("; ".join(faltan))
			)
	creados, enlazados = [], []

	if not doc.tiene_variantes:
		codigos = [c.barcode for c in doc.codigos_barras if c.barcode]
		nombre, nuevo = _existente_o_nuevo(doc.item_code, codigos, plantilla=False, esperado=doc)
		if nuevo:
			item = _nuevo_item(doc, conf)
			for fila in doc.codigos_barras:
				if fila.barcode:
					item.append("barcodes", {"barcode": fila.barcode, "barcode_type": fila.barcode_type or ""})
			item.insert()
			nombre = item.name
		(creados if nuevo else enlazados).append(nombre)
		doc.item = nombre
		return {"item": nombre, "creados": creados, "enlazados": enlazados}

	atributos = naming.atributos_ordenados(doc)
	plantilla, nuevo = _existente_o_nuevo(
		doc.item_code, [], plantilla=True, esperado=doc, atributos=atributos
	)
	if nuevo:
		item = _nuevo_item(doc, conf)
		item.has_variants = 1
		item.variant_based_on = "Item Attribute"
		for atributo in atributos:
			item.append("attributes", _fila_atributo_plantilla(atributo))
		item.insert()
		plantilla = item.name
	(creados if nuevo else enlazados).append(plantilla)
	doc.item = plantilla

	for fila in doc.variantes:
		codigos = [fila.barcode] if fila.barcode else []
		nombre, nuevo = _existente_o_nuevo(
			fila.item_code, codigos, plantilla=False, esperado=fila, item_group=doc.item_group, variante_de=plantilla
		)
		if nuevo:
			valores = {a: fila.get(f"valor_{n}") for n, a in enumerate(atributos, start=1)}
			variante = create_variant(plantilla, valores)
			variante.item_code = fila.item_code
			variante.item_name = fila.item_name
			variante.description = doc.descripcion or fila.item_name
			variante.image = fila.foto or doc.imagen_principal
			variante.set("barcodes", [])
			if fila.barcode:
				variante.append("barcodes", {"barcode": fila.barcode, "barcode_type": fila.barcode_type or ""})
			variante.insert()
			nombre = variante.name
		(creados if nuevo else enlazados).append(nombre)
		fila.item = nombre
	return {"item": plantilla, "creados": creados, "enlazados": enlazados}


def _revalidar(doc: CapturadeProducto) -> None:
	"""HU-10 de nuevo: algo pudo cambiar desde que se aprobó (otro Item con el mismo código de barras,
	un atributo deshabilitado...). Los Items con el código de la propia captura no bloquean: son los
	que se enlazan si corresponden (ver `_existente_o_nuevo`)."""
	advertencias = validation.validar_captura(doc, para_aprobar=True, permitir_items_propios=True)
	bloqueantes = validation.bloqueantes_de(advertencias)
	if bloqueantes:
		raise ErrorCrearItem(_("La captura ya no cumple las reglas para aprobar: {0}").format("; ".join(bloqueantes)))


def _existente_o_nuevo(
	item_code: str,
	codigos: list[str],
	plantilla: bool,
	esperado,
	item_group: str | None = None,
	atributos: list[str] | None = None,
	variante_de: str | None = None,
) -> tuple[str | None, bool]:
	"""(nombre del Item existente, False) si ya existe y es el mismo producto; (None, True) si hay que
	crearlo. Lanza ErrorCrearItem si existe pero no se puede confirmar que corresponda a la captura.

	`esperado` es la captura o la fila de variante (item_name y, en la captura, item_group)."""
	existente = frappe.db.get_value(
		"Item", item_code, ["name", "has_variants", "item_name", "item_group", "variant_of"], as_dict=True
	)
	if not existente:
		return None, True
	if plantilla and not existente.has_variants:
		raise ErrorCrearItem(_("Ya existe el Item {0} y no es una plantilla con variantes.").format(item_code))
	if not plantilla and existente.has_variants:
		raise ErrorCrearItem(_("Ya existe el Item {0} y es una plantilla con variantes.").format(item_code))

	if codigos:
		actuales = set(
			frappe.get_all("Item Barcode", filters={"parent": existente.name, "parenttype": "Item"}, pluck="barcode")
		)
		faltan = [c for c in codigos if c not in actuales]
		if faltan:
			raise ErrorCrearItem(
				_("Ya existe el Item {0} con otros códigos de barras (no tiene {1}).").format(
					item_code, ", ".join(faltan)
				)
			)
	if variante_de and existente.variant_of != variante_de:
		raise ErrorCrearItem(
			_("Ya existe el Item {0}, pero no es variante de {1}.").format(item_code, variante_de)
		)
	if codigos:
		return existente.name, False

	# Sin códigos de barras no hay forma segura de saber si es el mismo producto: se exige que
	# coincidan nombre, grupo y (en plantillas) los atributos.
	grupo = item_group or esperado.get("item_group")
	diferencias = []
	if (existente.item_name or "") != (esperado.item_name or ""):
		diferencias.append(_("nombre «{0}»").format(existente.item_name))
	if grupo and existente.item_group != grupo:
		diferencias.append(_("grupo {0}").format(existente.item_group))
	if plantilla:
		actuales = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": existente.name, "parenttype": "Item"},
			pluck="attribute",
			order_by="idx asc",
		)
		if sorted(actuales) != sorted(atributos or []):
			diferencias.append(_("atributos {0}").format(", ".join(actuales) or "-"))
	if diferencias:
		raise ErrorCrearItem(
			_(
				"Ya existe el Item {0}, pero no coincide ({1}). Como la captura no tiene códigos de barras para "
				"comparar, no se enlaza solo: cambie el código (Recalcular nombre o a mano) o revise el Item."
			).format(item_code, ", ".join(diferencias))
		)
	return existente.name, False


def _nuevo_item(doc: CapturadeProducto, conf):
	item = frappe.new_doc("Item")
	item.update(
		{
			"item_code": doc.item_code,
			"item_name": doc.item_name,
			"item_group": doc.item_group,
			"stock_uom": doc.stock_uom,
			"brand": doc.marca or None,
			"description": doc.descripcion or doc.item_name,
			"image": doc.imagen_principal or None,
			"is_stock_item": cint(conf.is_stock_item),
			"is_sales_item": cint(conf.is_sales_item),
			"is_purchase_item": cint(conf.is_purchase_item),
		}
	)
	if conf.empresa:
		item.append(
			"item_defaults",
			{"company": conf.empresa, "default_warehouse": conf.almacen_defecto or None},
		)
	meta = frappe.get_meta("Item")
	if meta.has_field("custom_formato_venta"):
		formato = next((p.formato_venta for p in conf.prefijos if p.item_group == doc.item_group), None)
		item.set("custom_formato_venta", formato or None)
	return item


def _fila_atributo_plantilla(atributo: str) -> dict:
	datos = frappe.db.get_value(
		"Item Attribute", atributo, ["numeric_values", "from_range", "to_range", "increment"], as_dict=True
	)
	fila = {"attribute": atributo}
	if datos and datos.numeric_values:
		fila.update(
			numeric_values=1, from_range=datos.from_range, to_range=datos.to_range, increment=datos.increment
		)
	return fila
