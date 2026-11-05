// Borrador en el celular (ui-spec 8.2 y 8.5): IndexedDB guarda los Blob de las fotos.
// Si IndexedDB no está disponible (modo privado, almacenamiento bloqueado) se usa memoria: la app
// funciona igual, pero el borrador no sobrevive a recargar la página.

const DB_NAME = "captura_productos";
const STORE = "kv";
const memoria = new Map();
let dbPromise = null;
let persistente = true;

function abrir() {
	if (!dbPromise) {
		dbPromise = new Promise((resolve, reject) => {
			try {
				const req = indexedDB.open(DB_NAME, 1);
				req.onupgradeneeded = () => req.result.createObjectStore(STORE);
				req.onsuccess = () => resolve(req.result);
				req.onerror = () => reject(req.error);
				req.onblocked = () => reject(new Error("IndexedDB bloqueada"));
			} catch (e) {
				reject(e);
			}
		}).catch(() => {
			persistente = false;
			return null;
		});
	}
	return dbPromise;
}

function pedir(db, modo, fn) {
	return new Promise((resolve, reject) => {
		const tx = db.transaction(STORE, modo);
		const req = fn(tx.objectStore(STORE));
		tx.oncomplete = () => resolve(req && req.result);
		tx.onerror = () => reject(tx.error);
		tx.onabort = () => reject(tx.error);
	});
}

export function esPersistente() {
	return persistente;
}

export async function get(key) {
	const db = await abrir();
	if (!db) return memoria.get(key);
	try {
		return await pedir(db, "readonly", (s) => s.get(key));
	} catch (e) {
		return memoria.get(key);
	}
}

export async function set(key, value) {
	memoria.set(key, value);
	const db = await abrir();
	if (!db) return false;
	try {
		await pedir(db, "readwrite", (s) => s.put(value, key));
		return true;
	} catch (e) {
		return false;
	}
}

export async function del(key) {
	memoria.delete(key);
	const db = await abrir();
	if (!db) return;
	try {
		await pedir(db, "readwrite", (s) => s.delete(key));
	} catch (e) {
		/* nada que borrar */
	}
}

export async function keys(prefix) {
	const db = await abrir();
	let todas = Array.from(memoria.keys());
	if (db) {
		try {
			todas = (await pedir(db, "readonly", (s) => s.getAllKeys())) || [];
		} catch (e) {
			/* se usa memoria */
		}
	}
	return todas.filter((k) => typeof k === "string" && k.indexOf(prefix) === 0);
}
