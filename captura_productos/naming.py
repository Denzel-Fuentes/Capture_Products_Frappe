"""Composición de item_name e item_code (spec, secciones 5 y 13).

El módulo tiene dos partes:

1. Funciones puras (sin base de datos): normalización de texto y de medidas, abreviaturas,
   composición de plantillas. Se prueban sin crear documentos.
2. Funciones que consultan la base de datos (prefijos por grupo, Brand, Item Attribute, unicidad del
   código) y `recalcular_nombres`, que aplica todo sobre una Captura de Producto en memoria.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import frappe
from frappe import _

if TYPE_CHECKING:
	from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
		CapturadeProducto,
	)

# ---------------------------------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------------------------------

PLANTILLA_NOMBRE_DEFECTO = "{marca} {variante} {medida}"
PLANTILLA_CODIGO_DEFECTO = "{prefijo}-{marca_corta}-{variante_corta}-{medida}"
PREFIJO_DEFECTO = "PRD"
ATRIBUTOS_MEDIDA_DEFECTO = "Tamaño,Tamano,Presentacion"
LARGO_MAX_NOMBRE_DEFECTO = 80
LARGO_MAX_CODIGO_DEFECTO = 40

PLACEHOLDERS = frozenset({"prefijo", "tipo", "marca", "marca_corta", "variante", "variante_corta", "medida"})
UNIDADES = ("ml", "L", "g", "kg", "u")
REGEX_ITEM_CODE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{1,39}$")
REGEX_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")

# Estados cuyas capturas "reservan" su item_code (5.6).
ESTADOS_CODIGO_RESERVADO = ("Por revisar", "Aprobado", "Exportado")

# Prefijo de las líneas de advertencia que escribe este módulo en `advertencias_ia`.
PREFIJO_ADVERTENCIA_NOMBRE = "[Nombre] "

_CONECTORES = frozenset({"de", "del", "con", "y", "e", "en", "a", "al", "para"})
_PALABRAS_VACIAS_MARCA = frozenset({"DEL", "DE", "LA", "EL", "LOS", "LAS"})
_SIMBOLOS_NOMBRE = frozenset(" -.'&/()%")
_UNIDADES_EN_NOMBRE = {"ml": "ml", "l": "L", "g": "g", "kg": "kg", "u": "u"}

# ---------------------------------------------------------------------------------------------------
# Parte 1: funciones puras
# ---------------------------------------------------------------------------------------------------


def quitar_tildes(texto: str | None) -> str:
	"""á→a, é→e, ü→u, ñ→n, etc. (y sus mayúsculas)."""
	descompuesto = unicodedata.normalize("NFD", texto or "")
	return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def mayusculas_ascii(texto: str | None) -> str:
	return quitar_tildes(texto).upper()


def clave_comparacion(texto: str | None) -> str:
	"""Clave para comparar sin tildes, mayúsculas, espacios ni símbolos ("Coca-Cola" == "coca cola")."""
	return re.sub(r"[^a-z0-9]", "", quitar_tildes(texto).lower())


def formatear_numero(valor: float) -> str:
	"""Máximo 2 decimales, separador '.', sin ceros finales (1.50 → 1.5, 3.0 → 3)."""
	texto = f"{round(float(valor), 2):.2f}".rstrip("0").rstrip(".")
	return texto or "0"


def _capitalizar(palabra: str) -> str:
	"""Inicial mayúscula en cada parte separada por guion o apóstrofo ("frut-all" → "Frut-All")."""
	partes = re.split(r"([-'])", palabra)
	return "".join(p[:1].upper() + p[1:].lower() if p not in ("-", "'") else p for p in partes)


def a_titulo(texto: str | None, inicio: bool = True) -> str:
	"""Estilo Título de 5.3.

	Cada palabra con inicial mayúscula salvo los conectores (de, del, con, y, ...) y las unidades
	(ml, L, g, kg, u) detrás de un número. `inicio` indica si la primera palabra abre el nombre
	(en ese caso un conector también se capitaliza).
	"""
	palabras = (texto or "").split()
	resultado: list[str] = []
	for i, palabra in enumerate(palabras):
		minuscula = palabra.lower()
		anterior_es_numero = i > 0 and re.fullmatch(r"\d+(?:[.,]\d+)?", palabras[i - 1]) is not None
		if anterior_es_numero and minuscula in _UNIDADES_EN_NOMBRE:
			resultado.append(_UNIDADES_EN_NOMBRE[minuscula])
		elif minuscula in _CONECTORES and not (inicio and i == 0):
			resultado.append(minuscula)
		else:
			resultado.append(_capitalizar(palabra))
	return " ".join(resultado)


def limpiar_nombre(texto: str, quitar_acentos: bool = True) -> str:
	"""Deja solo letras, dígitos, espacio y - . ' & / ( ) %; colapsa espacios."""
	if quitar_acentos:
		texto = quitar_tildes(texto)
	permitido = "".join(c for c in texto if c.isalnum() or c in _SIMBOLOS_NOMBRE)
	return re.sub(r"\s+", " ", permitido).strip()


