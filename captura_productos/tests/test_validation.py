from frappe.tests import UnitTestCase

from captura_productos import validation
from captura_productos.tests.utils import CapturaTestCase, configurar, insertar_item, nueva_captura
from captura_productos.validation import (
	Advertencia,
	analizar_barcode,
	combinaciones_repetidas,
	digito_verificador_gtin,
	formas_equivalentes,
	gtin_valido,
	valor_numerico_valido,
)

EAN13_VALIDO = "4006381333931"
EAN13_INVALIDO = "4006381333932"
EAN8_VALIDO = "96385074"
UPCA_VALIDO = "036000291452"


class TestCodigosDeBarras(UnitTestCase):
	def test_digito_verificador(self):
		self.assertEqual(digito_verificador_gtin("400638133393"), 1)
		self.assertEqual(digito_verificador_gtin("9638507"), 4)
		self.assertEqual(digito_verificador_gtin("03600029145"), 2)
		self.assertEqual(digito_verificador_gtin("777160900209"), 4)  # Aquarius 300 ml (sección 7.3)

	def test_gtin_valido(self):
		for codigo in (EAN13_VALIDO, EAN8_VALIDO, UPCA_VALIDO, "7771609002094"):
			with self.subTest(codigo=codigo):
				self.assertTrue(gtin_valido(codigo))
		for codigo in (EAN13_INVALIDO, "96385075", "036000291453", "12345", "ABCDEFGH"):
			with self.subTest(codigo=codigo):
				self.assertFalse(gtin_valido(codigo))

	def test_tipo_segun_longitud(self):
		self.assertEqual(analizar_barcode(EAN13_VALIDO).tipo, "EAN-13")
		self.assertEqual(analizar_barcode(EAN8_VALIDO).tipo, "EAN-8")
		self.assertEqual(analizar_barcode(UPCA_VALIDO).tipo, "UPC-A")
		self.assertEqual(analizar_barcode(" 4006 3813 33931 ").codigo, EAN13_VALIDO)

	def test_verificador_invalido_bloquea(self):
		analisis = analizar_barcode(EAN13_INVALIDO)
		self.assertFalse(analisis.valido)
		self.assertTrue(analisis.advertencia.bloqueante)
		self.assertIn("mal leído", analisis.advertencia.mensaje)

	def test_codigo_interno_se_acepta_con_aviso(self):
		analisis = analizar_barcode("130885")  # HU-04: código interno
		self.assertTrue(analisis.valido)
		self.assertEqual(analisis.tipo, "")
		self.assertFalse(analisis.advertencia.bloqueante)

	def test_formato_invalido(self):
		for codigo in ("12", "ABC$123", "1" * 21, ""):
			with self.subTest(codigo=codigo):
				self.assertFalse(analizar_barcode(codigo).valido)

	def test_formas_equivalentes(self):
		self.assertEqual(formas_equivalentes(UPCA_VALIDO), [UPCA_VALIDO, "0" + UPCA_VALIDO])
		self.assertEqual(formas_equivalentes("0" + UPCA_VALIDO), ["0" + UPCA_VALIDO, UPCA_VALIDO])
		self.assertEqual(formas_equivalentes(EAN13_VALIDO), [EAN13_VALIDO])


class TestReglasPuras(UnitTestCase):
	def test_valor_numerico(self):
		self.assertIsNone(valor_numerico_valido("12", 6, 48, 6))
		self.assertIsNone(valor_numerico_valido("12,0", 6, 48, 6))
		self.assertIsNotNone(valor_numerico_valido("10", 6, 48, 6))  # no respeta el incremento
		self.assertIsNotNone(valor_numerico_valido("54", 6, 48, 6))  # fuera de rango
		self.assertIsNotNone(valor_numerico_valido("doce", 6, 48, 6))

	def test_combinaciones_repetidas(self):
		self.assertEqual(
			combinaciones_repetidas([("Durazno", "2 L"), ("Durazno", "3 L"), ("durazno", "2 L")]), [3]
		)
		self.assertEqual(combinaciones_repetidas([("Durazno",), ("Manzana",)]), [])

	def test_fusionar_conserva_lineas_de_ia_y_nombre(self):
		actual = "La foto no coincide con el dictado\n[Aviso] viejo\n[Nombre] Ya existe X"
		nuevo = validation.fusionar_advertencias(actual, [Advertencia("nuevo", True)])
		self.assertEqual(
			nuevo.splitlines(),
			["[Bloquea] nuevo", "La foto no coincide con el dictado", "[Nombre] Ya existe X"],
		)


