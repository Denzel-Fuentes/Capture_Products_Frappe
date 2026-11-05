"""Pantalla móvil /captura (docs/ui/ui-spec.md).

Página independiente (no extiende la plantilla del sitio web) para que cargue solo su propio CSS/JS
y no dependa del Desk ni del asistente de configuración de ERPNext. La lógica está en
`public/js/captura.bundle.js` y los estilos en `public/css/captura.bundle.css` (esbuild de Frappe:
nombres con hash, así el celular nunca usa una versión vieja del JS).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.jinja_globals import bundled_asset

from captura_productos.api import tiene_rol_app

no_cache = 1

ZXING = "/assets/captura_productos/js/vendor/zxing-browser-0.2.1/zxing-browser.min.js"
IDIOMA_VOZ_DEFECTO = "es-BO"


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/captura"
		# 302 y no el 301 por defecto: el navegador guardaría el 301 y, ya con sesión, seguiría
		# mandando /captura al login.
		raise frappe.Redirect(302)
	if not tiene_rol_app():
		frappe.throw(_("No tienes permiso para usar la captura de productos."), frappe.PermissionError)

	nombre = frappe.db.get_value("User", frappe.session.user, ["first_name", "full_name"], as_dict=True)
	context.no_cache = 1
	context.title = _("Captura de productos")
	context.csrf_token = frappe.sessions.get_csrf_token()
	boot = {
		"user": frappe.session.user,
		"nombre": (nombre and (nombre.first_name or nombre.full_name)) or frappe.session.user,
		"csrf_token": context.csrf_token,
		"zxing_url": bundled_asset(ZXING),
		# Idioma del dictado (Configuración Captura). Se lee sin permisos: el capturador no puede leer
		# el Single (tiene secretos) y este valor no lo es.
		"idioma_voz": frappe.db.get_single_value("Configuracion Captura", "idioma_voz") or IDIOMA_VOZ_DEFECTO,
	}
	# Va dentro de <script type="application/json">: "<" escapado para que un nombre no cierre el tag.
	context.boot_json = frappe.as_json(boot, indent=None).replace("<", "\\u003c")
	return context