def limpiar_segmento_codigo(texto: str | None) -> str:
	"""Un segmento del código: mayúsculas ASCII y solo [A-Z0-9.] (sin espacios ni guiones)."""
	return re.sub(r"[^A-Z0-9.]", "", mayusculas_ascii(texto))


def limpiar_codigo(texto: str) -> str:
	"""Código completo: mayúsculas ASCII, solo [A-Z0-9.-], guiones colapsados y extremos limpios."""
	codigo = re.sub(r"[^A-Z0-9.\-]", "", mayusculas_ascii(texto))
	codigo = re.sub(r"-{2,}", "-", codigo)
	return codigo.strip("-.")


# --- Medidas (5.2) ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Medida:
	"""Medida normalizada. Usar `crear_medida` para construirla con la conversión ml↔L y g↔kg."""

	valor: float
	unidad: str  # ml, L, g, kg, u

	def nombre(self) -> str:
		return f"{formatear_numero(self.valor)} {self.unidad}"

	def codigo(self) -> str:
		return f"{formatear_numero(self.valor)}{self.unidad.upper()}"


_ALIAS_UNIDAD = {
	"ml": "ml",
	"cc": "ml",
	"l": "L",
	"lt": "L",
	"lts": "L",
	"litro": "L",
	"litros": "L",
	"g": "g",
	"gr": "g",
	"grs": "g",
	"kg": "kg",
	"kgs": "kg",
	"u": "u",
	"un": "u",
	"und": "u",
	"unid": "u",
	"unidad": "u",
	"unidades": "u",
}


def crear_medida(valor: float | str | None, unidad: str | None) -> Medida | None:
	"""Normaliza valor/unidad: ≥1000 ml → L, <1 L → ml, ≥1000 g → kg, <1 kg → g. None si no es válida."""
	try:
		numero = float(str(valor).replace(",", ".")) if valor not in (None, "") else 0.0
	except ValueError:
		return None
	unidad_normal = _ALIAS_UNIDAD.get((unidad or "").strip().lower())
	if numero <= 0 or not unidad_normal:
		return None
	if unidad_normal == "ml" and numero >= 1000:
		numero, unidad_normal = numero / 1000, "L"
	elif unidad_normal == "L" and numero < 1:
		numero, unidad_normal = numero * 1000, "ml"
	elif unidad_normal == "g" and numero >= 1000:
		numero, unidad_normal = numero / 1000, "kg"
	elif unidad_normal == "kg" and numero < 1:
		numero, unidad_normal = numero * 1000, "g"
	return Medida(round(numero, 2), unidad_normal)


ADVERTENCIA_UNIDAD_NO_SOPORTADA = "Unidad no soportada: revise"

_NUMEROS = {
	"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
	"siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
	"catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
	"diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintidos": 22,
	"veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
	"veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
	"sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
	"doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
	"cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
	"seiscientos": 600, "seiscientas": 600, "setecientos": 700, "setecientas": 700,
	"ochocientos": 800, "ochocientas": 800, "novecientos": 900, "novecientas": 900,
}  # fmt: skip

_NO_SOPORTADO = re.compile(r"\b(onzas?|oz|libras?|lbs?|galon(es)?)\b|\d\s*x\s*\d")

# Frases fijas que se reemplazan antes de convertir números (el orden importa).
_FRASES_MEDIDA = (
	(re.compile(r"\btres cuartos?(?: de)? litros?\b"), " 750 ml "),
	(re.compile(r"\b(?:un |una )?cuarto(?: de)? kilos?\b"), " 250 g "),
	(re.compile(r"\b(?:un |una )?cuarto(?: de)? litros?\b"), " 250 ml "),
	(re.compile(r"\bmedio kilos?\b"), " 500 g "),
	(re.compile(r"\bmedio litros?\b"), " 500 ml "),
	(re.compile(r"\bmedia docena\b"), " 6 u "),
	(re.compile(r"\bun cuarto\b"), " 250 ml "),
)

_UNIDAD_RE = (
	r"mililitros?|ml|cc|centimetros? cubicos?|litros?|lts?|l"
	r"|kilogramos?|kilos?|kgs?|gramos?|grs?|g|unidades?|unid|und|u|docenas?"
)
_MEDIDA_RE = re.compile(
	r"(?:(?P<num>\d+(?:\.\d+)?)\s*)?\b(?P<unidad>" + _UNIDAD_RE + r")\b(?P<medio>\s+y\s+medi[oa])?"
)
_PAQUETE_RE = re.compile(r"\bpaquete(?: de)? (?P<num>\d+)\b")

# Unidades que se aceptan sin número delante ("pepsi de litro" = 1 L).
_UNIDAD_SIN_NUMERO = {"litro", "litros", "kilo", "kilos", "docena"}


