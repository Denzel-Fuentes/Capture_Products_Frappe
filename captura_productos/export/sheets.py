"""Exportación a Google Sheet compatible con Data Import de ERPNext v16 (spec HU-11, secciones 7 y 13).

Partes:
- Funciones puras (`encabezados`, `filas_item`, `construir_filas`): arman las filas sin red ni BD.
- `items_de_captura`: traduce una Captura de Producto a `ItemExport` (consulta la BD).
- `abrir_spreadsheet` / `escribir_pestanas`: gspread con cuenta de servicio.
- `exportar_capturas`: orquesta todo y pasa las capturas a Exportado. Bloquea las filas de las
  capturas (SELECT ... FOR UPDATE) mientras exporta, así dos exportaciones simultáneas no escriben las
  mismas capturas dos veces; si algo falla después de crear las pestañas, las borra.
- `exportar_aprobados`: acción masiva de la lista; encola `exportar_en_segundo_plano` (cola `long`) y
  avisa al usuario al terminar (Notification Log + evento realtime).

Pestaña 1 (`{prefijo}-{AAAAMMDD-HHMM}-1-Productos`): productos simples y plantillas.
Pestaña 2 (`…-2-Variantes`): variantes; se importa después de la 1.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_url, now_datetime

from captura_productos import naming, validation
from captura_productos.export import maestros

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

MAX_CAPTURAS = 200
SAVEPOINT = "captura_exportar"
TIMEOUT_GOOGLE = 30  # segundos por llamada a la API de Google

# Encabezados verificados contra el Data Import de v16 (spec 7.1 y 7.2).
ENCABEZADOS_BASE = (
	"Item Code",
	"Item Name",
	"Item Group",
	"Default Unit of Measure",
	"Brand",
	"Description",
	"Maintain Stock",
	"Allow Sales",
	"Allow Purchase",
	"Has Variants",
	"Variant Of",
	"Image",
	"Barcode (Barcodes)",
	"Barcode Type (Barcodes)",
	"Attribute (Variant Attributes)",
	"Attribute Value (Variant Attributes)",
)
ENCABEZADO_EMPRESA = "Company (Item Defaults)"
ENCABEZADO_ALMACEN = "Default Warehouse (Item Defaults)"
CAMPOS_CUSTOM = ("custom_plato_base", "custom_formato_venta")

# Columnas que se repiten en las filas siguientes de un mismo Item (tablas hijas).
_COLUMNAS_HIJAS = frozenset(
	{
		"Barcode (Barcodes)",
		"Barcode Type (Barcodes)",
		"Attribute (Variant Attributes)",
		"Attribute Value (Variant Attributes)",
	}
)


class ErrorExportacion(Exception):
	"""Fallo al conectar o escribir el Sheet. `mensaje` es para el usuario; `detalle`, para error_log."""

	def __init__(self, mensaje: str, detalle: str | None = None):
		super().__init__(mensaje)
		self.mensaje = mensaje
		self.detalle = detalle or mensaje


# ---------------------------------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------------------------------


@dataclass
class ItemExport:
	item_code: str
	item_name: str
	item_group: str
	stock_uom: str
	brand: str = ""
	description: str = ""
	image: str = ""
	has_variants: bool = False
	variant_of: str = ""
	barcodes: list[tuple[str, str]] = field(default_factory=list)  # (código, tipo)
	atributos: list[tuple[str, str]] = field(default_factory=list)  # (atributo, valor); valor "" en plantillas
	custom: dict[str, str] = field(default_factory=dict)
	captura: str = ""  # no se exporta: sirve para saber la fila de cada captura


@dataclass
class OpcionesExport:
	is_stock_item: int = 1
	is_sales_item: int = 1
	is_purchase_item: int = 1
	empresa: str = ""
	almacen: str = ""
	campos_custom: tuple[str, ...] = ()  # los de CAMPOS_CUSTOM que existen en Item


def encabezados(op: OpcionesExport) -> list[str]:
	columnas = list(ENCABEZADOS_BASE)
	if op.empresa:
		columnas.append(ENCABEZADO_EMPRESA)
		if op.almacen:
			columnas.append(ENCABEZADO_ALMACEN)
	columnas += [c for c in CAMPOS_CUSTOM if c in op.campos_custom]
	return columnas


def filas_item(item: ItemExport, op: OpcionesExport) -> list[list[str]]:
	"""Filas de un Item: la primera completa y las siguientes solo con columnas de tablas hijas
	(un código de barras y un atributo por fila)."""
	columnas = encabezados(op)
	padre = {
		"Item Code": item.item_code,
		"Item Name": item.item_name,
		"Item Group": item.item_group,
		"Default Unit of Measure": item.stock_uom,
		"Brand": item.brand,
		"Description": item.description,
		"Maintain Stock": str(cint(op.is_stock_item)),
		"Allow Sales": str(cint(op.is_sales_item)),
		"Allow Purchase": str(cint(op.is_purchase_item)),
		"Has Variants": "1" if item.has_variants else "0",
		"Variant Of": item.variant_of,
		"Image": item.image,
		ENCABEZADO_EMPRESA: op.empresa,
		ENCABEZADO_ALMACEN: op.almacen,
		**{c: item.custom.get(c, "") for c in CAMPOS_CUSTOM},
	}
	cantidad = max(1, len(item.barcodes), len(item.atributos))
	filas = []
	for i in range(cantidad):
		valores = dict(padre) if i == 0 else {}
		if i < len(item.barcodes):
			valores["Barcode (Barcodes)"], valores["Barcode Type (Barcodes)"] = item.barcodes[i]
		if i < len(item.atributos):
			atributo, valor = item.atributos[i]
			valores["Attribute (Variant Attributes)"] = atributo
			valores["Attribute Value (Variant Attributes)"] = valor
		filas.append([valores.get(c, "") or "" for c in columnas])
	return filas


@dataclass
class FilasExport:
	encabezados: list[str]
	productos: list[list[str]]  # pestaña 1, con encabezados
	variantes: list[list[str]]  # pestaña 2, con encabezados (vacía si no hay variantes)
	fila_por_captura: dict[str, int]  # primera fila (1 = encabezado) de cada captura en la pestaña 1


def construir_filas(
	productos: Sequence[ItemExport], variantes: Sequence[ItemExport], op: OpcionesExport
) -> FilasExport:
	columnas = encabezados(op)
	filas_productos = [columnas]
	fila_por_captura: dict[str, int] = {}
	for item in productos:
		if item.captura and item.captura not in fila_por_captura:
			fila_por_captura[item.captura] = len(filas_productos) + 1
		filas_productos += filas_item(item, op)
	filas_variantes = [columnas] if variantes else []
	for item in variantes:
		filas_variantes += filas_item(item, op)
	return FilasExport(columnas, filas_productos, filas_variantes, fila_por_captura)


def titulos_pestanas(prefijo: str, momento, existentes: set[str] = frozenset()) -> tuple[str, str]:
	"""`{prefijo}-{AAAAMMDD-HHMM}-1-Productos` y `-2-Variantes`; si ya existen, agrega -2, -3…"""
	base = f"{prefijo or 'Items'}-{momento.strftime('%Y%m%d-%H%M')}"
	candidato, n = base, 1
	while f"{candidato}-1-Productos" in existentes or f"{candidato}-2-Variantes" in existentes:
		n += 1
		candidato = f"{base}-{n}"
	return f"{candidato}-1-Productos", f"{candidato}-2-Variantes"


def url_imagen(ruta: str | None, base: str) -> str:
	"""URL absoluta pública de un archivo; vacío si es privado o si no hay URL base válida."""
	if not ruta:
		return ""
	if ruta.startswith(("http://", "https://")):
		return ruta
	if ruta.startswith("/private/") or not base:
		return ""
	return f"{base.rstrip('/')}/{ruta.lstrip('/')}"


def host_valido(url: str | None) -> bool:
	"""True si `url` es http(s) con un host que parece real: un dominio con punto, una IP o
	localhost. En un job, get_url() puede devolver hosts como http://default, que no sirven."""
	try:
		partes = urlsplit(url or "")
		host = partes.hostname or ""
	except ValueError:
		return False
	if partes.scheme not in ("http", "https") or not host:
		return False
	if host == "localhost" or ("." in host and not host.startswith(".") and not host.endswith(".")):
		return True
	try:
		ipaddress.ip_address(host)
		return True
	except ValueError:
		return False


