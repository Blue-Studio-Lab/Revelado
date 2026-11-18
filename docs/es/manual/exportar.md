# Exportar

*[English](../../en/manual/export.md)*

Elige las fotos y pulsa **Exportar**. Las opciones son:

- **Formato:** JPG (calidad 97, 4:4:4) o TIFF de 16 bits.
- **Lado largo:** tamaño original o un máximo en píxeles.
- **Nombres:** el original, el original con la película, o una secuencia numerada.
- **Destino:** una carpeta **fuera** de la de originales, en un lugar de exportación permitido (ver abajo).

Garantías:

- **escritura atómica:** nunca quedan archivos a medias;
- **nunca sobrescribe:** si el archivo ya existe, añade `_1`, `_2`…;
- **EXIF:** se copia del RAW **sin GPS**;
- **revelado previo:** las fotos sin revelar se revelan antes, por la cola del Horno.

Lugares de exportación permitidos: tu carpeta personal, la carpeta temporal, las carpetas compartidas y los discos
externos o de red (macOS `/Volumes` y `/Users/Shared`; Linux `/media`, `/mnt`, `/run/media`, `/run/user/<uid>`
(GVFS/SMB), `/srv`, `/data`; Windows cualquier unidad y rutas `\\servidor\recurso`, salvo las carpetas de Windows y
Archivos de programa). La caché del motor y la carpeta de originales se rechazan siempre, y los enlaces simbólicos se
resuelven antes de comprobar. Para exportar a otro sitio, añade las carpetas a `REVELADO_EXPORT_ROOTS` (separadas por
`:` en macOS/Linux y `;` en Windows) en `engine/.env` o en el entorno. También se aceptan las carpetas permitidas al
servidor MCP con `REVELADO_EXPORT_DIRS`.
