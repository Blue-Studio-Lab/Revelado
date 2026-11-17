---
name: rescate
description: Modo rescate de Revelado para fotos problemáticas (subexpuestas, con ruido, aberración cromática o dominantes de color) - diagnostica, propone una receta prudente y la prueba en una muestra con antes/después en pocas iteraciones acotadas. Usar cuando el usuario diga "rescata esta foto", "está muy oscura", "tiene mucho ruido", "salva esta toma", "fix this underexposed shot".
---

# Rescate de fotos difíciles

## Pasos

1. Identifica la foto (`list_photos`, o el id que dé el usuario) y mírala: `get_preview(photo_id, size=768)`.
2. Diagnóstico: `diagnose(photo_id, kinds=["noise","ca","shading"])`. Añade `include_masks=true` solo si
   necesitas ver dónde está el problema. Lee también `get_photo_info` (ISO, perfil recomendado: `nr_level`,
   `ca`, `exposure_hint`, `log_profile`).
3. Propón al usuario un plan corto en palabras simples (qué está mal, qué vas a probar).
4. Itera como máximo 4 veces sobre UNA muestra:
   - `develop_sample(photo_id, recipe_patch={...})` con cambios pequeños: `exposure_ev` en pasos de ±0.3
     (rango −2..2), `skin_retouch`/`grain` más bajos en fotos muy ruidosas.
   - Compara antes/después. Para si deja de mejorar, si aparecen altas luces quemadas o si la piel pierde
     textura (aspecto plástico). Un rescate nunca debe empeorar el recorte de altas luces.
   - Campos que el motor marca en `ignored_by_engine` (p. ej. `nr`, `ca` distintos de `auto`) todavía no tienen
     efecto: no prometas lo que no hacen.
5. Cuando el usuario apruebe, guarda la receta con `patch_recipe(session_id, patch={...})` para aplicarla a
   fotos parecidas con `develop_batch`.

## Reglas

- Una sola muestra en curso; si recibes `E_BUSY`, espera con `job_status`.
- Sé honesto: si la foto no tiene arreglo (movida, foco fallado), dilo en vez de forzar ajustes.
- Exportar solo con confirmación explícita (ver `revelar-sesion`).

English summary: inspect → diagnose noise/CA/shading → plain-language plan → at most 4 bounded sample iterations
(never increase highlight clipping, keep skin texture) → save the approved patch for similar frames.
