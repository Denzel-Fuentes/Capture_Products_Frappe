// Copyright (c) 2026, Estacion Tradicion and contributors
// For license information, please see license.txt

frappe.ui.form.on("Configuracion Captura", {
	setup(frm) {
		frm.set_query("item_group_defecto", () => ({ filters: { is_group: 0 } }));
		frm.set_query("almacen_defecto", () => ({
			filters: frm.doc.empresa ? { company: frm.doc.empresa, is_group: 0 } : { is_group: 0 },
		}));
	},

	refresh(frm) {
		frm.add_custom_button(__("Probar conexión IA"), () => probar(frm, "probar_conexion_ia"), __("Probar"));
		frm.add_custom_button(__("Probar Google Sheet"), () => probar(frm, "probar_google_sheet"), __("Probar"));
	},
});

function probar(frm, metodo) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Guarde la configuración antes de probar."));
		return;
	}
	// Con `doc`: llama al método del documento (run_doc_method), no a una función del módulo.
	frm.call({ doc: frm.doc, method: metodo, freeze: true, freeze_message: __("Probando…") }).then((r) => {
		frappe.msgprint({ title: __("OK"), indicator: "green", message: frappe.utils.escape_html(r.message) });
	});
}
