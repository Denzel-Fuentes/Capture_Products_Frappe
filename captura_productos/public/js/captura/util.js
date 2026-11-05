// Utilidades de la pantalla /captura (sin dependencias).

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(s) {
	return String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

export function icon(name, cls) {
	return (
		'<svg class="cp-icon ' +
		(cls || "") +
		'" aria-hidden="true" focusable="false"><use href="#i-' +
		name +
		'"/></svg>'
	);
}

export function announce(msg) {
	const el = document.getElementById("cp-live");
	if (!el) return;
	el.textContent = "";
	setTimeout(() => {
		el.textContent = msg;
	}, 60);
}

export function toast(text, actionLabel, actionFn) {
	const region = document.getElementById("cp-toasts");
	region.innerHTML = "";
	const t = document.createElement("div");
	t.className = "cp-toast";
	t.innerHTML =
		'<span class="cp-toast__text">' +
		esc(text) +
		"</span>" +
		(actionLabel
			? '<button type="button" class="cp-toast__action">' + esc(actionLabel) + "</button>"
			: "");
	region.appendChild(t);
	if (actionLabel) {
		t.querySelector("button").addEventListener("click", () => {
			region.innerHTML = "";
			actionFn();
		});
	}
	setTimeout(() => t.remove(), actionLabel ? 6000 : 3000);
}

export function clearToasts() {
	const region = document.getElementById("cp-toasts");
	if (region) region.innerHTML = "";
}

let audioCtx = null;

// Lectura correcta: vibración + pitido corto (ui-spec 8.4); el sonido se puede apagar.
export function feedbackOk(sonido) {
	if (navigator.vibrate) {
		try {
			navigator.vibrate(80);
		} catch (e) {
			/* sin vibración */
		}
	}
	if (!sonido) return;
	try {
		const Ctx = window.AudioContext || window.webkitAudioContext;
		if (!Ctx) return;
		audioCtx = audioCtx || new Ctx();
		const o = audioCtx.createOscillator();
		const g = audioCtx.createGain();
		o.frequency.value = 1320;
		o.type = "sine";
		g.gain.value = 0.12;
		o.connect(g);
		g.connect(audioCtx.destination);
		o.start();
		o.stop(audioCtx.currentTime + 0.12);
	} catch (e) {
		/* sin audio */
	}
}

export function feedbackError() {
	if (navigator.vibrate) {
		try {
			navigator.vibrate([60, 60, 60]);
		} catch (e) {
			/* sin vibración */
		}
	}
}

export function vibrarExito() {
	if (navigator.vibrate) {
		try {
			navigator.vibrate([40, 60, 120]);
		} catch (e) {
			/* sin vibración */
		}
	}
}

export function uuid() {
	if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
	const b = new Uint8Array(16);
	crypto.getRandomValues(b);
	b[6] = (b[6] & 0x0f) | 0x40;
	b[8] = (b[8] & 0x3f) | 0x80;
	const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
	return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
}

// Preferencias pequeñas (sonido, etc.). localStorage puede fallar en modo privado.
export const prefs = {
	get(key, def) {
		try {
			const v = localStorage.getItem("cp." + key);
			return v === null ? def : JSON.parse(v);
		} catch (e) {
			return def;
		}
	},
	set(key, val) {
		try {
			localStorage.setItem("cp." + key, JSON.stringify(val));
		} catch (e) {
			/* sin almacenamiento */
		}
	},
};

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function parseFecha(valor) {
	if (!valor) return null;
	if (valor instanceof Date) return valor;
	// Frappe devuelve "2026-11-05 10:40:12.123456" (hora del sitio, sin zona).
	const m = String(valor).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
	if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
	const d = new Date(valor);
	return isNaN(d) ? null : d;
}

// «Hoy, 10:40», «Ayer, 17:20», luego «12 nov, 09:10» (ui-spec 8.6).
export function fechaRelativa(valor) {
	const d = parseFecha(valor);
	if (!d) return "";
	const hora = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
	const hoy = new Date();
	const inicioHoy = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate());
	const dias = Math.round((inicioHoy - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
	if (dias === 0) return "Hoy, " + hora;
	if (dias === 1) return "Ayer, " + hora;
	return d.getDate() + " " + MESES[d.getMonth()] + ", " + hora;
}

export function estaInstalada() {
	return (
		(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
		window.navigator.standalone === true
	);
}

export async function estadoPermiso(nombre) {
	try {
		if (!navigator.permissions || !navigator.permissions.query) return "unknown";
		const r = await navigator.permissions.query({ name: nombre });
		return r.state;
	} catch (e) {
		return "unknown";
	}
}
