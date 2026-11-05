"""Datos por defecto de Configuracion Captura (idempotente: solo completa lo que falta)."""

from __future__ import annotations

import frappe

from captura_productos import naming

GRUPO_BEBIDAS = "1.9 Bebidas Embotelladas"

VALORES_POR_DEFECTO = {
	"modelo_ia": "claude-sonnet-5-5",
	"max_fotos_ia": 4,
	"timeout_ia_seg": 60,
	"idioma_voz": "es-BO",
	"pestana_sheet": "Items",
	"plantilla_nombre": naming.PLANTILLA_NOMBRE_DEFECTO,
	"plantilla_codigo": naming.PLANTILLA_CODIGO_DEFECTO,
	"estilo_mayusculas": "Título",
	"largo_max_nombre": naming.LARGO_MAX_NOMBRE_DEFECTO,
	"largo_max_codigo": naming.LARGO_MAX_CODIGO_DEFECTO,
	"atributos_de_medida": naming.ATRIBUTOS_MEDIDA_DEFECTO,
	"prefijo_defecto": naming.PREFIJO_DEFECTO,
}

# Checks con default 1: solo se fijan la primera vez (después pueden estar en 0 a propósito).
CHECKS_POR_DEFECTO = (
	"crear_maestros_al_exportar",
	"quitar_tildes_nombre",
	"is_stock_item",
	"is_sales_item",
	"is_purchase_item",
)

# Enlaces que se cargan solo si el registro existe en el sitio.
ENLACES_POR_DEFECTO = {
	"item_group_defecto": ("Item Group", GRUPO_BEBIDAS),
	"uom_defecto": ("UOM", "Unidad"),
	"empresa": ("Company", "Estacion Tradicion"),
	"almacen_defecto": ("Warehouse", "Depósito Bebidas - ET"),
}

PREFIJOS_POR_DEFECTO = {GRUPO_BEBIDAS: "BEB"}

# Sección 4.7 (las formas dictadas se derivan de la forma correcta y de HU-14).
GLOSARIO_INICIAL = (
	("coca cola, cocacola, coca", "Coca-Cola", "COCA"),
	("del valle, delvalle", "Del Valle", "VALLE"),
	("frut all, frutall, fruta all", "Frut-All", "FRUTALL"),
	("super frut, superfrut", "Super Frut", "SUPERFRUT"),
	("del tropico, tropico", "Del Tropico", "TROPICO"),
	("la cabana, la cabaña, cabana", "La Cabana", "CABANA"),
	("guarana antarctica, guarana", "Guarana Antarctica", "GUARANA"),
	("aquarius, acuarius", "Aquarius", None),
	("pepsi, pepsi cola", "Pepsi", None),
	("mendocina", "Mendocina", None),
)


def after_install() -> None:
	aplicar_configuracion_por_defecto(primera_vez=True)


def aplicar_configuracion_por_defecto(primera_vez: bool = False) -> None:
	"""Completa los campos vacíos de Configuracion Captura. `primera_vez` también carga los checks
	en 1 y el glosario inicial (si está vacío)."""
	conf = frappe.get_single("Configuracion Captura")

	for campo, valor in VALORES_POR_DEFECTO.items():
		if not conf.get(campo):
			conf.set(campo, valor)

	if primera_vez:
		for campo in CHECKS_POR_DEFECTO:
			conf.set(campo, 1)

	for campo, (doctype, nombre) in ENLACES_POR_DEFECTO.items():
		if not conf.get(campo) and frappe.db.exists(doctype, nombre):
			conf.set(campo, nombre)

	grupos_con_prefijo = {p.item_group for p in conf.prefijos}
	for grupo, prefijo in PREFIJOS_POR_DEFECTO.items():
		if grupo not in grupos_con_prefijo and frappe.db.exists("Item Group", grupo):
			conf.append("prefijos", {"item_group": grupo, "prefijo": prefijo})

	if primera_vez and not conf.glosario_marcas:
		for dicho, forma_correcta, abreviatura in GLOSARIO_INICIAL:
			conf.append(
				"glosario_marcas",
				{"dicho": dicho, "forma_correcta": forma_correcta, "abreviatura": abreviatura},
			)

	# Grupo y UOM por defecto son obligatorios pero pueden no existir todavía en el sitio.
	conf.flags.ignore_mandatory = True
	conf.flags.ignore_permissions = True
	conf.save()
