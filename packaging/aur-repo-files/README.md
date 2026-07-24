# Archivos para el repo de AUR

Estos dos archivos (`LICENSE`, `REUSE.toml`) **no van en el repo de GitHub del proyecto** —
son para copiarlos a la raíz del repo git específico de AUR (`ucs-git`) junto con
`PKGBUILD` y `.SRCINFO`, cuando lo crees.

Son la licencia de *los archivos de empaquetado en sí* (el PKGBUILD), no la licencia de
la aplicación (que sigue siendo GPL-2.0, ver `/LICENSE` en la raíz del proyecto). Arch
Linux pide 0BSD específicamente para esto (RFC40) — ver el paso 5 de las instrucciones
de submit.
