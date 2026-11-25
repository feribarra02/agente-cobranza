# Declaración de Uso de Inteligencia Artificial (Bitácora de Desarrollo)

## 1. Herramientas de IA utilizadas
- **Asistente de ingeniería de software (Antigravity / modelo Gemini):** Apoyo en diseño de arquitectura híbrida, implementación de módulos Python (`src/`), suite de pruebas (`tests/`) y documentación técnica (`README.md`).
- **Google Gemini API (`google-genai`, modelo `gemini-3.5-flash-lite`):** Utilizada en tiempo de ejecución únicamente para dos tareas acotadas con *Structured Outputs* (Pydantic):
  1. **Rol 1 (`src/note_analyzer.py`):** Extracción semántica de señales en texto libre (`notas`) con `temperature=0.0` y esquema `AnalisisNotas`.
  2. **Rol 2 (`src/gemini_agent.py`):** Redacción de borradores SMS/WhatsApp (`DraftRecordatorio`) con `temperature=0.2`, sujeta a guardrails determinísticos y fallback a templates aprobados.
- **Gemini 4 Neon Web:** Ayuda con el diseño del prompt para el agente de programación.

## 2. Decisiones y correcciones humanas aplicadas
1. **Arquitectura híbrida (IA fuera del cálculo numérico y reglas):** Se restringió el uso de LLM exclusivamente a comprensión de lenguaje natural y redacción; toda normalización de fechas/montos, conteo de días, conciliación de historial y cascada `P1 → P2 → P3 → P4` se implementó de forma 100% determinística en Python para evitar alucinaciones financieras.
2. **Consolidación de registros sucios en `cuentas.csv`:** Durante la inspección manual de los 63 renglones de `data/cuentas.csv` frente a las 60 cuentas únicas de `data/historial_pagos.csv`, se identificaron 3 duplicados (`C-1006` duplicado con monto redondeado `6976` vs `$6,975.85`, `C-1052` duplicado con nombre abreviado `Gabriela L. Ramírez`, y `C-1061` clonando a `C-1017` sin historial propio). Se decidió consolidarlos en `normalizer.py` conservando el registro más preciso y dejando traza auditable.
3. **Resiliencia ante fechas corruptas (`C-1033`):** Se detectó `2026-11-31` en `C-1033` (noviembre tiene 30 días) y formatos `MM/DD/YYYY` mezclados (`10/27/2026`, `09/30/2026`). Se ajustó `normalizer.py` para capturar errores de parseo sin abortar el pipeline y canalizar la cuenta a P2 (`Revisión Especialista Cobranza`).
4. **Distinción semántica en notas (`C-1037`, `C-1038`, `C-1001`, `C-1031`, `C-1041`):**
   - Se evitó que `"Convenio de pago cancelado..."` (`C-1037`) o `"Queja Q-1980 ... cerrada y resuelta"` (`C-1038`) activaran falsamente bloqueos P1/P2.
   - Se aseguró que amenazas regulatorias (`"voy a poner una queja en CONDUSEF"`, `C-1001`) prevalezcan como disputa abierta (P2) sobre la afirmación `"ya pagué"`.
   - Se distinguió la fecha de llamada telefónica previa (`"Prometió pago por teléfono el DD/MM/YYYY"`) de una contradicción en la fecha prometida de pago, validando además la ventana de `+2 días` de gracia en `fecha_promesa_pago`.
5. **Defensa contra *prompt injection* y límites de API:** Se bloqueó el paso al redactor LLM para cuentas en disputa/queja (P2), se delimitaron notas en `<nota_cliente>` con detector de patrones de inyección, se agregó backoff exponencial ante HTTP 429 y fallback automático a templates aprobados cuando falla el guardrail o la API.
6. **Control Human-in-the-Loop:** Se garantizó que ni el CLI ni la interfaz Streamlit realicen envíos automáticos de SMS/WhatsApp; todos los borradores requieren aprobación, edición o rechazo humano explícito.