def base_url_imagenes(conf) -> tuple[str, list[str]]:
	"""(URL base para la columna Image, avisos). Usa `url_base_imagenes` de la Configuración; si está
	vacía, la URL del sitio solo si su host parece válido. Si no, devuelve "" (columna Image vacía) y un
	aviso: mejor sin imagen que con URLs rotas en el Sheet."""
	configurada = (conf.url_base_imagenes or "").strip()
	if configurada:
		return configurada, []
	del_sitio = get_url()
	if host_valido(del_sitio):
		return del_sitio, []
	return "", [
		_(
			"Configure 'URL base de imágenes' en Configuración Captura: la URL del sitio ({0}) no es "
			"válida y la columna Image quedó vacía."
		).format(del_sitio)
	]


# ---------------------------------------------------------------------------------------------------
# Captura → ItemExport (BD)
# ---------------------------------------------------------------------------------------------------


def opciones_desde_config(conf) -> OpcionesExport:
	meta_item = frappe.get_meta("Item")
	return OpcionesExport(
		is_stock_item=cint(conf.is_stock_item),
		is_sales_item=cint(conf.is_sales_item),
		is_purchase_item=cint(conf.is_purchase_item),
		empresa=conf.empresa or "",
		almacen=(conf.almacen_defecto or "") if conf.empresa else "",
		campos_custom=tuple(c for c in CAMPOS_CUSTOM if meta_item.has_field(c)),
	)