def _unidad_canonica(texto_unidad: str) -> str:
	if re.fullmatch(r"mililitros?|ml|cc|centimetros? cubicos?", texto_unidad):
		return "ml"
	if re.fullmatch(r"litros?|lts?|l", texto_unidad):
		return "L"
	if re.fullmatch(r"kilogramos?|kilos?|kgs?", texto_unidad):
		return "kg"
	if re.fullmatch(r"gramos?|grs?|g", texto_unidad):
		return "g"
	if re.fullmatch(r"docenas?", texto_unidad):
		return "docena"
	return "u"


def _palabras_a_numeros(texto: str) -> str:
	"""'dos litros' → '2 litros', 'trescientos cincuenta ml' → '350 ml', 'dos mil' → '2000'."""
	palabras = texto.split()
	salida: list[str] = []
	i = 0
	while i < len(palabras):
		if palabras[i] not in _NUMEROS and palabras[i] != "mil":
			salida.append(palabras[i])
			i += 1
			continue
		total = actual = 0
		while i < len(palabras):
			palabra = palabras[i]
			if palabra in _NUMEROS:
				actual += _NUMEROS[palabra]
			elif palabra == "mil":
				total += (actual or 1) * 1000
				actual = 0
			elif palabra == "y" and i + 1 < len(palabras) and palabras[i + 1] in _NUMEROS:
				pass  # "treinta y cinco"
			else:
				break
			i += 1
		salida.append(str(total + actual))
	return " ".join(salida)


def _preparar_texto_medida(texto: str) -> str:
	t = quitar_tildes(texto).lower()
	t = re.sub(r"(\d),(\d)", r"\1.\2", t)  # coma decimal
	t = re.sub(r"(\d)([a-z])", r"\1 \2", t)  # 500ml → 500 ml
	t = re.sub(r"[^a-z0-9.\s]", " ", t)
	return re.sub(r"\s+", " ", t).strip()


def parsear_medida(texto: str | None) -> tuple[Medida | None, str | None]:
	"""Extrae la medida de un texto dictado o escrito (tabla 5.2).

	Devuelve (medida, advertencia). Si la unidad no está soportada (onzas, libras, galón,
	packs "6 x 500 ml") devuelve (None, "Unidad no soportada: revise").
	"""
	t = _preparar_texto_medida(texto or "")
	if not t:
		return None, None
	if _NO_SOPORTADO.search(t):
		return None, ADVERTENCIA_UNIDAD_NO_SOPORTADA
	for patron, reemplazo in _FRASES_MEDIDA:
		t = patron.sub(reemplazo, t)
	t = _palabras_a_numeros(re.sub(r"\s+", " ", t).strip())

	for m in _MEDIDA_RE.finditer(t):
		texto_unidad = m.group("unidad")
		if m.group("num") is None and texto_unidad not in _UNIDAD_SIN_NUMERO:
			continue
		valor = float(m.group("num") or 1)
		if m.group("medio"):
			valor += 0.5
		unidad = _unidad_canonica(texto_unidad)
		if unidad == "docena":
			valor, unidad = valor * 12, "u"
		return crear_medida(valor, unidad), None

	m = _PAQUETE_RE.search(t)
	if m:
		return crear_medida(m.group("num"), "u"), None
	return None, None


def resolver_medida(
	valor_ia: float | None, unidad_ia: str | None, texto: str | None
) -> tuple[Medida | None, list[str]]:
	"""Combina la medida de la IA con la que Python lee del texto (5.2). Python prevalece."""
	medida_ia = crear_medida(valor_ia, unidad_ia)
	medida_texto, advertencia = parsear_medida(texto)
	if advertencia:
		return None, [advertencia]
	if medida_ia and medida_texto and medida_ia != medida_texto:
		return medida_texto, [
			_("La medida de la IA ({0}) no coincide con lo dictado ({1}): se usó {1}").format(
				medida_ia.nombre(), medida_texto.nombre()
			)
		]
	return medida_texto or medida_ia, []


# --- Marca y abreviaturas (5.3, 5.4, 5.5) ------------------------------------------------------------


@dataclass(frozen=True)
class EntradaGlosario:
	formas: frozenset[str]  # claves de comparación de lo dictado (incluye la forma correcta)
	forma_correcta: str
	abreviatura: str | None = None


def construir_glosario(filas: Iterable[tuple[str | None, str | None, str | None]]) -> list[EntradaGlosario]:
	"""Filas (dicho, forma_correcta, abreviatura) → entradas listas para buscar."""
	glosario = []
	for dicho, forma_correcta, abreviatura in filas:
		if not forma_correcta:
			continue
		formas = {clave_comparacion(x) for x in (dicho or "").split(",")}
		formas.add(clave_comparacion(forma_correcta))
		formas.discard("")
		abreviatura = limpiar_segmento_codigo(abreviatura) or None
		glosario.append(EntradaGlosario(frozenset(formas), forma_correcta.strip(), abreviatura))
	return glosario


