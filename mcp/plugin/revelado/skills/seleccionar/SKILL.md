---
name: seleccionar
description: Selección (culling) de una sesión de fotos con Revelado - primero el filtro técnico local gratuito, luego Claude mira solo las finalistas en tamaño pequeño y marca pick/reject y estrellas. Usar cuando el usuario diga "selecciona las mejores", "haz el culling", "escoge N fotos", "quita las movidas", "pick the best shots".
---

# Seleccionar fotos con Revelado

Objetivo: llegar a la selección que pide el usuario gastando el mínimo de imágenes (cuota del usuario).

## Pasos

1. `engine_status` → `open_folder(ruta)` → `session_id`.
2. Filtro técnico local (gratis, sin nube): `auto_cull_technical(session_id, wait_seconds=300)`. Si sigue en
   marcha, `job_status(job_id, wait_seconds=120)` hasta `done`. Te devuelve el recuento keep/maybe/reject.
3. Candidatas: `list_photos(session_id, verdict="keep", burst_best_only=true)`; si hay pocas, añade
   `verdict="maybe"`. Las `reject` técnicas solo se revisan si el usuario lo pide.
4. Juicio de momento y expresión: `get_preview(photo_id, size=512)` de las candidatas, en tandas pequeñas
   (≤ 12 por tanda). Usa 1024 px solo para dudas finas (ojos, foco). Criterios: foco en los ojos, ojos abiertos,
   expresión, momento, composición, luz; entre fotos de una ráfaga quédate con una.
5. Marca: `set_rating(photo_id, flag="pick", rating=…)` para las elegidas y `flag="reject"` para las descartadas
   claras. Las valoraciones solo cambian el catálogo; no tocan archivos.
6. Resume: cuántas elegidas, por qué, y las dudosas para que el usuario decida. No borres nada nunca.

## Reglas

- Si el usuario pide "N fotos", entrega exactamente N picks (o explica por qué no hay suficientes buenas).
- No reveles ni exportes desde esta skill; para eso está `revelar-sesion`.
- No subas ni describas fotos fuera de esta conversación; no menciones nombres de personas que no te den.

English summary: free local technical cull → look only at finalists at 512 px in small batches → pick/reject +
stars in the catalog → report; never delete, develop or export here.