def formato_venta(conf, item_group: str | None) -> str:
	return next((p.formato_venta or "" for p in conf.prefijos if p.item_group == item_group), "")


def items_de_captura(
	doc: CapturadeProducto, conf, op: OpcionesExport, base_url: str
) -> tuple[ItemExport, list[ItemExport]]:
	"""(producto simple o plantilla, variantes) de una captura aprobada."""
	custom = {}
	if "custom_formato_venta" in op.campos_custom:
		custom["custom_formato_venta"] = formato_venta(conf, doc.item_group)
	imagen = url_imagen(doc.imagen_principal, base_url)
	comunes = {
		"item_group": doc.item_group or "",
		"stock_uom": doc.stock_uom or "",
		"brand": doc.marca or "",
		"description": doc.descripcion or "",
		"custom": custom,
		"captura": doc.name,
	}
	atributos = naming.atributos_ordenados(doc) if doc.tiene_variantes else []
	producto = ItemExport(
		item_code=doc.item_code,
		item_name=doc.item_name,
		image=imagen,
		has_variants=bool(doc.tiene_variantes),
		barcodes=[] if doc.tiene_variantes else [(c.barcode, c.barcode_type or "") for c in doc.codigos_barras],
		atributos=[(a, "") for a in atributos],
		**comunes,
	)
	variantes = []
	if doc.tiene_variantes:
		for fila in doc.variantes:
			variantes.append(
				ItemExport(
					item_code=fila.item_code,
					item_name=fila.item_name,
					image=url_imagen(fila.foto, base_url) or imagen,
					variant_of=doc.item_code,
					barcodes=[(fila.barcode, fila.barcode_type or "")] if fila.barcode else [],
					atributos=[(a, fila.get(f"valor_{n}") or "") for n, a in enumerate(atributos, start=1)],
					**comunes,
				)
			)
	return producto, variantes


# ---------------------------------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------------------------------


def _credenciales(conf) -> dict:
	texto = conf.get_password("google_service_account_json", raise_exception=False)
	if not texto:
		raise ErrorExportacion(_("Falta el JSON de la cuenta de servicio en Configuración Captura."))
	try:
		info = json.loads(texto)
	except json.JSONDecodeError:
		raise ErrorExportacion(_("El JSON de la cuenta de servicio no es válido."))
	if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
		raise ErrorExportacion(_("El JSON de la cuenta de servicio no tiene client_email y private_key."))
	return info