@dataclass(frozen=True)
class MarcaNormalizada:
	marca: str
	abreviatura: str | None  # la del glosario, si tiene
	origen: str  # "glosario", "brand", "nueva" o "" (sin marca)


def normalizar_marca(
	marca: str | None,
	glosario: Sequence[EntradaGlosario] = (),
	brands: Iterable[str] = (),
	estilo_titulo: bool = True,
) -> MarcaNormalizada:
	"""Glosario primero, luego la grafía de un Brand existente; si no, Título (5.3)."""
	clave = clave_comparacion(marca)
	if not clave:
		return MarcaNormalizada("", None, "")
	for entrada in glosario:
		if clave in entrada.formas:
			return MarcaNormalizada(entrada.forma_correcta, entrada.abreviatura, "glosario")
	for brand in brands:
		if clave_comparacion(brand) == clave:
			return MarcaNormalizada(brand, None, "brand")
	texto = re.sub(r"\s+", " ", (marca or "").strip())
	return MarcaNormalizada(a_titulo(texto) if estilo_titulo else texto, None, "nueva")


def marca_corta_auto(marca: str | None) -> str:
	"""Regla automática de 5.4 (sin glosario)."""
	palabras = [p for p in mayusculas_ascii(marca).split() if p not in _PALABRAS_VACIAS_MARCA]
	if not palabras:  # la marca era solo artículos ("La"): se usa tal cual
		palabras = mayusculas_ascii(marca).split()
	limpias = [re.sub(r"[^A-Z0-9]", "", p) for p in palabras]
	limpias = [p for p in limpias if p]
	unida = "".join(limpias)
	if len(unida) > 10:
		return limpias[0][:10]
	return unida


def _palabras_codigo(valor: str | None) -> list[str]:
	return [p for p in (re.sub(r"[^A-Z0-9.]", "", x) for x in mayusculas_ascii(valor).split()) if p]


def valor_corto_auto(valor: str | None) -> str:
	"""Regla 5.5.3: 1 palabra → 3 primeras letras; 2 o más → unidas y cortadas a 12."""
	palabras = _palabras_codigo(valor)
	if not palabras:
		return ""
	if len(palabras) == 1:
		return palabras[0][:3]
	return "".join(palabras)[:12]


def desambiguar_abreviaturas(
	valores: Sequence[str], fijas: Mapping[str, str] | None = None
) -> dict[str, str]:
	"""Abreviatura por valor; si dos valores distintos chocan (Mango/Manzana → MAN) se agrega una
	letra a cada uno hasta distinguirlos (MANG/MANZ). Las `fijas` (abbr de Item Attribute Value o
	forma de código de una medida) no se tocan."""
	fijas = fijas or {}
	resultado: dict[str, str] = {}
	completos: dict[str, str] = {}
	for valor in dict.fromkeys(v for v in valores if v):
		if valor in fijas:
			resultado[valor] = fijas[valor]
		else:
			resultado[valor] = valor_corto_auto(valor)
			completos[valor] = "".join(_palabras_codigo(valor))[:12]

	for _intento in range(12):
		por_abreviatura: dict[str, list[str]] = defaultdict(list)
		for valor, abreviatura in resultado.items():
			por_abreviatura[abreviatura].append(valor)
		hubo_cambio = False
		for grupo in por_abreviatura.values():
			if len(grupo) < 2:
				continue
			for valor in grupo:
				if valor not in completos:
					continue
				nueva = completos[valor][: len(resultado[valor]) + 1]
				if nueva != resultado[valor]:
					resultado[valor] = nueva
					hubo_cambio = True
		if not hubo_cambio:
			break
	return resultado


def prefijo_por_nombre_grupo(item_group: str | None) -> str:
	"""Respaldo de la sección 12.4: 3 primeras letras alfabéticas del grupo, en mayúsculas."""
	return re.sub(r"[^A-Z]", "", mayusculas_ascii(item_group))[:3]


# --- Plantillas (5.1) -------------------------------------------------------------------------------


def placeholders_de(plantilla: str | None) -> list[str]:
	return REGEX_PLACEHOLDER.findall(plantilla or "")


def placeholders_invalidos(plantilla: str | None) -> list[str]:
	return [p for p in placeholders_de(plantilla) if p not in PLACEHOLDERS]


def componer_plantilla(plantilla: str, valores: Mapping[str, str | None]) -> str:
	"""Reemplaza los placeholders. Uno vacío se elimina junto con el separador que lo precede
	(o el que lo sigue, si es el primero)."""
	partes = REGEX_PLACEHOLDER.split(plantilla)
	# partes = [literal0, ph1, literal1, ph2, literal2, ...]
	literales = partes[0::2]
	nombres = partes[1::2]
	salida = ""
	emitido = False
	for i, nombre in enumerate(nombres):
		valor = (valores.get(nombre) or "").strip()
		if not valor:
			continue
		# Si es el primero que se emite, solo lleva el texto inicial de la plantilla.
		salida += literales[i] if emitido else literales[0]
		salida += valor
		emitido = True
	if emitido:
		salida += literales[-1]
	return salida


