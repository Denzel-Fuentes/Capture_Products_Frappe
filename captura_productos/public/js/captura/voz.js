// Dictado con Web Speech API (ui-spec 8.3). Chrome Android manda el audio a Google: necesita internet.

import { estadoPermiso } from "./util";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const IDIOMAS_RESPALDO = ["es-BO", "es-419", "es-ES"];
const TOPE_MS = 20000;

// Primero el idioma de Configuración Captura (`idioma_voz`, llega en el boot de /captura); los demás
// quedan de respaldo si el navegador responde "language-not-supported".
let IDIOMAS = IDIOMAS_RESPALDO.slice();

export function configurarIdioma(idioma) {
	if (!idioma || !/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(idioma)) return;
	IDIOMAS = [idioma].concat(IDIOMAS_RESPALDO.filter((i) => i !== idioma));
}

export const soportaVoz = !!SR;

// Android repite a veces todo lo dicho en cada resultado final: si el nuevo empieza con lo
// acumulado, lo reemplaza en vez de sumarse.
function unir(acumulado, nuevo) {
	const a = acumulado.trim();
	const n = nuevo.trim();
	if (!a) return n;
	if (!n) return a;
	if (n.toLowerCase().indexOf(a.toLowerCase()) === 0) return n;
	if (a.toLowerCase().endsWith(n.toLowerCase())) return a;
	return a + " " + n;
}

/**
 * crearDictado({ onTexto(final, parcial), onFin(final), onError(tipo) })
 * tipo de error: "denied" | "nomic" | "nohear" | "offline" | "nosupport" | "error"
 */
export function crearDictado(cb) {
	let rec = null;
	let tope = null;
	let final = "";
	let cerrado = true;

	function limpiar() {
		clearTimeout(tope);
		tope = null;
		if (rec) {
			rec.onresult = rec.onerror = rec.onend = null;
		}
		rec = null;
		cerrado = true;
	}

	function arrancar(idx) {
		const r = new SR();
		rec = r;
		r.lang = IDIOMAS[idx];
		r.interimResults = true;
		r.continuous = true;
		r.maxAlternatives = 1;
		let error = null;
		let otroIdioma = false;

		r.onresult = (ev) => {
			let acumulado = "";
			let parcial = "";
			for (let i = 0; i < ev.results.length; i++) {
				const t = ev.results[i][0] ? ev.results[i][0].transcript : "";
				if (ev.results[i].isFinal) acumulado = unir(acumulado, t);
				else parcial += t;
			}
			final = acumulado;
			cb.onTexto(final, parcial.trim());
		};
		r.onerror = (ev) => {
			const e = ev.error;
			if (e === "language-not-supported" && idx < IDIOMAS.length - 1) {
				otroIdioma = true;
				return;
			}
			if (e === "aborted") return;
			if (e === "not-allowed" || e === "service-not-allowed") error = "denied";
			else if (e === "audio-capture") error = "nomic";
			else if (e === "no-speech") error = "nohear";
			else if (e === "network") error = "offline";
			else if (e === "language-not-supported") error = "nosupport";
			else error = "error";
		};
		r.onend = () => {
			if (rec !== r) return;
			if (otroIdioma) {
				arrancar(idx + 1);
				return;
			}
			const texto = final.trim();
			limpiar();
			// Con texto parcial (ej. se cortó la red) se conserva lo dicho (HU-05).
			if (error && !(texto && (error === "offline" || error === "nohear"))) cb.onError(error, texto);
			else if (!texto) cb.onError("nohear", "");
			else cb.onFin(texto);
		};
		try {
			r.start();
		} catch (e) {
			limpiar();
			cb.onError("error", "");
		}
	}

	return {
		async iniciar() {
			this.abortar();
			if (!SR) return cb.onError("nosupport", "");
			if (!navigator.onLine) return cb.onError("offline", "");
			if ((await estadoPermiso("microphone")) === "denied") return cb.onError("denied", "");
			final = "";
			cerrado = false;
			arrancar(0);
			tope = setTimeout(() => this.detener(), TOPE_MS);
		},
		detener() {
			clearTimeout(tope);
			if (rec) {
				try {
					rec.stop();
				} catch (e) {
					/* ya detenido */
				}
			}
		},
		abortar() {
			if (!rec) return;
			const r = rec;
			limpiar();
			try {
				r.abort();
			} catch (e) {
				/* ya detenido */
			}
		},
		get activo() {
			return !cerrado;
		},
	};
}