def abrir_spreadsheet(conf):
	"""Devuelve (spreadsheet, email de la cuenta de servicio). Traduce los errores de Google."""
	import gspread
	from google.auth.exceptions import GoogleAuthError

	if not conf.spreadsheet_id:
		raise ErrorExportacion(_("Falta el ID del spreadsheet en Configuración Captura."))
	info = _credenciales(conf)
	email = info["client_email"]
	try:
		cliente = gspread.service_account_from_dict(info)
		cliente.set_timeout(TIMEOUT_GOOGLE)
		return cliente.open_by_key(conf.spreadsheet_id.strip()), email
	except gspread.exceptions.SpreadsheetNotFound as e:
		raise ErrorExportacion(_mensaje_compartir(email), f"SpreadsheetNotFound: {e}")
	except gspread.exceptions.APIError as e:
		raise _error_api(e, email)
	except (GoogleAuthError, ValueError) as e:
		raise ErrorExportacion(
			_("Google rechazó las credenciales de la cuenta de servicio."), f"{type(e).__name__}: {e}"
		)
	except requests.RequestException as e:
		raise _error_red(e)


def _mensaje_compartir(email: str) -> str:
	return _("Comparta la hoja con {0} como Editor").format(email)


def _error_red(e: Exception) -> ErrorExportacion:
	return ErrorExportacion(_("No se pudo conectar con Google Sheets."), f"{type(e).__name__}: {e}")


def _error_api(e, email: str) -> ErrorExportacion:
	codigo = getattr(getattr(e, "response", None), "status_code", None)
	detalle = f"APIError {codigo}: {e}"
	if codigo in (403, 404):
		return ErrorExportacion(_mensaje_compartir(email), detalle)
	return ErrorExportacion(_("Error de Google Sheets (HTTP {0}).").format(codigo), detalle)


def escribir_pestanas(
	spreadsheet, pestanas: Sequence[tuple[str, list[list[str]]]], email: str, creadas: list | None = None
) -> list:
	"""Crea cada pestaña con el tamaño justo y escribe sus valores tal cual (RAW: los códigos con 0
	adelante siguen siendo texto). Devuelve las hojas creadas; si algo falla, borra las que ya creó.

	`creadas` (opcional) es una lista donde se van agregando las hojas, para que quien llama pueda
	borrarlas si falla un paso posterior."""
	import gspread
	from gspread.utils import ValueInputOption

	creadas = [] if creadas is None else creadas
	try:
		for titulo, filas in pestanas:
			if not filas:
				continue
			hoja = spreadsheet.add_worksheet(title=titulo, rows=len(filas), cols=len(filas[0]))
			creadas.append(hoja)
			hoja.update(values=filas, range_name="A1", value_input_option=ValueInputOption.raw)
	except gspread.exceptions.APIError as e:
		borrar_pestanas(spreadsheet, creadas)
		raise _error_api(e, email)
	except requests.RequestException as e:
		borrar_pestanas(spreadsheet, creadas)
		raise _error_red(e)
	return creadas


def borrar_pestanas(spreadsheet, hojas: list) -> None:
	"""Borra las pestañas creadas en una exportación que falló (sin ocultar el error original)."""
	for hoja in reversed(hojas):
		try:
			spreadsheet.del_worksheet(hoja)
		except Exception:
			frappe.log_error(title=_("No se pudo borrar la pestaña {0}").format(getattr(hoja, "title", "")))
	hojas.clear()


def probar_google_sheet(conf) -> str:
	"""Botón "Probar Google Sheet": abre la hoja y comprueba permiso de edición sin cambiar nada."""
	import gspread

	spreadsheet, email = abrir_spreadsheet(conf)
	try:
		spreadsheet.update_title(spreadsheet.title)  # escritura sin cambios: falla si no es Editor
	except gspread.exceptions.APIError as e:
		raise _error_api(e, email)
	return _("Acceso OK a «{0}» como {1}.").format(spreadsheet.title, email)


# ---------------------------------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------------------------------


