// Envío de un producto (ui-spec 8.5): crear (idempotente por client_uuid) → subir cada foto que
// falte → enviar con todos los datos. Cada paso guarda su avance en el celular, así un reintento
// sigue donde quedó y nunca crea una captura duplicada.

import { api } from "./api";

export function todasLasFotos(d) {
	const fotos = d.photos.slice();
	if (d.hasVariants) d.variants.forEach((v) => v.photos.forEach((p) => fotos.push(p)));
	return fotos;
}

export function datosDe(d) {
	const conVariantes = !!d.hasVariants && d.variants.length > 0;
	return {
		transcripcion: (d.dictado || "").trim(),
		metodo_entrada: d.metodo || (d.dictado ? "Texto" : ""),
		fotos: d.photos.map((p) => ({ file_url: p.url, es_principal: p.principal ? 1 : 0 })),
		codigos: d.codes.map((c) => ({ barcode: c.codigo, origen: c.origen })),
		sin_codigo: d.sinCodigo && !d.codes.length ? 1 : 0,
		tiene_variantes: conVariantes ? 1 : 0,
		variantes: conVariantes
			? d.variants.map((v) => ({
					transcripcion: (v.dictado || "").trim(),
					foto: v.photos[0] ? v.photos[0].url : null,
					barcode: v.codes[0] ? v.codes[0].codigo : null,
			  }))
			: [],
	};
}

/**
 * Envía el borrador `d`. `guardar()` persiste el avance; `progreso(i, n)` informa la foto que sube.
 * Devuelve { name, estado }. Lanza ApiError si algo falla (el borrador queda en el celular).
 */
export async function enviarBorrador(d, guardar, progreso) {
	const creada = await api.crearCaptura(d.uuid);
	d.serverName = creada.name;
	await guardar();
	if (creada.estado !== "Borrador") return creada; // ya se había enviado

	const fotos = todasLasFotos(d);
	for (let i = 0; i < fotos.length; i++) {
		const p = fotos[i];
		if (progreso) progreso(i + 1, fotos.length);
		if (p.url) continue;
		if (!p.blob) throw new Error("Falta una foto");
		const r = await api.subirFoto(d.serverName, p.blob, "foto-" + (i + 1) + ".jpg");
		p.url = r.file_url;
		await guardar();
	}
	if (progreso) progreso(0, fotos.length);
	return api.enviarCaptura(d.serverName, datosDe(d));
}
