# Cuarto oscuro

*[English](../../en/manual/develop.md)*

Aquí decides el aspecto **antes** de revelar toda la selección.

1. **Elige película.** Por defecto es **Neutro (sin película)**. Un aspecto *inspirado en Ektar 100* (id interno `kodak_ektar_100`, receta `editorial_ektar`) es el primer modelo físico: una emulación aproximada, sin relación ni aval de Kodak (ver [el modelo de película](../../technical/film-model.md#provenance-of-the-ektar-inspired-profile)). Las demás aparecerán en **Películas** cuando añadas tus escaneos; el entrenamiento llega en la v0.2.
2. **Elige una foto de muestra**: una foto típica de la sesión, con caras si hay personas.
3. **Revela 1 muestra.** Es un revelado real a resolución completa (unos 45 s). El resultado es exactamente lo que dará el lote.
4. **Compara** el antes y el después: dividido, lado a lado o alternando.
5. **Ajusta la receta** y revela otra vez. Si la receta cambia, la muestra se marca como desactualizada.

Controles de la receta:

- **Intensidad** (0–1);
- **Exposición** (−2 a +2 EV, sobre la automática);
- **Grano** (0–1,5; siempre es el último paso);
- **Retoque de piel** (0–100);
- **Granitos** (0–100);
- **Cabello** (apagado, suave, medio o fuerte; experimental y solo en macOS);
- **Acento del sujeto** (0–100);
- **Encuadre** (apagado, sugerir o aplicar);
- **Nitidez** (0–2);
- **Ruido** y **aberración cromática**: automáticos en la v0.1.

La receta se guarda con cada foto revelada (ver [Recetas y CLI](recetas-cli.md)).