@dataclass
class ConfigNombre:
	plantilla_nombre: str = PLANTILLA_NOMBRE_DEFECTO
	plantilla_codigo: str = PLANTILLA_CODIGO_DEFECTO
	quitar_tildes: bool = True
	estilo_titulo: bool = True
	largo_max_nombre: int = LARGO_MAX_NOMBRE_DEFECTO
	largo_max_codigo: int = LARGO_MAX_CODIGO_DEFECTO


@dataclass
class DatosNombre:
	"""Lo necesario para componer un nombre y un código (de la plantilla, un producto o una variante)."""

	prefijo: str = ""
	tipo: str = ""
	marca: str = ""  # ya normalizada (glosario / Brand / Título)
	marca_corta: str = ""
	variante: str = ""
	variante_corta: str = ""
	medida: Medida | None = None


@dataclass
class NombreCompuesto:
	item_name: str
	item_code: str
	advertencias: list[str] = field(default_factory=list)


def componer_nombre(cfg: ConfigNombre, datos: DatosNombre) -> str:
	tipo = a_titulo(datos.tipo) if cfg.estilo_titulo else (datos.tipo or "").strip()
	variante = a_titulo(datos.variante, inicio=False) if cfg.estilo_titulo else (datos.variante or "").strip()
	valores = {
		"prefijo": datos.prefijo,
		"tipo": tipo,
		"marca": datos.marca or tipo,
		"marca_corta": datos.marca_corta,
		"variante": variante,
		"variante_corta": datos.variante_corta,
		"medida": datos.medida.nombre() if datos.medida else "",
	}
	return limpiar_nombre(componer_plantilla(cfg.plantilla_nombre, valores), cfg.quitar_tildes)


def _codigo_con(cfg: ConfigNombre, datos: DatosNombre, marca_corta: str, variante_corta: str) -> str:
	valores = {
		"prefijo": limpiar_segmento_codigo(datos.prefijo),
		"tipo": limpiar_segmento_codigo(datos.tipo).replace(".", "")[:10],
		"marca": limpiar_segmento_codigo(datos.marca),
		"marca_corta": marca_corta,
		"variante": limpiar_segmento_codigo(datos.variante),
		"variante_corta": variante_corta,
		"medida": datos.medida.codigo() if datos.medida else "",
	}
	return limpiar_codigo(componer_plantilla(cfg.plantilla_codigo, valores))


def componer_codigo(cfg: ConfigNombre, datos: DatosNombre) -> tuple[str, list[str]]:
	"""Código según la plantilla; si excede el largo, corta variante_corta a 3 y luego marca_corta a 6."""
	marca_corta = limpiar_codigo(datos.marca_corta)
	variante_corta = limpiar_codigo(datos.variante_corta)
	codigo = _codigo_con(cfg, datos, marca_corta, variante_corta)
	if len(codigo) <= cfg.largo_max_codigo:
		return codigo, []

	original = codigo
	variante_corta = "-".join(p[:3] for p in variante_corta.split("-"))
	codigo = _codigo_con(cfg, datos, marca_corta, variante_corta)
	if len(codigo) > cfg.largo_max_codigo:
		codigo = _codigo_con(cfg, datos, marca_corta[:6], variante_corta)
	advertencias = [
		_("El código {0} superaba {1} caracteres: se acortó a {2}").format(
			original, cfg.largo_max_codigo, codigo
		)
	]
	if len(codigo) > cfg.largo_max_codigo:
		advertencias.append(
			_("El código {0} sigue teniendo {1} caracteres (máximo {2}): corríjalo a mano").format(
				codigo, len(codigo), cfg.largo_max_codigo
			)
		)
	return codigo, advertencias


def componer(cfg: ConfigNombre, datos: DatosNombre) -> NombreCompuesto:
	nombre = componer_nombre(cfg, datos)
	codigo, advertencias = componer_codigo(cfg, datos)
	if len(nombre) > cfg.largo_max_nombre:
		advertencias.append(
			_("El nombre tiene {0} caracteres (máximo {1})").format(len(nombre), cfg.largo_max_nombre)
		)
	return NombreCompuesto(nombre, codigo, advertencias)


@dataclass
class ValorAtributo:
	"""Valor de un atributo en una variante, ya resuelto contra Item Attribute Value."""

	atributo: str
	valor: str
	valor_corto: str
	es_medida: bool = False
	medida: Medida | None = None


