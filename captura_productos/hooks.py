app_name = "captura_productos"
app_title = "Captura de Productos"
app_publisher = "Estacion Tradicion"
app_description = "Captura de productos desde el celular (fotos, voz y codigo de barras) con estructuracion por IA para ERPNext"
app_email = "dev@estaciontradicion.example"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

# Rol nuevo de la app (spec sección 1). Solo se exporta este rol; los permisos van en los DocTypes.
fixtures = [{"dt": "Role", "filters": [["name", "=", "Capturador de Productos"]]}]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "captura_productos",
# 		"logo": "/assets/captura_productos/logo.png",
# 		"title": "Captura Productos",
# 		"route": "/captura_productos",
# 		"has_permission": "captura_productos.api.permission.has_app_permission",
# 	}
# ]

# The dock, the rail down the left of the desk, is a document rather than a hook. Author it in
# Manage Dock on a developer-mode site and press Export to App, and it is written to
# `captura_productos/dock/captura_productos/captura_productos.json` for git to carry. An app that ships none has no
# rail: its sidebar gets a switcher in the header instead.
#
# A companion app, one that extends a host app rather than standing on its own, says so with
# `mount_on` on that same record, and its entries are appended to the host's rail. Mounting keeps
# the companion off the apps screen, so it takes precedence over any add_to_apps_screen above.

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/captura_productos/css/captura_productos.css"
# app_include_js = "/assets/captura_productos/js/captura_productos.js"

# include js, css files in header of web template
# web_include_css = "/assets/captura_productos/css/captura_productos.css"
# web_include_js = "/assets/captura_productos/js/captura_productos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "captura_productos/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_kanban_js = {"doctype" : "public/js/doctype_kanban.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "captura_productos/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# El capturador no entra al Desk (desk_access = 0): después del login va directo a /captura
# (captura_productos/www/captura.html). Los supervisores siguen yendo al Desk.
role_home_page = {"Capturador de Productos": "captura"}

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "captura_productos.utils.jinja_methods",
# 	"filters": "captura_productos.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "captura_productos.install.before_install"
after_install = "captura_productos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "captura_productos.uninstall.before_uninstall"
# after_uninstall = "captura_productos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "captura_productos.utils.before_app_install"
# after_app_install = "captura_productos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "captura_productos.utils.before_app_uninstall"
# after_app_uninstall = "captura_productos.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "captura_productos.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "captura_productos.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "captura_productos.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["captura_productos.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"captura_productos.tasks.all"
# 	],
# 	"daily": [
# 		"captura_productos.tasks.daily"
# 	],
# 	"hourly": [
# 		"captura_productos.tasks.hourly"
# 	],
# 	"weekly": [
# 		"captura_productos.tasks.weekly"
# 	],
# 	"monthly": [
# 		"captura_productos.tasks.monthly"
# 	],
# }

# IA (ver captura_productos/ai/extractor.py): reintentos programados de errores transitorios y rescate
# de capturas que quedaron en Procesando sin job. Requiere el scheduler activo en el sitio.
scheduler_events = {
	"cron": {
		"* * * * *": ["captura_productos.ai.extractor.reencolar_reintentos"],
		"*/15 * * * *": ["captura_productos.ai.extractor.rescatar_atascadas"],
	},
}

# Testing
# -------

# before_tests = "captura_productos.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "captura_productos.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "captura_productos.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "captura_productos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["captura_productos.utils.before_request"]
# after_request = ["captura_productos.utils.after_request"]

# Job Events
# ----------
# before_job = ["captura_productos.utils.before_job"]
# after_job = ["captura_productos.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"captura_productos.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
