# Transcripción y minutas de reuniones (Windows)

Graba reuniones y llamadas en **Windows 10/11**, las transcribe en local en **inglés y español**
(también mezclados), toma capturas de pantalla con OCR y genera con Claude una **versión anotada**
y una **minuta formal** con decisiones y acuerdos.

- **Transcripción local**, sin nube: Parakeet o Whisper en tu equipo. El audio no sale de tu máquina.
- **Audio de la llamada**: con audífonos también graba lo que dicen los demás.
- **Detección de llamadas**: un icono junto al reloj pregunta "¿Grabar?" cuando Teams, Zoom, Meet… toman el micrófono.
- **Capturas** con atajo, por área o por ventana, y **escenas automáticas** cuando cambia la pantalla.
- **Hablantes** separados automáticamente (diarización).
- **Versión anotada, minuta y chat** sobre la reunión, con Claude Code.

## Instalación (una vez)

Requisitos: Windows 10/11, Python 3.10+ (probado con 3.13) y unos 5 GB libres (torch CPU y modelos).
Opcional: [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) en el PATH para guardar el audio en MP3
(sin él se guarda en WAV), y [Claude Code](https://claude.com/claude-code) (`claude`) con sesión
iniciada para la versión anotada, la minuta y el chat.

```bat
setup.bat
```

Crea `.venv\` e instala todo (torch en versión CPU). La primera grabación descarga los modelos:
Parakeet (~640 MB), Silero VAD y EasyOCR; Whisper (~1.6 GB) solo si lo eliges.

## Uso

**Interfaz web** (recomendado):

```bat
webui.bat
```

Abre **http://127.0.0.1:8766** (guárdalo en favoritos). Solo si ese puerto estuviera ocupado usa el
siguiente libre; la dirección real siempre está en `.webui\server.json` y en la consola. En PowerShell, desde la carpeta, escribe `.\webui.bat`.
Elige el idioma, el área de captura (pantalla completa, un área o una ventana; la ventana se
captura aunque esté tapada por otras) y pulsa **Grabar**. El resto de ajustes está en **▸ Opciones**.
La interfaz está en español; el botón **ES** junto al tema la cambia a inglés.

**Consola**:

```bat
record.bat miproyecto                     :: auto (inglés/español), 180 min máx.
record.bat miproyecto 60 --lang es        :: solo español, se detiene a los 60 min
record.bat miproyecto --speakers 2        :: con diarización (2 hablantes)
record.bat miproyecto --save-audio --system-audio
```

Durante la grabación:
- `Ctrl+Shift+S`: captura de pantalla (con OCR)
- `Ctrl+Shift+W`: captura el texto seleccionado (simula Ctrl+C y lee el portapapeles)
- `Ctrl+C`: detener (en consola)

Los atajos son globales: mientras graba, `Ctrl+Shift+S` / `Ctrl+Shift+W` no llegan a otras apps
(por ejemplo, Ctrl+Shift+W ya no cierra la ventana del navegador). Si otra app ya los tiene
registrados, se indica en el log.

## Grabación automática de llamadas

`callwatch.bat` deja un **icono junto al reloj** que vigila el micrófono. Cuando Teams, Zoom,
Webex, Slack, Meet en el navegador u otra app de llamadas toma el micrófono:

1. **Al empezar la llamada**, aparece un aviso con sonido abajo a la derecha **del monitor donde
   está el ratón**: "Llamada detectada en Teams — ¿Grabar?". Si no respondes en 2 minutos, no
   graba, pero mientras dure la llamada puedes empezar con clic derecho en el icono →
   **Grabar esta llamada**.
2. **Al terminar** (la app suelta el micrófono durante 15 s), detiene la grabación y pregunta si
   quieres **versión final + minuta**, **solo la final** o **nada**. Cuando están listas, te avisa.

Desde el menú del icono (clic derecho) se configura:
- **Al empezar una llamada:** preguntar (por defecto), grabar automáticamente o no hacer nada.
- **Al terminar la llamada:** preguntar, final + minuta, solo final o nada.
- **Iniciar con Windows:** añade el vigilante al inicio de sesión.
- **Reabrir si se cierra (cada 5 min):** una tarea programada de Windows lo vuelve a abrir si se
  cerró (por ejemplo, si Windows lo cerró por no responder). Si ya está abierto, no hace nada.
  También: `.venv\Scripts\python call_watcher.py --watchdog on|off`.
- También: abrir la interfaz, detener la grabación y salir.

Si el vigilante se reabre en medio de una llamada que estaba grabando, retoma esa grabación y la
detiene al colgar. Además mantiene **siempre encendido el servidor web**: si deja de responder, lo
arranca de nuevo, así http://127.0.0.1:8766 siempre contesta. Los errores inesperados quedan en
`.webui\call_watcher.log`.

El icono se pone **rojo** mientras graba. Las grabaciones van al último proyecto usado en la
interfaz; para fijar uno, pon `"project": "nombre"` en `.webui\call_watcher.json`. En ese mismo
archivo están los tiempos y la lista de apps que cuentan como llamada.

Para ver qué apps están usando el micrófono en este momento:
`.venv\Scripts\python call_watcher.py --status`.

**Cómo detecta las llamadas:** lee el mismo registro de Windows que enciende el icono de micrófono
de la barra de tareas. Solo cuentan las apps de la lista, así que el propio grabador o un dictado
no disparan nada.

> Avisa siempre a los participantes de que la reunión se graba. El modo automático graba sin
> preguntar: úsalo solo donde tengas consentimiento.

## Audio de la llamada con audífonos

Con audífonos, las voces de los demás van directo a tus oídos y el micrófono no las oye. Con el
interruptor **Audio de la llamada** (activado por defecto) se graba, mezclado con tu micrófono,
también **lo que suena en el equipo**, es decir, lo que dicen los demás.

- Por defecto se graban **todas las salidas** a la vez (audífonos, bocinas, monitor), así la
  llamada se capta suene por donde suene.
- En ⚙ Configuración → *Audio* puedes fijar un **micrófono** concreto (por defecto, el de Windows)
  y una **salida** concreta.
- Con *Audio de la llamada* también se graba cualquier otro sonido del equipo (un video,
  notificaciones) mientras grabas.
- En consola: `record.bat proyecto --system-audio [--mic-device "nombre"] [--output-device "nombre"]`.

Usa PyAudioWPatch (WASAPI loopback).

## Motores de reconocimiento

Se eligen en **Opciones → Motor** o con `--engine` en consola:

| Motor | Idiomas | Notas |
|---|---|---|
| `parakeet` (por defecto en la interfaz) | Detecta el idioma él mismo (25 idiomas europeos, incluidos inglés y español). Con **ES** o **EN** fijo, ver abajo | Muy rápido en CPU, con puntuación. No admite *hotwords*: usa `--vocab-correct` para aplicar a la salida las correcciones de tus notas |
| `whisper` | Inglés/español detectado en cada frase, o fijo con `--lang en/es` | Admite *hotwords* (los términos de tus notas ayudan al reconocimiento). Mucho más lento en CPU |

```bat
record.bat miproyecto --engine parakeet --vocab-file projects\miproyecto\notes.md --vocab-correct
```

**Idioma fijo con Parakeet.** Parakeet no acepta un idioma y, con audio ruidoso o con eco, a veces
contesta en inglés aunque se hable español. Si eliges **ES** o **EN** (o `--lang es/en`), cada frase
se revisa: si salió claramente en el otro idioma, se vuelve a transcribir con Whisper en el idioma
elegido. Whisper se carga en segundo plano al empezar (la primera vez descarga ~1.6 GB) y es unas
2–3 veces más lento que el tiempo real en CPU, así que solo se usa para esas frases. En **Auto** no se
revisa nada.

**Audio sin pausas.** Una frase se manda a transcribir tras una pausa de *Pausa entre frases*. Si el
audio no se detiene (un podcast o un video con *Audio de la llamada*, o gente hablando encima), se
corta igual: en la primera pausa corta después de 15 s, o en el momento con menos voz al llegar a
25 s (`SOFT_CHUNK_SEC` / `MAX_CHUNK_SEC` en `main.py`). Así el texto sigue apareciendo en vivo.

> Sin audífonos, el micrófono también capta las bocinas y eso se mezcla con el audio del sistema,
> con un pequeño desfase (eco). Empeora mucho el reconocimiento: usa audífonos o desactiva
> *Audio de la llamada* si la llamada suena por las bocinas.

Para comparar motores sobre una grabación propia (velocidad, memoria y, si das un texto de
referencia corregido a mano, la tasa de error WER/CER):

```bat
.venv\Scripts\python bench_asr.py projects\miproyecto\recording_XXXX.wav --dur 300
```

## Versión anotada

Botón **Anotar**: Claude convierte la transcripción y las capturas en un documento HTML completo.
Conserva todo lo dicho, identifica a los hablantes por su nombre, agrupa por temas, marca lo
NUEVO / CONFIRMA / ACTUALIZA frente a lo que ya se sabía y coloca las capturas en su momento.
Cada pulsación crea una versión (v1, v2…): *Actualizar con lo nuevo*, *Rehacer completa* o
*Versión final* (al detener la grabación; también se copia a `docs\`).

## Minutas

Pestaña **Minuta**: genera la minuta formal de la reunión con Claude, en el idioma de la reunión.
Incluye:
- datos de la reunión y asistentes con su rol;
- objetivo y temas tratados;
- **decisiones**;
- **acuerdos y tareas** con responsable, fecha compromiso y estado;
- riesgos, temas abiertos y próximos pasos.

Cada punto cita el minuto `[HH:MM:SS]` en que se dijo.

- Usa la versión anotada si existe, porque ahí los hablantes ya tienen nombre. Si no existe, usa la
  transcripción, sin las líneas ocultas. También tiene en cuenta las notas del proyecto.
- No inventa datos: lo que no se dijo queda como "Por definir", y distingue una decisión de una
  simple propuesta.
- Cada pulsación crea una versión nueva (m1, m2…) en `sessions\<sesión>\minutes\`, con copia en
  `projects\<proyecto>\docs\`.
- Desde consola: `.venv\Scripts\python minutes.py <proyecto> latest`.

## Escenas automáticas

En vez de pulsar `Ctrl+Shift+S`, la pantalla se captura cada segundo y solo se guardan los momentos
que muestran algo nuevo. Se activa con **Escenas automáticas** antes de grabar, con el botón
**Auto** durante la grabación o con `--auto-frames 1` en consola.

- **Nivel 0 (píxeles):** solo cuenta una imagen cuando la pantalla se ha quedado quieta después de
  un cambio, así que las transiciones, el scroll o alguien tecleando no generan capturas. Detecta
  cuando se **vuelve** a una diapositiva anterior y cuando una diapositiva **se actualiza**.
- **Nivel 1 (CLIP):** clasifica cada escena (diapositiva, diagrama, código, web, documento…),
  descarta las caras de la webcam y junta las que son casi iguales. La primera vez descarga el
  modelo (~600 MB).
- **Detectar video** (en el menú de captura): si capturas una ventana entera de Teams o del
  navegador, encuentra el reproductor y recorta las escenas a él. El recorte también se puede
  ajustar a mano con el ratón sobre la vista previa.
- Pestaña **Escenas**: **Elegir** se queda con las N escenas de las que más se habló (frases como
  "como pueden ver" o "en esta diapositiva", y el tiempo en pantalla). Las visibles entran en la
  versión anotada como capturas.
- **Ocultar**: cada línea, captura o escena tiene un ojo para ocultarla; lo oculto no entra en la
  versión anotada. Otro botón oculta también todas las parecidas, con opción de deshacer.

Las escenas se guardan en `projects\<proyecto>\frames\<sesión>\`.

## Otras funciones

- **Pausar el micrófono** (botón *Pausa* / *Reanudar*) sin detener la grabación.
- **Continuar una sesión anterior**: se elige la sesión y se pulsa *Grabar*; lo nuevo se añade a la
  misma transcripción y la diarización se recalcula sobre todo.
- **Hablantes en modo *Detectar***: solo se agrupan los fragmentos de voz de al menos 1.5 s (los más
  cortos dan una "huella" poco fiable y se asignan al hablante más parecido). Un "hablante" con
  menos de 4 s de voz se une al más cercano, y los cambios de hablante de una o dos palabras entre
  dos turnos de la misma persona se corrigen. Para rehacerlo sobre una sesión ya grabada:
  `.venv\Scripts\python rediarize.py <proyecto> [--speakers N]` (escribe el resultado en `transcripts\rediarized\`, sin tocar la transcripción).
- **Notas del proyecto** (pestaña *Notas*): contexto y correcciones "se oyó → debe ser" que usan la
  versión anotada, la minuta, el chat y el reconocimiento en vivo.
- **Preguntar**: chat sobre la reunión mientras sigue la grabación.

## Ajustes útiles (en `main.py`)

| Parámetro | Valor | Nota |
|---|---|---|
| `MODEL_SIZE` | `large-v3-turbo` | Modelo de Whisper; en equipos lentos prueba `medium` o `small` |
| `DEVICE` / `COMPUTE_TYPE` | `cpu` / `int8` | Con GPU NVIDIA y CUDA: `cuda` / `float16` |
| `LANG_CONFIDENT` | `0.75` | Cuánta seguridad hace falta para cambiar de idioma en una frase (Whisper) |
| `OCR_LANGS` | `["es", "en"]` | Idiomas del OCR |

## Archivos

| Archivo | Para qué |
|---|---|
| `main.py` | Grabador: audio, detección de voz, transcripción, capturas, OCR, diarización |
| `audio_sources.py` | Micrófono + audio del sistema (WASAPI loopback) |
| `asr_engines.py` | Motores Parakeet / Whisper |
| `screenshot_helper.py` | Atajos globales, capturas por pantalla/área/ventana, escenas automáticas |
| `server.py` | Interfaz web (FastAPI + WebSocket) |
| `call_watcher.py` | Detección de llamadas e icono junto al reloj |
| `beautify.py` | Versión anotada con Claude Code |
| `minutes.py`, `minutes_api.py` | Minutas |
| `autoframes.py`, `frames_l1.py`, `video_detect.py`, `select_frames.py`, `similar.py` | Escenas automáticas |
| `speaker_id.py`, `rediarize.py` | Diarización y re-diarización |
| `web/` | Interfaz (HTML/CSS/JS sin compilación; `i18n.js` traduce al español) |
| `setup.bat`, `webui.bat`, `record.bat`, `callwatch.bat` | Instalar y lanzar |

<!-- upstream -->
## Traer las mejoras del proyecto original

Esta copia tiene el repositorio original como remoto `upstream` **solo de lectura** (el push está
deshabilitado). El trabajo propio vive en la rama `windows`.

```bat
git fetch upstream
git log --oneline windows..upstream/master      :: ¿qué hay nuevo?
git merge upstream/master                       :: integrarlo
```

Al integrar, revisa sobre todo:
- Cambios del helper de capturas original: hay que replicarlos en `screenshot_helper.py`.
  Los archivos para otras plataformas se descartan.
- Cosas específicas del ruso: idiomas, OCR, prompts.
<!-- /upstream -->

## Notas

- La captura de pantalla completa toma el monitor **principal**; para otro monitor usa "área" o "ventana".
- Las ventanas minimizadas no se pueden capturar: se hace captura de pantalla completa y se avisa en el log.
- Las coordenadas son píxeles físicos (el helper es DPI-aware), así que funciona con escalado 125 %/150 %.
- El modelo de diarización de SpeechBrain se copia en vez de enlazarse (los enlaces simbólicos
  en Windows necesitan permisos de administrador).

## Créditos

Basado en [raliev/audiotranscripts](https://github.com/raliev/audiotranscripts), adaptado y ampliado
para Windows en inglés y español.
