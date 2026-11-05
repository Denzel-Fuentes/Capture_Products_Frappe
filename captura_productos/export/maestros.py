"""Maestros que necesita un Item (Brand e Item Attribute Value), comunes a la exportación y a Crear Item.

Los Item Attribute no se crean: la validación exige que existan (spec sección 13).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import frappe
from frappe import _

from captura_productos import naming

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)


def valores_por_atributo(doc: CapturadeProducto) -> dict[str, dict[str, str]]:
	"""{atributo: {valor: abreviatura}} de las variantes de la captura (solo atributos de lista)."""
	if not doc.tiene_variantes:
		return {}
	resultado: dict[str, dict[str, str]] = {}
	for n, atributo in enumerate(naming.atributos_ordenados(doc), start=1):
		if frappe.db.get_value("Item Attribute", atributo, "numeric_values"):
			continue
		for fila in doc.variantes or []:
			valor = (fila.get(f"valor_{n}") or "").strip()
			if valor:
				corto = fila.get(f"valor_corto_{n}") or naming.valor_corto_auto(valor)
				resultado.setdefault(atributo, {}).setdefault(valor, corto)
	return resultado


def _valores_existentes(atributo: str) -> set[str]:
	return {
		naming.clave_comparacion(v)
		for v in frappe.get_all(
			"Item Attribute Value",
			filters={"parent": atributo, "parenttype": "Item Attribute"},
			pluck="attribute_value",
		)
	}


def maestros_faltantes(doc: CapturadeProducto) -> list[str]:
	"""Mensajes por cada Brand o Item Attribute Value que falta para importar la captura."""
	faltan = []
	if doc.marca and not frappe.db.exists("Brand", doc.marca):
		faltan.append(_("Falta la marca {0} en Brand").format(doc.marca))
	for atributo, valores in valores_por_atributo(doc).items():
		existentes = _valores_existentes(atributo)
		for valor in valores:
			if naming.clave_comparacion(valor) not in existentes:
				faltan.append(_("Falta el valor {0} en el atributo {1}").format(valor, atributo))
	return faltan


def asegurar_maestros(docs: Iterable[CapturadeProducto]) -> list[str]:
	"""Crea los Brand y los Item Attribute Value (con su abbr) que falten. Devuelve lo creado."""
	creados: list[str] = []
	nuevos_por_atributo: dict[str, dict[str, str]] = {}
	for doc in docs:
		if doc.marca and not frappe.db.exists("Brand", doc.marca):
			frappe.get_doc({"doctype": "Brand", "brand": doc.marca}).insert()
			creados.append(_("Marca {0}").format(doc.marca))
		for atributo, valores in valores_por_atributo(doc).items():
			for valor, corto in valores.items():
				nuevos_por_atributo.setdefault(atributo, {}).setdefault(valor, corto)

	for atributo, valores in nuevos_por_atributo.items():
		existentes = _valores_existentes(atributo)
		faltantes = {v: c for v, c in valores.items() if naming.clave_comparacion(v) not in existentes}
		if not faltantes:
			continue
		attr_doc = frappe.get_doc("Item Attribute", atributo)
		abreviaturas = {(f.abbr or "").upper() for f in attr_doc.item_attribute_values}
		for valor, corto in faltantes.items():
			abbr = _abreviatura_libre((corto or naming.valor_corto_auto(valor) or valor).upper(), abreviaturas)
			abreviaturas.add(abbr)
			attr_doc.append("item_attribute_values", {"attribute_value": valor, "abbr": abbr})
			creados.append(_("Valor {0} ({1}) en {2}").format(valor, abbr, atributo))
		attr_doc.save()
	return creados


def _abreviatura_libre(abbr: str, usadas: set[str]) -> str:
	if abbr not in usadas:
		return abbr
	n = 2
	while f"{abbr}{n}" in usadas:
		n += 1
	return f"{abbr}{n}"
