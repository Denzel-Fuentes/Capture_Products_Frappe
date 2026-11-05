// Lector de códigos de barras (ui-spec 8.4): BarcodeDetector nativo y, solo si no existe o no lee
// EAN-13, ZXing (archivo local de la app, cargado bajo demanda: nunca desde un CDN).

import { estadoPermiso } from "./util";

const FORMATOS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"];
const INTERVALO_MS = 150;
const LECTURAS_IGUALES = 2; // estabilidad: el mismo código 2 veces seguidas

let detectorPromise = null;
let zxingPromise = null;

function cargarScript(src) {
	if (!zxingPromise) {
		zxingPromise = new Promise((resolve, reject) => {
			const s = document.createElement("script");
			s.src = src;
			s.async = true;
			s.onload = () => (window.ZXingBrowser ? resolve(window.ZXingBrowser) : reject(new Error("ZXing")));
			s.onerror = () => {
				zxingPromise = null;
				reject(new Error("No se pudo cargar ZXing"));
			};
			document.head.appendChild(s);
		});
	}
	return zxingPromise;
}

async function detectorNativo() {
	if (!("BarcodeDetector" in window)) return null;
	try {
		const soportados = await window.BarcodeDetector.getSupportedFormats();
		if (soportados.indexOf("ean_13") === -1) return null;
		const d = new window.BarcodeDetector({ formats: FORMATOS.filter((f) => soportados.indexOf(f) !== -1) });
		return {
			tipo: "nativo",
			async leer(video) {
				const r = await d.detect(video);
				return r && r[0] ? { valor: r[0].rawValue, formato: r[0].format } : null;
			},
		};
	} catch (e) {
		return null;
	}
}

async function detectorZxing(url) {
	const Z = await cargarScript(url);
	const lector = new Z.BrowserMultiFormatReader(new Map());
	const F = Z.BarcodeFormat;
	lector.possibleFormats = [F.EAN_13, F.EAN_8, F.UPC_A, F.UPC_E, F.CODE_128];
	const canvas = document.createElement("canvas");
	const ctx = canvas.getContext("2d", { willReadFrequently: true });
	return {
		tipo: "zxing",
		async leer(video) {
			const w = video.videoWidth;
			const h = video.videoHeight;
			if (!w || !h) return null;
			const escala = Math.min(1, 960 / w);
			canvas.width = Math.round(w * escala);
			canvas.height = Math.round(h * escala);
			ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
			try {
				const r = lector.decodeFromCanvas(canvas);
				return { valor: r.getText(), formato: String(F[r.getBarcodeFormat()] || "").toLowerCase() };
			} catch (e) {
				return null; // NotFoundException: no hay código en este cuadro
			}
		},
	};
}

function obtenerDetector(zxingUrl) {
	if (!detectorPromise) {
		detectorPromise = detectorNativo()
			.then((d) => d || detectorZxing(zxingUrl))
			.catch((e) => {
				detectorPromise = null;
				throw e;
			});
	}
	return detectorPromise;
}

/**
 * crearEscaner({ video, zxingUrl, onCodigo(valor, formato), onError(tipo) })
 * tipo de error: "denied" | "nocam" | "error"
 */
export function crearEscaner(cb) {
	const video = cb.video;
	let stream = null;
	let track = null;
	let timer = null;
	let generacion = 0;
	let ultimo = null;
	let repeticiones = 0;

	function detener() {
		generacion++;
		clearTimeout(timer);
		timer = null;
		if (stream) stream.getTracks().forEach((t) => t.stop());
		stream = null;
		track = null;
		video.srcObject = null;
	}

	async function iniciar() {
		detener();
		const gen = generacion;
		ultimo = null;
		repeticiones = 0;
		if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return cb.onError("nocam");
		if ((await estadoPermiso("camera")) === "denied") return cb.onError("denied");
		let s;
		try {
			s = await navigator.mediaDevices.getUserMedia({
				video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
				audio: false,
			});
		} catch (e) {
			if (gen !== generacion) return;
			const n = e && e.name;
			if (n === "NotAllowedError" || n === "SecurityError") return cb.onError("denied");
			if (n === "NotFoundError" || n === "OverconstrainedError" || n === "NotReadableError") return cb.onError("nocam");
			return cb.onError("error");
		}
		if (gen !== generacion) {
			s.getTracks().forEach((t) => t.stop());
			return;
		}
		stream = s;
		track = s.getVideoTracks()[0] || null;
		video.srcObject = s;
		try {
			await video.play();
		} catch (e) {
			/* se reintenta al mover el <video> */
		}
		let detector;
		try {
			detector = await obtenerDetector(cb.zxingUrl);
		} catch (e) {
			if (gen === generacion) {
				detener();
				cb.onError("error");
			}
			return;
		}
		if (gen !== generacion) return;
		if (cb.onListo) cb.onListo({ linterna: tieneLinterna(), detector: detector.tipo });
		const ciclo = async () => {
			if (gen !== generacion) return;
			let r = null;
			if (video.readyState >= 2) {
				try {
					r = await detector.leer(video);
				} catch (e) {
					r = null;
				}
			}
			if (gen !== generacion) return;
			if (r && r.valor) {
				repeticiones = r.valor === ultimo ? repeticiones + 1 : 1;
				ultimo = r.valor;
				if (repeticiones >= LECTURAS_IGUALES) {
					detener();
					cb.onCodigo(r.valor, r.formato);
					return;
				}
			}
			timer = setTimeout(ciclo, INTERVALO_MS);
		};
		ciclo();
	}

	function tieneLinterna() {
		try {
			const caps = track && track.getCapabilities ? track.getCapabilities() : null;
			return !!(caps && caps.torch);
		} catch (e) {
			return false;
		}
	}

	async function linterna(prender) {
		if (!track) return false;
		try {
			await track.applyConstraints({ advanced: [{ torch: !!prender }] });
			return true;
		} catch (e) {
			return false;
		}
	}

	return {
		iniciar,
		detener,
		linterna,
		get activo() {
			return !!stream;
		},
	};
}
