# Visor y máscaras de diagnóstico

*[English](../../en/manual/viewer.md)*

Abre una foto desde la cuadrícula (Enter). Verás el JPG de cámara o la previa incluida en el RAW, y **recortes 1:1** de la imagen a resolución completa, para juzgar el enfoque al tamaño real.

**Fotos en log.** Las fotos en S-Log3 se ven planas. El visor aplica una transformación **técnica** S-Log3 → Rec.709 para poder juzgarlas. No es un look, y se puede apagar.

## Máscaras

| Máscara | Qué muestra | Cómo leerla |
|---|---|---|
| **Ruido** | nivel de ruido en zonas lisas (mapa cividis); las texturas salen en gris | oscuro = limpio, claro = ruidoso. Incluye la mediana en sombras, medios y altas, y un veredicto: bajo, moderado o alto. |
| **Aberración cromática** | CA lateral por zona (desalineación R/B en píxeles del sensor) y píxeles con franja morada o verde | morado rojizo = franjas moradas, verde azulado = franjas verdes |
| **Dominantes de color** | cambios lentos de color en el encuadre (esquinas magenta o verdes) | experimental |

Los colores son aptos para daltónicos. Las máscaras se calculan desde el RAW a media resolución, antes de cualquier reducción de ruido de la cámara. Los algoritmos se explican en la documentación técnica: [ruido](../../technical/noise-reduction.md) y [aberración cromática](../../technical/chromatic-aberration.md).
