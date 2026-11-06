# Copyright (c) 2026, Estacion Tradicion and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

from captura_productos import naming

REGEX_PREFIJO = re.compile(r"^[A-Z]{2,5}$")
REGEX_ABREVIATURA = re.compile(r"^[A-Z0-9]{2,10}$")


class ConfiguracionCaptura(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from captura_productos.captura_productos.doctype.glosario_de_marcas.glosario_de_marcas import (
			GlosariodeMarcas,
		)
		from captura_productos.captura_productos.doctype.prefijo_por_grupo.prefijo_por_grupo import (
			PrefijoporGrupo,
		)

		almacen_defecto: DF.Link | None
		anthropic_api_key: DF.Password | None
		atributos_de_medida: DF.Data | None
		crear_maestros_al_exportar: DF.Check
		empresa: DF.Link | None
		estilo_mayusculas: DF.Literal["Título", "Como se dictó"]
		glosario_marcas: DF.Table[GlosariodeMarcas]
		google_service_account_json: DF.Password | None
		idioma_voz: DF.Literal["es-BO", "es-419", "es-ES"]
		instrucciones_extra_ia: DF.LongText | None
		is_purchase_item: DF.Check
		is_sales_item: DF.Check
		is_stock_item: DF.Check
		item_group_defecto: DF.Link
		largo_max_codigo: DF.Int
		largo_max_nombre: DF.Int
		max_fotos_ia: DF.Int
		modelo_ia: DF.Data
		pestana_sheet: DF.Data
		plantilla_codigo: DF.Data
		plantilla_nombre: DF.Data
		prefijo_defecto: DF.Data
		prefijos: DF.Table[PrefijoporGrupo]
		proveedor_ia: DF.Literal["API local", "Anthropic directo"]
		quitar_tildes_nombre: DF.Check
		spreadsheet_id: DF.Data | None
		timeout_ia: DF.Int
		timeout_ia_seg: DF.Int
		url_base_imagenes: DF.Data | None
		uom_defecto: DF.Link
	# end: auto-generated types

	@frappe.whitelist()
	def probar_conexion_ia(self) -> str:
		"""Botón "Probar conexión IA" (HU-14): con la API local, que el servicio responda y que el token
		sirva (consulta mínima); con Anthropic directo, la API key y el modelo guardados."""
		from captura_productos.ai import extractor

		frappe.only_for("System Manager")
		try:
			if extractor.proveedor_de(self) == extractor.PROVEEDOR_LOCAL:
				from captura_productos.ai import cliente_local

				return cliente_local.probar_conexion()
			api_key = self.get_password("anthropic_api_key", raise_exception=False)
			if not api_key:
				frappe.throw(_("Falta la API key de Anthropic: cárguela y guarde antes de probar."))
			return extractor.probar_conexion(api_key, (self.modelo_ia or extractor.MODELO_DEFECTO).strip())
		except extractor.ErrorIA as e:
			frappe.throw(e.detalle, title=_("Conexión IA"))

	@frappe.whitelist()
	def probar_google_sheet(self) -> str:
		"""Botón "Probar Google Sheet" (HU-14): abre la hoja con la cuenta de servicio y verifica que
		pueda editarla."""
		from captura_productos.export import sheets

		frappe.only_for("System Manager")
		try:
			return sheets.probar_google_sheet(self)
		except sheets.ErrorExportacion as e:
			frappe.log_error(title=_("Probar Google Sheet"), message=e.detalle)
			frappe.throw(e.mensaje, title=_("Google Sheet"))

	def validate(self) -> None:
		self._validar_timeout_ia()
		self._validar_plantillas()
		self._validar_largos()
		self._validar_prefijos()
		self._validar_glosario()

	def _validar_timeout_ia(self) -> None:
		if self.proveedor_ia == "API local" and not 30 <= (self.timeout_ia or 0) <= 900:
			frappe.throw(_("El timeout de la API local debe estar entre 30 y 900 segundos."))

	def _validar_plantillas(self) -> None:
		for plantilla in (self.plantilla_nombre, self.plantilla_codigo):
			for placeholder in naming.placeholders_invalidos(plantilla):
				frappe.throw(_("Placeholder no válido: {0}").format("{" + placeholder + "}"))
		if self.plantilla_codigo and not naming.placeholders_de(self.plantilla_codigo):
			frappe.throw(_("La plantilla de código debe tener al menos un placeholder."))

	def _validar_largos(self) -> None:
		if not 10 <= (self.largo_max_nombre or 0) <= 140:
			frappe.throw(_("El largo máximo del nombre debe estar entre 10 y 140."))
		if not 10 <= (self.largo_max_codigo or 0) <= 40:
			frappe.throw(_("El largo máximo del código debe estar entre 10 y 40."))

	def _validar_prefijos(self) -> None:
		self.prefijo_defecto = (self.prefijo_defecto or "").strip().upper()
		if not REGEX_PREFIJO.match(self.prefijo_defecto):
			frappe.throw(_("El prefijo por defecto debe tener de 2 a 5 letras A-Z."))
		vistos: set[str] = set()
		for fila in self.prefijos:
			fila.prefijo = naming.mayusculas_ascii(fila.prefijo).strip()
			if not REGEX_PREFIJO.match(fila.prefijo):
				frappe.throw(_("Fila {0}: el prefijo debe tener de 2 a 5 letras A-Z.").format(fila.idx))
			if fila.item_group in vistos:
				frappe.throw(_("Fila {0}: el grupo {1} está repetido.").format(fila.idx, fila.item_group))
			vistos.add(fila.item_group)

	def _validar_glosario(self) -> None:
		for fila in self.glosario_marcas:
			if fila.abreviatura:
				fila.abreviatura = naming.mayusculas_ascii(fila.abreviatura).strip()
				if not REGEX_ABREVIATURA.match(fila.abreviatura):
					frappe.throw(
						_(
							"Glosario, fila {0}: la abreviatura debe tener de 2 a 10 caracteres A-Z o 0-9."
						).format(fila.idx)
					)