## 3. Prompts Utilizados
1. **Prompt para Gemini 4 Neon**: 

          Redacta un prompt técnico, detallado y listo para ejecutar dirigido a un agente de programación para construir desde cero un Agente de Cobranza Preventiva (Human-in-the-Loop) en Python.

          El prompt que generes debe instruir al agente a seguir estas especificaciones:

          1. **Inspección previa del workspace:** Antes de programar, revisar los archivos de datos y políticas existentes (`cuentas`, `historial_pagos`, `politica_cobranza.md`), soportando `.csv` con formatos sucios (fechas mixtas en español/ISO y montos heterogéneos convertidos a `Decimal`).
          2. **Arquitectura híbrida y stack:** Usar `google-genai` (`gemini-2.5-flash`), `pydantic`, `pandas`, `streamlit` y `pytest`. La limpieza de datos y el motor de reglas deben ser 100% determinísticos; la IA con Structured Outputs se acota solo a (a) extraer intención/evidencia de las notas de texto y (b) redactar borradores de mensajes con validación determinística post-generación (guardrails) y fallback automático a templates.
          3. **Reglas de negocio y cascada de precedencia (`P1 → P2 → P3 → P4`):**
            - **P1 (No contactar):** Convenio activo, promesa de pago vigente (considerando que la fecha registrada ya incluye 2 días de gracia), pago/comprobante reciente o contacto en los últimos 5 días.
            - **P2 (Revisión Especialista):** Quejas/disputas abiertas, falta de teléfono o inconsistencias de datos (ej. deuda activa sin periodos pendientes en el historial), evitando escalar estos casos a Gerente.
            - **P3 (Escalar a Gerente):** Monto vencido > $50,000, atraso estático > 60 días o >= 2 promesas incumplidas (sumando +1 si la promesa actual venció), generando una ficha estructurada.
            - **P4 (Drafts SMS/WhatsApp):** Mensajes segmentados por tono (amistoso, firme, formal con aviso explícito de llamada) que incluyan monto exacto, vencimiento y únicamente sucursal o transferencia SPEI como medios de pago.
          4. **Entregables completos:**
            - Código modular en `src/` (normalizador, conciliador, analizador de notas, motor de reglas, agente Gemini, pipeline CLI y app en `Streamlit` para aprobar/editar/rechazar borradores).
            - Suite de pruebas en `pytest` con casos frontera y evaluación de KPIs (0% falsos positivos de contacto, 100% recall en escalamientos críticos y 100% fidelidad de datos en borradores).
            - `README.md` (con diagrama Mermaid, justificación híbrida, riesgos/mitigaciones y plan de despliegue gradual), `requirements.txt` y `.env.example`.
            - Ejecución real de las pruebas y del pipeline sobre el dataset del workspace mostrando el desglose de resultados.