def datos_de_variante(padre: DatosNombre, valores: Sequence[ValorAtributo]) -> DatosNombre:
	"""Sección 13: {variante} = valores no-medida unidos por espacio, {variante_corta} = sus
	abreviaturas unidas por '-', {medida} = el valor del atributo de medida (o la del padre)."""
	normales = [v for v in valores if not (v.es_medida and v.medida) and v.valor]
	medida = next((v.medida for v in valores if v.es_medida and v.medida), None)
	return DatosNombre(
		prefijo=padre.prefijo,
		tipo=padre.tipo,
		marca=padre.marca,
		marca_corta=padre.marca_corta,
		variante=" ".join(v.valor for v in normales),
		variante_corta="-".join(v.valor_corto for v in normales if v.valor_corto),
		medida=medida or padre.medida,
	)


@dataclass
class InfoAtributo:
	"""Datos de un Item Attribute para resolver valores."""

	nombre: str
	existe: bool = True
	deshabilitado: bool = False
	numerico: bool = False
	desde: float = 0.0
	hasta: float = 0.0
	incremento: float = 0.0
	es_medida: bool = False
	# clave de comparación → (valor exacto, abbr)
	valores: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class ValorResuelto:
	valor: str  # grafía final para valor_N
	abreviatura: str | None  # fija (abbr de IAV o código de medida); None = regla automática
	medida: Medida | None = None
	existe: bool = False  # coincide con un Item Attribute Value (o es numérico)


def resolver_valor_atributo(
	info: InfoAtributo, valor: str | None, estilo_titulo: bool = True
) -> ValorResuelto:
	"""Valor exacto y abreviatura de un valor de atributo (secciones 5.5 y 13)."""
	texto = re.sub(r"\s+", " ", (valor or "").strip())
	if not texto:
		return ValorResuelto("", None)

	if info.numerico:
		try:
			numero = float(texto.replace(",", "."))
		except ValueError:
			return ValorResuelto(texto, None)
		return ValorResuelto(formatear_numero(numero), formatear_numero(numero), existe=True)

	if info.es_medida:
		medida, _adv = parsear_medida(texto)
		if medida:
			for valor_existente, abbr in info.valores.values():
				medida_existente, _adv2 = parsear_medida(valor_existente)
				if medida_existente == medida:
					return ValorResuelto(
						valor_existente, (abbr or medida.codigo()).upper(), medida, existe=True
					)
			return ValorResuelto(medida.nombre(), medida.codigo(), medida)

	existente = info.valores.get(clave_comparacion(texto))
	if existente:
		valor_existente, abbr = existente
		return ValorResuelto(valor_existente, limpiar_segmento_codigo(abbr) or None, existe=True)
	return ValorResuelto(a_titulo(texto) if estilo_titulo else texto, None)


# ---------------------------------------------------------------------------------------------------
# Parte 2: funciones con base de datos
# ---------------------------------------------------------------------------------------------------


@dataclass
class ContextoNombre:
	cfg: ConfigNombre
	prefijos: dict[str, str]
	glosario: list[EntradaGlosario]
	atributos_medida: frozenset[str]
	prefijo_defecto: str
	_brands: list[str] | None = None
	_atributos: dict[str, InfoAtributo] = field(default_factory=dict)

	@property
	def brands(self) -> list[str]:
		if self._brands is None:
			self._brands = frappe.get_all("Brand", pluck="name")
		return self._brands

	def info_atributo(self, nombre: str) -> InfoAtributo:
		if nombre not in self._atributos:
			self._atributos[nombre] = cargar_info_atributo(nombre, self.atributos_medida)
		return self._atributos[nombre]


def cargar_contexto() -> ContextoNombre:
	"""Lee Configuracion Captura con respaldo a los valores por defecto de la spec."""
	conf = frappe.get_single("Configuracion Captura")
	cfg = ConfigNombre(
		plantilla_nombre=conf.plantilla_nombre or PLANTILLA_NOMBRE_DEFECTO,
		plantilla_codigo=conf.plantilla_codigo or PLANTILLA_CODIGO_DEFECTO,
		quitar_tildes=bool(conf.quitar_tildes_nombre),
		estilo_titulo=(conf.estilo_mayusculas or "Título") == "Título",
		largo_max_nombre=conf.largo_max_nombre or LARGO_MAX_NOMBRE_DEFECTO,
		largo_max_codigo=conf.largo_max_codigo or LARGO_MAX_CODIGO_DEFECTO,
	)
	atributos_medida = conf.atributos_de_medida
	if atributos_medida is None:
		atributos_medida = ATRIBUTOS_MEDIDA_DEFECTO
	return ContextoNombre(
		cfg=cfg,
		prefijos={
			p.item_group: (p.prefijo or "").upper() for p in conf.prefijos if p.item_group and p.prefijo
		},
		glosario=construir_glosario((g.dicho, g.forma_correcta, g.abreviatura) for g in conf.glosario_marcas),
		atributos_medida=frozenset(clave_comparacion(a) for a in atributos_medida.split(",") if a.strip()),
		prefijo_defecto=(conf.prefijo_defecto or PREFIJO_DEFECTO).upper(),
	)


