"""Compatibilidad: el extractor vive en `captura_productos.ai.extractor` (y el prompt en `ai.prompts`)."""

from captura_productos.ai.extractor import encolar_procesamiento, procesar_captura

__all__ = ["encolar_procesamiento", "procesar_captura"]
