from typing import ClassVar

from frappe.tests import UnitTestCase

from captura_productos import naming
from captura_productos.naming import (
	ConfigNombre,
	DatosNombre,
	Medida,
	a_titulo,
	componer,
	componer_plantilla,
	crear_medida,
	desambiguar_abreviaturas,
	marca_corta_auto,
	normalizar_marca,
	parsear_medida,
	quitar_tildes,
	resolver_medida,
	valor_corto_auto,
)
from captura_productos.tests.utils import (
	GRUPO_BEBIDAS,
	GRUPO_DESCARTABLES,
	CapturaTestCase,
	aislar_codigos,
	configurar,
	insertar_item,
	nueva_captura,
)


class TestMedidas(UnitTestCase):
	"""Tabla de la sección 5.2."""

	TABLA: ClassVar[dict[str, str]] = {
		"500 ml": "500 ml",
		"500ml": "500 ml",
		"500 cc": "500 ml",
		"500 centímetros cúbicos": "500 ml",
		"medio litro": "500 ml",
		"cuarto litro": "250 ml",
		"cuarto de litro": "250 ml",
		"un cuarto": "250 ml",
		"tres cuartos de litro": "750 ml",
		"litro": "1 L",
		"un litro": "1 L",
		"1 lt": "1 L",
		"1 l": "1 L",
		"1000 ml": "1 L",
		"litro y medio": "1.5 L",
		"1,5 litros": "1.5 L",
		"1.5 lts": "1.5 L",
		"1500 ml": "1.5 L",
		"dos litros y medio": "2.5 L",
		"2,5 L": "2.5 L",
		"tres litros": "3 L",
		"3 lts": "3 L",
		"trescientos cincuenta ml": "350 ml",
		"medio kilo": "500 g",
		"500 gr": "500 g",
		"500 grs": "500 g",
		"500 gramos": "500 g",
		"kilo": "1 kg",
		"un kilo": "1 kg",
		"1000 g": "1 kg",
		"1 kg": "1 kg",
		"kilo y medio": "1.5 kg",
		"cuarto de kilo": "250 g",
		"6 unidades": "6 u",
		"media docena": "6 u",
		"paquete de 6": "6 u",
		"docena": "12 u",
		"paquete de cincuenta": "50 u",
		"0.5 L": "500 ml",
	}

	NO_SOPORTADAS: ClassVar[tuple[str, ...]] = ("16 onzas", "2 libras", "un galón", "6 x 500 ml", "6x500ml")

	def test_tabla_de_unidades(self):
		for entrada, esperado in self.TABLA.items():
			with self.subTest(entrada=entrada):
				medida, advertencia = parsear_medida(entrada)
				self.assertIsNone(advertencia)
				self.assertIsNotNone(medida, entrada)
				self.assertEqual(medida.nombre(), esperado)

	def test_unidades_no_soportadas(self):
		for entrada in self.NO_SOPORTADAS:
			with self.subTest(entrada=entrada):
				self.assertEqual(parsear_medida(entrada), (None, naming.ADVERTENCIA_UNIDAD_NO_SOPORTADA))

	def test_medida_dentro_de_un_dictado(self):
		casos = {
			"coca cola sin azúcar de tres litros": "3 L",
			"pepsi de litro": "1 L",
			"del valle durazno medio litro": "500 ml",
			"fanta naranja litro y medio": "1.5 L",
			"agua vital sin gas dos litros y medio": "2.5 L",
			"jugo frut all de un litro": "1 L",
			"vaso descartable paquete de cincuenta": "50 u",
			"galleta de 36 g": "36 g",
		}
		for texto, esperado in casos.items():
			with self.subTest(texto=texto):
				self.assertEqual(parsear_medida(texto)[0].nombre(), esperado)

	def test_sin_medida(self):
		for texto in ("pop", "del valle durazno", "coca cola", ""):
			with self.subTest(texto=texto):
				self.assertEqual(parsear_medida(texto), (None, None))

	def test_formato_nombre_y_codigo(self):
		self.assertEqual(crear_medida(1.50, "L").codigo(), "1.5L")
		self.assertEqual(crear_medida(500, "ml").codigo(), "500ML")
		self.assertEqual(crear_medida(36, "g").codigo(), "36G")
		self.assertEqual(crear_medida(1, "kg").codigo(), "1KG")
		self.assertEqual(crear_medida(6, "u").codigo(), "6U")
		self.assertEqual(crear_medida(1.255, "L").nombre(), "1.25 L")
		self.assertIsNone(crear_medida(0, "L"))
		self.assertIsNone(crear_medida(3, "oz"))

	def test_resolver_medida_prefiere_python(self):
		medida, advertencias = resolver_medida(2, "L", "coca cola tres litros")
		self.assertEqual(medida, Medida(3, "L"))
		self.assertEqual(len(advertencias), 1)
		self.assertEqual(resolver_medida(1500, "ml", "litro y medio"), (Medida(1.5, "L"), []))
		self.assertEqual(resolver_medida(500, "ml", "sin medida en el texto"), (Medida(500, "ml"), []))
		self.assertEqual(resolver_medida(500, "ml", "6 x 500 ml")[0], None)


