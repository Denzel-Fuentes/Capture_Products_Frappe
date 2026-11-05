// Copyright (c) 2026, Estacion Tradicion and contributors
// For license information, please see license.txt

const COLORES_ESTADO = {
	Borrador: "gray",
	Procesando: "blue",
	"Por revisar": "yellow",
	Error: "red",
	Aprobado: "green",
	Exportado: "green",
	Creado: "green",
};

frappe.listview_settings["Captura de Producto"] = {
	add_fields: ["estado", "imagen_principal", "confianza_ia"],
	filters: [["estado", "=", "Por revisar"]],
	// El estado se ve en el indicador (no hay columna "Estado" aparte).
	get_indicator(doc) {
		return [__(doc.estado), COLORES_ESTADO[doc.estado] || "gray", `estado,=,${doc.estado}`];
	},
	formatters: {
		// HU-07: miniatura de la foto principal en la lista.
		imagen_principal(value) {
			if (!value || !/^(\/|https?:\/\/)/.test(value)) return "";
			const url = frappe.utils.escape_html(value);
			return `<img src="${url}" alt="" loading="lazy"
				style="height: 36px; width: 36px; object-fit: cover; border-radius: 4px;">`;
		},
	},
	onload(listview) {
		if (!frappe.user.has_role(["Item Manager", "Stock Manager", "System Manager"])) return;
		listview.page.add_actions_menu_item(__("Exportar aprobados a Google Sheet"), () =>
			exportar_aprobados(listview)
		);
		// Resultado de la exportación en segundo plano (también queda en las notificaciones).
		frappe.realtime.off("captura_exportacion");
		frappe.realtime.on("captura_exportacion", (res) => mostrar_resultado(listview, res || {}));
	},
};

function exportar_aprobados(listview) {
	const seleccion = listview.get_checked_items();
	const nombres = seleccion.filter((d) => d.estado === "Aprobado").map((d) => d.name);
	if (!nombres.length) {
		frappe.msgprint(__("Seleccione capturas en estado Aprobado."));
		return;
	}
	const omitidas = seleccion.length - nombres.length;
	let texto =
		nombres.length === 1
			? __("Se exportará 1 captura.")
			: __("Se exportarán {0} capturas.", [nombres.length]);
	if (omitidas === 1) texto += " " + __("Se omite 1 que no está en Aprobado.");
	else if (omitidas > 1) texto += " " + __("Se omiten {0} que no están en Aprobado.", [omitidas]);
	frappe.confirm(texto, () =>
		frappe
			.call({
				method: "captura_productos.export.sheets.exportar_aprobados",
				args: { nombres },
				freeze: true,
				freeze_message: __("Enviando…"),
			})
			.then(() => {
				frappe.show_alert({
					message: __("La exportación sigue en segundo plano. Le avisaremos al terminar."),
					indicator: "blue",
				});
				listview.clear_checked_items();
			})
	);
}

function mostrar_resultado(listview, res) {
	const esc = frappe.utils.escape_html;
	if (!res.ok) {
		frappe.msgprint({
			title: __("No se pudo exportar"),
			indicator: "red",
			message: esc(res.mensaje || ""),
		});
		listview.refresh();
		return;
	}
	let html = `<p>${__("Exportadas: {0}", [(res.exportadas || []).length])}</p>`;
	if ((res.hojas || []).length) {
		html += `<p>${__("Pestañas")}: ${esc(res.hojas.join(", "))}</p>`;
	}
	(res.avisos || []).forEach((a) => (html += `<p class="text-warning">${esc(a)}</p>`));
	if ((res.excluidas || []).length) {
		html += `<p>${__("Excluidas")}:</p><ul>${res.excluidas
			.map((e) => `<li>${esc(e.captura)}: ${esc(e.motivo)}</li>`)
			.join("")}</ul>`;
	}
	frappe.msgprint({ title: __("Exportación a Google Sheet"), message: html });
	listview.refresh();
}
