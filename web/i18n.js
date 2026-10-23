// Windows build: Spanish interface without touching the author's app.js.
// Translates the UI's own texts after they render (exact strings + a few patterns), in text
// nodes and in title / placeholder / aria-label attributes. User content (transcript, chat,
// notes, OCR) is never translated. ES/EN switch next to the theme button; EN = original UI.
window.I18N = (() => {
  let lang = 'es';
  try { lang = localStorage.getItem('vr-lang') || 'es'; } catch (_) { /* */ }

  const D = {
    // top bar, panels, tabs
    'studio': 'studio', 'Project': 'Proyecto', 'Session': 'Sesión', 'Capture': 'Captura', 'Full screen': 'Pantalla completa',
    'Toggle theme': 'Cambiar tema', 'Settings': 'Configuración', 'Projects': 'Proyectos', 'Sessions': 'Sesiones',
    'New project…': 'Nuevo proyecto…', 'New project': 'Nuevo proyecto', 'New session': 'Nueva sesión',
    'Transcript': 'Transcripción', 'Beautified': 'Anotada', 'Minutes': 'Minuta', 'Screenshots': 'Capturas',
    'Frames': 'Escenas', 'Ask': 'Preguntar', 'Notes': 'Notas', 'Connecting…': 'Conectando…', 'Connected': 'Conectado',
    'Disconnected from server — retrying…': 'Sin conexión con el servidor — reintentando…',
    'Automatic screen frames': 'Escenas automáticas de pantalla', '↓ Jump to live': '↓ Ir a lo último',
    'Find in transcript': 'Buscar en la transcripción', 'Recognizer console': 'Consola del reconocedor',
    // control strip
    'Start recording': 'Grabar', 'Starts a new session': 'Empieza una sesión nueva', 'Appends to': 'Continúa',
    'new session instead': 'o empezar una nueva', 'Engine': 'Motor', 'Language': 'Idioma', 'Speakers': 'Hablantes',
    'Auto-stop': 'Detener tras', 'Phrase gap': 'Pausa entre frases', 'Save audio': 'Guardar audio',
    'Call audio': 'Audio de la llamada', 'Auto frames': 'Escenas automáticas', 'Detect': 'Detectar', 'Off': 'No',
    'Speech recognition model': 'Modelo de reconocimiento de voz', 'Speech recognition engine': 'Motor de reconocimiento',
    'Detect English/Spanish per phrase': 'Detectar inglés/español en cada frase', 'English only': 'Solo inglés',
    'Spanish only': 'Solo español',
    'Also record what you hear: the other participants of a call when you use a headset (Windows)':
      'Graba también lo que escuchas: los demás participantes de una llamada cuando usas audífonos',
    'Capture the screen every second and keep the frames that show something new':
      'Captura la pantalla cada segundo y guarda las escenas que muestran algo nuevo',
    'How long a silence ends a phrase. Shorter: text appears sooner, in smaller pieces. Longer: fewer, longer chunks. Can be changed while recording.':
      'Cuánto silencio cierra una frase. Menos: el texto aparece antes, en trozos pequeños. Más: menos trozos y más largos. Se puede cambiar mientras grabas.',
    'Starting recording…': 'Iniciando grabación…', 'Preparing the recognizer': 'Preparando el reconocedor',
    'Launching…': 'Iniciando…', 'Stopping…': 'Deteniendo…', 'Finishing the last phrase': 'Terminando la última frase',
    'Finalizing session': 'Cerrando la sesión', 'Transcribing the remaining speech': 'Transcribiendo lo que falta',
    'Stop recording': 'Detener', 'Stop (same as Ctrl+C)': 'Detener (igual que Ctrl+C)', 'Pause': 'Pausa', 'Resume': 'Reanudar',
    'Mute the microphone; the session keeps running': 'Silencia el micrófono; la sesión sigue',
    'Unmute the microphone and continue': 'Activa el micrófono y continúa', 'PAUSED': 'EN PAUSA', 'REC': 'GRAB',
    'Snap': 'Captura', 'Take a screenshot of the capture area': 'Captura el área configurada',
    'Take a screenshot now': 'Tomar una captura ahora', 'Detected language': 'Idioma detectado',
    'SPEAKERS': 'HABLANTES', 'Speaker diarization runs after the session': 'Los hablantes se asignan al terminar la sesión',
    'Recording in': 'Grabando en', 'Stop automatic frames': 'Detener escenas automáticas', 'Show console': 'Ver consola',
    'Dismiss': 'Cerrar', 'Recognizer error': 'Error del reconocedor', 'Listening': 'Escuchando', 'Listening…': 'Escuchando…',
    'Paused': 'En pausa', 'Up to date': 'Al día', 'waiting for speech': 'esperando voz',
    'microphone muted · session continues': 'micrófono silenciado · la sesión sigue',
    'microphone is muted · press Resume to continue': 'el micrófono está silenciado · pulsa Reanudar para seguir',
    'Whisper is working through this chunk; text appears as it is recognized.': 'Se está transcribiendo este fragmento; el texto aparece a medida que se reconoce.',
    'all speech transcribed': 'todo transcrito',
    // empty states
    'Create a project to begin': 'Crea un proyecto para empezar',
    'Projects keep transcripts, screenshots and documents together.': 'Cada proyecto agrupa transcripciones, capturas y documentos.',
    'Ready when you are': 'Todo listo', 'Choose what screenshots should capture (top right), then press': 'Elige qué deben capturar las capturas (arriba a la derecha) y pulsa',
    '. Speech appears here as it is recognized; press': '. El texto aparece aquí a medida que se reconoce; pulsa',
    'or': 'o', 'to capture the screen.': 'para capturar la pantalla.',
    'No screenshots yet': 'Aún no hay capturas', 'Press': 'Pulsa',
    "while recording. Each screenshot is OCR'd and used by Beautify to fix names and identify speakers.":
      'mientras grabas. Se extrae el texto de cada captura y se usa para corregir nombres e identificar hablantes.',
    'No beautified version yet': 'Aún no hay versión anotada', 'No minutes yet': 'Aún no hay minuta',
    'No recordings yet': 'Aún no hay grabaciones', 'No auto frames': 'No hay escenas automáticas',
    'No preview yet': 'Aún no hay vista previa', 'No windows found': 'No se encontraron ventanas',
    'Beautify now': 'Generar versión anotada', 'Write minutes': 'Escribir minuta', 'New minutes': 'Nueva minuta',
    'Write the formal minutes of this meeting': 'Escribe la minuta formal de esta reunión',
    'A formal minute in the language of the meeting: attendees, topics, decisions, action items with owner and due date, risks, open questions and next steps. It uses the beautified version when there is one, otherwise the transcript.':
      'Una minuta formal en el idioma de la reunión: asistentes, temas, decisiones, acuerdos con responsable y fecha, riesgos, temas abiertos y próximos pasos. Usa la versión anotada si existe; si no, la transcripción.',
    'Beautify turns the raw transcript and screenshots into a full annotated document: every utterance kept, real speakers identified, callouts compared with what you already know.':
      'Convierte la transcripción y las capturas en un documento anotado completo: se conserva todo lo dicho, se identifica a los hablantes y se compara con lo que ya sabías.',
    // beautify
    'Beautify': 'Anotar', 'More options': 'Más opciones', 'Beautification finished': 'Versión anotada lista',
    'Final version ready': 'Versión final lista', 'Open the beautified version': 'Abrir la versión anotada',
    'Update with new material': 'Actualizar con lo nuevo', 'Full rebuild': 'Rehacer completa', 'Final version': 'Versión final',
    'Regenerates the whole document from the transcript': 'Regenera todo el documento desde la transcripción',
    'Available after the recording stops (runs automatically)': 'Disponible al detener la grabación',
    'Definitive document with every speaker identified; also saved to docs/': 'Documento definitivo con todos los hablantes identificados; también se guarda en docs/',
    'Open': 'Abrir', 'docs/ copy': 'copia en docs/', "Copy saved in the project's docs/ folder": 'Copia guardada en la carpeta docs/ del proyecto',
    'Cancel': 'Cancelar', 'Close': 'Cerrar', 'Save': 'Guardar', 'Delete': 'Borrar', 'Not now': 'Ahora no',
    'Recording finished': 'Grabación terminada', 'Build final version': 'Generar versión final',
    'Hidden lines, screenshots and frames are left out. You can always start it later with': 'Las líneas, capturas y escenas ocultas no se incluyen. Puedes generarla después con',
    'Build the final beautified version of': 'Generar ahora la versión final anotada de', 'now?': '?',
    'Finished': 'Terminado', 'Failed': 'Falló', 'Cancelled': 'Cancelado', 'Reading the transcript': 'Leyendo la transcripción',
    'Studying the style reference': 'Estudiando la referencia de estilo', 'Studying the minutes template': 'Estudiando la plantilla de minuta',
    'Rebuilding the full document': 'Rehaciendo el documento completo', 'Updating the previous version': 'Actualizando la versión anterior',
    'Building the final version': 'Generando la versión final', 'Writing document…': 'Escribiendo el documento…',
    'Editing document…': 'Editando el documento…', 'Editing document': 'Editando el documento', 'Reading…': 'Leyendo…',
    'Planning': 'Planificando', 'Searching project knowledge…': 'Buscando en el conocimiento del proyecto…',
    // chat / notes
    'Ask about this session': 'Pregunta sobre esta sesión', 'Ask about the transcript…': 'Pregunta sobre la transcripción…',
    'Send (Enter)': 'Enviar (Enter)', 'New chat': 'Nueva conversación', 'Start a new conversation': 'Empezar una conversación nueva',
    'Recording keeps running while you chat.': 'La grabación sigue mientras conversas.',
    'Chat needs the Claude Code CLI (claude) installed': 'El chat necesita Claude Code (claude) instalado',
    'Sent with every': 'Se envían con cada', 'and': 'y',
    "request in this project: who's who, the client, what was wrong last time.": 'solicitud de este proyecto: quién es quién, el cliente, qué salió mal la última vez.',
    'Select text in the transcript': 'Selecciona texto en la transcripción',
    'to add a correction here. Corrections also guide live recognition.': 'para añadir una corrección aquí. Las correcciones también guían el reconocimiento en vivo.',
    'Correct the transcript': 'Corregir la transcripción', 'Heard': 'Se oyó', 'Should be': 'Debe ser', 'Comment': 'Comentario',
    '(optional)': '(opcional)', 'Add to notes': 'Añadir a notas', 'e.g. product name, person\'s name': 'p. ej. nombre de producto o de persona',
    'Saved to Notes as a rule, so Beautify and Ask apply it everywhere, and live recognition uses it as a hint.':
      'Se guarda en Notas como regla: la versión anotada y el chat la aplican en todo y el reconocimiento en vivo la usa como pista.',
    'save ·': 'guardar ·', 'close': 'cerrar',
    // screenshots, frames, capture
    'Screenshot': 'Captura', 'Selected text': 'Texto seleccionado', 'Delete screenshot': 'Borrar captura',
    'Delete this screenshot?': '¿Borrar esta captura?', 'Screenshot deleted · kept in screenshots/.trash': 'Captura borrada · se guarda en screenshots/.trash',
    'Show in the transcript': 'Ver en la transcripción', 'Loading OCR text…': 'Cargando texto OCR…',
    'Best': 'Mejores', 'Picked': 'Elegidas', 'Pick': 'Elegir', 'Visible frames go to Beautify as screenshots, at their place in the transcript':
      'Las escenas visibles entran en la versión anotada como capturas, en su lugar de la transcripción',
    'Keep the N frames with the most said about them (pointing phrases, time on screen) and hide the rest. The eye brings any frame back.':
      'Conserva las N escenas de las que más se habló (frases como "como pueden ver", tiempo en pantalla) y oculta el resto. El ojo recupera cualquiera.',
    'Changed while on screen; the card shows the last state': 'Cambió mientras estaba en pantalla; la tarjeta muestra el último estado',
    'Looking for similar pictures…': 'Buscando imágenes parecidas…',
    'What gets captured when you take a screenshot': 'Qué se captura al tomar una captura', 'Screenshot area': 'Área de captura',
    'main display': 'pantalla principal', 'drag to select': 'arrastra para elegir', 'click a window': 'haz clic en una ventana',
    'Choose from window list': 'Elegir de la lista de ventanas', 'Loading windows…': 'Cargando ventanas…',
    'Refresh preview': 'Actualizar vista previa', 'Detect video': 'Detectar video', 'Detect again': 'Detectar de nuevo',
    'Set by hand': 'Ajustar a mano', 'Use whole area': 'Usar toda el área', 'the framed part only': 'solo la parte enmarcada',
    'Auto frames will keep only the video area': 'Las escenas automáticas guardarán solo el área del video',
    'Drag the frame onto the video': 'Arrastra el marco sobre el video', 'Drag to move · drag an edge or corner to resize':
      'Arrastra para mover · arrastra un borde o esquina para cambiar el tamaño', 'Selection cancelled': 'Selección cancelada',
    // settings
    'Knowledge folder': 'Carpeta de conocimiento', 'Style reference': 'Referencia de estilo',
    'Built-in annotated-transcript style': 'Estilo integrado de transcripción anotada',
    'An HTML document whose look to copy (style only, never its content).': 'Un documento HTML cuyo aspecto copiar (solo el estilo, nunca el contenido).',
    'Context and corrections live in the': 'El contexto y las correcciones están en la pestaña', 'in the project folder).': 'en la carpeta del proyecto).',
    'tab (': '(', 'AI': 'IA', 'Beautify engine': 'Motor para la versión anotada', 'Beautify model': 'Modelo para la versión anotada',
    'Chat model': 'Modelo del chat', 'Claude Code (recommended)': 'Claude Code (recomendado)',
    'Gemini / OpenAI API (single pass, no tools)': 'API de Gemini / OpenAI (una pasada, sin herramientas)',
    'Offer to build the final version when recording stops': 'Ofrecer la versión final al detener la grabación',
    'Meetings vault': 'Vault de reuniones',
    'Private folder where every finished minute is archived as a Markdown note, with a glossary per project. Leave it empty to turn it off.':
      'Carpeta privada donde cada minuta terminada se archiva como nota Markdown, con un glosario por proyecto. Déjala vacía para desactivarlo.',
    'Recognizer': 'Reconocedor', 'Python': 'Python', 'Same as the server': 'El mismo que el servidor', 'Check': 'Comprobar',
    'The interpreter that has faster-whisper, torch and easyocr installed.': 'El intérprete que tiene faster-whisper, torch y easyocr instalados.',
    'Microphone': 'Micrófono', 'Windows default microphone': 'Micrófono predeterminado de Windows',
    'Call audio from': 'Audio de la llamada desde', 'All outputs (headset, speakers, monitor)': 'Todas las salidas (audífonos, bocinas, monitor)',
    'Windows default output': 'Salida predeterminada de Windows',
    'Smart filter (CLIP): drop webcam faces, merge look-alike frames': 'Filtro inteligente (CLIP): descarta caras de webcam y junta escenas parecidas',
    'Applies to the next recording. Off = pixel comparison only.': 'Se aplica a la próxima grabación. Apagado = solo comparación de píxeles.',
    'Settings saved': 'Configuración guardada', '(claude CLI not found)': '(no se encontró claude)',
    'Claude Code CLI not found': 'No se encontró Claude Code', 'Name': 'Nombre', 'Create project': 'Crear proyecto',
    'e.g. acme-discovery': 'p. ej. acme-descubrimiento',
    'Each project keeps its own transcripts, screenshots and documents in': 'Cada proyecto guarda sus transcripciones, capturas y documentos en',
    'Recording finished · transcript saved': 'Grabación terminada · transcripción guardada',
    'Next recording starts a fresh transcript': 'La próxima grabación empieza una transcripción nueva',
    'Recording continues in the background.': 'La grabación sigue en segundo plano.', 'Meeting minutes': 'Minuta de reunión',
    'turns': 'turnos', 'callouts': 'anotaciones', 'screenshots': 'capturas', 'covers': 'cubre', 'updated': 'actualizada',
    'decision': 'decisión', 'decisions': 'decisiones', 'action item': 'acuerdo', 'action items': 'acuerdos', 'risk': 'riesgo', 'risks': 'riesgos',
    'min': 'min', 'FINAL': 'FINAL', 'just now': 'ahora',
    // settings: subtitle and helps split around <b> tags
    'Beautify and chat run through Claude Code, so they can view screenshots and use your MCP servers.':
      'La versión anotada, la minuta y el chat usan Claude Code, así que pueden ver las capturas y usar tus servidores MCP.',
    'Claude Code runs in this folder, so the MCP servers configured there (e.g.': 'Claude Code se ejecuta en esta carpeta, así que los servidores MCP configurados ahí (p. ej.',
    ') power the NEW / CONFIRMS / UPDATES callouts.': ') alimentan las anotaciones NUEVO / CONFIRMA / ACTUALIZA.',
    'With': 'Con',
    'on, what plays here is recorded together with the microphone, so the other participants are transcribed even with a headset.':
      'activado, lo que suena aquí se graba junto con el micrófono, así se transcribe a los demás participantes aunque uses audífonos.',
    'Parakeet v3 (EN+ES, fast)': 'Parakeet v3 (EN+ES, rápido)', 'Whisper turbo': 'Whisper turbo',
    'Hide this line and the ones like it (same or nearly the same words)': 'Ocultar esta línea y las parecidas (mismas o casi mismas palabras)',
    'Hide this line: it stays here, faded, but Beautify leaves it out': 'Ocultar esta línea: se queda aquí atenuada, pero no entra en la versión anotada',
    'Switch the interface to English': 'Cambiar la interfaz a inglés',
    // frames / ask empty states
    'Turn on': 'Activa',
    'before or during a recording. The screen is captured every second; only frames that show something new and stay still are kept, talking heads and transitions are skipped.':
      'antes o durante una grabación. La pantalla se captura cada segundo; solo se guardan las escenas que muestran algo nuevo y se quedan quietas; se descartan caras y transiciones.',
    'Try': 'Prueba',
    'Summarize the discussion so far': 'Resume lo conversado hasta ahora', 'List decisions and action items': 'Lista las decisiones y los acuerdos',
    'Who is speaking, and what are their roles?': '¿Quién habla y qué rol tiene cada uno?',
    'What open questions should I follow up on?': '¿Qué temas abiertos debo dar seguimiento?',
    'Ask anything about this transcript.': 'Pregunta lo que quieras sobre esta transcripción.',
    // Windows build (windows.js)
    'Options': 'Opciones', 'Recording': 'Grabación', 'Audio': 'Audio',
    'Download': 'Descargar', 'Not generated yet': 'Aún no generada', 'Annotated version': 'Versión anotada',
    'Download the transcript, the annotated version or the minutes': 'Descargar la transcripción, la versión anotada o la minuta',
    'Plain text (.txt), every line with its time': 'Texto (.txt), cada línea con su hora',
    'HTML with the screenshots inside; opens in any browser': 'HTML con las capturas incluidas; se abre en cualquier navegador',
    'HTML; open it and print or save as PDF / Word': 'HTML; ábrela e imprímela o guárdala como PDF / Word',
  };
  const MON = { Jan: 'ene', Feb: 'feb', Mar: 'mar', Apr: 'abr', May: 'may', Jun: 'jun', Jul: 'jul', Aug: 'ago', Sep: 'sept', Oct: 'oct', Nov: 'nov', Dec: 'dic' };
  // Claude API errors (Beautify / minutes / chat) arrive as raw JSON; say what happened.
  const limitMsg = d => `Se alcanzó el límite de uso de tu cuenta de Claude${d ? `; se renueva el ${d}` : ''}. Súbelo en console.anthropic.com o espera a esa fecha.`;
  const P = [
    [/^API Error: \d+\b(?=[\s\S]*?(?:usage limits|spend limit))(?:[\s\S]*?(\d{4}-\d{2}-\d{2}))?[\s\S]*$/, (m, d) => limitMsg(d)],
    [/^API Error: \d+\b[\s\S]*?Credit balance is too low[\s\S]*$/, () => 'Tu cuenta de Claude no tiene crédito. Recárgala y vuelve a generar.'],
    [/^API Error: (?:400|429) \{"type":"error"[\s\S]*$/, () => 'Error de la cuenta de Claude (límite de uso o crédito). Pasa el ratón para ver el detalle.'],
    [/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})( · \d{2}:\d{2})?$/, (m, a, d, t) => `${d} ${MON[a]}${t || ''}`],
    [/^(\d+) min ago$/, (m, n) => `hace ${n} min`],
    [/^Live · (.*)$/, (m, d) => `En vivo · ${tr(d)}`],
    [/^of (\d{2}:\d{2}:\d{2}–\d{2}:\d{2}:\d{2})$/, (m, r) => `de ${r}`],
    [/^Answers come from the (live|saved) transcript( and its screenshots)?\.$/, (m, k, sh) => `Las respuestas salen de la transcripción ${k === 'live' ? 'en vivo' : 'guardada'}${sh ? ' y sus capturas' : ''}.`],
    [/^(.*) detects the language by itself$/, (m, e) => `${tr(e)} detecta el idioma por sí solo`],
    [/^(\d+) lines?$/, (m, n) => `${n} líneas`],
    [/^v(\d+) · (.*) · (\d+) turns$/, (m, v, t, n) => `v${v} · ${tr(t)} · ${n} turnos`],
    [/^m(\d+) · (.*)$/, (m, v, t) => `m${v} · ${tr(t)}`],
    [/^Beautifying v(\d+) · (.*)$/, (m, v, k) => `Anotando v${v} · ${tr(k)}`],
    [/^Writing minutes m(\d+)(.*)$/, (m, v, rest) => `Escribiendo minuta m${v}${rest.replace(' from the raw transcript', ' desde la transcripción').replace(/ from the beautified version v(\d+)/, ' desde la versión anotada v$1')}`],
    [/^from the (raw transcript|beautified version v\d+)(.*)$/, (m, s, rest) => `desde ${s === 'raw transcript' ? 'la transcripción' : s.replace('beautified version', 'la versión anotada')}${rest.replace(' transcript lines', ' líneas')}`],
    [/^Writing document \((.*)\)$/, (m, k) => `Escribiendo el documento (${k})`],
    [/^Looking at (.*)$/, (m, f) => `Mirando ${f}`], [/^Reading (.*)$/, (m, f) => `Leyendo ${f}`],
    [/^Reading beautified v(\d+)$/, (m, v) => `Leyendo la versión anotada v${v}`],
    [/^Claude \((.*)\) started(.*)$/, (m, k, rest) => `Claude (${k}) iniciado${rest.replace(' · project knowledge connected', ' · conocimiento del proyecto conectado')}`],
    [/^Searching files: (.*)$/, (m, q) => `Buscando archivos: ${q}`],
    [/^Beautification finished · v(\d+)(.*)$/, (m, v, f) => `Versión anotada lista · v${v}${f.replace('(final)', '(final)')}`],
    [/^Beautify failed: ([\s\S]*)$/, (m, e) => `Falló la versión anotada: ${tr(e)}`],
    [/^Minutes ready(.*)$/, (m, rest) => `Minuta lista${rest}`], [/^Minutes failed: ([\s\S]*)$/, (m, e) => `Falló la minuta: ${tr(e)}`],
    [/^⚠ v(\d+) failed$/, (m, v) => `⚠ falló v${v}`],
    [/^⚠ m(\d+) failed: ([\s\S]*)$/, (m, v, e) => `⚠ falló m${v}: ${tr(e)}`], [/^(\d+) to write$/, (m, d) => `${d} en escribir`],
    [/^(\d+:\d{2}) to (write|generate)$/, (m, d) => `${d} en generarse`],
    [/^Windows defaults now: (.*)$/, (m, d) => `Predeterminados ahora: ${d}`],
    [/^(\d+) of (\d+) frames go to Beautify$/, (m, a, b) => `${a} de ${b} escenas van a la versión anotada`],
    [/^Recording finished · (.*)$/, (m, r) => `Grabación terminada · ${r}`],
  ];
  // user content: never translated
  const SKIP = '#transcript .body, .msg .md, .msg.user, .sel-quote, textarea, pre, code, .ocr, .ocr-text, .lb-ocr, .chunk-text, .mi-title, #lastLog, .console, iframe';

  function tr(s) {
    const t = s.trim();
    if (!t) return s;
    let out = Object.prototype.hasOwnProperty.call(D, t) ? D[t] : null;
    if (out == null) for (const [re, fn] of P) { if (re.test(t)) { out = t.replace(re, fn); break; } }
    if (out == null || out === t) return s;
    return s.replace(t, out);
  }

  function skip(node) {
    const el = node.nodeType === 1 ? node : node.parentElement;
    return !el || !!el.closest(SKIP);
  }

  function translateNode(root) {
    if (root.nodeType === 3) {
      if (!skip(root)) { const v = tr(root.data); if (v !== root.data) root.data = v; }
      return;
    }
    if (root.nodeType !== 1 || skip(root)) return;
    for (const a of ['title', 'placeholder', 'aria-label', 'data-q']) {
      const els = root.matches(`[${a}]`) ? [root, ...root.querySelectorAll(`[${a}]`)] : root.querySelectorAll(`[${a}]`);
      for (const el of els) { if (skip(el)) continue; const v = el.getAttribute(a), t = tr(v); if (t !== v) el.setAttribute(a, t); }
    }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (w.nextNode()) nodes.push(w.currentNode);
    for (const n of nodes) if (!skip(n)) { const v = tr(n.data); if (v !== n.data) n.data = v; }
  }

  function start() {
    if (lang !== 'es') return;
    document.documentElement.lang = 'es';
    translateNode(document.body);
    new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === 'characterData') translateNode(m.target);
        else if (m.type === 'attributes') translateNode(m.target);
        else m.addedNodes.forEach(translateNode);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true,
                                attributeFilter: ['title', 'placeholder', 'aria-label', 'data-q'] });
  }

  function setLang(l) {
    try { localStorage.setItem('vr-lang', l); } catch (_) { /* */ }
    location.reload();
  }

  return { get lang() { return lang; }, tr: s => (lang === 'es' ? tr(s) : s), start, setLang };
})();
