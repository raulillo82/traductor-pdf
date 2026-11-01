# Glosario

Fichero: `glosario_ganchillo_es.txt` (el de la imagen). El worker usa `~/.config/traductor/glosario.txt`
si existe, y entonces se edita **sin reconstruir nada**: el cambio vale para la siguiente traducción.

## Formato

```
origen = destino        # una por línea; # para comentarios
```

Hay tres tipos de entrada:

| Tipo | Cómo se escribe | Cuándo usarlo |
|---|---|---|
| Término o frase **antes** de traducir | `plastic canvas = rejilla de plástico` | Solo lo que el motor traduce **mal**. Se protege con un marcador `ZQX<n>Z` y se restaura al final. |
| Sigla que se conserva | `SC = SC` | Siglas que el motor podría cambiar (`st` → "pt"). Se deja tal cual estaba escrita. |
| Regex | `re:\bRound (\d+) = Vuelta \1` | Cuando el sentido depende del contexto ("Round 3" sí, "round body" no). |
| **Después** de traducir | bajo una línea `[después]` | Palabras que el motor **deja en inglés**. Se corrigen una vez traducida la frase. |

El separador es el **último** ` = ` de la línea, así que una regex puede llevar `=` dentro (p. ej. `(?=...)`).

## Lo aprendido (para no repetir errores)

- **No metas en "antes" sustantivos que el motor ya traduce bien.** Un marcador en un sustantivo femenino
  rompe el artículo ("el puerta") y con muchos marcadores el motor deja la frase en inglés. Esos van en `[después]`.
- Un marcador al principio de la frase se corrompía (`Q5Q` → `P5Q`); por eso son `ZQX<n>Z`.
- Si tras traducir falta un marcador o no cuadran los paréntesis, se reintenta la frase quitando entradas.
- Las mayúsculas del original se respetan (`Plastic canvas` → `Rejilla de plástico`).
- Un término de una letra o muy corto es peligroso: `red` es una palabra española ("la red"), por eso solo se
  corrige como color delante de ciertos sustantivos.
- Estilo: infinitivo para las órdenes ("Coser", "Rellenar", "Esconder los cabos").

## Revisar una traducción

```
tools/inspect_job.py --list
tools/inspect_job.py <id o parte del nombre> <página>
```

Muestra, texto a texto, el original y su traducción con el glosario actual. Es la forma de convertir
"esto está mal, página 12" en una entrada del glosario.