2. **Prompt para Antigravity (Generado con el prompt anterior y modificado)**:

        Actúa como un Senior AI & Data Engineer. Implementa un Agente de Cobranza Preventiva (Human-in-the-Loop) en Python usando la API de Gemini (`google-genai`, modelo `gemini-2.5-flash`), bajo una arquitectura híbrida: limpieza y motor de reglas 100% determinísticos en Python + IA acotada con Pydantic Structured Outputs para (1) comprensión semántica del campo `notas` y (2) redacción de borradores con guardrails y fallback a templates.

        ---

        ### 1. Inspección inicial obligatoria del workspace
        Antes de escribir código, inspecciona todos los archivos existentes en el workspace (`cuentas.csv` / `cuentas.md`, `historial_pagos.csv` / `historial_pagos.md`, `politica_cobranza.md`, etc.).
        Soporta lectura de `.csv` que contengan tablas/CSV y maneja todos los formatos sucios reales:
        - Fechas mezcladas: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-mmm-YYYY` con meses en español (`ene`, `feb`, `mar`, `abr`, `may`, `jun`, `jul`, `ago`, `sep`, `oct`, `nov`, `dic`).
        - Montos heterogéneos: formato europeo (`"4.041,13"`), con símbolo (`"$4,041.13"`), con sufijo (`"4,041.13 MXN"`), decimales (`4041.13`) y enteros (`4041`), convirtiendo todo a `Decimal` de 2 decimales.

        ---

        ### 2. Reglas de Negocio Definitivas (Validadas con Cobranza — Cumplimiento Estricto)
        Todas las ventanas de tiempo se calculan en **días naturales** contra un parámetro configurable `fecha_corte` (default: `2026-11-24`, configurable por CLI y en Streamlit).

        **Criterios de datos confirmados (NO romper):**
        1. **Monto a cobrar:** Usar siempre `monto_vencido` de `cuentas` (aunque sea mayor a la cuota histórica habitual, ej. 1.5x; NO marcar un salto de cuota como inconsistencia).
        2. **Días de atraso:** Usar los `dias_atraso` estáticos que vienen en el Excel/CSV de `cuentas` para segmentación y reglas (NO marcar desfase de días contra `fecha_vencimiento` como error).
        3. **Fecha de último pago:** La fecha en `historial_pagos` es la fecha real en cajero y `cuentas.fecha_ultimo_pago` es cuando se refleja en el banco. Confiar en `historial_pagos` como fecha real de pago y NO marcar el desfase entre ambas fechas como inconsistencia.
        4. **Fecha promesa de pago:** El campo `fecha_promesa_pago` de `cuentas` **YA INCLUYE el periodo de gracia de 2 días**.
          - Por lo tanto, una promesa está vigente si `fecha_corte <= fecha_promesa_pago` (NO volver a sumar +2 días).
          - Si en `notas` aparece una fecha de promesa (ej. `"20/11"`) y `fecha_promesa_pago` es `2026-11-22` (`fecha_nota + 2 días`), es **consistente** (NO marcar como inconsistencia). Solo marcar conflicto si la fecha en notas contradice claramente tanto a `fecha_promesa_pago` como a `fecha_promesa_pago - 2 días`.
        5. **Promesas incumplidas (+1 automático):** Si existe una `fecha_promesa_pago` y ya venció (`fecha_corte > fecha_promesa_pago`) sin pago registrado, sumar `+1` al contador `promesas_incumplidas` del archivo (`promesas_incumplidas_efectivas = promesas_incumplidas + 1`). Si el resultado es `>= 2`, dispara la regla de escalamiento a Gerente.
        6. **Buen historial (`<= 1` atraso en 12 meses):** Evaluar respetando estrictamente la etiqueta de estatus en `historial_pagos`. Todo registro marcado como `"a tiempo"` cuenta como puntual (aunque la fecha de pago sea `+1 día` después del vencimiento). Contar atrasos sobre registros pagados tarde/atrasados (excluyendo `"a tiempo"` y `"pendiente"`).
        7. **Caso `C-1007` (Datos que no cuadran):** Si una cuenta aparece activa/vencida en `cuentas` (con `monto_vencido > 0` o `dias_atraso > 0`), pero en `historial_pagos` NO tiene ninguna fila `"pendiente"` (todos sus periodos están marcados `"a tiempo"`), **debe marcarse obligatoriamente como `"datos que no cuadran"`** y enviarse a **Revisión Especialista Cobranza (P2)**.

        **Cascada estricta de precedencia (`P1 → P2 → P3 → P4`):**
        - **Prioridad 1 — NO CONTACTAR (En espera):**
          1. Convenio vigente (en columna o detectado en `notas`).
          2. Promesa de pago vigente: `fecha_corte <= fecha_promesa_pago`. *(Regla de precedencia confirmada: si hay promesa vigente, el caso se queda retenido en cobranza y NO escala a Gerente).*
          3. Pago ya realizado o comprobante pendiente de aplicar detectado explícitamente en `notas` (siempre que no caiga en el patrón inconsistente de `C-1007`).
          4. Contacto reciente: `(fecha_corte - fecha_ultimo_contacto).days <= 5` (días naturales).
        - **Prioridad 2 — REVISIÓN ESPECIALISTA COBRANZA (NO escala a Gerente):**
          1. Queja o disputa abierta (en columna o detectada en `notas`). *(Regla confirmada: el caso se queda con cobranza si hay queja abierta y NO sube a Gerente; además, bloquear antes del LLM redactor para mitigar prompt injection).*
          2. Cuenta sin teléfono válido registrado.
          3. Datos que no cuadran:
            - Patrón `C-1007`: deuda en `cuentas` sin registro `"pendiente"` en `historial_pagos`.
            - Inconsistencias críticas: `monto_vencido <= 0` con `dias_atraso > 0` (o viceversa), datos corruptos no parseables, o fecha de promesa en `notas` que contradice tanto a `fecha_promesa_pago` como a `fecha_promesa_pago - 2 días`.
          *(Nota de visibilidad: si una cuenta detenida en P1 o P2 también supera algún umbral de P3, mantenerla en P1/P2 como destino principal y agregar una etiqueta informativa `alerta_secundaria_gerente` visible en el reporte y en Streamlit).*
        - **Prioridad 3 — ESCALAR A GERENTE:**
          1. `monto_vencido > 50000.00` estricto (`$50,000.00` exactos NO escala).
          2. `dias_atraso > 60` usando `dias_atraso` estático (`60` exactos NO escala).
          3. `promesas_incumplidas_efectivas >= 2` (incluyendo el `+1` por promesa vencida).
          - Salida obligatoria (`FichaGerente`): cliente, `cuenta_id`, `monto_vencido`, `dias_atraso`, `fecha_vencimiento` y regla exacta activada (ej. `"monto $64,000.00 > $50,000.00"` o `"2 promesas incumplidas (1 previa + 1 vencida el 2026-11-22)"`).
        - **Prioridad 4 — GENERAR DRAFT (SMS / WhatsApp — Human-in-the-Loop, NUNCA envío automático):**
          - **Amistoso:** `dias_atraso` entre `1 y 7` (o `1 y 15` si `buen_historial == True`).
          - **Firme + opciones de pago:** `dias_atraso` entre `8 y 30` (o `16 y 30` si `buen_historial == True`).
          - **Formal + llamada:** `dias_atraso` entre `31 y 60`.
          - **Reglas obligatorias del mensaje:**
            - Incluir: nombre del cliente, `monto_vencido` exacto de `cuentas` (`$X,XXX.XX`), `fecha_vencimiento`.
            - **Medios de pago autorizados (únicos permitidos):** **ir a sucursal** o **transferencia SPEI**. No inventar otros medios, ligas, tiendas ni descuentos.
            - **Segmento Firme (`8–30 días`):** Mencionar opciones para regularizarse comunicándose con cobranza además de sucursal o transferencia SPEI (sin inventar quitas ni plazos no autorizados).
            - **Segmento Formal (`31–60 días`):** El borrador **debe indicar explícitamente que un asesor/ejecutivo se comunicará por teléfono (se le va a llamar)**, y además activar `requiere_llamada_cobrador = True` en la salida estructurada.

        ---

        ### 3. Arquitectura Técnica y Uso Acotado de IA
        Stack gratuito: `google-genai`, `pydantic`, `pandas`, `streamlit`, `pytest`.

        Crea esta estructura limpia:
        - `src/models.py`: Modelos Pydantic (`CuentaNormalizada`, `AnalisisNotas`, `DecisionCobranza`, `FichaGerente`, `DraftRecordatorio`).
        - `src/normalizer.py`: Limpieza de fechas multi-formato (ES/ISO) y montos (`Decimal` a 2 decimales).
        - `src/note_analyzer.py`:
          - **Rol 1 de IA (Extractor Semántico):** Solo se invoca si `notas` tiene texto. Llama a `gemini-2.5-flash` con `temperature=0.0` y `response_schema=AnalisisNotas` pasándole `notas` y `fecha_corte` para devolver:
            - `menciona_queja_o_disputa: bool`
            - `menciona_pago_o_comprobante: bool`
            - `menciona_convenio_o_acuerdo: bool`
            - `menciona_promesa_pago: bool`
            - `fecha_promesa_en_nota: Optional[date]`
            - `nota_inconsistente_o_sensible: bool`
            - `evidencia_literal: str`
          - Incluye manejo de rate limits (backoff exponencial) y un **extractor determinístico por regex/palabras clave** como fallback automático cuando `USE_MOCK_LLM=true` o si falla la API.
        - `src/reconciler.py`: Cruce `cuentas` ↔ `historial_pagos`, conteo `"a tiempo"`, detección patrón `C-1007`, validación de promesa en `notas` vs `fecha_promesa_pago` (`-2 días`), y cálculo de `promesas_incumplidas_efectivas`.
        - `src/rules_engine.py`: Motor determinístico puro `P1 → P2 → P3 → P4`.
        - `src/gemini_agent.py`:
          - **Rol 2 de IA (Redactor de Drafts):** Llama a `gemini-2.5-flash` (`temperature=0.2`, `response_schema=DraftRecordatorio`) solo para cuentas P4.
          - **Guardrail determinístico post-generación:** Verifica literalmente:
            1. Nombre del cliente presente.
            2. `monto_vencido` exacto presente (`$X,XXX.XX`).
            3. `fecha_vencimiento` presente.
            4. Mención a los medios autorizados (`sucursal` y `SPEI`).
            5. Si el segmento es `Formal (31–60 días)`, verificación obligatoria de mención explícita a que **se le va a llamar** (`llamar` / `llamada` / `comunicará por teléfono`).
            6. Longitud `<= 400` caracteres y cero palabras prohibidas (amenazas legales, embargos, buró, descuentos inventados, menciones a terceros).
          - Si el guardrail falla, reintenta una vez con el error específico; si vuelve a fallar o hay error/rate-limit de API (o `USE_MOCK_LLM=true`), aplica **fallback a templates determinísticos aprobados** por tono, registrando `origen_borrador = "llm" | "template_fallback"`.
        - `src/pipeline.py`: Script CLI (`--fecha-corte YYYY-MM-DD`, `--mock-llm`) que procesa todo el dataset, imprime tabla resumen en consola y guarda `output/reporte_cobranza.json`.
        - `src/app.py`: Interfaz Streamlit con:
          - Selector de `fecha_corte` y toggle `Modo Mock / Gemini API`.
          - Tarjetas KPI superiores (Total cuentas, Drafts, Especialista, Gerente, En espera).
          - 4 pestañas: `Borradores por Aprobar`, `Revisión Especialista`, `Escalamientos Gerente`, `No Contactar / En Espera`.
          - En cada cuenta: motivo exacto de clasificación, análisis estructurado de `notas` (con evidencia literal), etiqueta `origen_borrador`, alerta secundaria si aplica, y en Drafts botones de `Aprobar`, `Editar` y `Rechazar` (actualizando estado en sesión sin enviar mensajes reales).

        ---

        ### 4. Suite de Pruebas y Evals (`tests/`)
        Implementa `pytest` completo cubriendo:
        - `tests/test_normalizer.py`: Fechas ISO/DD-MM-YYYY/DD-mmm-YYYY en español y montos europeos/MXN/enteros.
        - `tests/test_rules_engine.py`:
          - Fronteras exactas: `$50,000.00` vs `$50,000.01`, `60` vs `61` días estáticos, `5` vs `6` días naturales de último contacto, `15` vs `16` días con buen historial.
          - Promesa vigente (`fecha_corte == fecha_promesa_pago`) → P1 (NO escala aunque deba >$50k).
          - Promesa vencida (`fecha_corte > fecha_promesa_pago`) suma `+1` → si llega a 2, escala a Gerente (P3).
          - Consistencia de `fecha_promesa_pago == fecha_nota + 2 días` (tipo `C-1031`) → NO marca inconsistencia.
          - Salto de monto (1.5x) y desfase bancario en `fecha_ultimo_pago` → NO marcan inconsistencia.
          - Pagos `+1 día` etiquetados `"a tiempo"` → cuentan como buen historial.
          - Cuenta tipo `C-1007` (sin fila `pendiente` en historial) → P2 (`datos que no cuadran`).
          - Queja abierta con monto >$50,000 → se queda en P2 (Especialista Cobranza, NO sube a Gerente).
          - Guardrail de drafts: exige `sucursal` y `SPEI`, y en `31–60 días` exige aviso de llamada.
        - `tests/test_evaluacion.py`: Ejecuta el pipeline sobre el dataset real + casos sintéticos y valida 3 KPIs obligatorios:
          1. Tasa de Falsos Positivos en Contacto == `0.0%`.
          2. Recall en Escalamientos Críticos al Gerente == `100.0%`.
          3. Fidelidad de Datos en Drafts (montos, vencimiento, medios, aviso de llamada) == `100.0%`.

        ---

        ### 5. Documentación y Entregables
        Genera también:
        - `README.md`: Diseño completo con diagrama Mermaid, justificación de arquitectura híbrida (dónde SÍ y dónde NO se usa IA), tabla de las 11 reglas validadas con negocio, instrucciones de instalación y ejecución, resultados reales sobre las 60 cuentas del workspace, sección de **Riesgos y Mitigaciones** (desfase de pagos bancarios vs cajero, prompt injection en notas, rate limits de API gratuita con fallback a templates) y **Plan de Despliegue Gradual** (Semanas 1-2 modo asistido 100% humano → Semanas 3-4 automatización parcial solo segmento amistoso 1–7 días tras >98% de aprobación sin ediciones).
        - `requirements.txt` y `.env.example`.

        ### Orden de ejecución
        1. Inspecciona los archivos reales del workspace y muéstrame brevemente el esquema de columnas encontrado y cuántas filas tienen `notas` no vacías.
        2. Implementa todo el proyecto (`src/`, `tests/`, `README.md`, `DECLARACION_IA.md`, `requirements.txt`, `.env.example`).
        3. Ejecuta `pytest -v` y corre `src/pipeline.py --mock-llm --fecha-corte 2026-11-24` sobre los datos reales, mostrándome la salida real de las pruebas, el desglose exacto de las 60 cuentas por categoría (P1, P2, P3, P4) y cómo quedó clasificada `C-1007` y `C-1031`.