# Import

*[Español](../../es/manual/importar.md)*

1. Go to **Library** and type or paste the path of a folder. A browser cannot open the system folder picker, so you enter the path by hand.
2. Revelado scans the folder **and its subfolders**. It pairs each RAW with its JPG by file name, even when they live in separate `RAW/` and `JPG/` folders.
3. Folders whose name starts with `.` or `_` are skipped (derived folders, rejects). The Library tells you which were skipped; you can include them.

Supported RAW formats are everything LibRaw reads: ARW, CR2, CR3, NEF, DNG, RAF, ORF, RW2, PEF and more. A photo with only a JPG can be viewed and culled, but developing needs the RAW.

**Persistence.** Reopening the same folder restores your ratings, flags, cull results and developed outputs. The catalog is a SQLite file in the cache folder, never in your photo folder.

**Privacy.** Body and lens serial numbers are hashed as soon as they are read. GPS and owner tags are never requested.
