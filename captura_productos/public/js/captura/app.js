// Pantalla /captura: estado, navegación, vistas y eventos (docs/ui/ui-spec.md).
// Las vistas siguen las funciones del prototipo (docs/ui/prototipo.html) con datos reales.

import {
	esc,
	icon,
	announce,
	toast,
	clearToasts,
	feedbackOk,
	feedbackError,
	vibrarExito,
	uuid,
	prefs,
	fechaRelativa,
	estaInstalada,
	estadoPermiso,
} from "./util";
import * as codigos from "./codigos";
import * as store from "./store";
import { api, setCsrf, cerrarSesion } from "./api";
import { comprimir, srcDe, liberar } from "./fotos";
import { crearDictado, soportaVoz, configurarIdioma } from "./voz";
import { crearEscaner } from "./escaner";
import { enviarBorrador, todasLasFotos } from "./envio";

/* ================= Datos y estado ================= */
const STEPS = [
	{ key: "fotos", label: "Fotos", icon: "camera" },
	{ key: "dictar", label: "Dictar", icon: "mic" },
	{ key: "codigo", label: "Código", icon: "barcode" },
	{ key: "variantes", label: "Variantes", icon: "layers" },
	{ key: "resumen", label: "Revisar y enviar", icon: "send" },
];
const VSTEPS = [
	{ key: "var-foto", label: "Foto", icon: "camera" },
	{ key: "var-dictar", label: "Dictar", icon: "mic" },
	{ key: "var-codigo", label: "Código", icon: "barcode" },
];
const ORDER = STEPS.map((s) => s.key);
const VORDER = VSTEPS.map((s) => s.key);
const MAX_FOTOS = 4;
const MAX_CODIGOS = 5;
const MAX_VARIANTES_EXTRA = 19; // 20 variantes en total contando la 1
const MIN_DICTADO = 3; // como el servidor: transcripción de 3 caracteres o más

const boot = leerBoot();
setCsrf(boot.csrf_token);
configurarIdioma(boot.idioma_voz);
const K = (k) => boot.user + ":" + k; // borradores separados por usuario en el mismo celular

const state = {
	draft: null, // producto en curso
	vdraft: null, // variante en curso
	toReview: false, // volver al resumen después de "Cambiar"
	sound: prefs.get("sound", true),
	torch: false,
	linterna: false,
	online: navigator.onLine,
	camPermiso: "unknown",
	capturas: { estado: "idle", lista: [] },
	pendientes: [],
	lastSent: null,
	ui: {},
};

function leerBoot() {
	try {
		return JSON.parse(document.getElementById("cp-boot").textContent);
	} catch (e) {
		return { user: "", nombre: "", csrf_token: "", zxing_url: "", idioma_voz: "" };
	}
}

function emptyDraft() {
	return {
		uuid: uuid(),
		creado: new Date().toISOString(),
		photos: [],
		dictado: "",
		metodo: "",
		codes: [],
		sinCodigo: false,
		hasVariants: null,
		variants: [],
		lastStep: "fotos",
		serverName: null,
	};
}

function emptyVariant() {
	return { id: "v" + Date.now(), photos: [], dictado: "", metodo: "", codes: [], sinCodigo: false };
}

function hidratar(d) {
	d.photos = (d.photos || []).filter((p) => p.blob || p.url);
	d.photos.forEach((p) => (p.loading = false));
	if (d.photos.length && !d.photos.some((p) => p.principal)) d.photos[0].principal = true;
	d.codes = d.codes || [];
	d.variants = (d.variants || []).map(hidratar);
	return d;
}

function tieneContenido(d) {
	return !!(d && (d.photos.length || (d.dictado || "").trim() || d.codes.length));
}

/* ================= Borrador en el celular ================= */
let saveTimer = null;

function copiaGuardable(d) {
	const c = Object.assign({}, d);
	c.photos = d.photos
		.filter((p) => !p.loading && (p.blob || p.url))
		.map((p) => ({ id: p.id, blob: p.blob || null, url: p.url || null, principal: !!p.principal }));
	if (d.variants) c.variants = d.variants.map(copiaGuardable);
	return c;
}

function persist() {
	clearTimeout(saveTimer);
	saveTimer = setTimeout(guardarYa, 250);
}

async function guardarYa() {
	clearTimeout(saveTimer);
	if (state.draft) await store.set(K("draft"), copiaGuardable(state.draft));
	else await store.del(K("draft"));
	if (state.vdraft) await store.set(K("vdraft"), copiaGuardable(state.vdraft));
	else await store.del(K("vdraft"));
}

async function cargarPendientes() {
	const claves = await store.keys(K("pend:"));
	const lista = [];
	for (const k of claves) {
		const d = await store.get(k);
		if (d) lista.push(d);
	}
	lista.sort((a, b) => String(b.creado).localeCompare(String(a.creado)));
	state.pendientes = lista;
}

