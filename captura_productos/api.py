"""Endpoints whitelisted para la pantalla /captura (spec sección 1, ui-spec 8.4 y 8.5).

Todos exigen sesión iniciada y un rol de la app. Las consultas a Item / Item Barcode se hacen en el
servidor sin pasar por los permisos del capturador (que no tiene acceso a Item), devolviendo solo
lo necesario.

Envío desde el celular (decisión, ui-spec 8.5): el borrador vive en el celular hasta que se toca
"Enviar". Entonces el cliente hace, en orden y con reintentos:

1. `crear_captura(client_uuid)`: crea la captura en Borrador o devuelve la que ya tiene ese uuid.
2. `subir_foto(captura)` una vez por foto (producto y variantes): cada foto va en su propio request,
   así se puede mostrar "Subiendo foto i de n" y un corte de red no obliga a subir todo de nuevo.
   Subir dos veces la misma foto devuelve el mismo archivo (se compara el hash del contenido).
3. `enviar_captura(captura, datos)`: guarda en una sola escritura la transcripción, las fotos, los
   códigos y las variantes, y llama a `enviar()` del controlador (Borrador → Procesando + IA).
   Si la captura ya se había enviado, devuelve su estado sin tocarla (reintento idempotente).

`guardar_paso` guarda los mismos datos sin enviar (por si se quiere sincronizar el borrador por pasos).
"""

from __future__ import annotations

import io
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

from captura_productos import validation
from captura_productos.captura_productos.doctype.captura_de_producto.captura_de_producto import (
	LARGO_MAX_TRANSCRIPCION,
	MAX_CODIGOS,
	MAX_FOTOS,
	MAX_VARIANTES,
	ROLES_SUPERVISOR,
	es_supervisor,
)

DOCTYPE = "Captura de Producto"
ROL_CAPTURADOR = "Capturador de Productos"
ROLES_APP = ROLES_SUPERVISOR | {ROL_CAPTURADOR}
DIAS_MIS_CAPTURAS = 30
LIMITE_MAX_MIS_CAPTURAS = 200

# Límites de las secciones 4 y 8 de la spec (el celular usa 4 fotos; el servidor acepta hasta 6). Los
# máximos de fotos, códigos, variantes y largo del dictado están en el controlador (validate()).
MAX_BYTES_FOTO = 2 * 1024 * 1024
MAX_LADO_FOTO = 2000  # el celular reduce a 1280 px; más que esto no es una foto de /captura
FORMATOS_FOTO = {"JPEG": "jpg", "PNG": "png"}
MAX_ARCHIVOS_POR_CAPTURA = MAX_FOTOS + MAX_VARIANTES + 10  # margen para fotos cambiadas
REGEX_UUID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
LARGO_HUELLA = 12  # caracteres del hash del contenido en el nombre del archivo

# Llamadas por minuto y por usuario (spec sección 1: enviar 30/min).
LIMITES_POR_MINUTO = {"crear": 30, "foto": 150, "enviar": 30}


def _exigir_rol_app() -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("Inicie sesión para continuar."), frappe.AuthenticationError)
	if not tiene_rol_app():
		frappe.throw(_("No tiene permiso para usar la captura de productos."), frappe.PermissionError)


