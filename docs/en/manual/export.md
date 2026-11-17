# Export

*[Español](../../es/manual/exportar.md)*

Select photos and choose **Export**.

| Option | Values |
|---|---|
| Format | JPG (quality 97, 4:4:4) or TIFF 16-bit |
| Long edge | original size, or a maximum in pixels (Lanczos) |
| Naming | original file name, original + film, or a numbered sequence |
| Destination | any folder **outside** the source folder |

Guarantees:

- files are written atomically (`.part`, then rename), so there are no half-written files;
- an existing file is **never overwritten**: `_1`, `_2`… is added;
- EXIF is copied from the RAW, with orientation reset and **GPS removed**;
- photos not yet developed are developed first, through the Oven queue, unless you switch that off.
