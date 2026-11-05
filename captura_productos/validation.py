"""Validaciones de una Captura de Producto (spec HU-03, HU-04, HU-10, secciones 4, 5.6 y 13).

`validar_captura(doc)` devuelve una lista de `Advertencia` (bloqueantes o no). El controlador las
escribe en `advertencias_ia` con `fusionar_advertencias`, que conserva las líneas de la IA y las de
nombre ("[Nombre] ...").

Las funciones de códigos de barras y de valores numéricos son puras; las de duplicados consultan la BD.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import frappe
from frappe import _

from captura_productos import naming

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

PREFIJO_BLOQUEA = "[Bloquea] "
PREFIJO_AVISO = "[Aviso] "
PREFIJOS_VALIDACION = (PREFIJO_BLOQUEA, PREFIJO_AVISO)

# Estados en los que la captura ya tiene datos de IA y se valida para aprobar (HU-10).
ESTADOS_REVISION = ("Por revisar", "Aprobado", "Exportado")
# Estados en los que un Item con el código de la propia captura es el suyo (creado con "Crear Item" o
# importado desde el Sheet) y no un duplicado.
ESTADOS_CON_ITEM_PROPIO = ("Exportado", "Creado")

TIPOS_GTIN = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13"}
REGEX_BARCODE = re.compile(r"^[A-Z0-9-]{4,20}$")
REGEX_MARCA_CORTA = re.compile(r"^[A-Z0-9]{2,10}$")
REGEX_VARIANTE_CORTA = re.compile(r"^[A-Z0-9]{2,12}$")
REGEX_VALOR_CORTO = re.compile(r"^[A-Z0-9.]{1,12}$")


@dataclass(frozen=True)
class Advertencia:
	mensaje: str
	bloqueante: bool = False

	def linea(self) -> str:
		return (PREFIJO_BLOQUEA if self.bloqueante else PREFIJO_AVISO) + self.mensaje


def bloquea(mensaje: str) -> Advertencia:
	return Advertencia(mensaje, True)


def aviso(mensaje: str) -> Advertencia:
	return Advertencia(mensaje, False)


def fusionar_advertencias(texto_actual: str | None, advertencias: Iterable[Advertencia]) -> str:
	"""Reemplaza las líneas [Bloquea]/[Aviso] de `advertencias_ia` y conserva las demás."""
	conservadas = [
		linea
		for linea in (texto_actual or "").splitlines()
		if linea.strip() and not linea.startswith(PREFIJOS_VALIDACION)
	]
	nuevas = [a.linea() for a in advertencias]
	return "\n".join(dict.fromkeys(nuevas + conservadas))


def bloqueantes_de(advertencias: Iterable[Advertencia]) -> list[str]:
	return [a.mensaje for a in advertencias if a.bloqueante]


# ---------------------------------------------------------------------------------------------------
# Códigos de barras (puras)
# ---------------------------------------------------------------------------------------------------


def normalizar_barcode(codigo: str | None) -> str:
	return re.sub(r"\s+", "", codigo or "").upper()


def digito_verificador_gtin(cuerpo: str) -> int:
	"""Dígito verificador GS1 (EAN-8, UPC-A, EAN-13, GTIN-14) para los dígitos sin el verificador."""
	total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(cuerpo)))
	return (10 - total % 10) % 10


def gtin_valido(codigo: str) -> bool:
	return (
		codigo.isdigit()
		and len(codigo) in (8, 12, 13, 14)
		and digito_verificador_gtin(codigo[:-1]) == int(codigo[-1])
	)


@dataclass(frozen=True)
class AnalisisBarcode:
	codigo: str
	tipo: str  # EAN-13, EAN-8, UPC-A o ""
	advertencia: Advertencia | None = None

	@property
	def valido(self) -> bool:
		return not (self.advertencia and self.advertencia.bloqueante)


def analizar_barcode(codigo: str | None) -> AnalisisBarcode:
	"""Formato y dígito verificador (HU-04). No consulta la BD."""
	codigo = normalizar_barcode(codigo)
	if not REGEX_BARCODE.match(codigo):
		return AnalisisBarcode(
			codigo,
			"",
			bloquea(
				_(
					"Código de barras {0} no válido: solo números, letras y guion, de 4 a 20 caracteres"
				).format(codigo or "''")
			),
		)
	if codigo.isdigit() and len(codigo) in TIPOS_GTIN:
		if gtin_valido(codigo):
			return AnalisisBarcode(codigo, TIPOS_GTIN[len(codigo)])
		return AnalisisBarcode(
			codigo, "", bloquea(_("Código mal leído, escanee de nuevo: {0}").format(codigo))
		)
	return AnalisisBarcode(
		codigo,
		"",
		aviso(_("El código {0} no es EAN-13, EAN-8 ni UPC-A: se guarda sin tipo").format(codigo)),
	)


def formas_equivalentes(codigo: str) -> list[str]:
	"""Un UPC-A (12) y el mismo código como EAN-13 con 0 adelante son el mismo producto."""
	codigo = normalizar_barcode(codigo)
	formas = [codigo]
	if codigo.isdigit() and len(codigo) == 12:
		formas.append("0" + codigo)
	elif codigo.isdigit() and len(codigo) == 13 and codigo.startswith("0"):
		formas.append(codigo[1:])
	return formas


# ---------------------------------------------------------------------------------------------------
# Códigos de barras (BD)
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CodigoExistente:
	barcode: str
	origen: str  # "item" o "captura"
	item_code: str | None = None
	item_name: str | None = None
	captura: str | None = None
	capturado_por: str | None = None
	estado: str | None = None
	transcripcion: str | None = None


def _expandir(codigos: Iterable[str]) -> dict[str, str]:
	"""forma equivalente → código original."""
	mapa: dict[str, str] = {}
	for codigo in codigos:
		for forma in formas_equivalentes(codigo):
			mapa.setdefault(forma, normalizar_barcode(codigo))
	return mapa


def buscar_en_items(codigos: Iterable[str]) -> dict[str, CodigoExistente]:
	"""Códigos que ya están en `Item Barcode` (clave: código consultado)."""
	mapa = _expandir(codigos)
	if not mapa:
		return {}
	filas = frappe.db.sql(
		"""
		select ib.barcode, ib.parent, i.item_name
		from `tabItem Barcode` ib
		left join `tabItem` i on i.name = ib.parent
		where ib.parenttype = 'Item' and ib.barcode in %(codigos)s
		""",
		{"codigos": tuple(mapa)},
		as_dict=True,
	)
	resultado: dict[str, CodigoExistente] = {}
	for fila in filas:
		original = mapa.get(fila.barcode) or mapa.get(normalizar_barcode(fila.barcode))
		if original and original not in resultado:
			resultado[original] = CodigoExistente(
				fila.barcode, "item", item_code=fila.parent, item_name=fila.item_name or fila.parent
			)
	return resultado


def buscar_en_capturas(
	codigos: Iterable[str], excluir_captura: str | None = None
) -> dict[str, CodigoExistente]:
	"""Códigos que están en otra Captura de Producto en cualquier estado salvo Creado (HU-03)."""
	mapa = _expandir(codigos)
	if not mapa:
		return {}
	filas = frappe.db.sql(
		"""
		select cb.barcode, c.name, c.capturado_por, c.estado, c.item_name, c.item_code, c.transcripcion,
			c.modified
		from `tabCaptura Codigo Barras` cb
		join `tabCaptura de Producto` c on c.name = cb.parent and cb.parenttype = 'Captura de Producto'
		where cb.barcode in %(codigos)s and c.estado != 'Creado' and c.name != %(excluir)s
		union all
		select v.barcode, c.name, c.capturado_por, c.estado, v.item_name, v.item_code, v.transcripcion,
			c.modified
		from `tabCaptura Variante` v
		join `tabCaptura de Producto` c on c.name = v.parent and v.parenttype = 'Captura de Producto'
		where v.barcode in %(codigos)s and c.estado != 'Creado' and c.name != %(excluir)s
		order by modified desc
		""",
		{"codigos": tuple(mapa), "excluir": excluir_captura or ""},
		as_dict=True,
	)
	resultado: dict[str, CodigoExistente] = {}
	for fila in filas:
		original = mapa.get(fila.barcode) or mapa.get(normalizar_barcode(fila.barcode))
		if original and original not in resultado:
			resultado[original] = CodigoExistente(
				fila.barcode,
				"captura",
				item_code=fila.item_code,
				item_name=fila.item_name,
				captura=fila.name,
				capturado_por=fila.capturado_por,
				estado=fila.estado,
				transcripcion=fila.transcripcion,
			)
	return resultado


# ---------------------------------------------------------------------------------------------------
# Atributos y variantes
# ---------------------------------------------------------------------------------------------------


def valor_numerico_valido(valor: str | None, desde: float, hasta: float, incremento: float) -> str | None:
	"""None si el valor respeta from/to/increment del Item Attribute numérico; si no, el motivo."""
	try:
		numero = float(str(valor).replace(",", "."))
	except TypeError, ValueError:
		return _("no es un número")
	if (desde or hasta) and not (desde <= numero <= hasta):
		return _("está fuera del rango {0} a {1}").format(
			naming.formatear_numero(desde), naming.formatear_numero(hasta)
		)
	if incremento:
		pasos = (numero - desde) / incremento
		if abs(pasos - round(pasos)) > 1e-6:
			return _("no respeta el incremento {0}").format(naming.formatear_numero(incremento))
	return None


def combinaciones_repetidas(combinaciones: Sequence[tuple[str, ...]]) -> list[int]:
	"""Índices (base 1) de las variantes cuya combinación de valores ya apareció antes."""
	vistas: set[tuple[str, ...]] = set()
	repetidas = []
	for i, combinacion in enumerate(combinaciones, start=1):
		clave = tuple(naming.clave_comparacion(v) for v in combinacion)
		if not any(clave):
			continue
		if clave in vistas:
			repetidas.append(i)
		vistas.add(clave)
	return repetidas


# ---------------------------------------------------------------------------------------------------
# Validación completa
# ---------------------------------------------------------------------------------------------------


def validar_captura(
	doc: CapturadeProducto,
	ctx: naming.ContextoNombre | None = None,
	para_aprobar: bool | None = None,
	permitir_items_propios: bool | None = None,
) -> list[Advertencia]:
	"""Valida la captura y ajusta campos derivados en `doc` (barcode_type, duplicado_en, grafía de
	los valores de atributo, filas de código repetidas). No guarda.

	`para_aprobar` (por defecto: estado en Por revisar, Aprobado o Exportado) agrega las reglas de
	HU-10 sobre campos obligatorios, que no tienen sentido mientras la IA no procesó la captura.

	`permitir_items_propios` (por defecto: estado Exportado o Creado) hace que un Item cuyo código es
	el de la propia captura (o el Item enlazado) no cuente como duplicado: es el que creó "Crear Item"
	o el que se importó desde el Sheet.
	"""
	ctx = ctx or naming.cargar_contexto()
	if para_aprobar is None:
		para_aprobar = doc.estado in ESTADOS_REVISION
	if permitir_items_propios is None:
		permitir_items_propios = doc.estado in ESTADOS_CON_ITEM_PROPIO
	excluir = None if doc.is_new() else doc.name
	propios = codigos_propios(doc) if permitir_items_propios else frozenset()

	advertencias: list[Advertencia] = []
	advertencias += _validar_codigos_barras(doc, excluir, propios)
	advertencias += _validar_maestros(doc, para_aprobar)
	advertencias += _validar_marca(doc, ctx)
	advertencias += _validar_medida(doc, para_aprobar)
	advertencias += _validar_variantes(doc, ctx, para_aprobar)
	if para_aprobar:
		advertencias += _validar_nombres_y_codigos(doc, ctx, excluir, propios)
	return list(dict.fromkeys(advertencias))


def codigos_propios(doc: CapturadeProducto) -> frozenset[str]:
	"""Códigos de Item (en minúsculas) que pertenecen a la captura: los suyos y los Items enlazados."""
	codigos = [doc.item_code, doc.get("item")]
	if doc.tiene_variantes:
		for fila in doc.get("variantes") or []:
			codigos += [fila.item_code, fila.get("item")]
	return frozenset(c.lower() for c in codigos if c)


def _validar_codigos_barras(
	doc: CapturadeProducto, excluir: str | None, propios: frozenset[str] = frozenset()
) -> list[Advertencia]:
	advertencias: list[Advertencia] = []

	# El mismo código dos veces en la tabla del padre: se ignora el segundo (HU-03).
	vistos: set[str] = set()
	filas = []
	for fila in doc.get("codigos_barras") or []:
		fila.barcode = normalizar_barcode(fila.barcode)
		if fila.barcode and fila.barcode in vistos:
			continue
		vistos.add(fila.barcode)
		filas.append(fila)
	if len(filas) != len(doc.get("codigos_barras") or []):
		doc.set("codigos_barras", filas)
		for idx, fila in enumerate(filas, start=1):
			fila.idx = idx

	if doc.tiene_variantes and filas:
		advertencias.append(
			bloquea(_("Con variantes, los códigos de barras van en cada variante, no en el producto"))
		)

	# (código, fila, etiqueta) de todo el documento
	todos: list[tuple[str, object, str]] = [(f.barcode, f, _("producto")) for f in filas]
	if doc.tiene_variantes:
		for fila in doc.get("variantes") or []:
			fila.barcode = normalizar_barcode(fila.barcode) or None
			if fila.barcode:
				todos.append((fila.barcode, fila, _("variante {0}").format(fila.idx)))

	validos = []
	for codigo, fila, _etiqueta in todos:
		analisis = analizar_barcode(codigo)
		fila.barcode_type = analisis.tipo
		if analisis.advertencia:
			advertencias.append(analisis.advertencia)
		if analisis.valido:
			validos.append(codigo)

	# Repetidos entre el producto y las variantes, o entre variantes.
	donde: dict[str, list[str]] = {}
	for codigo, _fila, etiqueta in todos:
		for forma in formas_equivalentes(codigo):
			donde.setdefault(forma, []).append(etiqueta)
	for codigo, _fila, _etiqueta in todos:
		etiquetas = donde.get(codigo, [])
		if len(etiquetas) > 1:
			advertencias.append(
				bloquea(
					_("El código {0} está repetido en esta captura ({1})").format(
						codigo, ", ".join(etiquetas)
					)
				)
			)

	en_items = buscar_en_items(validos)
	for codigo, existente in en_items.items():
		if (existente.item_code or "").lower() in propios:
			continue
		advertencias.append(
			bloquea(
				_("Este producto ya existe: {0} ({1}). Código {2}").format(
					existente.item_name, existente.item_code, codigo
				)
			)
		)

	en_capturas = buscar_en_capturas([c for c in validos if c not in en_items], excluir)
	for codigo, fila, _etiqueta in todos:
		existente = en_capturas.get(codigo)
		if fila.meta.has_field("duplicado_en"):
			fila.duplicado_en = existente.captura if existente else None
		if existente:
			advertencias.append(
				aviso(_("El código {0} ya fue capturado en {1}").format(codigo, existente.captura))
			)
	return advertencias


def _validar_maestros(doc: CapturadeProducto, para_aprobar: bool) -> list[Advertencia]:
	advertencias: list[Advertencia] = []
	if doc.item_group:
		es_grupo = frappe.db.get_value("Item Group", doc.item_group, "is_group")
		if es_grupo is None:
			advertencias.append(bloquea(_("El grupo {0} no existe").format(doc.item_group)))
		elif es_grupo:
			advertencias.append(
				bloquea(_("El grupo {0} agrupa otros grupos: elija un grupo final").format(doc.item_group))
			)
	elif para_aprobar:
		advertencias.append(bloquea(_("Falta el grupo de producto")))

	if doc.stock_uom:
		if not frappe.db.exists("UOM", doc.stock_uom):
			advertencias.append(bloquea(_("La unidad {0} no existe").format(doc.stock_uom)))
	elif para_aprobar:
		advertencias.append(bloquea(_("Falta la unidad de stock")))
	return advertencias


def _validar_marca(doc: CapturadeProducto, ctx: naming.ContextoNombre) -> list[Advertencia]:
	if not doc.marca:
		return []
	normalizada = naming.normalizar_marca(doc.marca, ctx.glosario, ctx.brands, ctx.cfg.estilo_titulo)
	advertencias = []
	if normalizada.origen in ("glosario", "brand") and normalizada.marca != doc.marca:
		advertencias.append(
			aviso(
				_("La marca {0} se escribe {1}: use Recalcular nombre").format(doc.marca, normalizada.marca)
			)
		)
	elif normalizada.origen == "nueva":
		advertencias.append(
			aviso(_("Marca nueva: {0} (no está en el glosario ni en Brand)").format(doc.marca))
		)
	if doc.marca_corta and not REGEX_MARCA_CORTA.match(doc.marca_corta):
		advertencias.append(aviso(_("La marca para el código debe tener de 2 a 10 caracteres A-Z o 0-9")))
	if doc.variante_corta and not REGEX_VARIANTE_CORTA.match(doc.variante_corta):
		advertencias.append(aviso(_("La variante para el código debe tener de 2 a 12 caracteres A-Z o 0-9")))
	return advertencias


def _validar_medida(doc: CapturadeProducto, para_aprobar: bool) -> list[Advertencia]:
	if doc.medida_valor and doc.medida_valor < 0:
		return [bloquea(_("La medida debe ser mayor que 0"))]
	if doc.medida_valor and not doc.medida_unidad:
		return [bloquea(_("Falta la unidad de la medida"))] if para_aprobar else []
	if doc.medida_unidad and not doc.medida_valor:
		return [aviso(_("Hay unidad de medida pero no valor: se ignora"))]
	return []


def _validar_variantes(
	doc: CapturadeProducto, ctx: naming.ContextoNombre, para_aprobar: bool
) -> list[Advertencia]:
	if not doc.tiene_variantes:
		return []
	advertencias: list[Advertencia] = []
	filas_atributo = doc.get("atributos") or []
	nombres = [f.atributo for f in filas_atributo if f.atributo]
	if len(nombres) > 3:
		advertencias.append(bloquea(_("Se permiten como máximo 3 atributos por captura")))
	if len(set(nombres)) != len(nombres):
		advertencias.append(bloquea(_("Hay atributos repetidos")))

	atributos = naming.atributos_ordenados(doc)
	infos = [ctx.info_atributo(a) for a in atributos]
	for info in infos:
		if not info.existe:
			advertencias.append(bloquea(_("El atributo {0} no existe").format(info.nombre)))
		elif info.deshabilitado:
			advertencias.append(bloquea(_("El atributo {0} está deshabilitado").format(info.nombre)))

	variantes = doc.get("variantes") or []
	if para_aprobar:
		if not atributos:
			advertencias.append(bloquea(_("Indique al menos un atributo para las variantes")))
		if not variantes:
			advertencias.append(bloquea(_("Marcó que tiene variantes pero no hay ninguna")))

	combinaciones = []
	for fila in variantes:
		valores = []
		for n, info in enumerate(infos, start=1):
			valor = (fila.get(f"valor_{n}") or "").strip()
			if not valor:
				if para_aprobar:
					advertencias.append(
						bloquea(_("Variante {0}: falta el valor de {1}").format(fila.idx, info.nombre))
					)
				valores.append("")
				continue
			advertencias += _validar_valor(fila, n, info, valor, ctx)
			valores.append(fila.get(f"valor_{n}") or "")
		for n in range(len(infos) + 1, 4):
			if fila.get(f"valor_{n}"):
				advertencias.append(
					aviso(_("Variante {0}: el valor {1} no tiene atributo y se ignora").format(fila.idx, n))
				)
		combinaciones.append(tuple(valores))

	for idx in combinaciones_repetidas(combinaciones):
		advertencias.append(bloquea(_("La variante {0} repite la combinación de otra variante").format(idx)))
	return advertencias


def _validar_valor(
	fila, n: int, info: naming.InfoAtributo, valor: str, ctx: naming.ContextoNombre
) -> list[Advertencia]:
	if not info.existe:
		return []
	if info.numerico:
		motivo = valor_numerico_valido(valor, info.desde, info.hasta, info.incremento)
		if motivo:
			return [bloquea(_("Variante {0}: {1} = {2} {3}").format(fila.idx, info.nombre, valor, motivo))]
		return []

	resuelto = naming.resolver_valor_atributo(info, valor, ctx.cfg.estilo_titulo)
	fila.set(f"valor_{n}", resuelto.valor)
	if resuelto.abreviatura and not fila.get(f"valor_corto_{n}"):
		fila.set(f"valor_corto_{n}", resuelto.abreviatura)
	advertencias = []
	if not resuelto.existe:
		advertencias.append(aviso(_("Valor nuevo: {0} en atributo {1}").format(resuelto.valor, info.nombre)))
	corto = fila.get(f"valor_corto_{n}")
	if corto and not REGEX_VALOR_CORTO.match(corto):
		advertencias.append(
			aviso(
				_("Variante {0}: la abreviatura {1} debe tener hasta 12 caracteres A-Z, 0-9 o punto").format(
					fila.idx, corto
				)
			)
		)
	return advertencias


def _validar_nombres_y_codigos(
	doc: CapturadeProducto,
	ctx: naming.ContextoNombre,
	excluir: str | None,
	propios: frozenset[str] = frozenset(),
) -> list[Advertencia]:
	advertencias: list[Advertencia] = []
	largo_nombre = ctx.cfg.largo_max_nombre
	largo_codigo = ctx.cfg.largo_max_codigo

	filas: list[tuple[str, str | None, str | None]] = [(_("Producto"), doc.item_name, doc.item_code)]
	if doc.tiene_variantes:
		filas += [
			(_("Variante {0}").format(v.idx), v.item_name, v.item_code) for v in doc.get("variantes") or []
		]

	vistos: dict[str, str] = {}
	for etiqueta, nombre, codigo in filas:
		if not nombre:
			advertencias.append(bloquea(_("{0}: falta el nombre del ítem").format(etiqueta)))
		elif len(nombre) > largo_nombre:
			advertencias.append(
				bloquea(
					_("{0}: el nombre tiene {1} caracteres (máximo {2})").format(
						etiqueta, len(nombre), largo_nombre
					)
				)
			)
		if not codigo:
			advertencias.append(bloquea(_("{0}: falta el código del ítem").format(etiqueta)))
			continue
		if not naming.REGEX_ITEM_CODE.match(codigo) or len(codigo) > largo_codigo:
			advertencias.append(
				bloquea(
					_("{0}: el código {1} no es válido (A-Z, 0-9, punto y guion; máximo {2})").format(
						etiqueta, codigo, largo_codigo
					)
				)
			)
		clave = codigo.lower()
		if clave in vistos:
			advertencias.append(
				bloquea(_("{0}: el código {1} se repite en {2}").format(etiqueta, codigo, vistos[clave]))
			)
		vistos[clave] = etiqueta
		if clave in propios:
			continue
		# La collation de la columna (utf8mb4_unicode_ci) ya ignora mayúsculas; sin lower() se usa la PK.
		existente = frappe.db.sql("select name, item_name from `tabItem` where name = %s", (codigo,))
		if existente:
			advertencias.append(
				bloquea(
					_("{0}: ya existe el Item {1} '{2}'").format(etiqueta, existente[0][0], existente[0][1])
				)
			)
		else:
			otro = naming.buscar_codigo_ocupado(codigo, excluir)
			if otro:
				advertencias.append(
					aviso(
						_("{0}: el código {1} ya lo usa otra captura ('{2}')").format(etiqueta, codigo, otro)
					)
				)
	return advertencias