class TestTextoYAbreviaturas(UnitTestCase):
	def test_quitar_tildes(self):
		self.assertEqual(
			quitar_tildes("Sin Azúcar Cabaña Pingüino ÁÉÍÓÚÑ"), "Sin Azucar Cabana Pinguino AEIOUN"
		)

	def test_titulo(self):
		self.assertEqual(a_titulo("sin azúcar"), "Sin Azúcar")
		self.assertEqual(a_titulo("del valle"), "Del Valle")
		self.assertEqual(a_titulo("agua con gas", inicio=False), "Agua con Gas")
		self.assertEqual(a_titulo("con gas", inicio=False), "con Gas")
		self.assertEqual(a_titulo("frut-all"), "Frut-All")
		self.assertEqual(a_titulo("jugo de 2 l"), "Jugo de 2 L")

	def test_marca_corta(self):
		casos = {
			"Del Valle": "VALLE",
			"Frut-All": "FRUTALL",
			"Super Frut": "SUPERFRUT",
			"Guarana Antarctica": "GUARANA",
			"Coca-Cola": "COCACOLA",
			"Vaso Descartable": "VASO",
			"La Cabaña": "CABANA",
		}
		for marca, esperado in casos.items():
			with self.subTest(marca=marca):
				self.assertEqual(marca_corta_auto(marca), esperado)

	def test_valor_corto(self):
		casos = {
			"Durazno": "DUR",
			"Manzana": "MAN",
			"Naranja": "NAR",
			"Sin Azucar": "SINAZUCAR",
			"Sin Azúcar": "SINAZUCAR",
			"Sin Gas": "SINGAS",
			"Rojo": "ROJ",
		}
		for valor, esperado in casos.items():
			with self.subTest(valor=valor):
				self.assertEqual(valor_corto_auto(valor), esperado)

	def test_desambiguar(self):
		self.assertEqual(
			desambiguar_abreviaturas(["Mango", "Manzana", "Durazno"]),
			{"Mango": "MANG", "Manzana": "MANZ", "Durazno": "DUR"},
		)
		# Una abreviatura fija (abbr de Item Attribute Value) no cambia.
		self.assertEqual(
			desambiguar_abreviaturas(["Mango", "Manzana"], {"Manzana": "MAN"}),
			{"Mango": "MANG", "Manzana": "MAN"},
		)

	def test_glosario_y_brand(self):
		glosario = naming.construir_glosario([("coca cola, cocacola, coca", "Coca-Cola", "coca")])
		for dicho in ("coca cola", "CocaCola", "coca", "Coca-Cola", "cóca cola"):
			with self.subTest(dicho=dicho):
				self.assertEqual(
					normalizar_marca(dicho, glosario),
					naming.MarcaNormalizada("Coca-Cola", "COCA", "glosario"),
				)
		self.assertEqual(normalizar_marca("frut all", glosario, ["Frut-All"]).marca, "Frut-All")
		self.assertEqual(
			normalizar_marca("fanta", glosario, []), naming.MarcaNormalizada("Fanta", None, "nueva")
		)
		self.assertEqual(normalizar_marca("", glosario).origen, "")

	def test_plantilla_con_placeholders_vacios(self):
		plantilla = "{prefijo}-{marca_corta}-{variante_corta}-{medida}"
		self.assertEqual(componer_plantilla(plantilla, {"prefijo": "BEB", "marca_corta": "POP"}), "BEB-POP")
		self.assertEqual(componer_plantilla(plantilla, {"marca_corta": "POP", "medida": "2L"}), "POP-2L")
		self.assertEqual(componer_plantilla("{marca} {variante} {medida}", {"marca": "Pop"}), "Pop")
		self.assertEqual(naming.placeholders_invalidos("{marca} {sabor} {medida}"), ["sabor"])

	def test_largo_del_codigo(self):
		cfg = ConfigNombre(largo_max_codigo=20)
		datos = DatosNombre(
			prefijo="BEB", marca="Supermarca", marca_corta="SUPERMARCA", variante="Sin Azucar Extra",
			variante_corta="SINAZUCAREXT", medida=Medida(1.5, "L"),
		)  # fmt: skip
		resultado = componer(cfg, datos)
		self.assertEqual(resultado.item_code, "BEB-SUPERM-SIN-1.5L")
		self.assertTrue(resultado.advertencias)

	def test_nombre_demasiado_largo_no_se_trunca(self):
		cfg = ConfigNombre(largo_max_nombre=15)
		resultado = componer(cfg, DatosNombre(prefijo="BEB", marca="Coca-Cola", variante="Sin Azucar"))
		self.assertEqual(resultado.item_name, "Coca-Cola Sin Azucar")
		self.assertTrue(any("caracteres" in a for a in resultado.advertencias))


