// Llamadas a captura_productos.api con la sesión de Frappe (cookie) y su token CSRF.

const BASE = "/api/method/captura_productos.api.";
let csrfToken = "";

export function setCsrf(token) {
	csrfToken = token || "";
}

/**
 * tipo:
 * - "red": no hubo respuesta (sin internet, servidor caído, tiempo agotado). Reintentar luego.
 * - "sesion": la sesión se cerró; hay que volver a entrar.
 * - "limite": demasiados intentos por minuto.
 * - "validacion" | "permiso" | "servidor": respuesta de error del servidor.
 */
export class ApiError extends Error {
	constructor(mensaje, tipo, status) {
		super(mensaje);
		this.tipo = tipo;
		this.status = status || 0;
	}
}

// DOMParser y no innerHTML: un documento de DOMParser no ejecuta scripts ni carga imágenes, así un
// mensaje del servidor con <img onerror=…> no corre nada.
function textoPlano(html) {
	const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
	return ((doc.body && doc.body.textContent) || "").trim();
}

function mensajeDe(data) {
	try {
		if (data && data._server_messages) {
			const msgs = JSON.parse(data._server_messages).map((m) => {
				try {
					return JSON.parse(m).message;
				} catch (e) {
					return m;
				}
			});
			return textoPlano(msgs.join(" "));
		}
	} catch (e) {
		/* formato inesperado */
	}
	// `data.exception` es el texto técnico de la excepción (clase, módulo, método): no se muestra.
	return "";
}

async function sesionActiva() {
	try {
		const r = await fetch("/api/method/frappe.auth.get_logged_user", {
			credentials: "same-origin",
			headers: { Accept: "application/json" },
		});
		if (!r.ok) return false;
		const d = await r.json();
		return !!d.message && d.message !== "Guest";
	} catch (e) {
		return true; // sin red: no se puede saber; se trata como error de red
	}
}

export async function llamar(metodo, args, opciones) {
	const { get = false, formData = null, timeoutMs = 30000 } = opciones || {};
	let url = BASE + metodo;
	const init = {
		method: get ? "GET" : "POST",
		credentials: "same-origin",
		headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrfToken },
	};
	if (get) {
		const qs = new URLSearchParams(args || {}).toString();
		if (qs) url += "?" + qs;
	} else if (formData) {
		init.body = formData;
	} else {
		init.headers["Content-Type"] = "application/json";
		init.body = JSON.stringify(args || {});
	}

	const control = window.AbortController ? new AbortController() : null;
	if (control) init.signal = control.signal;
	const reloj = control ? setTimeout(() => control.abort(), timeoutMs) : null;

	let res;
	try {
		res = await fetch(url, init);
	} catch (e) {
		throw new ApiError("No hay conexión con el servidor.", "red");
	} finally {
		if (reloj) clearTimeout(reloj);
	}

	let data = null;
	try {
		data = await res.json();
	} catch (e) {
		data = null;
	}
	if (res.ok) return data ? data.message : null;

	const excType = (data && data.exc_type) || "";
	const mensaje = mensajeDe(data);
	if (res.status === 401 || excType === "AuthenticationError" || excType === "CSRFTokenError") {
		throw new ApiError(mensaje || "La sesión se cerró.", "sesion", res.status);
	}
	if (res.status === 429 || excType === "RateLimitExceededError") {
		throw new ApiError(mensaje || "Demasiados intentos seguidos.", "limite", res.status);
	}
	if (res.status === 403) {
		// Un método sin allow_guest llamado con la sesión vencida también responde 403.
		if (!(await sesionActiva())) throw new ApiError("La sesión se cerró.", "sesion", 403);
		throw new ApiError(mensaje || "No tienes permiso.", "permiso", 403);
	}
	if (res.status === 502 || res.status === 503 || res.status === 504) {
		// 502/503/504 del proxy: el servidor no está disponible, se reintenta como un corte de red.
		throw new ApiError("El servidor no responde.", "red", res.status);
	}
	if (res.status === 417 || excType === "ValidationError") {
		throw new ApiError(mensaje || "Datos no válidos.", "validacion", res.status);
	}
	// Solo mensajes pensados para el usuario (frappe.throw); si no hay, uno genérico.
	throw new ApiError(
		mensaje || "Hubo un problema en el servidor. Intenta de nuevo en un rato.",
		"servidor",
		res.status
	);
}

// Cierra la sesión (celulares compartidos). `logout` de Frappe acepta POST con el token CSRF.
export async function cerrarSesion() {
	try {
		await fetch("/api/method/logout", {
			method: "POST",
			credentials: "same-origin",
			headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrfToken },
		});
	} catch (e) {
		/* sin red: igual se manda al login; la sesión vence sola */
	}
}

export const api = {
	misCapturas: (limit) => llamar("mis_capturas", { limit: limit || 50 }, { get: true, timeoutMs: 15000 }),
	verificarCodigo: (codigo, captura) =>
		llamar("verificar_codigo", captura ? { codigo, captura } : { codigo }, { get: true, timeoutMs: 5000 }),
	crearCaptura: (clientUuid) => llamar("crear_captura", { client_uuid: clientUuid }),
	subirFoto: (captura, blob, nombre) => {
		const fd = new FormData();
		fd.append("captura", captura);
		fd.append("file", blob, nombre || "foto.jpg");
		return llamar("subir_foto", null, { formData: fd, timeoutMs: 60000 });
	},
	enviarCaptura: (captura, datos) => llamar("enviar_captura", { captura, datos }),
	reintentarCaptura: (captura) => llamar("reintentar_captura", { captura }),
};