/* ================= Utilidades de interfaz ================= */
function parseHash() {
	return location.hash.replace(/^#\/?/, "") || "inicio";
}
let curRoute = parseHash();
function route() {
	return curRoute;
}
function go(r) {
	if (r === curRoute) {
		render();
		return;
	}
	curRoute = r;
	try {
		history.pushState({ r }, "", "#/" + r);
	} catch (e) {
		/* sin historial */
	}
	onRouteChange();
}
function onRouteChange() {
	stopRecog();
	stopScan();
	clearToasts();
	closeSheets();
	render();
}
function ui(key, def) {
	if (!(key in state.ui)) state.ui[key] = def;
	return state.ui[key];
}
function setUi(key, val) {
	state.ui[key] = val;
	render();
}

function header(title, exitAction) {
	return (
		'<header class="cp-header">' +
		'<button type="button" class="cp-header-btn" data-action="' +
		(exitAction || "exit") +
		'">' +
		icon("x") +
		"<span>Salir</span></button>" +
		'<span class="cp-header__title">' +
		esc(title) +
		"</span></header>"
	);
}

function progress(steps, current) {
	const idx = steps.findIndex((s) => s.key === current);
	const items = steps
		.map((s, i) => {
			const cls = i < idx ? "is-done" : i === idx ? "is-current" : "";
			const stateText = i < idx ? " (hecho)" : i === idx ? " (paso actual)" : "";
			return (
				'<li class="cp-progress__step ' +
				cls +
				'"' +
				(i === idx ? ' aria-current="step"' : "") +
				'><span class="cp-progress__dot">' +
				icon(i < idx ? "check" : s.icon) +
				'</span><span class="cp-visually-hidden">' +
				esc(s.label) +
				stateText +
				"</span></li>"
			);
		})
		.join("");
	return (
		'<nav class="cp-progress" aria-label="Progreso"><ol class="cp-progress__list">' +
		items +
		'</ol><p class="cp-progress__text">Paso ' +
		(idx + 1) +
		" de " +
		steps.length +
		": <span>" +
		esc(steps[idx].label) +
		"</span></p></nav>"
	);
}

function footer(opts) {
	const nextDisabled = !!opts.nextDisabled;
	const hint = opts.hint
		? '<p class="cp-footer__hint" id="cp-next-hint"' + (nextDisabled ? "" : " hidden") + ">" + esc(opts.hint) + "</p>"
		: "";
	const back = opts.back
		? '<button type="button" class="cp-btn cp-btn--secondary cp-btn--lg" data-action="' +
		  opts.back +
		  '">' +
		  icon("left") +
		  "<span>Atrás</span></button>"
		: "";
	if (!opts.next) {
		return '<footer class="cp-footer"><div class="cp-footer__row cp-footer__row--single">' + back + "</div></footer>";
	}
	const next =
		'<button type="button" class="cp-btn cp-btn--primary cp-btn--lg' +
		(nextDisabled ? " is-disabled" : "") +
		'"' +
		(nextDisabled ? ' aria-disabled="true" aria-describedby="cp-next-hint"' : "") +
		' data-action="' +
		opts.next +
		'" data-disabled="' +
		nextDisabled +
		'"><span>' +
		esc(opts.nextLabel || "Siguiente") +
		"</span>" +
		icon(opts.nextIcon || "right") +
		"</button>";
	return (
		'<footer class="cp-footer">' +
		hint +
		'<div class="cp-footer__row' +
		(back ? "" : " cp-footer__row--single") +
		'">' +
		back +
		next +
		"</div></footer>"
	);
}

function alertBox(kind, title, text, extra) {
	const ic = kind === "success" ? "check" : kind === "info" ? "info" : "alert";
	return (
		'<div class="cp-alert cp-alert--' +
		kind +
		'" role="' +
		(kind === "danger" || kind === "warning" ? "alert" : "status") +
		'">' +
		icon(ic, "cp-icon--lg") +
		'<div class="cp-alert__body"><p class="cp-alert__title">' +
		esc(title) +
		"</p>" +
		(text ? '<p class="cp-alert__text">' + text + "</p>" : "") +
		(extra || "") +
		"</div></div>"
	);
}

function permissionHelp(what) {
	if (estaInstalada()) {
		return (
			'<ol class="cp-alert__steps"><li>Abre <strong>Ajustes</strong> del celular → <strong>Aplicaciones</strong> → <strong>Chrome</strong> → <strong>Permisos</strong> → <strong>' +
			what +
			"</strong> → <strong>Permitir</strong>.</li><li>Vuelve aquí y toca <strong>Intentar de nuevo</strong>.</li></ol>"
		);
	}
	return (
		'<ol class="cp-alert__steps">' +
		"<li>Arriba, junto a la dirección de la página, toca el candado " +
		icon("lock") +
		".</li>" +
		"<li>Toca <strong>Permisos</strong> y luego <strong>" +
		what +
		"</strong>.</li>" +
		"<li>Elige <strong>Permitir</strong>.</li>" +
		"<li>Vuelve aquí y toca <strong>Intentar de nuevo</strong>.</li></ol>"
	);
}

const CHIPS = {
	procesando: ["Procesando", "loader"],
	revisar: ["Por revisar", "clock"],
	error: ["Error", "alert"],
	aprobado: ["Aprobado", "check"],
	pendiente: ["Sin enviar", "wifi-off"],
};
const CHIP_DE_ESTADO = {
	Procesando: "procesando",
	"Por revisar": "revisar",
	Error: "error",
	Aprobado: "aprobado",
	Exportado: "aprobado",
	Creado: "aprobado",
	Borrador: "pendiente",
};
function chip(clave) {
	const m = CHIPS[clave] || CHIPS.pendiente;
	return '<span class="cp-chip cp-chip--' + clave + '">' + icon(m[1]) + m[0] + "</span>";
}

/* ---------- Hojas (M1-M5) ---------- */
function closeSheets() {
	document.querySelectorAll(".cp-sheet-backdrop").forEach((b) => (b._close ? b._close(true) : b.remove()));
}

function confirmSheet(opts) {
	closeSheets();
	const back = document.createElement("div");
	back.className = "cp-sheet-backdrop";
	back.innerHTML =
		'<div class="cp-sheet" role="alertdialog" aria-modal="true" aria-labelledby="cp-c-title" aria-describedby="cp-c-text">' +
		'<h2 class="cp-sheet__title" id="cp-c-title" tabindex="-1">' +
		esc(opts.title) +
		'</h2><p class="cp-sheet__text" id="cp-c-text">' +
		esc(opts.text) +
		"</p>" +
		'<button type="button" class="cp-btn cp-btn--primary cp-btn--lg cp-btn--block" data-sheet="cancel">' +
		esc(opts.cancelLabel) +
		"</button>" +
		'<button type="button" class="cp-btn ' +
		(opts.danger ? "cp-btn--danger" : "cp-btn--secondary") +
		' cp-btn--block" data-sheet="ok">' +
		esc(opts.okLabel) +
		"</button></div>";
	document.body.appendChild(back);
	const prev = document.activeElement;
	back.querySelector('[data-sheet="cancel"]').focus();
	function close(silencioso) {
		back.remove();
		document.removeEventListener("keydown", onKey);
		if (!silencioso && prev && document.body.contains(prev)) prev.focus();
	}
	back._close = close;
	function onKey(e) {
		if (e.key === "Escape") {
			close();
			if (opts.onCancel) opts.onCancel();
		}
		trapFocus(e, back);
	}
	document.addEventListener("keydown", onKey);
	back.addEventListener("click", (e) => {
		if (e.target === back || e.target.closest('[data-sheet="cancel"]')) {
			close();
			if (opts.onCancel) opts.onCancel();
		} else if (e.target.closest('[data-sheet="ok"]')) {
			close(true);
			opts.onOk();
		}
	});
}

function trapFocus(e, cont) {
	if (e.key !== "Tab") return;
	const f = cont.querySelectorAll("input, button, textarea");
	if (!f.length) return;
	const first = f[0];
	const last = f[f.length - 1];
	if (e.shiftKey && document.activeElement === first) {
		e.preventDefault();
		last.focus();
	} else if (!e.shiftKey && document.activeElement === last) {
		e.preventDefault();
		first.focus();
	}
}

/* ================= Fotos ================= */
function photosOf(scope) {
	return scope === "var" ? state.vdraft.photos : state.draft.photos;
}
function maxFotos(scope) {
	return scope === "var" ? 1 : MAX_FOTOS;
}

let photoScope = "main";
function crearInput(conCamara) {
	const input = document.createElement("input");
	input.type = "file";
	input.accept = "image/*";
	input.hidden = true;
	if (conCamara) input.setAttribute("capture", "environment");
	else input.multiple = true;
	input.addEventListener("change", () => {
		const files = Array.from(input.files || []);
		input.value = "";
		// Sin archivo = la persona canceló la cámara: no es un error (ui-spec 8.2).
		if (files.length) agregarFotos(photoScope, files);
	});
	document.body.appendChild(input);
	return input;
}
const inputCamara = crearInput(true);
const inputGaleria = crearInput(false);

function tomarFoto(scope, galeria) {
	if (photosOf(scope).length >= maxFotos(scope)) return;
	photoScope = scope;
	if (!galeria && state.camPermiso === "denied") {
		state.ui[scope + ":camErr"] = true;
		feedbackError();
		render();
		announce("No se pudo abrir la cámara");
		return;
	}
	state.ui[scope + ":camErr"] = false;
	(galeria ? inputGaleria : inputCamara).click();
}

async function agregarFotos(scope, files) {
	const owner = scope === "var" ? state.vdraft : state.draft;
	if (!owner) return;
	const photos = owner.photos;
	const libres = maxFotos(scope) - photos.length;
	for (const file of files.slice(0, Math.max(0, libres))) {
		const p = {
			id: "f" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
			blob: null,
			url: null,
			principal: !photos.some((x) => x.principal),
			loading: true,
			preview: URL.createObjectURL(file),
		};
		photos.push(p);
		render();
		try {
			if (file.type && file.type.indexOf("image/") !== 0) throw new Error("No es una imagen");
			p.blob = await comprimir(file);
			p.loading = false;
			announce("Foto " + (photos.indexOf(p) + 1) + " lista");
			persist();
		} catch (e) {
			const i = photos.indexOf(p);
			if (i >= 0) photos.splice(i, 1);
			if (p.principal && photos.length) photos[0].principal = true;
			state.ui[scope + ":camErr"] = true;
			feedbackError();
			announce("No se pudo abrir la cámara");
		} finally {
			URL.revokeObjectURL(p.preview);
			p.preview = null;
		}
		render();
	}
}

function delPhoto(scope, id) {
	const photos = photosOf(scope);
	const i = photos.findIndex((p) => p.id === id);
	if (i < 0) return;
	const removed = photos.splice(i, 1)[0];
	const wasPrincipal = removed.principal;
	if (wasPrincipal && photos.length) photos[0].principal = true;
	liberar(removed);
	persist();
	render();
	announce("Foto borrada");
	toast("Foto borrada", "Deshacer", () => {
		const lista = photosOf(scope);
		if (lista.length >= maxFotos(scope)) return;
		if (wasPrincipal) lista.forEach((p) => (p.principal = false));
		lista.splice(Math.min(i, lista.length), 0, removed);
		persist();
		render();
		announce("Foto recuperada");
	});
}

function setPrincipal(scope, id) {
	photosOf(scope).forEach((p) => (p.principal = p.id === id));
	persist();
	render();
	announce("Foto principal cambiada");
}

function photosBlock(photos, opts) {
	const max = opts.max;
	const camErr = ui(opts.scope + ":camErr", false);
	const sc = ' data-scope="' + opts.scope + '"';
	let out = "";
	if (camErr) {
		out += alertBox(
			"danger",
			"No se pudo abrir la cámara",
			"Tu celular no dejó usar la cámara.",
			permissionHelp("Cámara") +
				'<div class="cp-alert__actions">' +
				'<button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="take-photo"' +
				sc +
				">" +
				icon("repeat") +
				"Intentar de nuevo</button>" +
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="gallery-photo"' +
				sc +
				">" +
				icon("image") +
				"Elegir de la galería</button></div>"
		);
	}
	if (camErr && !photos.length) return out;
	if (!photos.length) {
		out +=
			'<div class="cp-empty">' +
			icon("camera", "cp-icon--xl") +
			'<p class="cp-empty__title">Todavía no hay fotos</p><p class="cp-empty__text">' +
			esc(opts.emptyText) +
			"</p></div>";
	} else {
		out +=
			'<ul class="cp-photo-grid' +
			(max === 1 ? " cp-photo-grid--single" : "") +
			'" aria-label="Fotos tomadas">' +
			photos
				.map((p, i) => {
					const n = i + 1;
					return (
						'<li class="cp-photo' +
						(p.principal && max > 1 ? " is-principal" : "") +
						'"><div class="cp-photo__frame"><img src="' +
						esc(srcDe(p)) +
						'" alt="Foto ' +
						n +
						(p.principal ? ", principal" : "") +
						'">' +
						(p.principal && max > 1
							? '<span class="cp-photo__badge">' + icon("star", "cp-icon--fill") + "Principal</span>"
							: "") +
						(p.loading
							? '<div class="cp-photo__loading"><div class="cp-spinner cp-spinner--light"></div>Preparando…</div>'
							: "") +
						'</div><div class="cp-photo__actions">' +
						(max > 1
							? p.principal
								? '<span class="cp-photo__note">Foto principal</span>'
								: '<button type="button" class="cp-photo__action" data-action="set-principal"' +
								  sc +
								  ' data-id="' +
								  p.id +
								  '" aria-label="Poner la foto ' +
								  n +
								  ' como principal">' +
								  icon("star") +
								  "Principal</button>"
							: '<span class="cp-photo__note">Foto ' + n + "</span>") +
						'<button type="button" class="cp-photo__action cp-photo__action--danger" data-action="del-photo"' +
						sc +
						' data-id="' +
						p.id +
						'" aria-label="Borrar la foto ' +
						n +
						'"' +
						(p.loading ? " disabled" : "") +
						">" +
						icon("trash") +
						"Borrar</button></div></li>"
					);
				})
				.join("") +
			"</ul>";
	}
	const full = photos.length >= max;
	const label = full
		? max === 1
			? "Ya tienes la foto"
			: "Ya tienes " + max + " fotos (máximo)"
		: photos.length
		? "Tomar otra foto (" + (photos.length + 1) + " de " + max + ")"
		: "Tomar foto";
	if (!full || max > 1) {
		out +=
			'<button type="button" class="cp-btn cp-btn--' +
			(photos.length ? "secondary" : "primary") +
			' cp-btn--xl cp-btn--block"' +
			(full ? " disabled" : "") +
			' data-action="take-photo"' +
			sc +
			">" +
			icon("camera", "cp-icon--lg") +
			"<span>" +
			label +
			"</span></button>";
	}
	if (full && max === 1) {
		out +=
			'<button type="button" class="cp-btn cp-btn--secondary cp-btn--lg cp-btn--block" data-action="retake-photo"' +
			sc +
			">" +
			icon("repeat") +
			"Volver a tomar la foto</button>";
	}
	if (!full) {
		out +=
			'<button type="button" class="cp-btn cp-btn--ghost cp-btn--block" data-action="gallery-photo"' +
			sc +
			">" +
			icon("image") +
			"Elegir de la galería</button>";
	}
	if (photos.length > 1) {
		out +=
			'<p class="cp-help">La foto principal es la que se verá en el sistema. Toca <strong>Principal</strong> en otra foto para cambiarla.</p>';
	}
	return out;
}

/* ================= Dictado ================= */
let dictado = null;
function dictTarget(scope) {
	return scope === "var" ? state.vdraft : state.draft;
}
function minDictado(scope) {
	return scope === "var" ? 1 : MIN_DICTADO;
}

function startMic(scope) {
	const t = dictTarget(scope);
	stopRecog();
	t.dictado = "";
	state.ui[scope + ":interim"] = "";
	setUi(scope + ":dict", "listening");
	announce("Te estoy escuchando");
	dictado = crearDictado({
		onTexto(final, parcial) {
			t.dictado = final;
			state.ui[scope + ":interim"] = parcial;
			if (state.ui[scope + ":dict"] === "listening") render();
		},
		onFin(final) {
			dictado = null;
			t.dictado = final;
			t.metodo = "Voz";
			state.ui[scope + ":interim"] = "";
			persist();
			setUi(scope + ":dict", "done");
			announce("Listo. Dijiste: " + final);
		},
		onError(tipo) {
			dictado = null;
			state.ui[scope + ":interim"] = "";
			const modo = { denied: "denied", nomic: "nomic", offline: "offline", nosupport: "nosupport" }[tipo] || "nohear";
			if (modo !== "nosupport") feedbackError();
			setUi(scope + ":dict", modo);
			announce(
				{
					denied: "No podemos usar el micrófono",
					nomic: "No encontramos el micrófono",
					offline: "Para dictar necesitas internet",
					nosupport: "Aquí no se puede dictar",
				}[modo] || "No te escuché"
			);
		},
	});
	dictado.iniciar();
}

function finishMic() {
	if (dictado) dictado.detener();
}

function stopRecog() {
	if (dictado) {
		dictado.abortar();
		dictado = null;
	}
	["main", "var"].forEach((scope) => {
		if (state.ui[scope + ":dict"] === "listening") {
			const t = dictTarget(scope);
			state.ui[scope + ":dict"] = t && t.dictado ? "done" : "idle";
			state.ui[scope + ":interim"] = "";
		}
	});
}

function dictBlock(scope) {
	const t = dictTarget(scope);
	const mode = ui(scope + ":dict", t.dictado ? "done" : soportaVoz ? "idle" : "nosupport");
	const interim = ui(scope + ":interim", "");
	const isVar = scope === "var";
	const sc = ' data-scope="' + scope + '"';
	let out =
		'<div class="cp-guide">' +
		icon("info", "cp-icon--lg") +
		"<div>" +
		(isVar
			? '<p class="cp-guide__title">Di lo que cambia: sabor, tamaño o color</p>' +
			  (mode === "listening"
					? ""
					: '<p class="cp-guide__example">Ejemplo: «sabor durazno» o «sabor durazno de 2 litros»</p>')
			: '<p class="cp-guide__title">Di: marca, tipo, sabor y tamaño</p>' +
			  (mode === "listening"
					? ""
					: '<p class="cp-guide__example">Ejemplo: «Coca-Cola, gaseosa, sin azúcar, 3 litros»</p>')) +
		"</div></div>";

	const micBtn = (caption) =>
		'<div class="cp-mic-wrap"><button type="button" class="cp-mic" data-action="mic-start"' +
		sc +
		' aria-label="Empezar a dictar">' +
		icon("mic") +
		'</button><p class="cp-mic__caption">' +
		caption +
		"</p></div>";
	const typeBtn =
		'<button type="button" class="cp-btn cp-btn--ghost cp-btn--block" data-action="dict-type"' +
		sc +
		">" +
		icon("keyboard") +
		"Prefiero escribir</button>";

	if (mode === "idle") {
		out += micBtn("Toca el micrófono y habla") + typeBtn;
	} else if (mode === "listening") {
		out +=
			'<div class="cp-transcript is-live" aria-live="polite"><span class="cp-transcript__label">Lo que vas diciendo:</span>' +
			(t.dictado || interim
				? esc(t.dictado) + ' <span class="cp-transcript__interim">' + esc(interim) + "</span>"
				: '<span class="cp-transcript__placeholder">Habla ahora…</span>') +
			"</div>" +
			'<div class="cp-mic-wrap"><button type="button" class="cp-mic is-listening" data-action="mic-stop"' +
			sc +
			' aria-label="Terminar de dictar">' +
			icon("stop", "cp-icon--fill") +
			'</button><p class="cp-mic__caption">Te estoy escuchando…<br><span class="cp-help">Toca el botón rojo cuando termines</span></p></div>';
	} else if (mode === "done") {
		out +=
			'<div class="cp-transcript"><span class="cp-transcript__label">Lo que dijiste:</span>' +
			esc(t.dictado) +
			"</div>" +
			'<div class="cp-row">' +
			(soportaVoz
				? '<button type="button" class="cp-btn cp-btn--secondary" data-action="mic-start"' +
				  sc +
				  ">" +
				  icon("repeat") +
				  "Repetir</button>"
				: "") +
			'<button type="button" class="cp-btn cp-btn--secondary" data-action="dict-type"' +
			sc +
			">" +
			icon("edit") +
			"Corregir</button></div>" +
			'<p class="cp-help">No importa si está un poco mal escrito: el sistema lo corrige y tu supervisor lo revisa.</p>';
	} else if (mode === "nohear") {
		out +=
			alertBox(
				"warning",
				"No te escuché",
				"Habla más cerca del celular, sin ruido alrededor, y toca el micrófono otra vez."
			) +
			micBtn("Toca el micrófono y habla") +
			typeBtn;
	} else if (mode === "denied" || mode === "nomic") {
		out +=
			alertBox(
				"danger",
				mode === "nomic" ? "No encontramos el micrófono" : "No podemos usar el micrófono",
				mode === "nomic"
					? "Revisa que ninguna otra app esté usando el micrófono y vuelve a intentar."
					: "Para dictar, la página necesita permiso del micrófono.",
				(mode === "nomic" ? "" : permissionHelp("Micrófono")) +
					'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="mic-start"' +
					sc +
					">" +
					icon("repeat") +
					"Intentar de nuevo</button></div>"
			) + typeBtn;
	} else if (mode === "offline") {
		out +=
			alertBox(
				"warning",
				"Para dictar necesitas internet",
				"No hay señal ahora. Escribe el producto o espera a tener señal y toca el micrófono."
			) +
			micBtn("Toca el micrófono y habla") +
			typeBtn;
	} else {
		// "nosupport" o "typing"
		out +=
			'<div class="cp-field"><label class="cp-label" for="cp-dict-text">' +
			(isVar ? "Escribe lo que cambia" : "Escribe el producto") +
			"</label>" +
			'<textarea class="cp-textarea" id="cp-dict-text" data-input="dict"' +
			sc +
			' rows="3" maxlength="1000" placeholder="' +
			(isVar ? "Ej.: sabor durazno" : "Ej.: Coca-Cola gaseosa sin azúcar 3 litros") +
			'">' +
			esc(t.dictado) +
			"</textarea></div>";
		if (mode === "nosupport") {
			out += alertBox(
				"info",
				"Aquí no se puede dictar",
				"Tu navegador no permite dictar en esta página. <strong>Consejo:</strong> toca el micrófono " +
					icon("mic") +
					" del teclado de tu celular para dictar igual."
			);
		}
		if (mode === "typing" && soportaVoz) {
			out +=
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="mic-start"' +
				sc +
				">" +
				icon("mic") +
				"Mejor dictar con la voz</button>";
		}
	}
	return out;
}

/* ================= Código de barras ================= */
const video = document.createElement("video");
video.setAttribute("playsinline", "");
video.setAttribute("aria-hidden", "true");
video.muted = true;
video.autoplay = true;

let escaner = null;
let scanScope = "main";
let lecturaId = 0;

function codeTarget(scope) {
	return scope === "var" ? state.vdraft : state.draft;
}
function maxCodigos(scope) {
	return scope === "var" ? 1 : MAX_CODIGOS;
}

function getEscaner() {
	if (!escaner) {
		escaner = crearEscaner({
			video,
			zxingUrl: boot.zxing_url,
			onListo(info) {
				state.linterna = info.linterna;
				state.ui[scanScope + ":scan"] = "scanning";
				render();
			},
			onCodigo(valor, formato) {
				onDetected(scanScope, valor, formato, "Escaneado");
			},
			onError(tipo) {
				state.ui[scanScope + ":scan"] = tipo === "denied" ? "denied" : "nocam";
				if (tipo === "denied") state.camPermiso = "denied";
				feedbackError();
				render();
				announce(tipo === "denied" ? "No podemos usar la cámara" : "No se pudo abrir la cámara");
			},
		});
	}
	return escaner;
}

function startScan(scope) {
	const t = codeTarget(scope);
	if (!t) return;
	state.ui[scope + ":result"] = null;
	if (t.codes.length >= maxCodigos(scope)) return;
	t.sinCodigo = false;
	scanScope = scope;
	state.torch = false;
	state.ui[scope + ":scan"] = "starting";
	render();
	getEscaner().iniciar();
}

function stopScan() {
	if (escaner && escaner.activo) escaner.detener();
	lecturaId++;
	["main", "var"].forEach((scope) => {
		const s = state.ui[scope + ":scan"];
		if (s === "scanning" || s === "starting") state.ui[scope + ":scan"] = "paused";
	});
}

function codigoUsadoEn(code, scope) {
	const clave = codigos.clave(code);
	const d = state.draft;
	if (!d) return null;
	if (scope === "var" && d.codes.some((c) => codigos.clave(c.codigo) === clave)) {
		return "Variante 1 (este producto)";
	}
	for (let i = 0; i < d.variants.length; i++) {
		if (d.variants[i].codes.some((c) => codigos.clave(c.codigo) === clave)) return "Variante " + (i + 2);
	}
	return null;
}

// "Coca-Cola 500 ml (BEB-COCA-500ML)" (HU-03).
function productoExistente(nombre, itemCode) {
	if (nombre && itemCode && nombre !== itemCode) return nombre + " (" + itemCode + ")";
	return nombre || itemCode || "";
}

async function onDetected(scope, raw, formato, origen) {
	const t = codeTarget(scope);
	if (!t) return;
	let code = codigos.normalizar(raw);
	if (formato === "upc_e" && /^\d{8}$/.test(code)) code = codigos.upcEtoA(code);
	stopScan();
	state.ui[scope + ":scan"] = "paused";
	const a = codigos.analizar(code);
	if (!a.valido) {
		state.ui[scope + ":result"] = { type: "invalid", code };
		feedbackError();
		render();
		announce("El código no se leyó bien");
		return;
	}
	if (t.codes.some((c) => codigos.clave(c.codigo) === codigos.clave(code))) {
		state.ui[scope + ":result"] = null;
		render();
		toast("Ese código ya está en la lista");
		return;
	}
	const usado = codigoUsadoEn(code, scope);
	if (usado) {
		state.ui[scope + ":result"] = { type: "used", code, name: usado };
		feedbackError();
		render();
		announce("Ya usaste este código en " + usado);
		return;
	}

	const id = ++lecturaId;
	state.ui[scope + ":result"] = { type: "checking", code };
	render();
	let r = null;
	if (navigator.onLine) {
		try {
			r = await api.verificarCodigo(code, state.draft && state.draft.serverName);
		} catch (e) {
			r = null; // sin red o error: el servidor vuelve a validar al enviar
		}
	}
	if (id !== lecturaId || codeTarget(scope) !== t) return; // la persona ya hizo otra cosa
	if (r && !r.valido) {
		state.ui[scope + ":result"] = { type: "invalid", code };
		feedbackError();
		render();
		announce("El código no se leyó bien");
	} else if (r && r.bloquea) {
		state.ui[scope + ":result"] = {
			type: "dup",
			code,
			name: r.item_name || r.nombre || r.item_code,
			itemCode: r.item_code,
		};
		feedbackError();
		render();
		announce("Este producto ya existe: " + productoExistente(r.item_name || r.nombre, r.item_code));
	} else if (r && r.existe) {
		state.ui[scope + ":result"] = {
			type: "dupcap",
			code,
			origen,
			persona: r.capturado_por,
			texto: r.nombre,
			estado: r.estado,
		};
		feedbackError();
		render();
		announce("Este código ya lo capturó " + (r.capturado_por || "otra persona"));
	} else {
		aceptarCodigo(scope, code, origen);
	}
}

function aceptarCodigo(scope, code, origen) {
	const t = codeTarget(scope);
	t.codes.push({ codigo: code, origen: origen || "Escaneado" });
	t.sinCodigo = false;
	state.ui[scope + ":scan"] = "paused";
	state.ui[scope + ":result"] = { type: "ok", code };
	feedbackOk(state.sound);
	persist();
	render();
	announce("Código leído: " + code);
}

function codeBlock(scope) {
	const t = codeTarget(scope);
	// Sin guardar el valor por defecto: render() abre la cámara si el estado todavía no existe.
	const scan = state.ui[scope + ":scan"] || (t.codes.length || t.sinCodigo ? "paused" : "starting");
	const res = ui(scope + ":result", null);
	const sc = ' data-scope="' + scope + '"';
	const lleno = t.codes.length >= maxCodigos(scope);
	let out = "";

	if (scan === "denied" || scan === "nocam") {
		out += alertBox(
			"danger",
			scan === "denied" ? "No podemos usar la cámara" : "No se pudo abrir la cámara",
			scan === "denied"
				? "Para leer el código, la página necesita la cámara."
				: "Revisa que ninguna otra app esté usando la cámara y vuelve a intentar.",
			(scan === "denied" ? permissionHelp("Cámara") : "") +
				'<div class="cp-alert__actions">' +
				'<button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("repeat") +
				"Intentar de nuevo</button>" +
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="code-manual"' +
				sc +
				">" +
				icon("keyboard") +
				"Escribir el código</button></div>"
		);
	} else {
		const reading = scan === "scanning";
		const checking = res && res.type === "checking";
		const justRead = scan === "paused" && res && res.type === "ok";
		const paused = scan === "paused" && !justRead && !checking;
		const conCamara = scan === "starting" || reading;
		out +=
			'<div class="cp-scanner' +
			(justRead ? " is-read" : "") +
			(paused ? " is-paused" : "") +
			'" role="group" aria-label="Cámara para leer el código">' +
			(conCamara ? '<div data-video-slot style="position:absolute;inset:0"></div>' : "") +
			(scan === "starting"
				? '<div class="cp-scanner__msg"><div class="cp-spinner cp-spinner--light"></div>Abriendo la cámara…</div>'
				: reading || justRead || checking
				? '<div class="cp-scanner__frame" aria-hidden="true">' +
				  (reading ? '<div class="cp-scanner__line"></div>' : "") +
				  "</div>"
				: '<div class="cp-scanner__msg">' + icon("barcode", "cp-icon--lg") + "Cámara en pausa</div>") +
			(reading
				? '<div class="cp-scanner__tools">' +
				  (state.linterna
						? '<button type="button" class="cp-scanner__tool" data-action="toggle-torch" aria-pressed="' +
						  state.torch +
						  '">' +
						  icon("flash") +
						  "Luz</button>"
						: "") +
				  '<button type="button" class="cp-scanner__tool" data-action="toggle-sound" aria-pressed="' +
				  state.sound +
				  '" aria-label="Sonido al leer">' +
				  icon(state.sound ? "volume" : "volume-off") +
				  (state.sound ? "Sonido" : "Sin sonido") +
				  "</button></div>"
				: "") +
			(reading || justRead || checking
				? '<span class="cp-scanner__status" aria-live="polite">' +
				  (reading
						? icon("loader") + "Buscando código…"
						: checking
						? icon("loader") + "Revisando el código…"
						: icon("check") + "¡Código leído!") +
				  "</span>"
				: "") +
			"</div>";
		if (reading) out += '<p class="cp-help" style="text-align:center">Pon el código dentro del recuadro. Se lee solo.</p>';
	}

	if (res && res.type === "dup") {
		out += alertBox(
			"danger",
			"Este producto ya existe: " + productoExistente(res.name, res.itemCode),
			"No hace falta capturarlo otra vez. Si es otro producto, escanea el código correcto.",
			'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("barcode") +
				"Escanear otro código</button>" +
				(scope === "main"
					? '<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="discard-product">' +
					  icon("home") +
					  "Dejar este producto</button>"
					: "") +
				"</div>"
		);
	} else if (res && res.type === "dupcap") {
		const estado = res.estado ? " (" + res.estado.toLowerCase() + ")" : "";
		out += alertBox(
			"danger",
			"Este código ya lo capturó " + (res.persona || "otra persona") + ": " + (res.texto || "") + estado,
			"Si es el mismo producto, no hace falta capturarlo otra vez. Si es otro producto, escanea el código correcto.",
			'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("barcode") +
				"Escanear otro código</button>" +
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="code-accept"' +
				sc +
				' data-id="' +
				esc(res.code) +
				'">' +
				icon("plus") +
				"Agregarlo igual</button>" +
				(scope === "main"
					? '<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="discard-product">' +
					  icon("home") +
					  "Dejar este producto</button>"
					: "") +
				"</div>"
		);
	} else if (res && res.type === "used") {
		out += alertBox(
			"danger",
			"Ya usaste este código en: " + res.name,
			"Cada variante necesita su propio código. Escanea el código de esta variante.",
			'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("barcode") +
				"Escanear de nuevo</button></div>"
		);
	} else if (res && res.type === "invalid") {
		out += alertBox(
			"warning",
			"El código no se leyó bien",
			"Los números <strong>" +
				esc(codigos.formatCode(res.code)) +
				"</strong> no son un código válido. Vuelve a escanear con más luz o con el código más derecho.",
			'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("barcode") +
				"Escanear de nuevo</button>" +
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="code-manual"' +
				sc +
				">" +
				icon("keyboard") +
				"Escribir el código</button></div>"
		);
	}

	if (t.codes.length) {
		out +=
			'<h2 class="cp-subtitle">Códigos leídos (' +
			t.codes.length +
			')</h2><ul class="cp-code-list">' +
			t.codes
				.map(
					(c, i) =>
						'<li class="cp-code' +
						(res && res.type === "ok" && res.code === c.codigo ? " is-new" : "") +
						'">' +
						icon("check", "cp-icon--lg cp-code__ok") +
						'<span class="cp-code__num"><span class="cp-visually-hidden">Código ' +
						(i + 1) +
						": </span>" +
						esc(codigos.formatCode(c.codigo)) +
						"</span>" +
						'<button type="button" class="cp-code__del" data-action="del-code"' +
						sc +
						' data-id="' +
						esc(c.codigo) +
						'" aria-label="Quitar el código ' +
						esc(c.codigo) +
						'">' +
						icon("trash") +
						"Quitar</button></li>"
				)
				.join("") +
			"</ul>";
		const sinAlerta = !(res && res.type !== "ok");
		if (scan === "paused" && sinAlerta && !lleno) {
			out +=
				'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="scan-again"' +
				sc +
				">" +
				icon("plus") +
				"Escanear otro código</button>" +
				'<p class="cp-help">Algunos productos tienen más de un código. Si ves otro, escanéalo también.</p>';
		}
		if (lleno && scope === "main") out += '<p class="cp-help">Llegaste al máximo de ' + MAX_CODIGOS + " códigos.</p>";
	}

	if (scan !== "denied" && scan !== "nocam" && !(res && res.type === "invalid") && !lleno) {
		out +=
			'<button type="button" class="cp-btn cp-btn--ghost cp-btn--block" data-action="code-manual"' +
			sc +
			">" +
			icon("keyboard") +
			"Escribir el código a mano</button>";
	}
	if (!t.codes.length) {
		out +=
			'<button type="button" class="cp-toggle" data-action="toggle-sincodigo"' +
			sc +
			' aria-pressed="' +
			!!t.sinCodigo +
			'"><span class="cp-toggle__box">' +
			icon("check") +
			"</span>" +
			(scope === "var" ? "Esta variante no tiene código" : "Este producto no tiene código") +
			"</button>";
	}
	return out;
}

function openCodeSheet(scope) {
	stopScan();
	render();
	closeSheets();
	const back = document.createElement("div");
	back.className = "cp-sheet-backdrop";
	back.innerHTML =
		'<div class="cp-sheet" role="dialog" aria-modal="true" aria-labelledby="cp-sheet-title">' +
		'<h2 class="cp-sheet__title" id="cp-sheet-title" tabindex="-1">Escribe el código</h2>' +
		'<p class="cp-sheet__text">Copia los números que están debajo de las barras.</p>' +
		'<div class="cp-field"><label class="cp-label" for="cp-code-input">Números del código</label>' +
		'<input class="cp-input cp-input--code" id="cp-code-input" inputmode="numeric" pattern="[0-9]*" autocomplete="off" maxlength="20" aria-describedby="cp-code-err">' +
		'<p class="cp-field-error" id="cp-code-err" hidden></p></div>' +
		'<button type="button" class="cp-btn cp-btn--primary cp-btn--lg cp-btn--block" data-sheet="ok">' +
		icon("check") +
		"Guardar código</button>" +
		'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-sheet="cancel">Cancelar</button></div>';
	document.body.appendChild(back);
	const input = back.querySelector("input");
	const err = back.querySelector("#cp-code-err");
	const prevFocus = document.activeElement;
	input.focus();
	function close(silencioso) {
		back.remove();
		document.removeEventListener("keydown", onKey);
		if (!silencioso && prevFocus && document.body.contains(prevFocus)) prevFocus.focus();
	}
	back._close = close;
	function showErr(msg) {
		err.innerHTML = icon("alert") + "<span>" + esc(msg) + "</span>";
		err.hidden = false;
		input.setAttribute("aria-invalid", "true");
		input.focus();
	}
	function onKey(e) {
		if (e.key === "Escape") close();
		trapFocus(e, back);
	}
	document.addEventListener("keydown", onKey);
	input.addEventListener("input", () => {
		input.value = input.value.replace(/\D/g, "");
		err.hidden = true;
		input.removeAttribute("aria-invalid");
	});
	input.addEventListener("keydown", (e) => {
		if (e.key === "Enter") back.querySelector('[data-sheet="ok"]').click();
	});
	back.addEventListener("click", (e) => {
		if (e.target === back || e.target.closest('[data-sheet="cancel"]')) {
			close();
			return;
		}
		if (e.target.closest('[data-sheet="ok"]')) {
			const v = input.value;
			const error = codigos.errorManual(v);
			if (error) {
				showErr(error);
				return;
			}
			close(true);
			onDetected(scope, v, null, "Manual");
		}
	});
}

/* ================= Envío y cola sin internet ================= */
function faltaParaEnviar(d) {
	if (!d.photos.length || d.photos.some((p) => p.loading)) return "fotos";
	if ((d.dictado || "").trim().length < MIN_DICTADO) return "dictar";
	return null;
}

async function send() {
	const d = state.draft;
	if (!d || state.ui.send === "sending") return;
	const falta = faltaParaEnviar(d);
	if (falta) {
		feedbackError();
		state.toReview = true;
		toast(falta === "fotos" ? "Falta tomar al menos 1 foto." : "Falta decir qué producto es.");
		go(falta);
		return;
	}
	state.ui.send = "sending";
	state.ui.sendMsg = "";
	render();
	await guardarYa();
	try {
		await enviarBorrador(d, guardarYa, (i, n) => {
			state.ui.sendMsg = i ? (n > 1 ? "Subiendo foto " + i + " de " + n : "Subiendo la foto") : "Guardando los datos…";
			const el = document.getElementById("cp-send-msg");
			if (el) el.textContent = state.ui.sendMsg;
		});
	} catch (e) {
		state.ui.send = "error";
		state.ui.sendError = { tipo: e.tipo || "servidor", mensaje: e.message || "" };
		feedbackError();
		render();
		announce(e.tipo === "red" ? "No se pudo enviar: no hay internet" : "No se pudo enviar");
		return;
	}
	state.lastSent = { titulo: d.dictado, pendiente: false };
	await descartarBorrador();
	vibrarExito();
	go("exito");
	cargarCapturas(true);
}

async function sendLater() {
	const d = state.draft;
	if (!d) return;
	await store.set(K("pend:" + d.uuid), copiaGuardable(d));
	state.lastSent = { titulo: d.dictado, pendiente: true };
	state.draft = null;
	state.vdraft = null;
	state.ui = {};
	await guardarYa();
	await cargarPendientes();
	go("exito");
}

async function descartarBorrador() {
	if (state.draft) todasLasFotos(state.draft).forEach(liberar);
	if (state.vdraft) state.vdraft.photos.forEach(liberar);
	state.draft = null;
	state.vdraft = null;
	state.toReview = false;
	state.ui = {};
	await guardarYa();
}

let colaActiva = false;
async function procesarCola() {
	if (colaActiva || !navigator.onLine) return 0;
	colaActiva = true;
	let enviados = 0;
	try {
		for (const k of await store.keys(K("pend:"))) {
			const d = await store.get(k);
			if (!d) continue;
			try {
				await enviarBorrador(d, () => store.set(k, d));
				await store.del(k);
				enviados++;
			} catch (e) {
				d.ultimoError = e.tipo || "servidor";
				await store.set(k, d);
				if (e.tipo === "red" || e.tipo === "sesion") break;
			}
		}
	} finally {
		colaActiva = false;
		await cargarPendientes();
	}
	if (enviados) {
		toast(enviados === 1 ? "Se envió 1 producto guardado" : "Se enviaron " + enviados + " productos guardados");
		cargarCapturas(true);
	} else if (route() === "inicio" || route() === "capturas") {
		render();
	}
	return enviados;
}

/* ================= Mis capturas ================= */
let sondeo = null;
async function cargarCapturas(silencioso) {
	if (!silencioso || state.capturas.estado === "idle") {
		state.capturas.estado = "cargando";
		if (route() === "capturas") render();
	}
	try {
		const lista = await api.misCapturas(50);
		state.capturas = { estado: "ok", lista: lista || [] };
		prefs.set("resumen", contarEstados(state.capturas.lista));
	} catch (e) {
		state.capturas.estado = silencioso && state.capturas.lista.length ? "ok" : "error";
		state.capturas.tipoError = e.tipo;
	}
	if (route() === "capturas" || route() === "inicio") render();
	programarSondeo();
}

function programarSondeo() {
	clearTimeout(sondeo);
	const hayProcesando = state.capturas.lista.some((c) => c.estado === "Procesando");
	if (route() === "capturas" && hayProcesando && document.visibilityState === "visible") {
		sondeo = setTimeout(() => cargarCapturas(true), 15000);
	}
}

function uuidsLocales() {
	const s = new Set(state.pendientes.map((p) => p.uuid));
	if (state.draft) s.add(state.draft.uuid);
	return s;
}

function contarEstados(lista) {
	const c = { procesando: 0, revisar: 0, error: 0, pendiente: 0 };
	const locales = uuidsLocales();
	lista.forEach((x) => {
		const k = CHIP_DE_ESTADO[x.estado];
		if (k === "pendiente" && locales.has(x.client_uuid)) return;
		if (k in c) c[k]++;
	});
	return c;
}

/* ================= Pantallas ================= */
const views = {};

views.inicio = function () {
	const c = state.capturas.estado === "ok" ? contarEstados(state.capturas.lista) : prefs.get("resumen", {});
	const pendientes = (c.pendiente || 0) + state.pendientes.length;
	const meta = [];
	if (c.procesando) meta.push(c.procesando + " procesando");
	if (c.revisar) meta.push(c.revisar + " por revisar");
	if (c.error) meta.push(c.error + " con error");
	if (pendientes) meta.push(pendientes + " sin enviar");
	const hasDraft = tieneContenido(state.draft);
	return (
		'<header class="cp-appbar"><div class="cp-appbar__text"><span class="cp-appbar__brand">Captura de productos</span><span class="cp-appbar__user">Hola, ' +
		esc(boot.nombre) +
		'</span></div><button type="button" class="cp-appbar__logout" data-action="logout" aria-label="Cerrar sesión">' +
		"Salir</button></header>" +
		(!state.online
			? '<div class="cp-banner" role="status">' +
			  icon("wifi-off") +
			  "<span>Sin internet. Puedes capturar igual; enviaremos cuando vuelva la señal.</span></div>"
			: "") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">¿Qué quieres hacer?</h1>' +
		(hasDraft
			? '<button type="button" class="cp-btn cp-btn--warning cp-btn--lg cp-btn--block" data-action="resume">' +
			  icon("edit") +
			  "Seguir con el producto sin terminar</button>"
			: "") +
		'<button type="button" class="cp-hero-btn" data-action="new"><span class="cp-hero-btn__icon">' +
		icon("camera", "cp-icon--xl") +
		'</span><span class="cp-hero-btn__label">Nuevo producto</span><span class="cp-hero-btn__hint">Fotos, voz y código de barras</span></button>' +
		'<button type="button" class="cp-menu-btn" data-action="go" data-to="capturas"><span class="cp-menu-btn__icon">' +
		icon("list", "cp-icon--lg") +
		'</span><span class="cp-menu-btn__text"><span class="cp-menu-btn__label">Mis capturas</span><span class="cp-menu-btn__meta">' +
		(meta.length ? esc(meta.join(" · ")) : "Todavía no enviaste productos") +
		"</span></span>" +
		icon("right") +
		"</button>" +
		(store.esPersistente()
			? ""
			: '<p class="cp-help">' +
			  icon("info") +
			  " Este navegador no deja guardar el producto en el celular: termina de enviarlo antes de cerrar la página.</p>") +
		"</main>"
	);
};

function nextLabel() {
	return state.toReview ? "Volver al resumen" : "Siguiente";
}

views.fotos = function () {
	const d = state.draft;
	const ready = d.photos.length > 0 && d.photos.every((p) => !p.loading);
	return (
		header("Nuevo producto") +
		progress(STEPS, "fotos") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">Toma fotos del producto</h1>' +
		'<p class="cp-lead">Saca el frente, con la marca bien visible. Puedes sacar hasta 4 fotos.</p>' +
		photosBlock(d.photos, { scope: "main", max: MAX_FOTOS, emptyText: "Toca el botón azul para abrir la cámara." }) +
		"</main>" +
		footer({
			next: "next",
			nextLabel: nextLabel(),
			nextDisabled: !ready,
			hint: d.photos.length ? "Espera a que la foto esté lista." : "Toma al menos 1 foto para seguir.",
			back: "exit-back",
		})
	);
};

views.dictar = function () {
	const d = state.draft;
	const mode = ui("main:dict", d.dictado ? "done" : soportaVoz ? "idle" : "nosupport");
	return (
		header("Nuevo producto") +
		progress(STEPS, "dictar") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">Di qué producto es</h1>' +
		dictBlock("main") +
		"</main>" +
		footer({
			next: "next",
			nextLabel: nextLabel(),
			nextDisabled: (d.dictado || "").trim().length < MIN_DICTADO || mode === "listening",
			hint: mode === "listening" ? "Termina de hablar para seguir." : "Dicta o escribe el producto para seguir.",
			back: "back",
		})
	);
};

views.codigo = function () {
	const d = state.draft;
	return (
		header("Nuevo producto") +
		progress(STEPS, "codigo") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">Escanea el código de barras</h1>' +
		codeBlock("main") +
		"</main>" +
		footer({
			next: "next",
			nextLabel: nextLabel(),
			nextDisabled: !(d.codes.length || d.sinCodigo),
			hint: "Escanea un código o marca «no tiene código».",
			back: "back",
		})
	);
};

function variantCard(v, n, isMain) {
	const p = v.photos.find((x) => x.principal) || v.photos[0];
	return (
		'<li class="cp-card"><div class="cp-item">' +
		'<div class="cp-item__thumb">' +
		(p ? '<img src="' + esc(srcDe(p)) + '" alt="">' : "") +
		"</div>" +
		'<div class="cp-item__body"><span class="cp-tag">Variante ' +
		n +
		(isMain ? " · la que ya registraste" : "") +
		"</span>" +
		'<p class="cp-item__title">«' +
		esc(v.dictado) +
		"»</p>" +
		'<p class="cp-item__meta">' +
		(v.codes.length
			? icon("barcode") + " " + v.codes.map((c) => esc(codigos.formatCode(c.codigo))).join(", ")
			: "Sin código") +
		"</p></div>" +
		(isMain
			? ""
			: '<div class="cp-item__actions"><button type="button" class="cp-btn cp-btn--secondary" data-action="del-variant" data-id="' +
			  v.id +
			  '">' +
			  icon("trash") +
			  "Borrar</button></div>") +
		"</div></li>"
	);
}

views.variantes = function () {
	const d = state.draft;
	let body;
	if (d.hasVariants !== true) {
		body =
			'<h1 class="cp-title" tabindex="-1">¿Este producto tiene variantes?</h1>' +
			'<p class="cp-lead">Variantes son el mismo producto en otro sabor, tamaño o color. Ejemplo: jugo Del Valle de durazno, de manzana y de naranja.</p>' +
			'<button type="button" class="cp-choice" data-action="has-variants" data-id="yes" aria-pressed="false"><span class="cp-choice__icon">' +
			icon("layers", "cp-icon--lg") +
			'</span><span class="cp-choice__text"><span class="cp-choice__label">Sí, tiene</span><span class="cp-choice__hint">Hay otros sabores, tamaños o colores</span></span></button>' +
			'<button type="button" class="cp-choice" data-action="has-variants" data-id="no" aria-pressed="' +
			(d.hasVariants === false) +
			'"><span class="cp-choice__icon cp-choice__icon--neutral">' +
			icon("check", "cp-icon--lg") +
			'</span><span class="cp-choice__text"><span class="cp-choice__label">No, es uno solo</span><span class="cp-choice__hint">Pasar a revisar y enviar</span></span></button>';
		return (
			header("Nuevo producto") +
			progress(STEPS, "variantes") +
			'<main class="cp-main">' +
			body +
			"</main>" +
			footer({ next: null, back: "back" })
		);
	}
	const puedeAgregar = d.variants.length < MAX_VARIANTES_EXTRA;
	body =
		'<h1 class="cp-title" tabindex="-1">Variantes del producto</h1>' +
		'<p class="cp-lead">Por cada variante: 1 foto, lo que cambia (ej. «sabor durazno») y su código.</p>' +
		(puedeAgregar
			? '<button type="button" class="cp-btn cp-btn--primary cp-btn--xl cp-btn--block" data-action="add-variant">' +
			  icon("plus", "cp-icon--lg") +
			  "Agregar variante " +
			  (d.variants.length + 2) +
			  "</button>"
			: '<p class="cp-help">Llegaste al máximo de 20 variantes.</p>') +
		'<h2 class="cp-subtitle">Variantes (' +
		(d.variants.length + 1) +
		")</h2>" +
		'<ul class="cp-list">' +
		d.variants
			.map((v, i) => variantCard(v, i + 2, false))
			.reverse()
			.join("") +
		variantCard(d, 1, true) +
		"</ul>" +
		(d.variants.length
			? ""
			: '<p class="cp-help">La variante 1 es la que ya registraste. Toca <strong>Agregar variante 2</strong> para seguir.</p>') +
		'<button type="button" class="cp-btn cp-btn--ghost cp-btn--block" data-action="has-variants" data-id="no">Cambié de idea: no tiene variantes</button>';
	return (
		header("Nuevo producto") +
		progress(STEPS, "variantes") +
		'<main class="cp-main">' +
		body +
		"</main>" +
		footer({
			next: "next",
			nextDisabled: !d.variants.length,
			hint: "Agrega al menos 1 variante, o elige «no tiene variantes».",
			back: "back",
		})
	);
};

function varTitle() {
	return "Variante " + (state.draft.variants.length + 2);
}

views["var-foto"] = function () {
	const v = state.vdraft;
	const ready = v.photos.length && !v.photos[0].loading;
	return (
		header(varTitle(), "var-exit") +
		progress(VSTEPS, "var-foto") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">Toma una foto de esta variante</h1>' +
		photosBlock(v.photos, { scope: "var", max: 1, emptyText: "Que se vea bien el sabor, tamaño o color." }) +
		"</main>" +
		footer({ next: "var-next", nextDisabled: !ready, hint: "Toma la foto para seguir.", back: "var-exit" })
	);
};

views["var-dictar"] = function () {
	const v = state.vdraft;
	const mode = ui("var:dict", v.dictado ? "done" : soportaVoz ? "idle" : "nosupport");
	return (
		header(varTitle(), "var-exit") +
		progress(VSTEPS, "var-dictar") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">¿Qué cambia en esta variante?</h1>' +
		dictBlock("var") +
		"</main>" +
		footer({
			next: "var-next",
			nextDisabled: !(v.dictado || "").trim() || mode === "listening",
			hint: mode === "listening" ? "Termina de hablar para seguir." : "Dicta o escribe lo que cambia.",
			back: "back",
		})
	);
};

views["var-codigo"] = function () {
	const v = state.vdraft;
	return (
		header(varTitle(), "var-exit") +
		progress(VSTEPS, "var-codigo") +
		'<main class="cp-main"><h1 class="cp-title" tabindex="-1">Escanea el código de esta variante</h1>' +
		codeBlock("var") +
		"</main>" +
		footer({
			next: "var-save",
			nextLabel: "Guardar variante",
			nextIcon: "check",
			nextDisabled: !(v.codes.length || v.sinCodigo),
			hint: "Escanea el código o marca «no tiene código».",
			back: "back",
		})
	);
};

function sendErrorBox(err) {
	const acciones =
		'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="send">' +
		icon("repeat") +
		"Intentar de nuevo</button>" +
		'<button type="button" class="cp-btn cp-btn--secondary cp-btn--block" data-action="send-later">' +
		icon("clock") +
		"Enviar después</button></div>";
	if (err.tipo === "red") {
		return alertBox(
			"danger",
			"No se pudo enviar: no hay internet",
			"Tu producto quedó guardado en este celular. Toca <strong>Intentar de nuevo</strong> cuando tengas señal.",
			acciones
		);
	}
	if (err.tipo === "sesion") {
		return alertBox(
			"danger",
			"Tu sesión se cerró",
			"Tu producto quedó guardado en este celular. Vuelve a entrar con tu usuario y toca <strong>Enviar</strong> otra vez.",
			'<div class="cp-alert__actions"><a class="cp-btn cp-btn--primary cp-btn--block" href="/login?redirect-to=' +
				encodeURIComponent("/captura#/resumen") +
				'">' +
				icon("lock") +
				"Entrar de nuevo</a></div>"
		);
	}
	return alertBox(
		"danger",
		"No se pudo enviar",
		"Tu producto quedó guardado en este celular. Intenta de nuevo en un rato; si sigue fallando, avisa a tu supervisor.",
		(err.mensaje ? '<p class="cp-alert__detail">' + esc(err.mensaje) + "</p>" : "") + acciones
	);
}

views.resumen = function () {
	const d = state.draft;
	const sending = ui("send", "idle");
	if (sending === "sending") {
		return (
			header("Nuevo producto", "noop") +
			progress(STEPS, "resumen") +
			'<main class="cp-main"><div class="cp-state" role="status"><div class="cp-spinner"></div><h1 class="cp-state__title" tabindex="-1">Enviando…</h1>' +
			'<p class="cp-state__text">No cierres la página. Esto tarda unos segundos.</p>' +
			'<p class="cp-progress-text" id="cp-send-msg">' +
			esc(state.ui.sendMsg || "") +
			"</p></div></main>"
		);
	}
	const section = (title, step, content) =>
		'<section class="cp-card" aria-label="' +
		esc(title) +
		'"><div class="cp-card__head"><h2 class="cp-subtitle">' +
		esc(title) +
		"</h2>" +
		'<button type="button" class="cp-btn cp-btn--secondary" data-action="edit-step" data-id="' +
		step +
		'" aria-label="Cambiar ' +
		esc(title.toLowerCase()) +
		'">' +
		icon("edit") +
		"Cambiar</button></div>" +
		content +
		"</section>";
	let body = '<h1 class="cp-title" tabindex="-1">Revisa y envía</h1>';
	if (sending === "error") body += sendErrorBox(state.ui.sendError || {});
	body += section(
		"Fotos",
		"fotos",
		'<div class="cp-thumbs">' +
			d.photos
				.map(
					(p, i) =>
						'<div class="cp-thumb' +
						(p.principal ? " is-principal" : "") +
						'"><img src="' +
						esc(srcDe(p)) +
						'" alt="Foto ' +
						(i + 1) +
						(p.principal ? ", principal" : "") +
						'"></div>'
				)
				.join("") +
			"</div>"
	);
	body += section("Lo que dijiste", "dictar", '<p class="cp-quote">«' + esc(d.dictado) + "»</p>");
	body += section(
		"Código de barras",
		"codigo",
		d.codes.length
			? '<ul class="cp-code-list">' +
					d.codes
						.map(
							(c) =>
								'<li class="cp-code">' +
								icon("check", "cp-code__ok") +
								'<span class="cp-code__num">' +
								esc(codigos.formatCode(c.codigo)) +
								"</span></li>"
						)
						.join("") +
					"</ul>"
			: '<p class="cp-item__meta">No tiene código</p>'
	);
	body += section(
		"Variantes",
		"variantes",
		d.hasVariants && d.variants.length
			? '<ul class="cp-list">' +
					d.variants
						.map((v, i) => {
							const p = v.photos[0];
							return (
								'<li class="cp-item"><div class="cp-item__thumb">' +
								(p ? '<img src="' + esc(srcDe(p)) + '" alt="">' : "") +
								'</div><div class="cp-item__body"><span class="cp-tag">Variante ' +
								(i + 2) +
								'</span><p class="cp-item__title">«' +
								esc(v.dictado) +
								'»</p><p class="cp-item__meta">' +
								(v.codes.length ? v.codes.map((c) => esc(codigos.formatCode(c.codigo))).join(", ") : "Sin código") +
								"</p></div></li>"
							);
						})
						.join("") +
					"</ul>"
			: '<p class="cp-item__meta">No tiene variantes</p>'
	);
	body += '<p class="cp-help">' + icon("info") + " El nombre final lo arma el sistema y lo revisa tu supervisor.</p>";
	return (
		header("Nuevo producto") +
		progress(STEPS, "resumen") +
		'<main class="cp-main">' +
		body +
		"</main>" +
		'<footer class="cp-footer"><div class="cp-footer__row"><button type="button" class="cp-btn cp-btn--secondary cp-btn--lg" data-action="back">' +
		icon("left") +
		"Atrás</button>" +
		'<button type="button" class="cp-btn cp-btn--success cp-btn--lg" data-action="send">' +
		icon("send") +
		"Enviar</button></div></footer>"
	);
};

views.exito = function () {
	const last = state.lastSent || {};
	return (
		'<main class="cp-main"><div class="cp-state"><div class="cp-state__icon cp-state__icon--success">' +
		icon("check") +
		"</div>" +
		'<h1 class="cp-state__title" tabindex="-1">' +
		(last.pendiente ? "Guardado" : "¡Listo!") +
		"</h1>" +
		'<p class="cp-state__text">' +
		(last.pendiente
			? "Lo enviaremos automáticamente cuando vuelva la señal. Puedes seguir capturando."
			: "Tu producto se envió. Tu supervisor lo va a revisar.") +
		"</p>" +
		(last.titulo ? '<p class="cp-quote">«' + esc(last.titulo) + "»</p>" : "") +
		'</div><div class="cp-stack">' +
		'<button type="button" class="cp-btn cp-btn--primary cp-btn--xl cp-btn--block" data-action="new">' +
		icon("camera", "cp-icon--lg") +
		"Siguiente producto</button>" +
		'<button type="button" class="cp-btn cp-btn--secondary cp-btn--lg cp-btn--block" data-action="go" data-to="capturas">' +
		icon("list") +
		"Ver mis capturas</button>" +
		"</div></main>"
	);
};

function tarjetaPendiente(p) {
	const foto = p.photos.find((x) => x.principal) || p.photos[0];
	const n = p.hasVariants ? p.variants.length + 1 : 0;
	return (
		'<li class="cp-card"><div class="cp-item"><div class="cp-item__thumb">' +
		(foto ? '<img src="' + esc(srcDe(foto)) + '" alt="">' : "") +
		'</div><div class="cp-item__body">' +
		'<p class="cp-item__title">' +
		esc(p.dictado || "Producto sin nombre") +
		"</p>" +
		'<p class="cp-item__meta">' +
		esc(fechaRelativa(new Date(p.creado))) +
		(n ? " · " + n + " variantes" : "") +
		"</p><div>" +
		chip("pendiente") +
		"</div>" +
		'<p class="cp-item__meta">Se enviará cuando haya señal.</p></div>' +
		'<div class="cp-item__actions"><button type="button" class="cp-btn cp-btn--primary" data-action="retry-pending" data-id="' +
		esc(p.uuid) +
		'">' +
		icon("send") +
		"Enviar ahora</button></div></div></li>"
	);
}

function tarjetaServidor(c) {
	const k = CHIP_DE_ESTADO[c.estado] || "pendiente";
	return (
		'<li class="cp-card"><div class="cp-item"><div class="cp-item__thumb">' +
		(c.foto_principal ? '<img src="' + esc(c.foto_principal) + '" alt="" loading="lazy">' : "") +
		'</div><div class="cp-item__body">' +
		'<p class="cp-item__title">' +
		esc(c.item_name || c.transcripcion || "Procesando…") +
		"</p>" +
		'<p class="cp-item__meta">' +
		esc(fechaRelativa(c.creado)) +
		(c.n_variantes ? " · " + c.n_variantes + " variantes" : "") +
		"</p><div>" +
		chip(k) +
		"</div>" +
		(c.estado === "Error" && c.mensaje_error_simple
			? '<p class="cp-item__note">' + esc(c.mensaje_error_simple) + "</p>"
			: "") +
		(c.estado === "Borrador" ? '<p class="cp-item__meta">No se terminó de enviar.</p>' : "") +
		"</div>" +
		(c.estado === "Error"
			? '<div class="cp-item__actions"><button type="button" class="cp-btn cp-btn--secondary" data-action="retry-error" data-id="' +
			  esc(c.name) +
			  '">' +
			  icon("repeat") +
			  "Reintentar</button></div>"
			: "") +
		"</div></li>"
	);
}

views.capturas = function () {
	const head =
		'<header class="cp-header"><button type="button" class="cp-header-btn" data-action="go" data-to="inicio">' +
		icon("left") +
		'<span>Inicio</span></button><span class="cp-header__title">Mis capturas</span></header>';
	let body =
		'<h1 class="cp-visually-hidden" tabindex="-1">Mis capturas</h1>' +
		'<div class="cp-row cp-refresh-row"><p class="cp-help">Aquí ves en qué va cada producto que enviaste.</p>' +
		'<button type="button" class="cp-btn cp-btn--secondary" data-action="refresh">' +
		icon("repeat") +
		"Actualizar</button></div>";
	const cap = state.capturas;
	const locales = uuidsLocales();
	const servidor = cap.lista.filter((c) => !(c.estado === "Borrador" && locales.has(c.client_uuid)));
	const vacia = cap.estado === "ok" && !servidor.length && !state.pendientes.length;
	if (state.pendientes.length) body += '<ul class="cp-list">' + state.pendientes.map(tarjetaPendiente).join("") + "</ul>";
	if (cap.estado === "cargando" || cap.estado === "idle") {
		body +=
			'<ul class="cp-list" aria-busy="true" aria-label="Cargando">' +
			[1, 2, 3]
				.map(
					() =>
						'<li class="cp-card"><div class="cp-item"><div class="cp-item__thumb cp-skeleton"></div><div class="cp-item__body"><div class="cp-skeleton" style="height:20px;width:80%"></div><div class="cp-skeleton" style="height:16px;width:40%"></div><div class="cp-skeleton" style="height:32px;width:110px;border-radius:999px"></div></div></div></li>'
				)
				.join("") +
			'</ul><p class="cp-visually-hidden" role="status">Cargando tus capturas</p>';
	} else if (cap.estado === "error") {
		body += alertBox(
			"danger",
			"No pudimos traer tus capturas",
			cap.tipoError === "sesion"
				? 'Tu sesión se cerró. <a href="/login?redirect-to=%2Fcaptura%23%2Fcapturas">Vuelve a entrar</a>.'
				: "Revisa que tengas internet y toca <strong>Intentar de nuevo</strong>.",
			'<div class="cp-alert__actions"><button type="button" class="cp-btn cp-btn--primary cp-btn--block" data-action="refresh">' +
				icon("repeat") +
				"Intentar de nuevo</button></div>"
		);
	} else if (vacia) {
		body +=
			'<div class="cp-empty">' +
			icon("list", "cp-icon--xl") +
			'<p class="cp-empty__title">Todavía no tienes capturas</p><p class="cp-empty__text">Cuando envíes un producto, aparecerá aquí.</p></div>' +
			'<button type="button" class="cp-btn cp-btn--primary cp-btn--xl cp-btn--block" data-action="new">' +
			icon("camera", "cp-icon--lg") +
			"Nuevo producto</button>";
	} else {
		body +=
			'<ul class="cp-list">' +
			servidor.map(tarjetaServidor).join("") +
			"</ul>" +
			'<details class="cp-card cp-legend"><summary class="cp-subtitle">' +
			icon("info") +
			'¿Qué significa cada color?</summary><ul class="cp-list">' +
			"<li>" +
			chip("procesando") +
			'<p class="cp-help">El sistema está leyendo tu producto. Espera unos minutos.</p></li>' +
			"<li>" +
			chip("revisar") +
			'<p class="cp-help">Ya está listo. Tu supervisor lo va a revisar.</p></li>' +
			"<li>" +
			chip("error") +
			'<p class="cp-help">Hubo un problema. Tu supervisor lo va a corregir; no tienes que hacer nada.</p></li>' +
			"<li>" +
			chip("aprobado") +
			'<p class="cp-help">Tu supervisor lo aprobó.</p></li>' +
			"<li>" +
			chip("pendiente") +
			'<p class="cp-help">Está guardado en tu celular. Se enviará cuando haya internet.</p></li></ul></details>';
	}
	const foot = vacia
		? ""
		: '<footer class="cp-footer"><button type="button" class="cp-btn cp-btn--primary cp-btn--lg cp-btn--block" data-action="new">' +
		  icon("camera") +
		  "Nuevo producto</button></footer>";
	return head + '<main class="cp-main">' + body + "</main>" + foot;
};

/* ================= Navegación del flujo ================= */
function nextStep() {
	const r = route();
	stopRecog();
	if (state.toReview) {
		state.toReview = false;
		go("resumen");
		return;
	}
	const i = ORDER.indexOf(r);
	if (i >= 0 && i < ORDER.length - 1) go(ORDER[i + 1]);
}

function prevStep() {
	const r = route();
	stopRecog();
	const i = ORDER.indexOf(r);
	const vi = VORDER.indexOf(r);
	if (r === "variantes" && state.draft.hasVariants === true && !state.draft.variants.length) {
		state.draft.hasVariants = null;
		persist();
		render();
		focusTitle();
		return;
	}
	if (r === "resumen") state.ui.send = "idle";
	if (vi > 0) {
		go(VORDER[vi - 1]);
		return;
	}
	if (i > 0) go(ORDER[i - 1]);
	else go("inicio");
}

async function startNew() {
	await descartarBorrador();
	state.draft = emptyDraft();
	persist();
	go("fotos");
}

function resume() {
	const paso = state.draft.lastStep || "fotos";
	if (VORDER.indexOf(paso) >= 0) go(state.vdraft ? paso : "variantes");
	else go(ORDER.indexOf(paso) >= 0 ? paso : "fotos");
}

// Al cerrar "¿Quieres salir?" sin salir, la cámara del escáner vuelve a abrirse si estaba abierta.
function pausarParaHoja() {
	const reabrir = escaner && escaner.activo ? scanScope : null;
	stopRecog();
	stopScan();
	render();
	return () => {
		if (reabrir && route() === (reabrir === "var" ? "var-codigo" : "codigo")) startScan(reabrir);
	};
}

/* ================= Eventos ================= */
document.addEventListener("click", (e) => {
	const el = e.target.closest("[data-action]");
	const root = document.getElementById("cp-root");
	if (!el || !root.contains(el)) return;
	const a = el.getAttribute("data-action");
	const id = el.getAttribute("data-id");
	const scope = el.getAttribute("data-scope");

	if (el.getAttribute("data-disabled") === "true") {
		const h = document.getElementById("cp-next-hint");
		if (h) {
			h.hidden = false;
			h.classList.remove("is-shake");
			void h.offsetWidth;
			h.classList.add("is-shake");
			announce(h.textContent);
		}
		return;
	}
	switch (a) {
		case "go":
			go(el.getAttribute("data-to"));
			break;
		case "new":
			if (tieneContenido(state.draft) && route() === "inicio") {
				confirmSheet({
					title: "Tienes un producto sin terminar",
					text: "Si empiezas uno nuevo, se borra el que dejaste a medias.",
					okLabel: "Empezar uno nuevo",
					cancelLabel: "Seguir con el anterior",
					danger: true,
					onOk: startNew,
					onCancel: resume,
				});
			} else startNew();
			break;
		case "resume":
			resume();
			break;
		case "next":
			nextStep();
			break;
		case "back":
			prevStep();
			break;
		case "noop":
			break;
		case "logout": {
			const sinEnviar = state.pendientes.length + (tieneContenido(state.draft) ? 1 : 0);
			confirmSheet({
				title: "¿Cerrar sesión?",
				text: sinEnviar
					? "Tienes productos sin enviar. Quedan guardados en este celular y vuelven a aparecer cuando entres con tu usuario."
					: "Otra persona podrá entrar con su usuario en este celular.",
				okLabel: "Cerrar sesión",
				cancelLabel: "Seguir aquí",
				onOk: async () => {
					await guardarYa();
					await cerrarSesion();
					window.location.href = "/login?redirect-to=/captura";
				},
			});
			break;
		}
		case "exit-back":
		case "exit": {
			const reanudar = pausarParaHoja();
			confirmSheet({
				title: "¿Quieres salir?",
				text: "Guardamos lo que hiciste en este celular. Puedes seguir después desde el inicio.",
				okLabel: "Salir",
				cancelLabel: "Seguir capturando",
				onOk: () => {
					guardarYa();
					go("inicio");
				},
				onCancel: reanudar,
			});
			break;
		}
		case "take-photo":
			tomarFoto(scope, false);
			break;
		case "gallery-photo":
			tomarFoto(scope, true);
			break;
		case "retake-photo":
			photosOf(scope).forEach(liberar);
			photosOf(scope).length = 0;
			persist();
			render();
			tomarFoto(scope, false);
			break;
		case "del-photo":
			delPhoto(scope, id);
			break;
		case "set-principal":
			setPrincipal(scope, id);
			break;
		case "mic-start":
			startMic(scope);
			break;
		case "mic-stop":
			finishMic();
			break;
		case "dict-type":
			stopRecog();
			setUi(scope + ":dict", "typing");
			setTimeout(() => {
				const t = document.getElementById("cp-dict-text");
				if (t) t.focus();
			}, 30);
			break;
		case "scan-again":
			startScan(scope);
			break;
		case "code-manual":
			openCodeSheet(scope);
			break;
		case "code-accept":
			aceptarCodigo(scope, id, (state.ui[scope + ":result"] || {}).origen);
			break;
		case "del-code": {
			const t = codeTarget(scope);
			t.codes = t.codes.filter((c) => c.codigo !== id);
			state.ui[scope + ":result"] = null;
			persist();
			render();
			announce("Código quitado");
			break;
		}
		case "toggle-sincodigo": {
			const t = codeTarget(scope);
			t.sinCodigo = !t.sinCodigo;
			if (t.sinCodigo) {
				stopScan();
				state.ui[scope + ":scan"] = "paused";
				state.ui[scope + ":result"] = null;
			}
			persist();
			render();
			break;
		}
		case "toggle-torch":
			if (escaner) {
				escaner.linterna(!state.torch).then((ok) => {
					if (!ok) return;
					state.torch = !state.torch;
					render();
					announce(state.torch ? "Luz prendida" : "Luz apagada");
				});
			}
			break;
		case "toggle-sound":
			state.sound = !state.sound;
			prefs.set("sound", state.sound);
			render();
			announce(state.sound ? "Sonido prendido" : "Sonido apagado");
			break;
		case "has-variants":
			if (id === "no") {
				const antes = state.draft.variants;
				const habia = state.draft.hasVariants === true && antes.length;
				state.draft.hasVariants = false;
				state.draft.variants = [];
				persist();
				go("resumen");
				if (habia) {
					setTimeout(
						() =>
							toast("Variantes quitadas", "Deshacer", () => {
								state.draft.hasVariants = true;
								state.draft.variants = antes;
								persist();
								go("variantes");
							}),
						50
					);
				}
			} else {
				state.draft.hasVariants = true;
				persist();
				render();
				focusTitle();
			}
			break;
		case "add-variant":
			state.vdraft = emptyVariant();
			["var:dict", "var:interim", "var:scan", "var:result", "var:camErr"].forEach((k) => delete state.ui[k]);
			persist();
			go("var-foto");
			break;
		case "del-variant": {
			const d = state.draft;
			const idx = d.variants.findIndex((v) => v.id === id);
			if (idx < 0) break;
			const removed = d.variants.splice(idx, 1)[0];
			persist();
			render();
			toast("Variante borrada", "Deshacer", () => {
				d.variants.splice(idx, 0, removed);
				persist();
				render();
			});
			break;
		}
		case "var-next": {
			stopRecog();
			const vi = VORDER.indexOf(route());
			go(VORDER[vi + 1]);
			break;
		}
		case "var-save":
			state.draft.variants.push(state.vdraft);
			state.vdraft = null;
			["var:dict", "var:interim", "var:scan", "var:result", "var:camErr"].forEach((k) => delete state.ui[k]);
			persist();
			go("variantes");
			setTimeout(() => toast("Variante guardada"), 50);
			break;
		case "var-exit": {
			const reanudar = pausarParaHoja();
			confirmSheet({
				title: "¿Dejar esta variante?",
				text: "Se borra lo que hiciste en esta variante. Las demás quedan guardadas.",
				okLabel: "Sí, dejarla",
				cancelLabel: "Seguir con la variante",
				danger: true,
				onOk: () => {
					if (state.vdraft) state.vdraft.photos.forEach(liberar);
					state.vdraft = null;
					persist();
					go("variantes");
				},
				onCancel: reanudar,
			});
			break;
		}
		case "edit-step":
			state.toReview = id !== "variantes";
			state.ui.send = "idle";
			go(id);
			break;
		case "send":
			send();
			break;
		case "send-later":
			sendLater();
			break;
		case "discard-product":
			confirmSheet({
				title: "¿Dejar este producto?",
				text: "Ya está registrado, no hace falta capturarlo. Se borran las fotos y lo que dictaste.",
				okLabel: "Sí, dejarlo",
				cancelLabel: "Seguir con el producto",
				danger: true,
				onOk: async () => {
					await descartarBorrador();
					go("inicio");
				},
			});
			break;
		case "refresh":
			cargarCapturas(false).then(() => {
				if (state.capturas.estado === "ok") toast("Lista actualizada");
			});
			break;
		case "retry-error":
			el.setAttribute("aria-disabled", "true");
			api
				.reintentarCaptura(id)
				.then(() => {
					toast("Se volvió a enviar");
					announce("Se volvió a enviar");
					return cargarCapturas(true);
				})
				.catch((err) => {
					el.removeAttribute("aria-disabled");
					toast(err.tipo === "red" ? "No hay internet. Intenta de nuevo." : err.message || "No se pudo reintentar.");
				});
			break;
		case "retry-pending":
			if (!navigator.onLine) {
				toast("Todavía no hay internet. Lo enviaremos automáticamente cuando vuelva.");
				break;
			}
			procesarCola().then((n) => {
				if (!n) toast("No se pudo enviar. Intenta de nuevo en un rato.");
			});
			break;
	}
});

document.addEventListener("input", (e) => {
	const el = e.target;
	if (el.getAttribute("data-input") !== "dict") return;
	const scope = el.getAttribute("data-scope");
	const t = dictTarget(scope);
	if (!t) return;
	t.dictado = el.value;
	t.metodo = "Texto";
	persist();
	// Habilitar "Siguiente" sin redibujar (no perder el cursor).
	const btn = document.querySelector(".cp-footer [data-disabled]");
	const off = el.value.trim().length < minDictado(scope);
	if (btn) {
		btn.setAttribute("data-disabled", off);
		btn.classList.toggle("is-disabled", off);
		if (off) btn.setAttribute("aria-disabled", "true");
		else btn.removeAttribute("aria-disabled");
		const h = document.getElementById("cp-next-hint");
		if (h) h.hidden = !off;
	}
});

window.addEventListener("popstate", () => {
	const r = parseHash();
	if (r !== curRoute) {
		curRoute = r;
		onRouteChange();
	}
});

window.addEventListener("online", () => {
	state.online = true;
	if (route() === "inicio") render();
	procesarCola();
});
window.addEventListener("offline", () => {
	state.online = false;
	if (route() === "inicio") render();
});

// La cámara se apaga si la app pasa a segundo plano y se vuelve a abrir al regresar (ui-spec 8.4).
document.addEventListener("visibilitychange", () => {
	if (document.visibilityState === "hidden") {
		stopRecog();
		if (escaner && escaner.activo) {
			stopScan();
			state.ui._reabrirEscaner = scanScope;
		}
		guardarYa();
	} else {
		const s = state.ui._reabrirEscaner;
		delete state.ui._reabrirEscaner;
		if (s && route() === (s === "var" ? "var-codigo" : "codigo")) startScan(s);
		else render();
		if (route() === "capturas") programarSondeo();
	}
});

/* ================= Render ================= */
let current = null;

function focusTitle() {
	const h = document.querySelector("#cp-root h1");
	if (h) h.focus({ preventScroll: true });
}

function render() {
	const r = route();
	const flow = ORDER.indexOf(r) >= 0;
	const vflow = VORDER.indexOf(r) >= 0;
	if ((flow || vflow) && !state.draft) {
		go("inicio");
		return;
	}
	if (vflow && !state.vdraft) {
		go("variantes");
		return;
	}
	if (flow || vflow) {
		if (state.draft.lastStep !== r) {
			state.draft.lastStep = r;
			persist();
		}
	}
	const view = views[r] || views.inicio;
	if (!views[r]) curRoute = "inicio";
	const ae = document.activeElement;
	let key = null;
	if (ae && ae.getAttribute && ae.getAttribute("data-action")) {
		key = ae.getAttribute("data-action") + "|" + (ae.getAttribute("data-id") || "");
	}
	const root = document.getElementById("cp-root");
	root.removeAttribute("aria-busy");
	root.innerHTML = view();
	if (curRoute !== current) {
		current = curRoute;
		window.scrollTo(0, 0);
		focusTitle();
		if (curRoute === "capturas") cargarCapturas(state.capturas.estado !== "idle");
	} else if (key) {
		const els = root.querySelectorAll("[data-action]");
		for (let i = 0; i < els.length; i++) {
			if (els[i].getAttribute("data-action") + "|" + (els[i].getAttribute("data-id") || "") === key) {
				els[i].focus({ preventScroll: true });
				break;
			}
		}
	}
	montarVideo();
	// Al entrar al paso de código sin códigos, la cámara se abre sola (ui-spec F1.4).
	const scope = r === "codigo" ? "main" : r === "var-codigo" ? "var" : null;
	if (scope && state.ui[scope + ":scan"] === undefined) {
		const t = codeTarget(scope);
		if (!t.codes.length && !t.sinCodigo) setTimeout(() => route() === r && startScan(scope), 0);
		else state.ui[scope + ":scan"] = "paused";
	}
}

function montarVideo() {
	const slot = document.querySelector("[data-video-slot]");
	if (!slot) {
		if (video.parentNode) video.parentNode.removeChild(video);
		return;
	}
	if (video.parentNode !== slot) slot.appendChild(video);
	if (video.srcObject && video.paused) video.play().catch(() => {});
}

/* ================= Inicio ================= */
async function init() {
	const [d, v] = await Promise.all([store.get(K("draft")), store.get(K("vdraft"))]);
	state.draft = d ? hidratar(d) : null;
	state.vdraft = d && v ? hidratar(v) : null;
	await cargarPendientes();
	state.camPermiso = await estadoPermiso("camera");
	try {
		const p = await navigator.permissions.query({ name: "camera" });
		p.onchange = () => (state.camPermiso = p.state);
	} catch (e) {
		/* sin Permissions API */
	}
	const r = parseHash();
	const necesitaBorrador = ORDER.indexOf(r) >= 0 || VORDER.indexOf(r) >= 0;
	if (!views[r] || (necesitaBorrador && !state.draft) || r === "exito") {
		curRoute = "inicio";
		try {
			history.replaceState({ r: "inicio" }, "", "#/inicio");
		} catch (e) {
			/* sin historial */
		}
	} else {
		curRoute = r;
	}
	render();
	if (curRoute !== "capturas") cargarCapturas(true);
	procesarCola();
}

init();
