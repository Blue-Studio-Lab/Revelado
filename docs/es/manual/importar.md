# Importar

*[English](../../en/manual/import.md)*

1. En **Biblioteca**, escribe o pega la ruta de una carpeta. El navegador no puede abrir el selector de carpetas del sistema.
2. Revelado busca en la carpeta **y en sus subcarpetas**, y empareja cada RAW con su JPG por nombre, aunque estén en `RAW/` y `JPG/` separadas.
3. Se saltan las carpetas que empiezan por `.` o `_` (carpetas derivadas o de descartes). La Biblioteca te dice cuáles, y puedes incluirlas.

Lee todos los RAW que entiende LibRaw: ARW, CR2, CR3, NEF, DNG, RAF, ORF, RW2 y otros. Una foto que solo tiene JPG se puede ver y seleccionar, pero para revelarla hace falta el RAW.

**Persistencia.** Al volver a abrir la misma carpeta se recuperan las estrellas, las marcas, la selección y los revelados. El catálogo se guarda en la carpeta de caché, nunca en tu carpeta de fotos.

**Privacidad.** Los números de serie se convierten en un hash en cuanto se leen. Nunca se piden el GPS ni los datos del propietario.