def tiene_rol_app(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Guest":
		return False
	return user == "Administrator" or bool(ROLES_APP.intersection(frappe.get_roles(user)))


def clave_limite(accion: str, user: str) -> bytes:
	return frappe.cache.make_key(f"captura_productos:limite:{accion}:{user}")


def limitar(accion: str, segundos: int = 60) -> None:
	"""Límite por usuario (no por IP: en la tienda varios celulares comparten la misma red).

	`frappe.rate_limiter.rate_limit` cuenta por IP o por un campo del request, por eso se usa el
	mismo mecanismo (contador en Redis con vencimiento) pero con el usuario como clave. INCR y EXPIRE
	van en una transacción de Redis (MULTI/EXEC) y EXPIRE NX solo fija el vencimiento si la clave no
	lo tiene: un corte entre los dos comandos ya no deja un contador sin vencimiento.
	"""
	clave = clave_limite(accion, frappe.session.user)
	tuberia = frappe.cache.pipeline(transaction=True)
	tuberia.incr(clave)
	tuberia.expire(clave, segundos, nx=True)
	cantidad = tuberia.execute()[0]
	if cantidad > LIMITES_POR_MINUTO[accion]:
		frappe.throw(
			_("Demasiados intentos seguidos. Espera un minuto y vuelve a intentar."),
			frappe.RateLimitExceededError,
		)


def _nombre_usuario(user: str | None) -> str | None:
	if not user:
		return None
	return frappe.db.get_value("User", user, "full_name") or user


@frappe.whitelist(methods=["GET", "POST"])
def verificar_codigo(
	codigo: str | None = None, captura: str | None = None, barcode: str | None = None
) -> dict:
	"""Valida el dígito verificador y busca duplicados (HU-03 y HU-04).

	`barcode` es un alias de `codigo` (la spec usa ese nombre). `captura` excluye la captura actual.

	Respuesta:
	    codigo, barcode_type, valido, mensaje,
	    existe, bloquea, origen ("item" | "captura" | None),
	    item_code, item_name, nombre, captura, capturado_por, estado
	"""
	_exigir_rol_app()
	analisis = validation.analizar_barcode(codigo or barcode)
	respuesta = {
		"codigo": analisis.codigo,
		"barcode_type": analisis.tipo,
		"valido": analisis.valido,
		"mensaje": analisis.advertencia.mensaje if analisis.advertencia else None,
		"existe": False,
		"bloquea": False,
		"origen": None,
		"item_code": None,
		"item_name": None,
		"nombre": None,
		"captura": None,
		"capturado_por": None,
		"estado": None,
	}
	if not analisis.valido:
		return respuesta

	en_items = validation.buscar_en_items([analisis.codigo])
	if analisis.codigo in en_items:
		existente = en_items[analisis.codigo]
		respuesta.update(
			existe=True,
			bloquea=True,
			origen="item",
			item_code=existente.item_code,
			item_name=existente.item_name,
			nombre=existente.item_name,
			mensaje=_("Este producto ya existe: {0} ({1})").format(existente.item_name, existente.item_code),
		)
		return respuesta

	en_capturas = validation.buscar_en_capturas([analisis.codigo], excluir_captura=captura)
	if analisis.codigo in en_capturas:
		existente = en_capturas[analisis.codigo]
		respuesta.update(
			existe=True,
			origen="captura",
			item_code=existente.item_code,
			item_name=existente.item_name,
			nombre=existente.item_name or existente.transcripcion,
			captura=existente.captura,
			capturado_por=_nombre_usuario(existente.capturado_por),
			estado=existente.estado,
			mensaje=_("Ya fue capturado en {0}").format(existente.captura),
		)
	return respuesta


@frappe.whitelist(methods=["GET"])
def mis_capturas(limit: int | str = 50) -> list[dict]:
	"""Capturas propias de los últimos 30 días, la más reciente primero (HU-06, ui-spec S8)."""
	_exigir_rol_app()
	limite = min(max(cint(limit) or 50, 1), LIMITE_MAX_MIS_CAPTURAS)
	filas = frappe.get_all(
		"Captura de Producto",
		filters={
			"capturado_por": frappe.session.user,
			"fecha_captura": [">=", add_days(now_datetime(), -DIAS_MIS_CAPTURAS)],
		},
		fields=[
			"name",
			"item_name",
			"transcripcion",
			"estado",
			"imagen_principal",
			"fecha_captura",
			"mensaje_capturador",
			"client_uuid",
			"tiene_variantes",
		],
		order_by="fecha_captura desc",
		limit=limite,
	)
	n_variantes = _contar_variantes([f.name for f in filas])
	return [
		{
			"name": f.name,
			"item_name": f.item_name,
			"transcripcion": f.transcripcion,
			"estado": f.estado,
			"foto_principal": f.imagen_principal,
			"n_variantes": n_variantes.get(f.name, 0) if f.tiene_variantes else 0,
			"creado": f.fecha_captura,
			"mensaje_error_simple": f.mensaje_capturador if f.estado == "Error" else None,
			"client_uuid": f.client_uuid,
		}
		for f in filas
	]


def _contar_variantes(capturas: list[str]) -> dict[str, int]:
	if not capturas:
		return {}
	filas = frappe.db.sql(
		"""
		select parent, count(*) from `tabCaptura Variante`
		where parenttype = 'Captura de Producto' and parent in %(capturas)s
		group by parent
		""",
		{"capturas": tuple(capturas)},
	)
	return {parent: total for parent, total in filas}


# ---------------------------------------------------------------------------------------------------
# Envío desde /captura
# ---------------------------------------------------------------------------------------------------


def _resumen(doc, **extra) -> dict:
	return {"name": doc.name, "estado": doc.estado, "client_uuid": doc.client_uuid, **extra}


def _captura_propia(captura: str | None):
	"""La captura si es del usuario (o si es supervisor); si no, error de permiso."""
	if not captura or not frappe.db.exists(DOCTYPE, captura):
		frappe.throw(_("No se encontró la captura {0}.").format(captura or ""), frappe.DoesNotExistError)
	doc = frappe.get_doc(DOCTYPE, captura)
	if doc.capturado_por != frappe.session.user and not es_supervisor():
		frappe.throw(_("No tienes permiso sobre la captura {0}.").format(captura), frappe.PermissionError)
	return doc


def _captura_editable(captura: str | None):
	doc = _captura_propia(captura)
	if doc.estado != "Borrador":
		frappe.throw(_("Esta captura ya se envió y no se puede modificar."), frappe.PermissionError)
	return doc


@frappe.whitelist(methods=["POST"])
def crear_captura(client_uuid: str | None = None) -> dict:
	"""T1: crea una captura en Borrador o, si `client_uuid` ya existe, devuelve esa misma.

	Respuesta: name, estado, client_uuid, creada (False si ya existía).
	"""
	_exigir_rol_app()
	limitar("crear")
	client_uuid = (client_uuid or "").strip() or None
	if client_uuid and not REGEX_UUID.match(client_uuid):
		frappe.throw(_("Identificador de captura no válido."))

	if client_uuid:
		existente = _por_uuid(client_uuid)
		if existente:
			return _resumen(existente, creada=False)

	doc = frappe.get_doc({"doctype": DOCTYPE, "client_uuid": client_uuid})
	try:
		# El capturador solo tiene permiso de lectura: las capturas se crean por esta API.
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError, frappe.DuplicateEntryError:
		# Dos reintentos simultáneos con el mismo uuid: gana el primero. Se relee con bloqueo: una
		# lectura normal usaría la foto de la transacción (REPEATABLE READ) y no vería la otra fila.
		existente = _por_uuid(client_uuid, bloquear=True) if client_uuid else None
		if not existente:
			raise
		return _resumen(existente, creada=False)
	return _resumen(doc, creada=True)


def _por_uuid(client_uuid: str, bloquear: bool = False):
	nombre = frappe.db.get_value(DOCTYPE, {"client_uuid": client_uuid}, "name", for_update=bloquear)
	if not nombre:
		return None
	doc = frappe.get_doc(DOCTYPE, nombre)
	if doc.capturado_por != frappe.session.user:
		# El uuid lo genera el celular de quien captura; si es de otra persona no se revela nada.
		frappe.throw(_("Identificador de captura no válido."), frappe.PermissionError)
	return doc


@frappe.whitelist(methods=["POST"])
def subir_foto(captura: str) -> dict:
	"""Adjunta a la captura una foto JPEG o PNG de 2 MB como máximo, como archivo público (spec 12.3).

	La foto llega en el campo multipart `file`. Respuesta: file_url y `repetida` (True si esa misma
	foto ya estaba subida a esta captura, por un reintento).
	"""
	_exigir_rol_app()
	limitar("foto")
	doc = _captura_editable(captura)
	return guardar_foto(doc, _contenido_del_request())


def _contenido_del_request() -> bytes:
	archivos = frappe.request.files if frappe.request else None
	archivo = archivos.get("file") if archivos else None
	if not archivo:
		frappe.throw(_("No llegó ninguna foto."))
	# Se lee un byte más que el máximo para detectar archivos grandes sin cargarlos enteros.
	return archivo.stream.read(MAX_BYTES_FOTO + 1)


def guardar_foto(doc, contenido: bytes) -> dict:
	if not contenido:
		frappe.throw(_("La foto llegó vacía."))
	if len(contenido) > MAX_BYTES_FOTO:
		frappe.throw(_("La foto pesa más de 2 MB. Vuelve a tomarla."))
	extension = _validar_imagen(contenido)

	from frappe.core.doctype.file.utils import get_content_hash

	adjunta_a = {"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name}
	# El hash del contenido recibido va en el nombre del archivo: Frappe puede reescribir el JPEG
	# (quitar EXIF) y entonces su `content_hash` ya no coincide con lo que mandó el celular.
	huella = get_content_hash(contenido)[:LARGO_HUELLA]
	nombre_archivo = f"{doc.name}-{huella}"
	repetida = frappe.db.get_value(
		"File", {**adjunta_a, "file_name": ["like", f"{nombre_archivo}%"]}, "file_url"
	) or frappe.db.get_value("File", {**adjunta_a, "content_hash": get_content_hash(contenido)}, "file_url")
	if repetida:
		return {"file_url": repetida, "repetida": True}
	if frappe.db.count("File", adjunta_a) >= MAX_ARCHIVOS_POR_CAPTURA:
		frappe.throw(_("Esta captura ya tiene demasiadas fotos."))

	_exigir_archivos_publicos_permitidos()
	archivo = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{nombre_archivo}.{extension}",
			"is_private": 0,
			"content": contenido,
			**adjunta_a,
		}
	)
	# El capturador no tiene permiso sobre File; ya se comprobó que es dueño de la captura.
	archivo.insert(ignore_permissions=True)
	return {"file_url": archivo.file_url, "repetida": False}


