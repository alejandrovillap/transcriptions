# voice-rec en Windows (inglés y español)

Port local de [raliev/audiotranscripts](https://github.com/raliev/audiotranscripts) para Windows 10/11,
con reconocimiento en **inglés y español**. El README original sigue valiendo para todo lo demás
(arquitectura, Beautify, re-diarización); aquí solo lo que cambia.

## Qué cambia respecto al original

| Original (macOS, ruso/inglés) | Este port (Windows, inglés/español) |
|---|---|
| `screenshot_helper.swift` (binario nativo) | `screenshot_helper.py` (Python + ctypes + Pillow + tkinter), mismos modos y mismo protocolo por stdout |
| Idiomas `ru` / `en`, detección por proporción de cirílico | Idiomas `en` / `es`. En `auto` se detecta **cada frase** (puedes mezclar idiomas en la misma sesión); frases cortas y ambiguas heredan el idioma anterior. Si Whisper adivina otro idioma (pt, it, gl…) se re-transcribe en en/es |
| OCR EasyOCR `ru` + `en` | OCR EasyOCR `es` + `en` |
| El servidor detiene la grabación con SIGINT | En Windows la detiene con el comando `STOP` por stdin (mismo efecto que Ctrl+C) |
| `webui.sh`, `compile.sh` | `setup.bat`, `webui.bat`, `record.bat` |
| Prompts de `process_transcript.py` en ruso | En inglés, con la regla de conservar el idioma original sin traducir |

## Instalación (una vez)

Requisitos: Python 3.10+ (probado con 3.13), unos 5 GB libres (torch CPU, modelos de Whisper y OCR).
Opcional: [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) en el PATH para guardar el audio en MP3
(sin él se guarda en WAV).

```bat
setup.bat
```

Crea `.venv\` e instala todo (torch en versión CPU). La primera grabación descarga los modelos:
Whisper `large-v3-turbo` (~1.6 GB), Silero VAD y EasyOCR.

## Uso

**Interfaz web** (recomendado):

```bat
webui.bat
```

Abre http://127.0.0.1:8765, o el siguiente puerto libre si Windows tiene reservado el 8765
(la consola muestra la dirección real). En PowerShell, desde la carpeta, escribe `.\webui.bat`. Elige el idioma (Auto / EN / ES), el área de captura (pantalla completa,
un área o una ventana; la ventana se captura aunque esté tapada por otras) y pulsa *Start recording*.

**Consola**:

```bat
record.bat miproyecto                     :: auto (inglés/español por frase), 180 min máx.
record.bat miproyecto 60 --lang es        :: solo español, auto-stop a los 60 min
record.bat miproyecto --speakers 2        :: con diarización (2 hablantes)
record.bat miproyecto --save-audio
```

Durante la grabación:
- `Ctrl+Shift+S`: captura de pantalla (con OCR)
- `Ctrl+Shift+W`: captura el texto seleccionado (simula Ctrl+C y lee el portapapeles)
- `Ctrl+C`: detener (en consola)

Los atajos son globales: mientras graba, `Ctrl+Shift+S` / `Ctrl+Shift+W` no llegan a otras apps
(por ejemplo, Ctrl+Shift+W ya no cierra la ventana del navegador). Si otra app ya los tiene
registrados, el helper lo indica en el log.

**Beautify** necesita el CLI de Claude Code (`claude`) en el PATH. **Ask** también.

## Motores de reconocimiento

Se eligen en la interfaz (**Engine**) o con `--engine` en consola:

| Motor | Idiomas | Notas |
|---|---|---|
| `whisper` (por defecto) | Inglés/español, detectado en cada frase; o fijo con `--lang en/es` | Admite *hotwords*: los términos de tus notas ayudan al reconocimiento. Es el más lento en CPU |
| `parakeet` | Detecta el idioma él mismo (25 idiomas europeos, incluidos inglés y español) | Mucho más rápido en CPU y con puntuación. No admite *hotwords*: usa `--vocab-correct` para aplicar a la salida las correcciones de tus notas |

```bat
record.bat miproyecto --engine parakeet --vocab-file projects\miproyecto\notes.md --vocab-correct
```

Para comparar motores sobre una grabación propia (velocidad, memoria y, si das un texto de
referencia corregido a mano, la tasa de error WER/CER):

```bat
.venv\Scripts\python bench_asr.py projects\miproyecto\recording_XXXX.wav --dur 300
```

(GigaAM, el tercer motor del autor, solo reconoce ruso y está desactivado en esta versión.)

## Otras funciones de esta versión

- **Pausar el micrófono** (botón *Pause* / *Resume*) sin detener la grabación: el tiempo sigue
  corriendo y no se transcribe nada mientras está en pausa.
- **Continuar una sesión anterior**: se elige la sesión y se pulsa *Start recording*; lo nuevo se
  añade a la misma transcripción y la diarización se recalcula sobre todo.

## Ajustes útiles (en `main.py`)

| Parámetro | Valor | Nota |
|---|---|---|
| `MODEL_SIZE` | `large-v3-turbo` | En equipos lentos prueba `medium` o `small` |
| `DEVICE` / `COMPUTE_TYPE` | `cpu` / `int8` | Con GPU NVIDIA y CUDA: `cuda` / `float16` |
| `LANG_CONFIDENT` | `0.75` | Cuánta seguridad hace falta para cambiar de idioma en una frase |
| `OCR_LANGS` | `["es", "en"]` | Idiomas del OCR |

## Traer las mejoras del autor

La copia tiene el repo original como remoto `upstream` **solo de lectura**: el push está deshabilitado,
así que desde aquí no se puede modificar el repo del autor. Tus cambios viven en la rama local `windows`.

```bat
git fetch upstream
git log --oneline windows..upstream/master      :: ¿qué hay nuevo?
git merge upstream/master                       :: integrarlo
```

Al integrar, revisa sobre todo:
- Cambios en `screenshot_helper.swift`: hay que replicarlos en `screenshot_helper.py`, que es el helper de Windows.
- Cosas específicas del ruso: idiomas, OCR, prompts.
- Llamadas que solo existen en macOS/Linux: SIGINT, `start_new_session`, rutas.

El autor también desarrolla en ramas que todavía no ha integrado en `master` (`asr-engines`,
`auto-screenshots`); se ven con `git branch -r`.

## Notas

- La captura de pantalla completa toma el monitor **principal**; para otro monitor usa "área" o "ventana".
- Las ventanas minimizadas no se pueden capturar: se hace captura de pantalla completa y se avisa en el log.
- Las coordenadas son píxeles físicos (el helper es DPI-aware), así que funciona con escalado 125 %/150 %.
- El modelo de diarización de SpeechBrain se copia en vez de enlazarse (los enlaces simbólicos
  en Windows necesitan permisos de administrador).
