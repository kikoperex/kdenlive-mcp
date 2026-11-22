# kdenlive-mcp

Servidor MCP (stdio, **sin dependencias de Python**) para editar vídeo en Linux con
Kdenlive/MLT: monta timelines, renderiza con `melt` y devuelve fotogramas como imágenes
para que el agente *vea* el resultado.

```
media_scan → project_create → clip_add … → preview (imágenes) → render | export_kdenlive
```

## Cómo funciona

El documento que edita el agente es un **EDL** en JSON (`proyecto.edl.json`). El
`.kdenlive` es un *artefacto compilado* desde el EDL, así que:

- el agente nunca hace cirugía frágil sobre XML,
- tú puedes abrir el `.kdenlive` en la GUI de Kdenlive y retocar a mano,
- `import_kdenlive` convierte un proyecto existente de vuelta a EDL (posición, fuente, fades).

El render usa `melt` (el mismo motor que usa Kdenlive), no la GUI: es determinista,
cabe en scripts y no necesita sesión gráfica.

## Instalación

Requisitos: `python3` (3.9+), `ffmpeg`/`ffprobe`, `melt` (paquete `mlt`) y, opcionalmente,
`kdenlive` para la GUI.

```bash
# registrar en pi (usuario)
pi mcp add kdenlive \
  --description "Kdenlive/MLT video editing: build timelines, render, preview frames" \
  -- python3 $HOME/mcp/kdenlive-mcp/server.py

# los renders tardan: sube el timeout por petición (por defecto son 60 s)
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home()/".pi/agent/mcp.json"
c = json.loads(p.read_text()); c["mcpServers"]["kdenlive"]["timeout"] = 3600
p.write_text(json.dumps(c, indent=2)+"\n")
EOF

pi mcp list      # debe decir: kdenlive: connected, 20 tools
```

Por defecto pi expone las herramientas en modo `codemode` (el agente escribe un script
que las encadena; ideal para añadir 20 clips de golpe). Si prefieres llamadas directas:

```json
"kdenlive": { "exposure": "deferred", ... }   // o "direct" para verlas siempre
```

## Herramientas

| Herramienta | Para qué |
|---|---|
| `project_create` / `project_show` / `project_set_profile` | crear, ver timeline, cambiar resolución/fps |
| `media_probe` / `media_scan` | duración, códec, fps, pistas de audio |
| `track_add` / `track_remove` | pistas V y A |
| `clip_add` | colocar media, color o título (`start`, `in`, `out`, `duration`, `fade_in/out`, `gain`, `append`) |
| `clip_update` / `clip_remove` / `clip_split` | mover, recortar, dividir |
| `effect_add` / `effect_remove` | filtros MLT crudos (`brightness`, `saturation`, `frei0r.*`, `affine`…) |
| `preview` | render rápido en baja resolución **+ fotogramas como imágenes** |
| `render` | H.264/AAC final (crf, preset, escala, rango) |
| `validate` | XML bien formado + render completo a consumidor nulo |
| `export_kdenlive` / `open_in_kdenlive` | generar el `.kdenlive` y abrirlo en la GUI |
| `import_kdenlive` | proyecto existente → EDL (mejor esfuerzo) |
| `output_check` | streams y pico de audio del render (detecta renders mudos) |

Tiempos: segundos o `'12.5s'`, `'150f'`, `'00:00:12.500'`.
`gain` es multiplicador lineal (0.5 = −6 dB).
Las pistas de vídeo componen de abajo arriba: V2 tapa a V1. El audio de una pista de
vídeo está silenciado, así que la música/la voz va en pistas A.

## Detalles del formato `.kdenlive`

Verificado contra el código de Kdenlive 26.08 y MLT 7.42 (no a ojo):

- el uuid del documento vive en `main_bin` como `kdenlive:docproperties.uuid` y debe
  coincidir con `kdenlive:sequenceproperties.documentuuid` del tractor de secuencia,
  o Kdenlive lo trata como secuencia *extranjera*;
- las pistas de vídeo se numeran V1..Vn en orden del tractor y las de audio al revés
  (la última del tractor es A1), por eso se emiten V1..Vn y luego An..A1;
- el compositing usa `qtblend` `always_active` y el audio `mix`, ambos con
  `internal_added` para que no aparezcan como transiciones del usuario;
- `black_track` fija la duración: MLT calcula la longitud del tractor por su pista más
  larga, así que el negro va clavado a la duración del contenido;
- los clips de título necesitan `resource` **vacío** para que el módulo
  `kdenlivetitle` lea el `xmldata` inline;
- `brightness.level` es un multiplicador 0..1, mientras `volume.level` va en **dB**
  (0 = ganancia unidad); los fades se escriben con esas escalas;
- el `producer` del elemento raíz apunta al tractor de secuencia para que
  `melt proyecto.kdenlive` renderice la timeline (Kdenlive nunca lee ese atributo).

## Tests

```bash
python3 tests/test_e2e.py      # 48 comprobaciones, ~2 min
```

Genera media de prueba con ffmpeg, habla JSON-RPC por stdio como un cliente real y
verifica píxeles: fade in/out a negro, clip de color, título visible, mezcla de audio.

## Notas

- `kdenlive --render` (render sin GUI) **hace segfault en 26.08.3 en Arch** con
  cualquier proyecto, incluso vacío; por eso el render va por `melt`.
- La carga en la GUI se comprueba abriendo el proyecto; `open_in_kdenlive` lo lanza.
- Si `melt` peta con "signal 11" al escalar, es el tamaño de salida: usa múltiplos de
  16 (`320x180`, `640x360`, `1280x720`). Las herramientas ya lo redondean.
