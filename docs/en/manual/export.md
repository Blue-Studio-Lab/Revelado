# Export

*[Español](../../es/manual/exportar.md)*

Select photos and choose **Export**.

| Option | Values |
|---|---|
| Format | JPG (quality 97, 4:4:4) or TIFF 16-bit |
| Long edge | original size, or a maximum in pixels (Lanczos) |
| Naming | original file name, original + film, or a numbered sequence |
| Destination | a folder **outside** the source folder, under an allowed export location (see below) |

Guarantees:

- files are written atomically (`.part`, then rename), so there are no half-written files;
- an existing file is **never overwritten**: `_1`, `_2`… is added;
- EXIF is copied from the RAW, with orientation reset and **GPS removed**;
- photos not yet developed are developed first, through the Oven queue, unless you switch that off.

Allowed export locations: your home folder, the temp folder, shared folders and external or network disks
(macOS `/Volumes` and `/Users/Shared`; Linux `/media`, `/mnt`, `/run/media`, `/run/user/<uid>` (GVFS/SMB), `/srv`,
`/data`; Windows any drive and `\\server\share` paths, except the Windows and Program Files folders). The engine
cache and the source folder are always refused, and symbolic links are followed before checking. To export anywhere
else, list the folders in `REVELADO_EXPORT_ROOTS` (separated by `:` on macOS/Linux, `;` on Windows) in `engine/.env`
or the environment. Folders allowed for the MCP server with `REVELADO_EXPORT_DIRS` are accepted too.
