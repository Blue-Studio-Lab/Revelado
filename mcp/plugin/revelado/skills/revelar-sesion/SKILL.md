---
name: revelar-sesion
description: Revela (develop) una sesión de fotos RAW con Revelado - abre la carpeta, revela UNA muestra con antes/después, ajusta la receta, revela el lote y exporta solo con confirmación. Usar cuando el usuario diga "revela la sesión", "revela estas fotos", "aplica la receta a todas", "develop this shoot", "export the selects".
---

# Revelar una sesión con Revelado

Herramientas: servidor MCP `revelado`. Los originales nunca se modifican; todo revelado va a la caché del motor
hasta que se exporta.

## Pasos

1. `engine_status`. Si `develop_available` es false, explica el motivo (`develop_unavailable_reason`) y para.
   Si falta la carpeta en `allowed_dirs`, dile al usuario cómo añadirla (README del MCP); no intentes rodeos.
2. `open_folder` con la ruta que dio el usuario → `session_id`. Si la carpeta ya estaba abierta se restauran
   valoraciones y revelados.
3. Decide QUÉ revelar: por defecto las fotos con bandera `pick` o `min_rating` que el usuario indique
   (`list_photos`). Si no hay selección, pregunta o usa la skill `seleccionar` primero.
4. Receta:
   - `get_recipe(session_id)`. La receta por defecto del motor puede traer un look de película.
   - **No apliques looks que el usuario no pidió.** Si pidió un resultado neutro/sin look, haz
     `patch_recipe` con `film_strength: 0, grain: 0, skin_retouch: 0, blemishes: 0` antes de revelar.
   - Solo películas con `status: trained` en `list_films`.
5. **Muestra primero, siempre**: `develop_sample` sobre una foto representativa (buena luz, piel visible).
   Mira el antes/después. Ajusta con `patch_recipe` (cambios pequeños: `exposure_ev` ±0.3) y repite como mucho
   3 veces. Enseña la muestra al usuario y pide su visto bueno.
6. `develop_batch` con los ids aprobados (mismo `session_id`). Sigue con `job_status(job_id, wait_seconds=120)`
   hasta `done`. Un trabajo pesado a la vez: si recibes `E_BUSY`, espera; no lances otro.
7. Revisa 2-3 resultados con `get_preview(source="developed")`.
8. Exportar SOLO si el usuario lo pide: confirma con él fotos, carpeta destino (dentro de las carpetas de
   exportación permitidas), formato y tamaño; entonces `export(..., confirm=true)`. Nunca exportes dentro de la
   carpeta de originales.

## Reglas

- Imágenes pequeñas (≤1024 px) y pocas: cuestan contexto y cuota del usuario.
- Errores del motor (`[E_...]`): explícalos en palabras simples; no reintentes en bucle.
- Nunca pidas ni uses claves de API; esta vía usa la sesión de Claude del propio usuario.

English summary: open folder → choose photos → neutral recipe unless a look was requested → develop ONE sample
and iterate → batch → spot-check → export only after explicit user confirmation.
