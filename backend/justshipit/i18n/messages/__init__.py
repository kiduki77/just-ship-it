"""Message catalogs, one module per area. Each module holds `MESSAGES: dict[str, Entry]` whose keys start with the
module's name; `justshipit.i18n.catalog()` finds every module here, so adding an area touches no other file."""
