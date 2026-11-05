// Códigos de barras: mismas reglas que captura_productos/validation.py (analizar_barcode).

const REGEX_BARCODE = /^[A-Z0-9-]{4,20}$/;
const TIPOS_GTIN = { 8: "EAN-8", 12: "UPC-A", 13: "EAN-13" };

// Dígito verificador GS1 (EAN-8, UPC-A, EAN-13, GTIN-14) de los dígitos sin el verificador.
export function checkDigit(body) {
	let sum = 0;
	const n = body.length;
	for (let i = 0; i < n; i++) sum += +body.charAt(n - 1 - i) * (i % 2 === 0 ? 3 : 1);
	return (10 - (sum % 10)) % 10;
}

export function isValidGtin(code) {
	if (!/^\d+$/.test(code)) return false;
	if ([8, 12, 13, 14].indexOf(code.length) === -1) return false;
	return checkDigit(code.slice(0, -1)) === +code.slice(-1);
}

export function normalizar(code) {
	return String(code || "")
		.replace(/\s+/g, "")
		.toUpperCase();
}

// UPC-E (8 dígitos: sistema + 6 + verificador) → UPC-A de 12 dígitos.
export function upcEtoA(upce) {
	if (!/^[01]\d{7}$/.test(upce)) return upce;
	const ns = upce[0];
	const x = upce.slice(1, 7);
	const check = upce[7];
	const last = +x[5];
	let cuerpo;
	if (last <= 2) cuerpo = x[0] + x[1] + x[5] + "0000" + x[2] + x[3] + x[4];
	else if (last === 3) cuerpo = x[0] + x[1] + x[2] + "00000" + x[3] + x[4];
	else if (last === 4) cuerpo = x[0] + x[1] + x[2] + x[3] + "00000" + x[4];
	else cuerpo = x[0] + x[1] + x[2] + x[3] + x[4] + "0000" + x[5];
	return ns + cuerpo + check;
}

// Un UPC-A (12) y el mismo código como EAN-13 con 0 adelante son el mismo producto.
export function clave(code) {
	const c = normalizar(code);
	return /^\d{12}$/.test(c) ? "0" + c : c;
}

/**
 * { codigo, tipo, valido, aviso }
 * - inválido: formato fuera de [A-Z0-9-]{4,20} o GTIN de 8/12/13 dígitos con verificador mal.
 * - válido con aviso: otro largo (ej. código interno 130885): el servidor lo guarda sin tipo.
 */
export function analizar(code) {
	const codigo = normalizar(code);
	if (!REGEX_BARCODE.test(codigo)) return { codigo, tipo: "", valido: false, aviso: false };
	if (/^\d+$/.test(codigo) && TIPOS_GTIN[codigo.length]) {
		const ok = isValidGtin(codigo);
		return { codigo, tipo: ok ? TIPOS_GTIN[codigo.length] : "", valido: ok, aviso: false };
	}
	return { codigo, tipo: "", valido: true, aviso: true };
}

// Hoja M2 «Escribe el código»: devuelve el mensaje de error o null. Acepta lo mismo que el servidor
// (HU-04): un código interno de otro largo (ej. 130885) se guarda sin tipo; solo los de 8, 12 o 13
// dígitos tienen que pasar el dígito verificador.
export function errorManual(v) {
	const codigo = normalizar(v);
	if (!codigo) return "Escribe los números del código.";
	if (codigo.length < 4) {
		return "El código tiene " + codigo.length + " números. Debe tener al menos 4.";
	}
	if (codigo.length > 20) return "El código es demasiado largo: como máximo 20 números.";
	if (!analizar(codigo).valido) {
		return "Estos números no forman un código válido. Revisa número por número.";
	}
	return null;
}

export function formatCode(c) {
	if (/^\d{13}$/.test(c)) return c.charAt(0) + " " + c.slice(1, 7) + " " + c.slice(7);
	if (/^\d{8}$/.test(c)) return c.slice(0, 4) + " " + c.slice(4);
	return c;
}
