// Copyright (c) 2026, Estacion Tradicion and contributors
// For license information, please see license.txt

const ROLES_SUPERVISOR = ["Item Manager", "Stock Manager", "System Manager"];

frappe.ui.form.on("Captura de Producto", {
	setup(frm) {
		frm.set_query("item_group", () => ({ filters: { is_group: 0 } }));
	},

	refresh(frm) {
		mostrar_avisos(frm);
		mostrar_galeria(frm);
		if (frm.is_new() || !frappe.user.has_role(ROLES_SUPERVISOR)) return;

		const estado = frm.doc.estado;
		const grupo = __("Acciones");

		if (estado === "Borrador") {
			frm.add_custom_button(__("Enviar"), () => llamar(frm, "enviar_desde_formulario"), grupo);
		}
		if (["Por revisar", "Aprobado"].includes(estado)) {
			frm.add_custom_button(__("Recalcular nombre"), () => recalcular_nombre(frm), grupo);
		}
		if (estado === "Por revisar") {
			frm.add_custom_button(__("Aprobar"), () => llamar(frm, "aprobar"));
			frm.change_custom_button_type(__("Aprobar"), null, "primary");
		}
		if (["Por revisar", "Error", "Procesando"].includes(estado)) {
			// Desde Procesando solo sirve si la captura quedó atascada (el servidor lo rechaza si la IA
			// todavía la está procesando).
			const texto =
				estado === "Procesando"
					? __("Se volverá a mandar a la IA si quedó detenida. ¿Continuar?")
					: __("Se sobrescribirán los campos extraídos. ¿Continuar?");
			frm.add_custom_button(
				__("Reprocesar IA"),
				() => frappe.confirm(texto, () => llamar(frm, "reprocesar_ia")),
				grupo
			);
		}
		if (estado === "Aprobado") {
			frm.add_custom_button(__("Exportar a Google Sheet"), () => exportar(frm), grupo);
			frm.add_custom_button(__("Volver a revisión"), () => llamar(frm, "volver_a_revision"), grupo);
		}
		if (["Aprobado", "Exportado"].includes(estado) && frappe.model.can_create("Item")) {
			frm.add_custom_button(__("Crear Item"), () => crear_item(frm));
			if (estado === "Exportado") frm.change_custom_button_type(__("Crear Item"), null, "primary");
		}
		if (frm.doc.item) {
			frm.add_custom_button(__("Ver Item"), () => frappe.set_route("Form", "Item", frm.doc.item));
		}
		if (estado === "Exportado") {
			frm.add_custom_button(
				__("Marcar para re-exportar"),
				() => llamar(frm, "marcar_para_reexportar"),
				grupo
			);
		}
	},
});

function mostrar_avisos(frm) {
	if (frm.doc.confianza_ia && frm.doc.confianza_ia < 60) {
		frm.dashboard.set_headline_alert(
			__("Confianza de la IA baja ({0}%): revise con las fotos.", [
				Math.round(frm.doc.confianza_ia),
			]),
			"red"
		);
	}
	const lineas = (frm.doc.advertencias_ia || "").split("\n").filter((l) => l.trim());
	if (lineas.length) {
		frm.set_intro(lineas.map((l) => frappe.utils.escape_html(l)).join("<br>"), "yellow");
	}
}

// HU-07: todas las fotos (de la captura y de cada variante) en una galería.
function fotos_de(frm) {
	const fotos = [];
	const vistas = new Set();
	const agregar = (url, rotulo) => {
		if (!url || vistas.has(url) || !/^(\/|https?:\/\/)/.test(url)) return;
		vistas.add(url);
		fotos.push({ url, rotulo });
	};
	(frm.doc.fotos || []).forEach((f, i) =>
		agregar(f.foto, f.es_principal ? __("Foto principal") : __("Foto {0}", [i + 1]))
	);
	if (frm.doc.tiene_variantes) {
		(frm.doc.variantes || []).forEach((v) => {
			const valores = [v.valor_1, v.valor_2, v.valor_3].filter(Boolean).join(" / ");
			agregar(v.foto, __("Variante {0}", [v.idx]) + (valores ? ": " + valores : ""));
		});
	}
	return fotos;
}

