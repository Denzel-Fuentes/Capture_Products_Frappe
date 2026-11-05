# Copyright (c) 2026, Estacion Tradicion and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from captura_productos import naming, validation

ROLES_SUPERVISOR = frozenset({"Item Manager", "Stock Manager", "System Manager"})

# Transiciones de la sección 3 (T1 es la creación en Borrador).
TRANSICIONES = frozenset(
	{
		("Borrador", "Procesando"),  # T2 Enviar
		("Procesando", "Por revisar"),  # T3 job OK
		("Procesando", "Error"),  # T4 job falla
		("Error", "Procesando"),  # T5 Reprocesar / Reintentar
		("Por revisar", "Procesando"),  # T6 Reprocesar IA
		("Por revisar", "Aprobado"),  # T7 Aprobar
		("Aprobado", "Por revisar"),  # T8 Volver a revisión
		("Aprobado", "Exportado"),  # T9 Exportar
		("Exportado", "Aprobado"),  # T10 Marcar para re-exportar
		("Aprobado", "Creado"),  # T11 Crear Item
		("Exportado", "Creado"),  # T11 Crear Item
	}
)

LARGO_MIN_TRANSCRIPCION = 3

# Límites de la spec (secciones 2, 4 y 8). Se validan en validate() para que ninguna vía (API REST,
# frappe.client, /captura o el Desk) los saltee.
LARGO_MAX_TRANSCRIPCION = 1000
MAX_FOTOS = 6
MAX_CODIGOS = 5
MAX_VARIANTES = 20