class TestEjemplosSpec(CapturaTestCase):
	"""Los 8 ejemplos de la sección 5.7 y el ejemplo multi-atributo de la sección 13."""

	def setUp(self):
		super().setUp()
		# B12: los códigos esperados no deben chocar con Items o capturas reales del sitio.
		aislar_codigos("BEB-%", "DES-%", "GAL-%")

	def componer_simple(self, dictado: str, marca: str, variante: str | None, grupo: str = GRUPO_BEBIDAS):
		medida, _advertencias = resolver_medida(None, None, dictado)
		doc = nueva_captura(
			transcripcion=dictado,
			marca=marca,
			variante_descripcion=variante,
			medida_valor=medida.valor if medida else None,
			medida_unidad=medida.unidad if medida else None,
			item_group=grupo,
		)
		naming.calcular_nombres(doc)
		return doc

	def test_ejemplos_simples(self):
		casos = [
			("coca cola sin azúcar de tres litros", "coca cola", "sin azúcar", "Coca-Cola Sin Azucar 3 L",
			 "BEB-COCA-SINAZUCAR-3L"),
			("pepsi de litro", "pepsi", None, "Pepsi 1 L", "BEB-PEPSI-1L"),
			("del valle durazno medio litro", "del valle", "durazno", "Del Valle Durazno 500 ml",
			 "BEB-VALLE-DUR-500ML"),
			("fanta naranja litro y medio", "fanta", "naranja", "Fanta Naranja 1.5 L", "BEB-FANTA-NAR-1.5L"),
			("agua vital sin gas dos litros y medio", "vital", "sin gas", "Vital Sin Gas 2.5 L",
			 "BEB-VITAL-SINGAS-2.5L"),
		]  # fmt: skip
		for dictado, marca, variante, nombre, codigo in casos:
			with self.subTest(dictado=dictado):
				doc = self.componer_simple(dictado, marca, variante)
				self.assertEqual(doc.item_name, nombre)
				self.assertEqual(doc.item_code, codigo)

	def test_ejemplo_6_variantes_sabor(self):
		doc = nueva_captura(
			transcripcion="jugo frut all de un litro",
			marca="frut all",
			medida_valor=1,
			medida_unidad="L",
			tiene_variantes=1,
			atributos=[{"atributo": "Sabor"}],
			variantes=[{"valor_1": "durazno"}, {"valor_1": "Manzana"}],
		)
		naming.calcular_nombres(doc)
		self.assertEqual((doc.item_name, doc.item_code), ("Frut-All 1 L", "BEB-FRUTALL-1L"))
		self.assertEqual(
			[(v.item_name, v.item_code) for v in doc.variantes],
			[("Frut-All Durazno 1 L", "BEB-FRUTALL-DUR-1L"), ("Frut-All Manzana 1 L", "BEB-FRUTALL-MAN-1L")],
		)
		self.assertEqual(doc.variantes[0].valor_1, "Durazno")  # grafía exacta del Item Attribute Value
		self.assertEqual(doc.variantes[0].valor_corto_1, "DUR")

	def test_ejemplo_7_variantes_tamano(self):
		doc = nueva_captura(
			transcripcion="pop",
			marca="pop",
			tiene_variantes=1,
			atributos=[{"atributo": "Tamaño"}],
			variantes=[{"valor_1": "dos litros"}, {"valor_1": "tres litros"}],
		)
		naming.calcular_nombres(doc)
		self.assertEqual((doc.item_name, doc.item_code), ("Pop", "BEB-POP"))
		self.assertEqual(
			[(v.item_name, v.item_code, v.valor_1) for v in doc.variantes],
			[("Pop 2 L", "BEB-POP-2L", "2 L"), ("Pop 3 L", "BEB-POP-3L", "3 L")],
		)

	def test_ejemplo_8_variantes_color_sin_marca(self):
		medida, _adv = resolver_medida(None, None, "vaso descartable paquete de cincuenta")
		doc = nueva_captura(
			transcripcion="vaso descartable paquete de cincuenta",
			tipo_producto="vaso descartable",
			medida_valor=medida.valor,
			medida_unidad=medida.unidad,
			item_group=GRUPO_DESCARTABLES,
			tiene_variantes=1,
			atributos=[{"atributo": "Color"}],
			variantes=[{"valor_1": "rojo"}, {"valor_1": "azul"}],
		)
		naming.calcular_nombres(doc)
		self.assertEqual((doc.item_name, doc.item_code), ("Vaso Descartable 50 u", "DES-VASO-50U"))
		self.assertEqual(
			[(v.item_name, v.item_code) for v in doc.variantes],
			[
				("Vaso Descartable Rojo 50 u", "DES-VASO-ROJ-50U"),
				("Vaso Descartable Azul 50 u", "DES-VASO-AZU-50U"),
			],
		)

	def test_multi_atributo_seccion_13(self):
		doc = nueva_captura(
			transcripcion="jugo frut all",
			marca="Frut-All",
			medida_valor=1,
			medida_unidad="L",
			tiene_variantes=1,
			atributos=[{"atributo": "Tamaño", "orden": 2}, {"atributo": "Sabor", "orden": 1}],
			variantes=[
				{"transcripcion": "sabor durazno de dos litros", "valor_1": "Durazno", "valor_2": "2 litros"},
				{"transcripcion": "manzana medio litro", "valor_1": "Manzana", "valor_2": "medio litro"},
			],
		)
		naming.calcular_nombres(doc)
		self.assertEqual(
			[(v.item_name, v.item_code) for v in doc.variantes],
			[
				("Frut-All Durazno 2 L", "BEB-FRUTALL-DUR-2L"),
				("Frut-All Manzana 500 ml", "BEB-FRUTALL-MAN-500ML"),
			],
		)
		self.assertEqual([v.valor_2 for v in doc.variantes], ["2 L", "500 ml"])
		self.assertEqual([v.valor_corto_2 for v in doc.variantes], ["2L", "500ML"])

	def test_valores_nuevos_se_desambiguan(self):
		doc = nueva_captura(
			marca="del valle",
			medida_valor=1,
			medida_unidad="L",
			tiene_variantes=1,
			atributos=[{"atributo": "Sabor"}],
			variantes=[{"valor_1": "mango"}, {"valor_1": "Mandarina"}, {"valor_1": "manzana"}],
		)
		naming.calcular_nombres(doc)
		# Manzana ya existe con abbr MAN (fija); Mango y Mandarina se alargan hasta distinguirse.
		self.assertEqual(
			[v.item_code for v in doc.variantes],
			["BEB-VALLE-MANG-1L", "BEB-VALLE-MAND-1L", "BEB-VALLE-MAN-1L"],
		)

	def test_grupo_sin_prefijo_usa_tres_letras(self):
		doc = self.componer_simple("galleta mabel 36 g", "mabel", None, grupo="Galletas")
		self.assertEqual(doc.item_code, "GAL-MABEL-36G")
		advertencias = naming.calcular_nombres(doc)
		self.assertTrue(any("no tiene prefijo" in a for a in advertencias))

	def test_plantilla_configurable(self):
		configurar(plantilla_nombre="{tipo} {marca} {variante} {medida}")
		doc = nueva_captura(tipo_producto="jugo", marca="jumbo", variante_descripcion="durazno",
			medida_valor=2.5, medida_unidad="L")  # fmt: skip
		naming.calcular_nombres(doc)
		self.assertEqual(doc.item_name, "Jugo Jumbo Durazno 2.5 L")

	def test_unicidad_con_sufijo(self):
		insertar_item("BEB-PEPSI-1L", "Pepsi 1 L")
		doc = self.componer_simple("pepsi de litro", "pepsi", None)
		self.assertEqual(doc.item_code, "BEB-PEPSI-1L-2")
		advertencias = naming.recalcular_nombres(doc)
		self.assertIn("Ya existe BEB-PEPSI-1L 'Pepsi 1 L': verifique si es el mismo producto", advertencias)
		self.assertIn("[Nombre] Ya existe BEB-PEPSI-1L", doc.advertencias_ia)

		# Otra captura activa con el mismo código también lo reserva.
		otra = self.componer_simple("pepsi de litro", "pepsi", None)
		otra.insert()
		otra.db_set({"estado": "Por revisar", "item_code": "BEB-PEPSI-1L-2"})
		doc = self.componer_simple("pepsi de litro", "pepsi", None)
		self.assertEqual(doc.item_code, "BEB-PEPSI-1L-3")

	def test_unicidad_entre_variantes_de_la_misma_captura(self):
		doc = nueva_captura(
			marca="pop",
			tiene_variantes=1,
			atributos=[{"atributo": "Tamaño"}],
			variantes=[{"valor_1": "2 L"}, {"valor_1": "dos litros"}],
		)
		naming.calcular_nombres(doc)
		self.assertEqual([v.item_code for v in doc.variantes], ["BEB-POP-2L", "BEB-POP-2L-2"])
