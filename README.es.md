# kdenlive-mcp

[English](README.md) | Español

[![CI](https://github.com/kikoperex/kdenlive-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kikoperex/kdenlive-mcp/actions/workflows/ci.yml)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

Servidor MCP para editar vídeo en Linux con Kdenlive/MLT: la edición vive en un EDL en
JSON, los fotogramas de previsualización vuelven como imágenes y el render pasa por
`melt`.

## Por qué

Las integraciones de edición de vídeo suelen o bien manejar una GUI o bien pedirle al
modelo que edite XML de MLT directamente. Ambas cosas son frágiles: una GUI necesita un
display y su estado puede cambiar por debajo, mientras que el XML tiene muchas
propiedades acopladas y no da mensajes de error útiles.

Este servidor mantiene separados el documento editable y el artefacto de compilación:

- **El EDL en JSON es la fuente de la verdad.** `project.edl.json` es pequeño, se puede
  diffear y se escribe de forma atómica. Todas las herramientas de edición modifican
  este fichero, nunca el XML.
- **El fichero `.kdenlive` es un artefacto de compilación.** `export_kdenlive` (y,
  internamente, `render`, `preview` y `validate`) compila el EDL en un proyecto de
  Kdenlive real. Puedes borrarlo y regenerarlo después de cualquier edición.
- **`melt` renderiza en lugar de la GUI.** Es el mismo motor MLT que usa Kdenlive, pero
  sin interfaz, determinista y scriptable. No hace falta una sesión X para editar,
  previsualizar o renderizar.
- **`preview` devuelve fotogramas como imágenes.** Un render a baja resolución más
  fotogramas JPEG en la respuesta MCP permiten que el agente vea de verdad el resultado
  y lo corrija, en vez de adivinarlo a partir de metadatos.

Una persona puede seguir abriendo el `.kdenlive` compilado en la GUI de Kdenlive,
retocarlo a mano y devolverlo al EDL con `import_kdenlive` (mejor esfuerzo, ver
[Limitaciones](#limitaciones)).

```text
media_scan --> project_create --> clip_add / clip_update / effect_add --> preview --> frames
                                        |                                  |          |
                                        |                                  |          +--> look, fix, repeat
                                        |                                  +--> low-res .mp4
                                        v
                               project.edl.json  (source of truth)
                                        |
             +--------------------------+--------------------------+
             |                          |                          |
             v                          v                          v
     export_kdenlive               validate                    render
             |                          |                          |
             v                          v                          v
     project.kdenlive         XML + null render            final .mp4
             |
             v
     Kdenlive GUI (optional)
```

## Características

- 20 herramientas MCP que cubren proyectos, medios, pistas, clips, efectos,
  previsualización, render, validación, exportación/importación y comprobación de la
  salida.
- Clips de medio, color sólido y título, con fundidos, ganancia de audio lineal y filtros
  MLT crudos.
- `preview` renderiza un H.264 pequeño y devuelve hasta 24 fotogramas inline como
  imágenes JPEG.
- `render` produce H.264/AAC con CRF, preset, tamaño de salida y rango temporal
  configurables. Si falta una dimensión de salida, se deriva del aspecto del proyecto.
- `validate` comprueba que el XML está bien formado, informa de medios que faltan y hace
  un render completo con `melt` al consumidor nulo.
- Exportación e importación de `.kdenlive`; `open_in_kdenlive` puede lanzar la GUI si
  está instalada.
- `output_check` informa de streams, duración y pico de audio, y detecta renders mudos.
- Cero dependencias de Python de terceros: solo la biblioteca estándar (Python 3.9+).
- Errores de validación claros para clips solapados, pistas duplicadas, ids
  desconocidos, perfiles inválidos y tiempos mal formados.
- No hace falta GUI ni sesión X para editar, previsualizar, validar o renderizar.

## Requisitos

- **Python 3.9+**. El servidor usa solo la biblioteca estándar.
- **`ffmpeg` y `ffprobe`** en `PATH`: sondeo de medios, extracción de fotogramas y
  medición de volumen.
- **`melt`** (de MLT) en `PATH`: la compilación se hace en Python, pero el render y la
  validación ejecutan `melt`. Este proyecto se desarrolla contra MLT 7.42.
- **`kdenlive`** (opcional): solo hace falta para `open_in_kdenlive` y para abrir
  proyectos exportados en la GUI. Probado con Kdenlive 26.08.3.
- **Linux.** El manejo de procesos, las rutas y el lanzamiento de la GUI están pensados
  para sistemas POSIX; macOS y Windows no están probados.

## Instalación

### Desde un clon

El servidor es un único punto de entrada y no tiene dependencias de Python, así que con
clonar basta:

```bash
git clone https://github.com/kikoperex/kdenlive-mcp.git
cd kdenlive-mcp
python3 server.py --version       # kdenlive-mcp 1.0.0
python3 server.py --list-tools    # un nombre de herramienta por línea, 20 en total
```

En los ejemplos siguientes se supone que el clon está en `$HOME/mcp/kdenlive-mcp`;
ajusta la ruta si la tuya es distinta.

### Con pipx o uvx

`pipx` y `uvx` necesitan el punto de entrada de consola de `pyproject.toml`. Cuando
exista esa metadata de empaquetado, cualquiera de las dos formas funciona:

```bash
# desde un clon
pipx install .

# o directamente desde Git, sin clonar a mano
uvx --from git+https://github.com/kikoperex/kdenlive-mcp kdenlive-mcp
```

Sin metadata de empaquetado, ejecuta `python3 server.py` desde el clon como se indica
arriba.

### Registrar en pi

```bash
pi mcp add kdenlive \
  --description "Kdenlive/MLT video editing: build timelines, render, preview frames" \
  --exposure codemode \
  -- python3 $HOME/mcp/kdenlive-mcp/server.py
```

Los renders tardan, y el timeout por petición por defecto (60 segundos) se queda corto
con metraje real. Súbelo en `~/.pi/agent/mcp.json`:

```bash
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".pi/agent/mcp.json"
c = json.loads(p.read_text())
c["mcpServers"]["kdenlive"]["timeout"] = 3600
p.write_text(json.dumps(c, indent=2) + "\n")
EOF

pi mcp list      # kdenlive debe aparecer conectado y listar 20 herramientas
```

Pi puede exponer las herramientas en tres modos:

| Exposición | Cómo llega el modelo a las herramientas | Cuándo usarlo |
|---|---|---|
| `codemode` (por defecto) | Desde scripts de codemode; las herramientas se descubren con `searchTools()` / `describeTool()` | Encadenar muchas ediciones en un script |
| `deferred` | Se encuentran con `tool_search` antes de usarlas | Mantener corta la lista, pero llamar a las herramientas directamente |
| `direct` | Se declaran al modelo como herramientas nativas en cada turno | Uso frecuente y simple; cuesta 20 declaraciones |

Se elige con `--exposure deferred` (o `direct`) al añadir el servidor, o con `"exposure"`
en la entrada del servidor en `mcp.json`.

### Cliente MCP genérico

Cualquier cliente MCP de stdio puede lanzar el servidor directamente:

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "python3",
      "args": ["/home/USER/mcp/kdenlive-mcp/server.py"]
    }
  }
}
```

Sustituye `/home/USER` por la ruta absoluta de tu máquina (JSON no expande `$HOME`). Las
claves de timeout y los modos de exposición dependen del cliente; en pi son `timeout` y
`exposure`, con segundos y los nombres de modo de arriba.

El render tiene dos timeouts: el timeout por petición del cliente MCP (debe cubrir toda
la llamada) y el timeout del subproceso de la propia herramienta. `render` usa 7200
segundos por defecto y acepta `timeout`; `preview` usa 1800 segundos por defecto. Para
renders largos, sube los dos.

## Herramientas

| Herramienta | Para qué sirve | Argumentos principales |
|---|---|---|
| `project_create` | Crea un EDL nuevo y devuelve su timeline. Por defecto, 1920x1080 a 25 fps. | `path`, `width`, `height`, `fps`, `profile` (p. ej. `1080p25`, `720p30`, `vertical1080`, `4k30`), `name` |
| `project_show` | Muestra pistas, clips, puntos in/out, fundidos, ganancia, efectos y duración total. | `edl` |
| `project_set_profile` | Cambia el tamaño de fotograma, la tasa de fotogramas o el flag progresivo. | `edl`, `width`, `height`, `fps`, `progressive` |
| `media_probe` | Inspecciona uno o varios ficheros: duración, resolución, fps, códecs, pistas de audio, entrelazado. | `path` o `paths` |
| `media_scan` | Lista los medios utilizables de un directorio con sus metadatos. | `dir`, `recursive` |
| `track_add` | Añade una pista de vídeo o audio; el nombre por defecto es el siguiente `Vn` / `An` libre. | `edl`, `type`, `name` |
| `track_remove` | Elimina una pista y todo lo que contiene. | `edl`, `track` |
| `clip_add` | Coloca un clip de medio, color o título. En medios, `in`/`out` son segundos de la fuente; el color necesita `duration`; el título toma `{text, style}` y una duración (5 s si se omite). `append` lo coloca después del último clip. | `edl`, `track`, `kind`, `media` / `color` / `title`, `start`, `in`, `out`, `duration`, `gain`, `fade_in`, `fade_out`, `label`, `append` |
| `clip_update` | Mueve, recoloca en el tiempo, recorta, cambia la ganancia, los fundidos o la etiqueta de un clip; también puede moverlo a otra pista. Acepta los mismos campos temporales que `clip_add`. | `edl`, `clip_id`, `track`, `start`, `duration`, `in`, `out`, `gain`, `fade_in`, `fade_out`, `label`, `title` |
| `clip_remove` | Borra un clip de la timeline. | `edl`, `clip_id` |
| `clip_split` | Divide un clip en una posición absoluta de la timeline; ambas mitades deben durar al menos un fotograma. | `edl`, `clip_id`, `at` |
| `effect_add` | Añade un filtro MLT crudo a un clip, por ejemplo `brightness` con `{level: 0.8}` o `frei0r.cartoon`. Las props se pasan sin modificar. | `edl`, `clip_id`, `service`, `props` |
| `effect_remove` | Elimina todos los efectos de un clip, o solo un servicio. | `edl`, `clip_id`, `service` |
| `preview` | Render rápido a baja resolución más hasta 24 fotogramas devueltos como imágenes. | `edl`, `times` o `count` (por defecto 4), `width` (por defecto 320), `inline`, `out` |
| `render` | Renderiza la timeline a H.264/AAC. El CRF por defecto es 20 y el preset, `medium`. | `edl`, `out`, `crf`, `preset`, `width` o `height` (la otra se deriva), `start`, `end`, `timeout` |
| `validate` | Compila el proyecto, comprueba el parseo del XML y los medios que faltan, y ejecuta un render completo con `melt` al consumidor nulo. | `edl`, `out` (ruta del `.kdenlive`) |
| `export_kdenlive` | Compila el EDL en un proyecto `.kdenlive` para la GUI. | `edl`, `out` |
| `open_in_kdenlive` | Compila el proyecto y lo abre en la GUI de Kdenlive. Requiere `kdenlive` y una sesión gráfica. | `edl`, `out` |
| `import_kdenlive` | Convierte un proyecto `.kdenlive` existente de vuelta a EDL (mejor esfuerzo). | `project`, `edl` |
| `output_check` | Inspecciona un fichero renderizado: streams, duración y pico de audio; avisa cuando la salida parece muda. | `file` |

Los ficheros derivados son hermanos del EDL:

- `edit` pasa a ser `edit.edl.json`; `edit.json` también se acepta como ruta de EDL.
- `edit.edl.json` compila a `edit.kdenlive`.
- `render` escribe `edit.mp4` salvo que se indique `out`.
- `preview` escribe `edit.preview.mp4` salvo que se indique `out`.

## Formatos de tiempo y semántica

Los tiempos pueden ser números en segundos o cadenas en cualquiera de estas formas:

```text
3.5                 # número, segundos
"3.5s"              # segundos
"87f"               # 87 fotogramas a la tasa del proyecto
"00:00:03.500"      # HH:MM:SS.mmm
```

El EDL guarda segundos como floats. Los valores en fotogramas se convierten usando el
`fps` del proyecto, y los timecodes MLT generados se ajustan a fotogramas completos.

- `start` es la posición en la timeline; `in` y `out` son posiciones en el medio de
  origen. En un clip de medio, `duration` es la longitud en la timeline; cuando se usa
  `out`, `duration = out - in`. Si solo se da `in`, el clip llega hasta el final de la
  fuente.
- `fade_in` y `fade_out` están en segundos. Los fundidos de vídeo pasan por negro; los
  de audio cambian el volumen.
- `gain` es un **multiplicador lineal**: `1.0` es unidad, `0.5` son aproximadamente
  -6 dB, `2.0` son aproximadamente +6 dB y `0` es silencio. El filtro de volumen
  generado para Kdenlive guarda el valor convertido a dB.
- Los clips de una misma pista son lineales y no pueden solaparse. Una edición en
  conflicto falla indicando el rango ocupado; usa `clip_update` para mover/recortar, o
  `append` / `append: true` para colocar un clip después del último.
- Las pistas de vídeo componen de abajo arriba: **V2 tapa a V1**. Las pistas de audio
  se mezclan de forma independiente.
- El audio de una pista de vídeo está silenciado por defecto (la pista generada lleva
  `hide="audio"`), así que la música y la voz van en pistas de audio. Los ficheros
  solo-audio deben ir en una pista `A`.

## Notas sobre el formato `.kdenlive`

El generador está escrito contra el formato de proyecto de Kdenlive 26.08 y MLT 7.42.
Estas propiedades son críticas:

- El UUID del documento vive en `main_bin` como `kdenlive:docproperties.uuid` y debe ser
  igual que `kdenlive:sequenceproperties.documentuuid` del tractor de secuencia; si no,
  Kdenlive trata la secuencia como extranjera.
- Las pistas de vídeo se numeran V1..Vn en orden del tractor, pero la numeración de audio
  va al revés: la última pista de audio del tractor es A1. Por eso se emiten V1..Vn y
  después An..A1.
- El compositing de vídeo usa `qtblend` y la mezcla de audio usa `mix`, ambos
  `always_active` y marcados con `internal_added` para que Kdenlive no los muestre como
  transiciones añadidas por el usuario.
- `black_track` está clavado a la duración del contenido. MLT calcula la longitud de un
  tractor a partir de su pista más larga, así que esto evita que la secuencia dure más
  que la edición.
- Los clips de título necesitan un `resource` **vacío** y `xmldata` inline; así el módulo
  `kdenlivetitle` lee el título inline en vez de intentar cargar un fichero.
- `brightness.level` es un multiplicador 0..1, mientras que `volume.level` va en **dB**
  (0 dB es ganancia unidad). Los fundidos se escriben con esas escalas.
- El elemento raíz `<mlt producer="...">` apunta al tractor de secuencia para que
  `melt project.kdenlive` renderice la timeline. Kdenlive no lee ese atributo; busca
  `main_bin`.

## Tests

```bash
python3 tests/test_e2e.py
```

El test end-to-end requiere `ffmpeg`, `ffprobe` y `melt`. Genera medios de prueba de
640x360 en `/tmp/kdenlive-mcp-test` (se puede cambiar con `KD_TEST_DIR`), arranca
`server.py` y habla JSON-RPC delimitado por líneas por stdio como un cliente real.

La suite tiene 48+ comprobaciones y tarda unos dos minutos. Verifica los esquemas de las
herramientas, el sondeo y escaneo de medios, la edición y el rechazo de solapes, los
cambios de perfil y pistas, la exportación e importación de `.kdenlive` (incluidos
fundidos y ganancia de audio), el número y el aspecto de las imágenes de
previsualización, la duración y el tamaño del render, y píxeles reales (clip de color,
clip rojo, fundido a negro, título visible), además del pico de audio, peticiones
grandes y el manejo de errores. CI ejecuta el mismo comando con Python 3.9 y 3.12.

## Solución de problemas

- **`melt` termina con señal 11 (segfault).** Normalmente es el tamaño de salida, no la
  timeline. Usa dimensiones pares, preferiblemente múltiplos de 16: `320x180`, `640x360`,
  `1280x720`. `render` y `preview` ajustan los tamaños pedidos a una cantidad par de
  píxeles, pero tamaños muy pequeños o raros todavía pueden molestar a libx264; prueba
  otro tamaño.
- **`kdenlive --render` hace segfault.** En Kdenlive 26.08.3 (Arch), el render sin GUI
  peta incluso con un proyecto vacío. Este servidor nunca lo usa: el render y la
  validación pasan por `melt`.
- **La llamada MCP agota el tiempo durante un render.** Sube el timeout por petición del
  cliente (`"timeout": 3600` o más en `mcp.json`) y, para trabajos muy largos, pasa un
  `timeout` mayor a `render`. La herramienta usa 7200 segundos por defecto; `preview`,
  1800.
- **`required executable not found: melt`, `ffmpeg` o `ffprobe`.** Instala MLT y FFmpeg y
  asegúrate de que sus binarios están en `PATH` (el servidor también busca `melt` en
  `/usr/bin` y `/sbin`).
- **El render no tiene audio.** Compruébalo con `output_check`; un pico por debajo de
  -60 dB se informa como silencio. El audio colocado en una pista de vídeo está silenciado
  por diseño, así que muévelo a una pista de audio.
- **Faltan fotogramas de previsualización.** `preview` limita la salida a 24 fotogramas y
  omite los JPEG individuales de más de unos 900 KB. Reintenta con un `width` menor.
- **`open_in_kdenlive` falla.** Necesita `kdenlive` instalado y una sesión gráfica; editar,
  previsualizar y renderizar no.

## Limitaciones

- `import_kdenlive` es mejor esfuerzo. Recupera la disposición de pistas, las posiciones y
  fuentes de los clips, los clips de color, el texto de los títulos, los fundidos simples
  de brillo y la ganancia de audio constante más los fundidos simples de volumen. **No**
  recupera transiciones, pilas de efectos por clip, curvas de keyframes arbitrarias,
  grupos ni marcadores. Los clips cuyo fichero de medio ya no existe se omiten.
- No hay bloqueo de ficheros. Dos agentes o procesos editando el mismo EDL pueden
  sobrescribir los cambios del otro (gana el último guardado atómico). Usa un EDL por
  editor o serializa las ediciones.
- El modelo de EDL es intencionadamente pequeño: una secuencia, cortes y fundidos simples,
  más filtros crudos por clip. No hay transiciones, efectos con keyframes, marcadores,
  grupos, flujos de proxy ni múltiples secuencias.
- Suposiciones solo-Linux: grupos de procesos POSIX, `shutil.which`, lanzamiento de GUI y
  rutas del sistema de ficheros. No se promete soporte para Windows ni macOS.
- El render está fijado a H.264/AAC con libx264; no hay codificación por GPU.
- El `.kdenlive` generado apunta al formato de Kdenlive 26.08. Otras versiones pueden
  cargarlo con avisos o rechazar propiedades más nuevas.

## Contribuir

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para saber cómo informar de problemas y enviar
cambios. Ejecuta `python3 tests/test_e2e.py` antes de abrir un pull request.

## Licencia

kdenlive-mcp es software libre bajo la **GNU General Public License, versión 2 o (a tu
elección) cualquier versión posterior** (`GPL-2.0-or-later`). Consulta [LICENSE](LICENSE)
para el texto completo. Se distribuye sin garantía alguna, en la medida permitida por la
ley.

`ffmpeg`, `ffprobe`, `melt`/MLT y Kdenlive son programas separados que se invocan como
subprocesos. No se incluyen en este repositorio y se distribuyen bajo sus propias
licencias y condiciones.
