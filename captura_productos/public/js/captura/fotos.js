// Fotos: reducción en el navegador antes de guardar/subir (ui-spec 8.2).
// createImageBitmap(from-image) corrige la rotación EXIF; volver a codificar en JPEG quita los
// metadatos (GPS incluido).

export const LADO_MAX = 1280;
const CALIDAD = 0.8;

function cargarComoImagen(file) {
	return new Promise((resolve, reject) => {
		const url = URL.createObjectURL(file);
		const img = new Image();
		img.onload = () => {
			URL.revokeObjectURL(url);
			resolve(img);
		};
		img.onerror = () => {
			URL.revokeObjectURL(url);
			reject(new Error("No se pudo leer la imagen"));
		};
		img.src = url;
	});
}

async function decodificar(file) {
	if (window.createImageBitmap) {
		try {
			return await createImageBitmap(file, { imageOrientation: "from-image" });
		} catch (e) {
			/* navegadores sin la opción: se usa <img>, que también respeta EXIF */
		}
	}
	return cargarComoImagen(file);
}

export async function comprimir(file) {
	const fuente = await decodificar(file);
	const ancho = fuente.width || fuente.naturalWidth;
	const alto = fuente.height || fuente.naturalHeight;
	if (!ancho || !alto) throw new Error("Imagen vacía");
	const escala = Math.min(1, LADO_MAX / Math.max(ancho, alto));
	const canvas = document.createElement("canvas");
	canvas.width = Math.round(ancho * escala);
	canvas.height = Math.round(alto * escala);
	const ctx = canvas.getContext("2d");
	ctx.fillStyle = "#fff"; // PNG con transparencia: fondo blanco en el JPEG
	ctx.fillRect(0, 0, canvas.width, canvas.height);
	ctx.drawImage(fuente, 0, 0, canvas.width, canvas.height);
	if (fuente.close) fuente.close();
	const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", CALIDAD));
	if (!blob) throw new Error("No se pudo comprimir la imagen");
	return blob;
}

// URL de vista previa de cada foto (Blob en memoria o archivo ya subido).
const urls = new Map();

export function srcDe(foto) {
	if (!foto) return "";
	if (foto.blob) {
		let u = urls.get(foto.id);
		if (!u || u.blob !== foto.blob) {
			if (u) URL.revokeObjectURL(u.url);
			u = { blob: foto.blob, url: URL.createObjectURL(foto.blob) };
			urls.set(foto.id, u);
		}
		return u.url;
	}
	if (foto.preview) return foto.preview;
	return foto.url || "";
}

export function liberar(foto) {
	const u = foto && urls.get(foto.id);
	if (u) {
		URL.revokeObjectURL(u.url);
		urls.delete(foto.id);
	}
}
