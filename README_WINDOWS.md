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

Abre http://127.0.0.1:8765. Elige el idioma (Auto / EN / ES), el área de captura (pantalla completa,
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

## Ajustes útiles (en `main.py`)

| Parámetro | Valor | Nota |
|---|---|---|
| `MODEL_SIZE` | `large-v3-turbo` | En equipos lentos prueba `medium` o `small` |
| `DEVICE` / `COMPUTE_TYPE` | `cpu` / `int8` | Con GPU NVIDIA y CUDA: `cuda` / `float16` |
| `LANG_CONFIDENT` | `0.75` | Cuánta seguridad hace falta para cambiar de idioma en una frase |
| `OCR_LANGS` | `["es", "en"]` | Idiomas del OCR |

## Notas

- La captura de pantalla completa toma el monitor **principal**; para otro monitor usa "área" o "ventana".
- Las ventanas minimizadas no se pueden capturar: se hace captura de pantalla completa y se avisa en el log.
- Las coordenadas son píxeles físicos (el helper es DPI-aware), así que funciona con escalado 125 %/150 %.
- El modelo de diarización de SpeechBrain se copia en vez de enlazarse (los enlaces simbólicos
  en Windows necesitan permisos de administrador).