function mostrar_galeria(frm) {
	const campo = frm.fields_dict.galeria;
	if (!campo) return;
	const fotos = fotos_de(frm);
	if (!fotos.length) {
		campo.$wrapper.html(`<p class="text-muted small">${__("La captura no tiene fotos.")}</p>`);
		return;
	}
	const esc = frappe.utils.escape_html;
	const tarjetas = fotos
		.map((f) => {
			const url = esc(f.url);
			return `<a class="captura-galeria__item" href="${url}" target="_blank" rel="noopener noreferrer"
				title="${esc(f.rotulo)}">
				<img src="${url}" alt="${esc(f.rotulo)}" loading="lazy">
				<span>${esc(f.rotulo)}</span></a>`;
		})
		.join("");
	campo.$wrapper.html(`
		<style>
			.captura-galeria { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
			.captura-galeria__item { width: 160px; text-decoration: none; color: var(--text-color); }
			.captura-galeria__item img { width: 160px; height: 160px; object-fit: contain; display: block;
				border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-light-gray); }
			.captura-galeria__item span { display: block; font-size: 12px; margin-top: 4px; }
		</style>
		<div class="captura-galeria">${tarjetas}</div>`);
}

function exigir_guardado(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Guarde los cambios antes de continuar."));
		return false;
	}
	return true;
}

// Siempre con `doc`: así frappe llama al método del documento (run_doc_method) y no busca una función
// del módulo con ese nombre (B1/B2 de QA).
function llamar_metodo(frm, metodo, opciones) {
	return frm.call({ doc: frm.doc, method: metodo, ...(opciones || {}) });
}

function llamar(frm, metodo) {
	if (!exigir_guardado(frm)) return;
	return llamar_metodo(frm, metodo).then(() => frm.reload_doc());
}

function exportar(frm) {
	if (!exigir_guardado(frm)) return;
	llamar_metodo(frm, "exportar", { freeze: true, freeze_message: __("Exportando…") }).then((r) => {
		const res = r.message || {};
		const esc = frappe.utils.escape_html;
		const avisos = (res.avisos || []).map((a) => `<p class="text-warning">${esc(a)}</p>`).join("");
		frappe.msgprint({
			title: __("Exportado"),
			indicator: avisos ? "orange" : "green",
			message: `<p>${__("Pestañas: {0}", [esc((res.hojas || []).join(", "))])}</p>${avisos}`,
		});
		frm.reload_doc();
	});
}

function crear_item(frm) {
	if (!exigir_guardado(frm)) return;
	const n = (frm.doc.variantes || []).length;
	const texto = frm.doc.tiene_variantes
		? n === 1
			? __("Se creará la plantilla {0} y 1 variante. ¿Continuar?", [frm.doc.item_code])
			: __("Se creará la plantilla {0} y {1} variantes. ¿Continuar?", [frm.doc.item_code, n])
		: __("Se creará el Item {0}. ¿Continuar?", [frm.doc.item_code]);
	frappe.confirm(frappe.utils.escape_html(texto), () =>
		llamar_metodo(frm, "crear_item", { freeze: true, freeze_message: __("Creando Item…") }).then(
			(r) => {
				const res = r.message || {};
				frappe.show_alert({
					message: __("Item {0}: {1} creados, {2} enlazados", [
						frappe.utils.escape_html(res.item || ""),
						(res.creados || []).length,
						(res.enlazados || []).length,
					]),
					indicator: "green",
				});
				frm.reload_doc();
			}
		)
	);
}

function recalcular_nombre(frm) {
	if (!exigir_guardado(frm)) return;
	llamar_metodo(frm, "recalcular_nombre", { args: { guardar: 0 } }).then((r) => {
		const res = r.message || {};
		if (!res.cambia) {
			frappe.show_alert({ message: __("El nombre y el código ya están al día."), indicator: "green" });
			return;
		}
		const esc = frappe.utils.escape_html;
		const filas = res.despues
			.map((d, i) => {
				const a = res.antes[i] || [];
				return `<tr><td>${esc(a[0] || "")}<br><small>${esc(a[1] || "")}</small></td><td>${esc(
					d[0] || ""
				)}<br><small>${esc(d[1] || "")}</small></td></tr>`;
			})
			.join("");
		// "Valor actual" y no "Actual": la traducción española de Frappe para "Actual" es "Real".
		frappe.confirm(
			`<p>${__("Se reemplazarán el nombre y el código:")}</p>
			<table class="table table-bordered"><tr><th>${__("Valor actual")}</th><th>${__(
				"Valor nuevo"
			)}</th></tr>${filas}</table>`,
			() =>
				llamar_metodo(frm, "recalcular_nombre", { args: { guardar: 1 } }).then(() =>
					frm.reload_doc()
				)
		);
	});
}