class TestValidarCaptura(CapturaTestCase):
	def bloqueantes(self, doc, para_aprobar=True) -> list[str]:
		return validation.bloqueantes_de(validation.validar_captura(doc, para_aprobar=para_aprobar))

	def mensajes(self, doc, para_aprobar=True) -> list[str]:
		return [a.mensaje for a in validation.validar_captura(doc, para_aprobar=para_aprobar)]

	def captura_valida(self, **campos):
		datos = {
			"marca": "Pepsi",
			"marca_corta": "PEPSI",
			"medida_valor": 2,
			"medida_unidad": "L",
			"item_name": "Pepsi 2 L",
			"item_code": "BEB-PEPSI-2L",
			"codigos_barras": [{"barcode": EAN13_VALIDO}],
		}
		datos.update(campos)
		return nueva_captura(**datos)

	def test_captura_valida_no_tiene_bloqueantes(self):
		doc = self.captura_valida()
		self.assertEqual(self.bloqueantes(doc), [])
		self.assertEqual(doc.codigos_barras[0].barcode_type, "EAN-13")

	def test_obligatorios_para_aprobar(self):
		doc = self.captura_valida(item_name=None, item_code=None, item_group=None, stock_uom=None)
		bloqueantes = self.bloqueantes(doc)
		self.assertEqual(len(bloqueantes), 4, bloqueantes)
		# En Borrador no se exigen.
		self.assertEqual(self.bloqueantes(doc, para_aprobar=False), [])

	def test_regex_y_largo_de_item_code(self):
		doc = self.captura_valida(item_code="beb pepsi")
		self.assertTrue(any("no es válido" in b for b in self.bloqueantes(doc)))
		configurar(largo_max_nombre=10)
		doc = self.captura_valida(item_name="Pepsi Cola 2 L")
		self.assertTrue(any("máximo 10" in b for b in self.bloqueantes(doc)))

	def test_item_code_existente_bloquea(self):
		insertar_item("BEB-PEPSI-2L", "Pepsi 2 L")
		self.assertTrue(any("ya existe el Item" in b for b in self.bloqueantes(self.captura_valida())))

	def test_barcode_en_item_bloquea(self):
		insertar_item("BEB-COCA-500ML", "Coca-Cola 500 ml", barcodes=(UPCA_VALIDO,))
		# Mismo código escrito como EAN-13 (con 0 adelante): también es duplicado.
		doc = self.captura_valida(codigos_barras=[{"barcode": "0" + UPCA_VALIDO}])
		bloqueantes = self.bloqueantes(doc)
		self.assertTrue(any("Coca-Cola 500 ml (BEB-COCA-500ML)" in b for b in bloqueantes), bloqueantes)

	def test_barcode_en_otra_captura_es_aviso(self):
		otra = self.captura_valida(item_code="BEB-PEPSI-2L-X")
		otra.insert()
		doc = self.captura_valida()
		mensajes = self.mensajes(doc)
		self.assertIn(f"El código {EAN13_VALIDO} ya fue capturado en {otra.name}", mensajes)
		self.assertEqual(doc.codigos_barras[0].duplicado_en, otra.name)
		self.assertEqual(self.bloqueantes(doc), [])

	def test_barcode_repetido_en_la_misma_captura(self):
		doc = self.captura_valida(codigos_barras=[{"barcode": EAN13_VALIDO}, {"barcode": EAN13_VALIDO}])
		self.assertEqual(self.bloqueantes(doc), [])
		self.assertEqual(len(doc.codigos_barras), 1)  # se ignora el segundo (HU-03)

	def test_barcode_mal_leido_bloquea(self):
		doc = self.captura_valida(codigos_barras=[{"barcode": EAN13_INVALIDO}])
		self.assertTrue(any("mal leído" in b for b in self.bloqueantes(doc, para_aprobar=False)))

	def test_grupo_y_uom_deben_existir(self):
		doc = self.captura_valida(item_group="All Item Groups", stock_uom="No Existe")
		bloqueantes = self.bloqueantes(doc)
		self.assertTrue(any("agrupa otros grupos" in b for b in bloqueantes))
		self.assertTrue(any("La unidad No Existe no existe" in b for b in bloqueantes))

	def test_glosario_de_marcas(self):
		mensajes = self.mensajes(self.captura_valida(marca="coca cola"))
		self.assertIn("La marca coca cola se escribe Coca-Cola: use Recalcular nombre", mensajes)
		mensajes = self.mensajes(self.captura_valida(marca="Inventada"))
		self.assertTrue(any(m.startswith("Marca nueva: Inventada") for m in mensajes))

	def variantes(self, **campos):
		datos = {
			"tiene_variantes": 1,
			"codigos_barras": [],
			"item_name": "Frut-All 1 L",
			"item_code": "BEB-FRUTALL-1L",
			"atributos": [{"atributo": "Sabor"}],
			"variantes": [
				{"valor_1": "Durazno", "item_name": "Frut-All Durazno 1 L", "item_code": "BEB-FRUTALL-DUR-1L",
				 "barcode": EAN8_VALIDO},
				{"valor_1": "Manzana", "item_name": "Frut-All Manzana 1 L", "item_code": "BEB-FRUTALL-MAN-1L"},
			],
		}  # fmt: skip
		datos.update(campos)
		return self.captura_valida(**datos)

	def test_variantes_validas(self):
		doc = self.variantes()
		self.assertEqual(self.bloqueantes(doc), [])
		self.assertEqual(doc.variantes[0].barcode_type, "EAN-8")
		self.assertEqual(doc.variantes[0].valor_corto_1, "DUR")

	def test_variantes_reglas_bloqueantes(self):
		doc = self.variantes(codigos_barras=[{"barcode": EAN13_VALIDO}])
		self.assertTrue(any("van en cada variante" in b for b in self.bloqueantes(doc)))

		doc = self.variantes(variantes=[])
		self.assertTrue(any("no hay ninguna" in b for b in self.bloqueantes(doc)))

		doc = self.variantes(atributos=[])
		self.assertTrue(any("al menos un atributo" in b for b in self.bloqueantes(doc)))

		doc = self.variantes()
		doc.variantes[1].valor_1 = "durazno"
		doc.variantes[1].item_code = "BEB-FRUTALL-DUR-1L"
		bloqueantes = self.bloqueantes(doc)
		self.assertTrue(any("repite la combinación" in b for b in bloqueantes), bloqueantes)
		self.assertTrue(any("se repite en" in b for b in bloqueantes), bloqueantes)

		doc = self.variantes()
		doc.variantes[1].valor_1 = ""
		self.assertTrue(any("falta el valor de Sabor" in b for b in self.bloqueantes(doc)))

		doc = self.variantes()
		doc.variantes[1].barcode = EAN8_VALIDO
		self.assertTrue(any("repetido en esta captura" in b for b in self.bloqueantes(doc)))

	def test_valor_nuevo_es_aviso(self):
		doc = self.variantes()
		doc.variantes[1].valor_1 = "maracuya"
		mensajes = self.mensajes(doc)
		self.assertIn("Valor nuevo: Maracuya en atributo Sabor", mensajes)
		self.assertEqual(self.bloqueantes(doc), [])

	def test_atributo_numerico(self):
		doc = self.variantes(atributos=[{"atributo": "Paquete"}])
		doc.variantes[0].valor_1 = "12"
		doc.variantes[1].valor_1 = "10"
		de_paquete = [b for b in self.bloqueantes(doc) if "Paquete" in b]
		self.assertEqual(len(de_paquete), 1, de_paquete)
		self.assertIn("Variante 2", de_paquete[0])
		self.assertIn("incremento", de_paquete[0])

	def test_mas_de_tres_atributos(self):
		doc = self.variantes(atributos=[{"atributo": a} for a in ("Sabor", "Tamaño", "Color", "Paquete")])
		self.assertTrue(any("máximo 3 atributos" in b for b in self.bloqueantes(doc)))