def _normalizar_seleccion(nombres: str | list[str] | None) -> list[str]:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		es_supervisor,
	)

	if not es_supervisor():
		frappe.throw(_("Solo un supervisor puede exportar."), frappe.PermissionError)
	if isinstance(nombres, str):
		nombres = frappe.parse_json(nombres)
	if not isinstance(nombres, list | tuple):
		frappe.throw(_("Seleccione al menos una captura."))
	nombres = list(dict.fromkeys(str(n) for n in nombres if n))
	if not nombres:
		frappe.throw(_("Seleccione al menos una captura."))
	if len(nombres) > MAX_CAPTURAS:
		frappe.throw(_("Se pueden exportar como máximo {0} capturas a la vez.").format(MAX_CAPTURAS))
	return nombres


def id_job_exportacion(nombres: list[str]) -> str:
	return "captura-exportar-" + frappe.generate_hash("|".join(sorted(nombres)), length=16)


@frappe.whitelist(methods=["POST"])
def exportar_aprobados(nombres: str | list[str]) -> dict:
	"""Acción masiva de la lista: encola la exportación de las capturas seleccionadas que estén en
	Aprobado. Exportar hasta 200 capturas puede tardar (Google + maestros): no se hace en el pedido
	web. Al terminar, el usuario recibe una notificación con el resultado."""
	nombres = _normalizar_seleccion(nombres)
	job_id = id_job_exportacion(nombres)
	frappe.enqueue(
		"captura_productos.export.sheets.exportar_en_segundo_plano",
		queue="long",
		timeout=900,
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
		nombres=nombres,
		usuario=frappe.session.user,
	)
	return {"encolado": True, "cantidad": len(nombres), "job_id": job_id}


def exportar_en_segundo_plano(nombres: list[str], usuario: str) -> dict | None:
	"""Job de `exportar_aprobados`: exporta y avisa a `usuario` (Notification Log + realtime)."""
	frappe.set_user(usuario)
	try:
		resultado = exportar_capturas(nombres)
	except Exception as e:
		frappe.db.rollback()
		mensaje = frappe.utils.strip_html(str(e)) if isinstance(e, frappe.ValidationError) else None
		if not mensaje:
			frappe.log_error(title=_("Exportación a Google Sheet"))
			mensaje = _("Error inesperado. El detalle quedó en el Error Log.")
		_avisar(usuario, _("No se pudo exportar a Google Sheet"), mensaje, {"ok": False, "mensaje": mensaje})
		return None
	frappe.db.commit()
	exportadas, excluidas = resultado["exportadas"], resultado["excluidas"]
	cuerpo = _("Exportadas: {0}. Excluidas: {1}.").format(len(exportadas), len(excluidas))
	if resultado.get("avisos"):
		cuerpo += " " + " ".join(resultado["avisos"])
	if resultado["hojas"]:
		cuerpo += " " + _("Pestañas: {0}.").format(", ".join(resultado["hojas"]))
	if excluidas:
		cuerpo += " " + "; ".join(f"{e['captura']}: {e['motivo']}" for e in excluidas[:20])
	_avisar(usuario, _("Exportación a Google Sheet terminada"), cuerpo, {"ok": True, **resultado})
	return resultado


def _avisar(usuario: str, asunto: str, cuerpo: str, datos: dict) -> None:
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": usuario,
			"type": "Alert",
			"document_type": "Captura de Producto",
			"subject": frappe.utils.escape_html(asunto),
			"email_content": frappe.utils.escape_html(cuerpo),
		}
	).insert(ignore_permissions=True)
	frappe.publish_realtime("captura_exportacion", datos, user=usuario, after_commit=True)
	frappe.db.commit()