def cargar_info_atributo(nombre: str, atributos_medida: frozenset[str] = frozenset()) -> InfoAtributo:
	es_medida = clave_comparacion(nombre) in atributos_medida
	datos = frappe.db.get_value(
		"Item Attribute",
		nombre,
		["name", "numeric_values", "from_range", "to_range", "increment", "disabled"],
		as_dict=True,
	)
	if not datos:
		return InfoAtributo(nombre, existe=False, es_medida=es_medida)
	filas = frappe.get_all(
		"Item Attribute Value",
		filters={"parent": datos.name, "parenttype": "Item Attribute"},
		fields=["attribute_value", "abbr"],
		order_by="idx asc",
	)
	return InfoAtributo(
		nombre=datos.name,
		deshabilitado=bool(datos.disabled),
		numerico=bool(datos.numeric_values),
		desde=float(datos.from_range or 0),
		hasta=float(datos.to_range or 0),
		incremento=float(datos.increment or 0),
		es_medida=es_medida,
		valores={clave_comparacion(f.attribute_value): (f.attribute_value, f.abbr or "") for f in filas},
	)


def obtener_prefijo(ctx: ContextoNombre, item_group: str | None) -> tuple[str, str | None]:
	"""Prefijo del grupo (o de un grupo ancestro); si no hay, 3 letras del nombre con advertencia."""
	if not item_group:
		return ctx.prefijo_defecto, None
	grupo: str | None = item_group
	visitados: set[str] = set()
	while grupo and grupo not in visitados:
		if grupo in ctx.prefijos:
			return ctx.prefijos[grupo], None
		visitados.add(grupo)
		grupo = frappe.db.get_value("Item Group", grupo, "parent_item_group")

	letras = prefijo_por_nombre_grupo(item_group)
	if len(letras) < 2:
		letras = ctx.prefijo_defecto
	return letras, _("El grupo {0} no tiene prefijo configurado: se usó {1}").format(item_group, letras)


def atributos_ordenados(doc: CapturadeProducto) -> list[str]:
	"""Atributos de la captura en el orden que define valor_1..valor_3."""
	filas = sorted(doc.get("atributos") or [], key=lambda f: (f.orden or f.idx or 0, f.idx or 0))
	return [f.atributo for f in filas if f.atributo][:3]


def buscar_codigo_ocupado(codigo: str, excluir_captura: str | None = None) -> str | None:
	"""Devuelve el nombre de lo que ya usa `codigo` (Item o captura activa), o None.

	La comparación no distingue mayúsculas por la collation de las columnas (utf8mb4_unicode_ci); sin
	LOWER() la consulta usa los índices de `tabItem.name` e `item_code`."""
	nombre = frappe.db.sql("select item_name from `tabItem` where name = %s limit 1", (codigo,))
	if nombre:
		return nombre[0][0] or codigo
	params = {"codigo": codigo, "excluir": excluir_captura or "", "estados": ESTADOS_CODIGO_RESERVADO}
	nombre = frappe.db.sql(
		"""
		select item_name from `tabCaptura de Producto`
		where item_code = %(codigo)s and estado in %(estados)s and name != %(excluir)s
		union all
		select v.item_name from `tabCaptura Variante` v
		join `tabCaptura de Producto` c on c.name = v.parent and v.parenttype = 'Captura de Producto'
		where v.item_code = %(codigo)s and c.estado in %(estados)s and c.name != %(excluir)s
		limit 1
		""",
		params,
	)
	if nombre:
		return nombre[0][0] or codigo
	return None


def asegurar_codigos_unicos(
	codigos: Sequence[str], excluir_captura: str | None = None, nombres: Sequence[str] | None = None
) -> tuple[list[str], list[str]]:
	"""Agrega -2, -3, ... a los códigos que chocan con Item, otras capturas o entre sí (5.6)."""
	usados: dict[str, str] = {}  # código en minúsculas → nombre de quien lo usa en esta captura
	resultado: list[str] = []
	advertencias: list[str] = []
	for i, codigo in enumerate(codigos):
		if not codigo:
			resultado.append(codigo)
			continue
		candidato = codigo
		n = 1
		while True:
			ocupado_por = usados.get(candidato.lower()) or buscar_codigo_ocupado(candidato, excluir_captura)
			if not ocupado_por:
				break
			if n == 1:
				advertencias.append(
					_("Ya existe {0} '{1}': verifique si es el mismo producto").format(codigo, ocupado_por)
				)
			n += 1
			candidato = f"{codigo}-{n}"
		usados[candidato.lower()] = (nombres[i] if nombres else None) or candidato
		resultado.append(candidato)
	return resultado, advertencias