def _exigir_archivos_publicos_permitidos() -> None:
	"""Las fotos se guardan públicas (spec 12.3). Si System Settings tiene activado "Only allow System
	Managers to upload public files", Frappe rechaza la foto del capturador con un mensaje en inglés
	que no le dice nada: se avisa claro y se deja el detalle para el administrador (docs/DEV.md)."""
	if "System Manager" in frappe.get_roles() or not frappe.get_system_settings(
		"only_allow_system_managers_to_upload_public_files"
	):
		return
	frappe.log_error(
		title=_("Captura de productos: fotos públicas bloqueadas"),
		message=_(
			"System Settings tiene activado 'Only allow System Managers to upload public files' y la "
			"captura necesita fotos públicas. Desactívelo (ver docs/DEV.md)."
		),
	)
	frappe.throw(_("No se pudo guardar la foto por un ajuste del sistema. Avisa al administrador."))


def _validar_imagen(contenido: bytes) -> str:
	"""Extensión del archivo si es un JPEG o PNG legible y de tamaño razonable; si no, error."""
	from PIL import Image

	formato, ancho, alto = None, 0, 0
	try:
		with Image.open(io.BytesIO(contenido)) as imagen:
			formato = imagen.format
			ancho, alto = imagen.size
			imagen.verify()
	except Image.DecompressionBombError, OSError, SyntaxError, ValueError:
		formato = None
	if formato not in FORMATOS_FOTO:
		frappe.throw(_("El archivo no es una foto JPEG o PNG."))
	if max(ancho, alto) > MAX_LADO_FOTO:
		frappe.throw(_("La foto es demasiado grande ({0} x {1} píxeles).").format(ancho, alto))
	return FORMATOS_FOTO[formato]


