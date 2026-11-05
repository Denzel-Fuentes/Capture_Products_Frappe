# Copyright (c) 2026, Estacion Tradicion and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CapturaVariante(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		barcode: DF.Data | None
		barcode_type: DF.Literal["", "EAN-13", "EAN-8", "UPC-A"]
		foto: DF.AttachImage | None
		item: DF.Link | None
		item_code: DF.Data | None
		item_name: DF.Data | None
		transcripcion: DF.SmallText | None
		valor_1: DF.Data | None
		valor_2: DF.Data | None
		valor_3: DF.Data | None
		valor_corto_1: DF.Data | None
		valor_corto_2: DF.Data | None
		valor_corto_3: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	pass