def calcular_nombres(doc: CapturadeProducto, ctx: ContextoNombre | None = None) -> list[str]:
	"""Recompone marca, abreviaturas, item_name e item_code del padre y de cada variante en `doc`
	(en memoria, sin guardar). Devuelve las advertencias de nombre."""
	ctx = ctx or cargar_contexto()
	advertencias: list[str] = []

	marca = normalizar_marca(doc.marca, ctx.glosario, ctx.brands, ctx.cfg.estilo_titulo)
	doc.marca = marca.marca or None
	doc.marca_corta = marca.abreviatura or marca_corta_auto(marca.marca or doc.tipo_producto) or None

	medida = crear_medida(doc.medida_valor, doc.medida_unidad)
	if medida:
		doc.medida_valor, doc.medida_unidad = medida.valor, medida.unidad

	prefijo, adv_prefijo = obtener_prefijo(ctx, doc.item_group)
	if adv_prefijo:
		advertencias.append(adv_prefijo)

	padre = DatosNombre(
		prefijo=prefijo,
		tipo=doc.tipo_producto or "",
		marca=marca.marca,
		marca_corta=doc.marca_corta or "",
		medida=medida,
	)
	if doc.tiene_variantes:
		doc.variante_corta = None
	else:
		padre.variante = doc.variante_descripcion or ""
		padre.variante_corta = valor_corto_auto(padre.variante)
		doc.variante_corta = padre.variante_corta or None

	compuesto = componer(ctx.cfg, padre)
	advertencias += compuesto.advertencias
	nombres = [compuesto.item_name]
	codigos = [compuesto.item_code]

	if doc.tiene_variantes:
		variantes_compuestas = _componer_variantes(doc, ctx, padre)
		for nombre_variante in variantes_compuestas:
			advertencias += nombre_variante.advertencias
			nombres.append(nombre_variante.item_name)
			codigos.append(nombre_variante.item_code)

	codigos, adv_unicos = asegurar_codigos_unicos(codigos, doc.name if not doc.is_new() else None, nombres)
	advertencias += adv_unicos

	doc.item_name, doc.item_code = nombres[0] or None, codigos[0] or None
	if doc.tiene_variantes:
		for fila, nombre, codigo in zip(doc.variantes, nombres[1:], codigos[1:], strict=True):
			fila.item_name, fila.item_code = nombre or None, codigo or None
	return list(dict.fromkeys(advertencias))


def _componer_variantes(
	doc: CapturadeProducto, ctx: ContextoNombre, padre: DatosNombre
) -> list[NombreCompuesto]:
	atributos = atributos_ordenados(doc)
	resueltos_por_fila: list[list[tuple[InfoAtributo, ValorResuelto]]] = []
	for fila in doc.variantes:
		resueltos = []
		for n, atributo in enumerate(atributos, start=1):
			info = ctx.info_atributo(atributo)
			resuelto = resolver_valor_atributo(info, fila.get(f"valor_{n}"), ctx.cfg.estilo_titulo)
			fila.set(f"valor_{n}", resuelto.valor or None)
			resueltos.append((info, resuelto))
		resueltos_por_fila.append(resueltos)

	# Abreviaturas automáticas desambiguadas por atributo (Mango/Manzana → MANG/MANZ).
	abreviaturas: list[dict[str, str]] = []
	for n in range(len(atributos)):
		valores = [r[n][1].valor for r in resueltos_por_fila]
		fijas = {r[n][1].valor: r[n][1].abreviatura for r in resueltos_por_fila if r[n][1].abreviatura}
		abreviaturas.append(desambiguar_abreviaturas(valores, fijas))

	compuestos = []
	for fila, resueltos in zip(doc.variantes, resueltos_por_fila, strict=True):
		valores_atributo = []
		for n, (info, resuelto) in enumerate(resueltos):
			corto = abreviaturas[n].get(resuelto.valor, "") if resuelto.valor else ""
			fila.set(f"valor_corto_{n + 1}", corto or None)
			valores_atributo.append(
				ValorAtributo(info.nombre, resuelto.valor, corto, info.es_medida, resuelto.medida)
			)
		for n in range(len(atributos) + 1, 4):
			fila.set(f"valor_corto_{n}", None)
		compuestos.append(componer(ctx.cfg, datos_de_variante(padre, valores_atributo)))
	return compuestos


def fusionar_advertencias_nombre(texto_actual: str | None, advertencias: Iterable[str]) -> str:
	"""Reemplaza las líneas "[Nombre] ..." de `advertencias_ia` y conserva las demás."""
	conservadas = [
		linea
		for linea in (texto_actual or "").splitlines()
		if linea.strip() and not linea.startswith(PREFIJO_ADVERTENCIA_NOMBRE)
	]
	nuevas = [PREFIJO_ADVERTENCIA_NOMBRE + a for a in advertencias]
	return "\n".join(dict.fromkeys(conservadas + nuevas))


def recalcular_nombres(doc: CapturadeProducto) -> list[str]:
	"""Recalcula nombres y códigos en `doc` y deja sus advertencias en `advertencias_ia` (no guarda)."""
	advertencias = calcular_nombres(doc)
	doc.advertencias_ia = fusionar_advertencias_nombre(doc.advertencias_ia, advertencias) or None
	return advertencias