def es_supervisor(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(ROLES_SUPERVISOR.intersection(frappe.get_roles(user)))


class CapturadeProducto(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from captura_productos.captura_productos.doctype.captura_atributo.captura_atributo import (
			CapturaAtributo,
		)
		from captura_productos.captura_productos.doctype.captura_codigo_barras.captura_codigo_barras import (
			CapturaCodigoBarras,
		)
		from captura_productos.captura_productos.doctype.captura_foto.captura_foto import CapturaFoto
		from captura_productos.captura_productos.doctype.captura_variante.captura_variante import (
			CapturaVariante,
		)

		advertencias_ia: DF.SmallText | None
		aprobado_en: DF.Datetime | None
		aprobado_por: DF.Link | None
		atributos: DF.Table[CapturaAtributo]
		capturado_por: DF.Link
		client_uuid: DF.Data | None
		codigos_barras: DF.Table[CapturaCodigoBarras]
		confianza_ia: DF.Percent
		descripcion: DF.SmallText | None
		error_log: DF.LongText | None
		estado: DF.Literal[
			"Borrador", "Procesando", "Por revisar", "Aprobado", "Exportado", "Creado", "Error"
		]
		exportado_en: DF.Datetime | None
		fecha_captura: DF.Datetime
		fila_sheet: DF.Int
		fotos: DF.Table[CapturaFoto]
		hoja_exportacion: DF.Data | None
		imagen_principal: DF.AttachImage | None
		intentos_ia: DF.Int
		item: DF.Link | None
		item_code: DF.Data | None
		item_group: DF.Link | None
		item_name: DF.Data | None
		marca: DF.Data | None
		marca_corta: DF.Data | None
		medida_unidad: DF.Literal["", "ml", "L", "g", "kg", "u"]
		medida_valor: DF.Float
		mensaje_capturador: DF.SmallText | None
		metodo_entrada: DF.Literal["", "Voz", "Texto"]
		modelo_ia: DF.Data | None
		reintentar_ia_en: DF.Datetime | None
		reintentos_ia: DF.Int
		respuesta_ia: DF.Code | None
		sin_codigo: DF.Check
		stock_uom: DF.Link | None
		tiene_variantes: DF.Check
		tipo_producto: DF.Data | None
		transcripcion: DF.SmallText | None
		variante_corta: DF.Data | None
		variante_descripcion: DF.Data | None
		variantes: DF.Table[CapturaVariante]
	# end: auto-generated types

	# --- Ciclo de vida ------------------------------------------------------------------------------

	def before_insert(self) -> None:
		if not self.flags.permitir_cambio_estado:
			self.estado = "Borrador"
		# Solo un supervisor puede registrar una captura a nombre de otra persona.
		if not self.capturado_por or not es_supervisor():
			self.capturado_por = frappe.session.user

	def validate(self) -> None:
		self._validar_cambio_de_estado()
		self._validar_edicion_del_capturador()
		self.client_uuid = (self.client_uuid or "").strip() or None
		self.transcripcion = (self.transcripcion or "").strip() or None
		self._validar_limites()
		self._validar_fotos_adjuntas()
		if not self.stock_uom:
			self.stock_uom = frappe.db.get_single_value("Configuracion Captura", "uom_defecto") or None
		self._normalizar_fotos()
		if self.flags.omitir_validacion_captura:
			# Cambio de estado sin cambios de datos (ya se validaron al guardarlos): no se repite la
			# validación completa, que consulta Item, Brand y otras capturas.
			return
		advertencias = validation.validar_captura(self)
		self.advertencias_ia = validation.fusionar_advertencias(self.advertencias_ia, advertencias) or None

	def on_trash(self) -> None:
		if self.estado == "Creado":
			frappe.throw(_("Una captura en estado Creado no se puede borrar."))

	def _validar_cambio_de_estado(self) -> None:
		if self.flags.permitir_cambio_estado:
			return
		anterior = self.get_doc_before_save()
		if self.is_new() or not anterior:
			if self.estado != "Borrador":
				frappe.throw(_("Una captura nueva empieza en estado Borrador."))
			return
		if anterior.estado != self.estado:
			frappe.throw(_("El estado no se cambia a mano: use los botones del formulario."))

	def _validar_edicion_del_capturador(self) -> None:
		if self.flags.permitir_cambio_estado or self.is_new() or es_supervisor():
			return
		anterior = self.get_doc_before_save()
		if anterior and anterior.estado != "Borrador":
			frappe.throw(_("Esta captura ya se envió y no se puede modificar."), frappe.PermissionError)

	def _validar_limites(self) -> None:
		textos = [self.transcripcion] + [v.transcripcion for v in self.variantes]
		if any(len(t or "") > LARGO_MAX_TRANSCRIPCION for t in textos):
			frappe.throw(_("Lo dictado no puede superar {0} caracteres.").format(LARGO_MAX_TRANSCRIPCION))
		for tabla, maximo, que in (
			(self.fotos, MAX_FOTOS, _("fotos")),
			(self.codigos_barras, MAX_CODIGOS, _("códigos de barras")),
			(self.variantes, MAX_VARIANTES, _("variantes")),
		):
			if len(tabla) > maximo:
				frappe.throw(_("Demasiadas {0}: el máximo es {1}.").format(que, maximo))

	def _validar_fotos_adjuntas(self) -> None:
		"""Quien no es supervisor solo puede usar fotos adjuntas a esta misma captura (subidas con
		`subir_foto`): así no puede apuntar `foto` a un archivo ajeno o privado para que la IA lo lea.
		Solo se revisan las URL nuevas o cambiadas (las que ya estaban se revisaron al guardarlas)."""
		if es_supervisor():
			return
		urls = {f.foto for f in self.fotos if f.foto} | {v.foto for v in self.variantes if v.foto}
		anterior = self.get_doc_before_save()
		if anterior:
			urls -= {f.foto for f in anterior.fotos if f.foto} | {v.foto for v in anterior.variantes if v.foto}
		if not urls:
			return
		adjuntas = set()
		if not self.is_new():
			adjuntas = set(
				frappe.get_all(
					"File",
					filters={
						"attached_to_doctype": self.doctype,
						"attached_to_name": self.name,
						"file_url": ["in", list(urls)],
					},
					pluck="file_url",
				)
			)
		if urls - adjuntas:
			frappe.throw(
				_("Una de las fotos no se subió a esta captura. Vuelve a intentar el envío."),
				frappe.PermissionError,
			)

	def _normalizar_fotos(self) -> None:
		"""Exactamente una foto principal (la primera si no hay) e `imagen_principal` copiada de ella."""
		fotos = [f for f in self.fotos if f.foto]
		principal = next((f for f in fotos if f.es_principal), fotos[0] if fotos else None)
		for fila in self.fotos:
			fila.es_principal = 1 if fila is principal else 0
		self.imagen_principal = principal.foto if principal else None

	# --- Estados ------------------------------------------------------------------------------------

	def bloquear_y_verificar_estado(self) -> None:
		"""Bloquea la fila hasta el fin de la transacción y comprueba que el estado guardado sea el de
		este documento. Evita que dos pedidos simultáneos (doble clic, dos pestañas) exporten o creen el
		Item dos veces, y que un documento viejo enviado por el cliente cambie un estado que ya no tiene."""
		if self.is_new():
			return
		actual = frappe.db.get_value(self.doctype, self.name, "estado", for_update=True)
		if actual != self.estado:
			frappe.throw(
				_("La captura cambió a {0} mientras tanto: recargue la página.").format(_(actual or "")),
				frappe.TimestampMismatchError,
			)

	def _guardar(self, validar: bool) -> None:
		"""Guarda sin exigir permiso de escritura: quien llama ya comprobó rol, dueño y estado (el
		capturador solo tiene permiso de lectura sobre sus capturas)."""
		flags = self.flags
		previo = (flags.ignore_permissions, flags.omitir_validacion_captura)
		flags.ignore_permissions = True
		flags.omitir_validacion_captura = not validar
		try:
			self.save()
		finally:
			flags.ignore_permissions, flags.omitir_validacion_captura = previo

	def _cambiar_estado(self, nuevo: str, validar: bool = False) -> None:
		"""Única vía para cambiar `estado`: valida la transición y guarda.

		`validar=True` repite la validación completa (solo hace falta cuando cambiaron los datos)."""
		if (self.estado, nuevo) not in TRANSICIONES:
			frappe.throw(_("No se puede pasar de {0} a {1}.").format(_(self.estado), _(nuevo)))
		self.bloquear_y_verificar_estado()
		self.estado = nuevo
		self.flags.permitir_cambio_estado = True
		try:
			self._guardar(validar)
		finally:
			self.flags.permitir_cambio_estado = False

	def _exigir_supervisor(self) -> None:
		if not es_supervisor():
			frappe.throw(_("Solo un supervisor puede hacer esto."), frappe.PermissionError)

	def _exigir_estado(self, *estados: str) -> None:
		if self.estado not in estados:
			frappe.throw(
				_("La captura está en {0}; esta acción requiere: {1}.").format(
					_(self.estado), ", ".join(_(e) for e in estados)
				)
			)

	def enviar(self) -> str:
		"""T2: Borrador → Procesando. Exige ≥ 1 foto y transcripción de ≥ 3 caracteres.

		No está en la whitelist: el capturador envía con `api.enviar_captura` (que aplica el límite por
		minuto) y el supervisor, con `enviar_desde_formulario`."""
		if self.capturado_por != frappe.session.user and not es_supervisor():
			frappe.throw(_("No tienes permiso sobre la captura {0}.").format(self.name), frappe.PermissionError)
		self._exigir_estado("Borrador")
		faltan = []
		if not any(f.foto for f in self.fotos):
			faltan.append(_("al menos 1 foto"))
		if len((self.transcripcion or "").strip()) < LARGO_MIN_TRANSCRIPCION:
			faltan.append(_("lo que se dictó (mínimo {0} caracteres)").format(LARGO_MIN_TRANSCRIPCION))
		if faltan:
			frappe.throw(_("Para enviar falta: {0}.").format(", ".join(faltan)))
		self._reiniciar_reintentos()
		self._cambiar_estado("Procesando")

		from captura_productos.ai import extractor

		extractor.encolar_procesamiento(self.name)
		return self.estado

	@frappe.whitelist()
	def enviar_desde_formulario(self) -> str:
		"""Botón "Enviar" del Desk (solo supervisores)."""
		from captura_productos.api import limitar

		self._exigir_supervisor()
		limitar("enviar")
		return self.enviar()

	def marcar_procesada(self) -> None:
		"""T3 (la llama el job de IA después de cargar los campos extraídos): compone nombre y código."""
		self._exigir_estado("Procesando")
		self.mensaje_capturador = None
		self._reiniciar_reintentos()
		naming.recalcular_nombres(self)
		self._cambiar_estado("Por revisar", validar=True)

	def marcar_error(self, detalle_tecnico: str, mensaje_simple: str | None = None) -> None:
		"""T4: el job de IA falló. `detalle_tecnico` nunca debe contener secretos."""
		self._exigir_estado("Procesando")
		self.error_log = detalle_tecnico
		self.mensaje_capturador = mensaje_simple or _("No se pudo procesar. El supervisor lo revisará.")
		self._reiniciar_reintentos()
		self._cambiar_estado("Error")

	def _reiniciar_reintentos(self) -> None:
		self.reintentos_ia = 0
		self.reintentar_ia_en = None

	@frappe.whitelist()
	def recalcular_nombre(self, guardar: int | str = 1) -> dict:
		"""HU-08: recompone item_name e item_code del padre y de las variantes con la plantilla vigente.

		Con `guardar=0` solo calcula (para que el formulario pida confirmación si algo cambia)."""
		self._exigir_supervisor()
		self._exigir_estado("Por revisar", "Aprobado")
		guardar = frappe.utils.cint(guardar)
		# En modo vista previa se calcula sobre una copia: run_doc_method devuelve `self` al formulario.
		destino = self if guardar else frappe.get_doc(self.as_dict())
		antes = self._nombres_actuales()
		advertencias = naming.recalcular_nombres(destino)
		despues = destino._nombres_actuales()
		if guardar:
			self.save()
		return {"cambia": antes != despues, "antes": antes, "despues": despues, "advertencias": advertencias}

	def _nombres_actuales(self) -> list[tuple[str | None, str | None]]:
		filas = [(self.item_name, self.item_code)]
		if self.tiene_variantes:
			filas += [(v.item_name, v.item_code) for v in self.variantes]
		return filas

	@frappe.whitelist()
	def aprobar(self) -> str:
		"""T7: Por revisar → Aprobado si no hay errores bloqueantes (HU-10)."""
		self._exigir_supervisor()
		self._exigir_estado("Por revisar")
		advertencias = validation.validar_captura(self, para_aprobar=True)
		bloqueantes = validation.bloqueantes_de(advertencias)
		if bloqueantes:
			frappe.throw(
				"<ul>" + "".join(f"<li>{frappe.utils.escape_html(b)}</li>" for b in bloqueantes) + "</ul>",
				title=_("No se puede aprobar"),
			)
		self.advertencias_ia = validation.fusionar_advertencias(self.advertencias_ia, advertencias) or None
		self.aprobado_por = frappe.session.user
		self.aprobado_en = now_datetime()
		self._cambiar_estado("Aprobado")
		return self.estado

	@frappe.whitelist()
	def volver_a_revision(self) -> str:
		"""T8: Aprobado → Por revisar."""
		self._exigir_supervisor()
		self._exigir_estado("Aprobado")
		self.aprobado_por = None
		self.aprobado_en = None
		self._cambiar_estado("Por revisar")
		return self.estado

	@frappe.whitelist()
	def marcar_para_reexportar(self) -> str:
		"""T10: Exportado → Aprobado; limpia la referencia al Sheet."""
		self._exigir_supervisor()
		self._exigir_estado("Exportado")
		self.hoja_exportacion = None
		self.fila_sheet = 0
		self.exportado_en = None
		self._cambiar_estado("Aprobado")
		return self.estado

	@frappe.whitelist()
	def reprocesar_ia(self) -> str:
		"""T5/T6 desde el Desk (solo supervisores): vuelve a Procesando y encola la IA.

		También se permite desde Procesando si no hay un job en cola ni corriendo (captura atascada
		porque el worker se cayó o Redis no recibió el job)."""
		from captura_productos.ai import extractor
		from captura_productos.api import limitar

		self._exigir_supervisor()
		self._exigir_estado("Por revisar", "Error", "Procesando")
		limitar("enviar")
		if self.estado == "Procesando" and extractor.job_en_cola(self.name):
			frappe.throw(_("La IA ya está procesando esta captura: espere a que termine."))
		return self._reprocesar()

	def reintentar(self) -> str:
		"""HU-09 "Reintentar" (api.reintentar_captura): el dueño, con su captura en Error; el
		supervisor, también desde Por revisar. No está en la whitelist: el límite lo aplica la API."""
		self._exigir_estado("Por revisar", "Error")
		if not es_supervisor() and not (self.estado == "Error" and self.capturado_por == frappe.session.user):
			frappe.throw(_("Solo un supervisor puede reprocesar esta captura."), frappe.PermissionError)
		return self._reprocesar()

	def _reprocesar(self) -> str:
		from captura_productos.ai import extractor

		self.error_log = None
		self.mensaje_capturador = None
		self._reiniciar_reintentos()
		if self.estado == "Procesando":
			self.bloquear_y_verificar_estado()
			self._guardar(validar=False)
		else:
			self._cambiar_estado("Procesando")
		extractor.encolar_procesamiento(self.name)
		return self.estado

	@frappe.whitelist()
	def exportar(self) -> dict:
		"""T9: exporta esta captura (en Aprobado) al Google Sheet."""
		self._exigir_supervisor()
		self._exigir_estado("Aprobado")
		from captura_productos.export import sheets

		resultado = sheets.exportar_capturas([self.name])
		if resultado["excluidas"]:
			frappe.throw(resultado["excluidas"][0]["motivo"], title=_("No se puede exportar"))
		self.reload()
		return resultado

	@frappe.whitelist()
	def crear_item(self) -> dict:
		"""T11: crea el Item (o la plantilla y sus variantes) en una sola transacción (HU-12)."""
		self._exigir_supervisor()
		self._exigir_estado("Aprobado", "Exportado")
		from captura_productos.export import erpnext_item

		return erpnext_item.crear_item(self)

