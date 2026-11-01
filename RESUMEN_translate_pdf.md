# translate_pdf.py — resumen para retomar el trabajo

Contexto y estado del script para continuar con Claude Code desde cero (Raspberry o portátil).

## Objetivo

Traducir de inglés a español un PDF **hecho de imágenes** (sin capa de texto), conservando el diseño: fondos, fotos y composición. El caso de partida fue un patrón de ganchillo amigurumi (22 páginas A4, una imagen JPG por página, generado con ReportLab).

## Archivos

- `translate_pdf.py`: el script (un único fichero, Python 3).
- `glosario_ganchillo.txt`: glosario de ejemplo de siglas de ganchillo EN→ES (`origen = destino`, una por línea, `#` para comentarios).

## Cómo funciona

1. **Rasteriza** el PDF a PNG con `pdftoppm` (`--dpi`, 130 por defecto).
2. **OCR** con Tesseract (`pytesseract`, imagen ampliada ×2, `--psm 11`, confianza mínima `--min-conf`). Agrupa las palabras por párrafo y guarda caja, texto, número de líneas y altura de línea. Descarta ruido (menos de 3 letras o menos de un 25 % de letras).
3. **Traduce** cada fragmento:
   - `--engine argos` (por defecto): local y gratuito. Protege las siglas del glosario con marcadores y las sustituye después.
   - `--engine claude`: API de Anthropic (`ANTHROPIC_API_KEY`), una llamada por página, con contexto y glosario. Es el que mejor calidad da.
   - `--extract-only` + `--from-json`: saca los textos a `<salida>_work/source.json` y se vuelven a cargar con las traducciones, de una lista con campo `tr` o de un diccionario `{id: texto}`.
4. **Borra** el texto original: máscara por luminancia dentro de cada caja (detecta si el texto es oscuro o claro frente al fondo), dilatada y rellenada con `cv2.inpaint` (Telea). Estima el color del texto para reutilizarlo.
5. **Escribe** el texto traducido: fuente TrueType, tamaño en función de la altura de línea original, con ajuste para que quepa. Dispone de un 25 % extra de ancho porque el español es más largo. Los textos cortos de una línea se centran, el resto va alineado a la izquierda. Se dibuja con supermuestreo ×3.
6. **Monta** el PDF con ReportLab, con páginas del tamaño de la imagen (`px * 72 / dpi`).

Carpeta de trabajo: `<salida>_work/` con `source.json`, `translations.json` y `out/` (JPG por página). Las imágenes de `pages/` se borran al final salvo con `--keep-images`.

## Dependencias

- Sistema: `poppler-utils` (`pdftoppm`), `tesseract-ocr` y el idioma de origen (`tesseract-ocr-eng`).
- Python: `opencv-python-headless numpy pillow pytesseract reportlab`, y según el motor `argostranslate` o `anthropic`.
- En Raspberry Pi OS: `sudo apt install poppler-utils tesseract-ocr tesseract-ocr-eng python3-venv`. `argostranslate` arrastra `ctranslate2`, que en ARM puede ser más pesado de instalar. Si da problemas, usar el portátil o `--engine claude`.

## Uso

```
python translate_pdf.py patron.pdf -o patron_es.pdf --glossary glosario_ganchillo.txt
python translate_pdf.py patron.pdf -o patron_es.pdf --pages 15-22 --engine claude --context "patrón de ganchillo amigurumi"
python translate_pdf.py patron.pdf -o patron_es.pdf --extract-only
python translate_pdf.py patron.pdf -o patron_es.pdf --from-json patron_es_work/translations.json
```

Otras opciones: `--dpi`, `--font`, `--font-bold` (esta última aún no se usa), `--psm`, `--min-conf`, `--src-lang` (código Tesseract, por defecto `eng`), `--from-code` y `--to-code` (ISO para Argos, `en`/`es`), `--model`, `--keep-images`.

## Estado de las pruebas

- **Probado:** extracción por OCR y renderizado de la página 16 con una traducción falsa. Borra y reescribe bien, y el PDF se genera.
- **Sin probar:** el motor Argos (en el sandbox no se podía instalar `argostranslate`) y el motor Claude (no hay API key). El código está escrito, pero sin ejecutar.
- **Corregido tras la prueba:** el filtro de ruido descartaba líneas con muchos números (`CH 20.`). Ya no lo hace.

## Problemas conocidos y mejoras pendientes

1. **Rótulos decorativos** en letra manuscrita o sobre fotos: el OCR los lee mal y se quedan en inglés o salen basura. Ideas: probar `--psm 6`/`12`, preprocesado (binarizado, CLAHE) o un OCR mejor (PaddleOCR, EasyOCR).
2. **Fuente:** por defecto es DejaVu Serif, más ancha que las originales. Probar fuentes más estrechas o que se ajusten mejor a cada página.
3. **Tipografía en negrita y cursiva:** el script usa una sola fuente. `--font-bold` está definido pero no se aplica.
4. **Texto rotado:** no se soporta (los rótulos inclinados de etiquetas quedan rectos).
5. **Borrado sobre fondos con textura:** pueden quedar manchas pequeñas. Probar parches con desenfoque, `INPAINT_NS` o ampliar la dilatación según el tipo de fondo.
6. **Cajas solapadas y tamaño de letra:** el tamaño se deduce de la altura de línea del OCR y puede salir irregular entre fragmentos parecidos. Se podría normalizar por tipo de elemento.
7. **Traducción por fragmento y no por página:** con Argos pierde contexto. Con Claude se manda la página entera, que funciona mejor.
8. **Glosario con Argos:** las siglas se protegen con marcadores `Q{n}Q`. Falta comprobar que el modelo no los altera.
9. **Rendimiento:** el OCR tarda unos 20-30 s por página en una CPU normal. Se paraleliza por procesos (hasta 8). En Raspberry puede ir lento.
10. **Revisión visual:** no hay modo para ver rápido el resultado de todas las páginas. Se podría añadir una hoja de contactos con miniaturas.

## Resultado del trabajo manual (para comparar)

Se hizo el PDF completo de 22 páginas con dos métodos:

- Páginas 1 a 14: método cuidadoso, ajustando a mano las coordenadas de cada texto.
- Páginas 15 a 22: método rápido, con OCR automático y una sola pasada de traducción (unos 4 minutos). Resultado considerado aceptable, aunque con algunos rótulos decorativos aún en inglés, pies de foto cortados y alguna mancha de borrado.

Las abreviaturas de ganchillo se pasaron al español así: SC→PB, SL ST→PBJ, HDC→MV, DC→V, INC→AUM, DEC→DISM, CH→CAD, MR→AM, FO→CR, BO→PP, RD/Rnd→Vta, FLO→HD, BLO→HT, ST/STS→pt/pts.

## Primeros pasos sugeridos para Claude Code

1. Crear un entorno virtual, instalar las dependencias y comprobar `tesseract --version` y `pdftoppm -v`.
2. Ejecutar con `--pages 16 --extract-only` y revisar `source.json`.
3. Probar `--engine argos` con una página y revisar el resultado a ojo. Si no instala bien, probar `--engine claude`.
4. Atacar los problemas de la lista anterior por orden de impacto: 1 (OCR de rótulos), 5 (manchas) y 2 (fuente).
5. Probar con un PDF distinto para comprobar que el script es general y no depende de este documento.
