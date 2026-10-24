# Backlog

Actualizado: 2026-10-23. Plan de fondo: strawman «Reuniones con memoria» (privado):
https://claude.ai/artifact/NRbBAqBNPwE5cVe6112mEU

Estados: **[ ]** pendiente · **[~]** en curso · **[x]** hecho.

## Decisiones tomadas

| Fecha | Decisión |
|---|---|
| 2026-10-21 | Las reuniones van en un **vault privado aparte** (`C:\Users\villa\meetings-vault`) con su propio índice. El KB personal `alejandrovillap/kwnowledgebase` es **público** y nunca recibe minutas. |
| 2026-10-21 | Embeddings **locales** (CPU) para el índice de reuniones: el texto de clientes no sale del equipo. |
| 2026-10-21 | Las llamadas a Claude van por la **CLI `claude -p`** (suscripción), no por API key. |
| 2026-10-21 | Ideas elegidas del strawman: A, B, C, E, F y G. Estacionadas: D, H, I, J y K. |

## Roadmap

### Fase 1 · que la memoria exista (hecha)
- [x] **A** · Cada minuta terminada se archiva como nota del vault, con índices y commit git (`vault.py`, aa038c1).
- [x] **F** · `Glosario.md` por proyecto alimenta el reconocimiento, la versión anotada y la minuta (aa038c1).
- [x] Anotar, Minuta y Preguntar trabajan en la carpeta del proyecto en el vault si no hay otra configurada.

### Fase 2 · un índice, dos usos (siguiente)
- [ ] Índice semántico **local** de las notas del vault (minutas y glosario), actualizado al archivar cada minuta. Falta elegir el modelo multilingüe (es/en) que corra en CPU.
- [ ] **B** · Los avisos NEW / CONFIRMS / UPDATES buscan en ese índice y citan la reunión y la fecha en «Prior view». Desde 46b5352, Anotar usa por defecto la **generación directa** (sin herramientas): ya no lee el vault por su cuenta y solo recibe el glosario y las 2 minutas más recientes de `docs/`. B tiene que meter en su prompt los fragmentos que devuelva el índice (solo el modo agente sigue usando grep en el vault).
- [ ] **E** · «Pregúntale a todas tus reuniones»: Preguntar responde sobre todo el historial del proyecto, con citas a reunión y minuto.

### Fase 3 · que trabaje por ti
- [ ] **G** · `Estado.md` por proyecto que se reescribe tras cada minuta: decisiones vigentes, acuerdos abiertos, riesgos y próximos pasos.
- [ ] **C** · Briefing antes de la llamada: cuando el vigilante detecta una llamada, muestra en segundos el estado vivo (G) y lo más relacionado del índice.

### Estacionado
- **D** · Tablero de acuerdos que se actualiza solo.
- **H** · Notas a mano del Boox dentro de la sesión.
- **I** · Diagramas de las capturas a Mermaid.
- **J** · Personas y temas enlazados (grafo).
- **K** · Minutas en el feed RSS para e-ink.

## Open items

### Por validar con una reunión real
- [ ] Llamada real de punta a punta: el vigilante detecta la llamada, se graba, sale la minuta y se archiva en el vault.
- [ ] Diarización con varias personas reales. Los umbrales (`MERGE_CENTROID_DIST` 0.45, `MIN_SPEAKER_SEC` 4 s) se calibraron solo con 2 sesiones: una de 3 voces y una de 1.
- [ ] Glosario durante una grabación en vivo con Parakeet (con audio sintético sí funciona).

### Bugs y riesgos conocidos
- [ ] **Respaldo de Whisper lento al empezar**: la primera frase en el idioma equivocado frena la transcripción mientras Whisper termina de cargar (unos 80 s, o la descarga de 1.6 GB la primera vez). En CPU, Whisper va unas 2 veces más lento que el tiempo real.
- [ ] **Claude hereda el entorno**: si el servidor se lanza desde una sesión de Claude Code, el `claude` que abre usa las variables de esa sesión y falla con 401. Hoy se evita arrancando con un script que limpia `CLAUDE*`/`ANTHROPIC*`; conviene que `claude_cli.py` las quite solo cuando detecte que corre anidado.
- [ ] **Login de la CLI aparte**: la sesión de la app de escritorio no renueva la de `claude`. Si Anotar o Minuta dan 401, hay que correr `claude auth login`.
- [ ] **Eco sin audífonos**: el micrófono capta las bocinas y se mezcla con el audio del sistema. No hay cancelación de eco; solo está documentado.
- [ ] **Minuta que se sale de la plantilla**: el archivado lee la estructura HTML de `minutes_template.html`. Si el modelo cambia las clases, la nota se guarda igual, pero el frontmatter puede quedar sin decisiones o acuerdos. Falta un aviso cuando eso pase.
- [ ] **Interjecciones cortas**: el suavizado de turnos reasigna turnos de 1–2 palabras («Sí, claro») entre dos turnos de la misma persona. Es un compromiso aceptado; revisarlo si molesta en reuniones reales.
- [ ] **Corte forzado del VAD**: tras un corte duro, el conteo de silencio se reinicia y la frase puede tardar 1.5 s más en cerrarse. Es menor.

### Interfaz
- [ ] Textos sin traducir: «1 session», «New project…», «Speaking · …», «recording · sent after a … pause», «Transcript saved to …».

### Decisiones pendientes
- [ ] **Respaldo del vault**: hoy es un git local sin remoto, y si el disco falla se pierde todo. Opciones: un remoto privado, una copia cifrada o OneDrive (que sube los datos a la nube).
- [ ] **Modelo de embeddings** para la fase 2 (multilingüe, CPU, tamaño razonable).

### Limpieza
- [ ] Sesión fantasma `20261019_171338.diarized` en el proyecto `test`, creada por el `rediarize.py` anterior. Contiene la segunda grabación de prueba; decidir si se conserva.
- [ ] Borrar el worktree `C:\Users\villa\tnm-asr` y las ramas ya fusionadas `ui/fluent-look`, `fix/asr-quality` y `feat/meetings-vault`.
- [ ] Actualización menor de FastAPI disponible (0.149 → 0.157).

## Historial

| Fecha | Commit | Qué |
|---|---|---|
| 2026-10-23 | 46b5352 | Anotar en una sola generación directa (sin agente), pensamiento acotado a 4000 tokens |
| 2026-10-23 | b97dc04 | Descargas (transcripción, versión anotada con capturas incrustadas, minuta); errores de límite de Claude legibles |
| 2026-10-22 | 950c00a | Vigilante: aviso en el monitor del mouse, 2 min para responder, «Grabar esta llamada» y el servidor siempre arriba |
| 2026-10-21 | c858578 | Este backlog |
| 2026-10-21 | aa038c1 | Vault de reuniones: minuta → nota, glosario, `--vocab-correct` con Parakeet, hotwords completos |
| 2026-10-21 | 64768c2 · 043c09c · 07fa51b | Etiqueta «En vivo» traducida · puerto fijo 8766 · vigilante que sobrevive cuelgues |
| 2026-10-19 | 8ae163e | Revisión: salida de `rediarize.py` a subcarpeta, suavizado lineal |
| 2026-10-19 | 6ac9d14 · 7efebd1 | Reconocimiento: cortes forzados, idioma fijo con Parakeet, hablantes razonables |
| 2026-10-19 | 1d59ab4 | Look Fluent 2 / Microsoft 365 |