@frappe.whitelist(methods=["POST"])
def guardar_paso(captura: str, datos: str | dict) -> dict:
	"""Guarda transcripción, fotos, códigos y variantes de una captura en Borrador (formato de
	`datos` en `aplicar_datos`)."""
	_exigir_rol_app()
	doc = _captura_editable(captura)
	aplicar_datos(doc, frappe.parse_json(datos) or {})
	doc.save(ignore_permissions=True)  # dueño y estado Borrador ya comprobados
	return _resumen(doc)


@frappe.whitelist(methods=["POST"])
def enviar_captura(captura: str, datos: str | dict | None = None) -> dict:
	"""T2: guarda `datos` (opcional) y pasa la captura de Borrador a Procesando con `doc.enviar()`.

	Si la captura ya no está en Borrador (reintento de un envío que sí llegó), devuelve su estado con
	`ya_enviada = True` sin modificarla.
	"""
	_exigir_rol_app()
	limitar("enviar")
	doc = _captura_propia(captura)
	if doc.estado != "Borrador":
		return _resumen(doc, ya_enviada=True)
	datos = frappe.parse_json(datos) if datos else None
	if datos:
		aplicar_datos(doc, datos)
		doc.save(ignore_permissions=True)  # dueño y estado Borrador ya comprobados
	doc.enviar()
	return _resumen(doc, ya_enviada=False)


@frappe.whitelist(methods=["POST"])
def reintentar_captura(captura: str) -> dict:
	"""HU-09: el capturador vuelve a mandar a la IA una captura suya en Error ("Reintentar" en Mis
	capturas). Las reglas (dueño + Error, o supervisor) las aplica `reintentar()` del controlador."""
	_exigir_rol_app()
	limitar("enviar")
	doc = _captura_propia(captura)
	doc.reintentar()
	return _resumen(doc)


