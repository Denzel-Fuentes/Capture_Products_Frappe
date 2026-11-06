"""Sitios existentes: la IA pasa a usar la API local (Claude Code en el mismo servidor).

Los campos `proveedor_ia` y `timeout_ia` son nuevos, así que en un Single ya instalado quedan vacíos;
se fija "API local" (el camino "Anthropic directo" sigue disponible desde Configuración) y el timeout
por defecto. La URL y el token de la API local van en site_config, no acá."""

import frappe

DOCTYPE = "Configuracion Captura"


def execute() -> None:
	frappe.db.set_single_value(DOCTYPE, "proveedor_ia", "API local")
	if not frappe.db.get_single_value(DOCTYPE, "timeout_ia"):
		frappe.db.set_single_value(DOCTYPE, "timeout_ia", 180)