def exportar_capturas(nombres: list[str]) -> dict:
	nombres = _normalizar_seleccion(nombres)
	conf = frappe.get_single("Configuracion Captura")
	crear_maestros = cint(conf.crear_maestros_al_exportar)
	docs, excluidas = _capturas_exportables(nombres, crear_maestros)
	if not docs:
		return {"exportadas": [], "excluidas": excluidas, "hojas": [], "url": None}

	frappe.db.savepoint(SAVEPOINT)
	hojas_creadas: list = []
	spreadsheet = None
	try:
		spreadsheet, email = abrir_spreadsheet(conf)
		creados = maestros.asegurar_maestros(docs) if crear_maestros else []
		op = opciones_desde_config(conf)
		base_url, avisos = base_url_imagenes(conf)
		productos, variantes = [], []
		for doc in docs:
			producto, vars_doc = items_de_captura(doc, conf, op, base_url)
			productos.append(producto)
			variantes += vars_doc
		filas = construir_filas(productos, variantes, op)
		existentes = {h.title for h in spreadsheet.worksheets()}
		titulo_1, titulo_2 = titulos_pestanas(conf.pestana_sheet, now_datetime(), existentes)
		escribir_pestanas(
			spreadsheet, [(titulo_1, filas.productos), (titulo_2, filas.variantes)], email, hojas_creadas
		)

		ahora = now_datetime()
		for doc in docs:
			doc.hoja_exportacion = f"{titulo_1}, {titulo_2}" if doc.tiene_variantes else titulo_1
			doc.fila_sheet = filas.fila_por_captura.get(doc.name, 0)
			doc.exportado_en = ahora
			doc.error_log = None
			doc._cambiar_estado("Exportado")
	except ErrorExportacion as e:
		_registrar_fallo([d.name for d in docs], e.detalle)
		frappe.throw(e.mensaje, title=_("No se pudo exportar"))
	except Exception as e:
		# Falló algo después de escribir el Sheet (o un error inesperado): sin pestañas huérfanas.
		if spreadsheet is not None:
			borrar_pestanas(spreadsheet, hojas_creadas)
		detalle = f"{type(e).__name__}: {e}"
		_registrar_fallo([d.name for d in docs], detalle)
		raise

	return {
		"exportadas": [d.name for d in docs],
		"excluidas": excluidas,
		"hojas": [titulo_1] + ([titulo_2] if filas.variantes else []),
		"maestros_creados": creados,
		"avisos": avisos,
		"url": getattr(spreadsheet, "url", None),
	}


def _capturas_exportables(nombres: list[str], crear_maestros: bool) -> tuple[list, list[dict]]:
	"""Capturas en Aprobado que pasan la validación de HU-10, bloqueadas (FOR UPDATE) hasta el fin de
	la transacción. Excluye, con su motivo, las que no existen, no están en Aprobado (por ejemplo, otra
	exportación las acaba de exportar), no validan o repiten un Item Code de otra de la selección."""
	ctx = naming.cargar_contexto()  # uno para todas: config, glosario, Brand y atributos se leen una vez
	docs, excluidas = [], []
	usados: dict[str, str] = {}  # item_code en minúsculas → captura que lo usa en esta exportación
	for nombre in sorted(nombres):  # orden fijo al bloquear: dos exportaciones no se bloquean en cruz
		if not frappe.db.exists("Captura de Producto", nombre):
			excluidas.append({"captura": nombre, "motivo": _("No existe")})
			continue
		doc = frappe.get_doc("Captura de Producto", nombre, for_update=True)
		if doc.estado != "Aprobado":
			excluidas.append({"captura": nombre, "motivo": _("Está en {0}, no en Aprobado").format(_(doc.estado))})
			continue
		motivos = validation.bloqueantes_de(validation.validar_captura(doc, ctx=ctx, para_aprobar=True))
		if not crear_maestros:
			motivos += maestros.maestros_faltantes(doc)
		codigos = [c for c in (doc.item_code, *(v.item_code for v in doc.variantes if doc.tiene_variantes)) if c]
		for codigo in codigos:
			otra = usados.get(codigo.lower())
			if otra:
				motivos.append(
					_("El código {0} ya está en {1}, que también se exporta: Data Import fallaría").format(
						codigo, otra
					)
				)
		if motivos:
			excluidas.append({"captura": nombre, "motivo": "; ".join(motivos)})
			continue
		for codigo in codigos:
			usados[codigo.lower()] = nombre
		docs.append(doc)
	orden = {n: i for i, n in enumerate(nombres)}
	docs.sort(key=lambda d: orden[d.name])  # el Sheet sigue el orden de la selección
	return docs, excluidas


def _registrar_fallo(nombres: list[str], detalle: str) -> None:
	"""Deshace lo hecho (maestros) y deja el detalle en `error_log` sin cambiar el estado."""
	from captura_productos.export.erpnext_item import deshacer_y_anotar_error

	deshacer_y_anotar_error(SAVEPOINT, nombres, detalle, _("Exportación a Google Sheet"))