def aplicar_datos(doc, datos: dict) -> None:
	"""Carga en `doc` lo capturado en el celular. No guarda.

	`datos`:
	    transcripcion: str, metodo_entrada: "Voz" | "Texto",
	    fotos: [{file_url, es_principal}]  (subidas antes con `subir_foto` a esta captura),
	    codigos: [{barcode, origen: "Escaneado" | "Manual"}], sin_codigo: 0 | 1,
	    tiene_variantes: 0 | 1,
	    variantes: [{transcripcion, foto, barcode}]  (de la variante 2 en adelante).

	Con variantes, la variante 1 es el producto de los pasos 1 a 3 (ui-spec 9.1): su fila toma la
	transcripción, la foto principal y el primer código. Si el producto tenía más de un código, los
	demás quedan en la tabla del padre, donde la validación avisa al supervisor que debe moverlos.
	"""
	if not isinstance(datos, dict):
		frappe.throw(_("Datos de la captura no válidos."))
	archivos = fotos_subidas(doc.name)

	transcripcion = _texto(datos.get("transcripcion"))
	filas_fotos = []
	for foto in _lista(datos.get("fotos"), MAX_FOTOS, _("fotos")):
		url = _url_de_foto(foto.get("file_url") or foto.get("foto"), archivos)
		if url and all(f["foto"] != url for f in filas_fotos):
			filas_fotos.append({"foto": url, "es_principal": 1 if cint(foto.get("es_principal")) else 0})

	codigos = [_codigo(c) for c in _lista(datos.get("codigos"), MAX_CODIGOS, _("códigos"))]
	codigos = [c for c in codigos if c]
	variantes = _lista(datos.get("variantes"), MAX_VARIANTES - 1, _("variantes"))
	tiene_variantes = bool(cint(datos.get("tiene_variantes")) or variantes)

	doc.transcripcion = transcripcion
	metodo = datos.get("metodo_entrada")
	doc.metodo_entrada = metodo if metodo in ("Voz", "Texto") else None
	doc.set("fotos", filas_fotos)
	doc.tiene_variantes = 1 if tiene_variantes else 0
	doc.sin_codigo = 1 if cint(datos.get("sin_codigo")) and not codigos else 0

	if not tiene_variantes:
		doc.set("codigos_barras", codigos)
		doc.set("variantes", [])
		return

	principal = next((f["foto"] for f in filas_fotos if f["es_principal"]), None)
	if not principal and filas_fotos:
		principal = filas_fotos[0]["foto"]
	filas_variantes = [
		{
			"transcripcion": transcripcion,
			"foto": principal,
			"barcode": codigos[0]["barcode"] if codigos else None,
		}
	]
	for variante in variantes:
		codigo = _codigo({"barcode": variante.get("barcode")})
		filas_variantes.append(
			{
				"transcripcion": _texto(variante.get("transcripcion")),
				"foto": _url_de_foto(variante.get("foto") or variante.get("file_url"), archivos),
				"barcode": codigo["barcode"] if codigo else None,
			}
		)
	doc.set("codigos_barras", codigos[1:])
	doc.set("variantes", filas_variantes)


def _texto(valor) -> str | None:
	texto = re.sub(r"\s+", " ", str(valor or "")).strip()
	return texto[:LARGO_MAX_TRANSCRIPCION] or None


def _lista(valor, maximo: int, que: str) -> list[dict]:
	if not valor:
		return []
	if not isinstance(valor, list) or not all(isinstance(v, dict) for v in valor):
		frappe.throw(_("Datos de la captura no válidos: {0}.").format(que))
	if len(valor) > maximo:
		frappe.throw(_("Demasiadas {0}: el máximo es {1}.").format(que, maximo))
	return valor


def fotos_subidas(captura: str) -> set[str]:
	"""URL de las fotos que `subir_foto` adjuntó a la captura: archivo público llamado
	`{captura}-{hash}.jpg|png` y de 2 MB como máximo. Cualquier otro adjunto (privado, de otro
	documento o subido desde el Desk) no se acepta como foto de la captura."""
	patron = re.compile(rf"^{re.escape(captura)}-[0-9a-f]{{{LARGO_HUELLA}}}([0-9a-f]{{6}})?\.(jpg|png)$")
	filas = frappe.get_all(
		"File",
		filters={"attached_to_doctype": DOCTYPE, "attached_to_name": captura, "is_private": 0},
		fields=["file_url", "file_name", "file_size"],
	)
	return {
		f.file_url
		for f in filas
		if f.file_url and patron.match(f.file_name or "") and 0 < (f.file_size or 0) <= MAX_BYTES_FOTO
	}


def _url_de_foto(url: str | None, archivos: set[str]) -> str | None:
	if not url:
		return None
	if url not in archivos:
		# Solo se aceptan fotos subidas con `subir_foto` a esta misma captura.
		frappe.throw(_("Una de las fotos no se subió a esta captura. Vuelve a intentar el envío."))
	return url


def _codigo(fila: dict) -> dict | None:
	analisis = validation.analizar_barcode(fila.get("barcode"))
	if not fila.get("barcode") or not analisis.codigo:
		return None
	if not analisis.valido:
		frappe.throw(analisis.advertencia.mensaje)
	origen = fila.get("origen")
	return {
		"barcode": analisis.codigo,
		"origen": origen if origen in ("Escaneado", "Manual") else "Escaneado",
	}
