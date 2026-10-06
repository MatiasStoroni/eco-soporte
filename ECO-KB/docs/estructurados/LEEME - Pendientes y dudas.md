# Pendientes y dudas para los operadores

Esta carpeta contiene los documentos ya reestructurados (`soporte/` y `ventas/`), listos para subir a Drive
respetando las subcarpetas. **No subas este archivo** (está fuera de las carpetas de audiencia y se ignoraría).
Solo se reescribió el contenido que tenían los PDF, sin añadir nada. Faltan estas cosas:

## Quién ve cada archivo (actualizado 2026-10-06)
- **Soporte o ventas** lo define la carpeta de primer nivel (`soporte/`, `ventas/`) y no se cambia desde el panel.
- **Qué tipos de cliente lo consultan** se gestiona en el panel: `/admin` → **Archivos**. Los cambios aplican al
  instante y no hace falta volver a ingerir.
- La subcarpeta (`hotel/`, `comun/`, …) es solo la **sugerencia inicial** para un archivo nuevo: `hotel/` → Hotel,
  `comun/` → Todos. Una carpeta que no sea un tipo de cliente (por ejemplo `equipos/`) deja el archivo
  **sin habilitar** hasta que alguien lo tilde en el panel. Después, la ingesta nunca pisa lo elegido.
- Para compartir un archivo entre tipos **no lo copies**: dejá uno solo y tildá los tipos en el panel.

## Cómo se preparan los archivos nuevos
- Se puede mandar el original (PDF, Word o Google Docs exportado) sin darle formato. Si ya viene bien organizado
  (títulos por tema, texto real), se usa tal cual. Si no, un asistente de IA lo reorganiza en un borrador: no
  agrega ni cambia datos, y marca lo que no pudo leer.
- Cada borrador lo revisa una persona contra el original antes de habilitarlo. Lo que salga de imágenes (tablas,
  diagramas) hay que mirarlo con más cuidado.
- Lo único que hay que indicar de cada archivo es si es de **soporte** o de **ventas**.

## Contenido que está en imágenes y no se pudo leer
1. **Manual operativo ECO360 – hotelería**, páginas 8 a 12 sin texto:
   - "Instrucciones de aplicación del sistema ECO360" (páginas 7 a 11 del índice): cómo se aplica cada producto,
     dosis o diluciones, tiempos de contacto, cantidades.
   - "Desinfección de manos" (BIO SANITIZER): pasos de uso.
   Hay que transcribirlas a texto (títulos + pasos numerados) y añadirlas al manual.
2. **X4 y X5**: ya transcritos (`soporte/comun/`). En la ficha de X4, la sección "Compatibilidad e información
   adicional" (página 2) salió ilegible y se omitió. *(2026-10-06: el asistente de IA la leyó del PDF original:
   "Compatible con agua ozonizada (SOW) dentro de los protocolos ECO360. No mezclar con productos clorados o
   alcalinos" + presentación de 1 litro. Falta que alguien lo confirme contra el PDF antes de agregarlo.)*
3. **Ficha del Carro Ozonify**: las secciones "Aplicaciones industriales", "Diagrama del equipo" y
   "Panel de control · indicadores" son imágenes. *(2026-10-06: el asistente de IA las transcribió en un borrador
   y detectó una inconsistencia del original: el display dice "Bomba 1: X1" y los indicadores "Bomba 1: X3".
   Falta revisarlo antes de publicarlo.)*

## Equipos de ozono y bodegas (actualizado 2026-10-05)
- Hay **dos equipos de ozono**: el **Carro Ozonify Industrial** (bodegas; su ficha está ahora en `soporte/bodega/`)
  y **OZONIFY PRO** (hoteles y restaurantes). De OZONIFY PRO solo está cargado qué es: un único archivo en
  `soporte/hotel/`, habilitado en el panel para **Hotel y Restaurante**. **Falta su ficha técnica**
  (especificaciones, presión y caudal, panel, instalación). Al recibirla, reemplazar la sección
  "Especificaciones y ficha técnica de OZONIFY PRO" de ese archivo.
- Confirmar la frase "es el equipo con el que se genera el agua ozonizada (OZONIFY)" de OZONIFY PRO.
- **Bodega no tiene procedimientos propios**: limpieza de depósitos, tanques, barricas, vendimia, etc. Hoy un cliente
  de bodega solo puede consultar el Carro Ozonify Industrial, X4 y X5; lo demás termina en "no tengo cargado".
  Tampoco hay material comercial propio de bodega ni de restaurante.
- Los clientes **genéricos** no ven ninguno de los dos equipos (no están tildados para Genérico en el panel).
- Regla de X3 añadida al manual de hotelería (confirmada por el equipo): **no se aplica sobre muebles de madera ni
  tapizados**. Si hay más materiales prohibidos para X3, agregarlos en la sección de X3.

## Productos citados sin documentación
El manual menciona **X3**, **SURFACE PROTECTANT**, **BIO SANITIZER** y **Blue Test** pero no hay ficha de ninguno.
Si un cliente pregunta cómo usar X3 o qué es el Blue Test, el bot no tendrá nada que citar.

## Ambigüedades en el manual (confirmar antes de publicar)
- La fila "Microorganismos (hongos, virus, bacterias)" de la matriz de la página 6: no se distingue qué productos
  marca. Por eso se omitió. Confirmar si es OZONIFY, X3 y/o SURFACE PROTECTANT.
- "Superficies de alto contacto" (página 13): se interpretó como OZONIFY + SURFACE PROTECTANT (X3 sin marcar).
- En el código de colores, "mesas, sillas y banquetas" aparece tanto en Verde (cocinas) como en Blanco
  (áreas comunes, con un asterisco sin explicación). ¿Cuál aplica en cada caso?
- Los pasillos no aparecen en la tabla de frecuencia operativa (página 13): no se indicó frecuencia de limpieza.
- "Blue Test": ¿qué es y cómo se usa?

## Para las próximas versiones de documentos
- Entregar Google Docs o DOCX con títulos reales (Título 1 / Título 2) en lugar de PDF de diseño.
- Cifras y unidades siempre escritas igual (por ejemplo `20 ml/L`, `2 %`).
- Un documento de ventas distinto (beneficios) para X4, X5 y Ozonify; no reutilizar las fichas técnicas.
- Una sola versión vigente por documento; borrar la antigua de Drive.
