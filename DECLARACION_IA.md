# Declaración de Uso de Inteligencia Artificial (Bitácora de Desarrollo)

## 1. Herramientas de IA utilizadas
- **Asistente de ingeniería de software (Antigravity / modelo Gemini):** Apoyo en diseño de arquitectura híbrida, implementación de módulos Python (`src/`), suite de pruebas (`tests/`) y documentación técnica (`README.md`).
- **Google Gemini API (`google-genai`, modelo `gemini-2.5-flash`):** Utilizada en tiempo de ejecución únicamente para dos tareas acotadas con *Structured Outputs* (Pydantic):
  1. **Rol 1 (`src/note_analyzer.py`):** Extracción semántica de señales en texto libre (`notas`) con `temperature=0.0` y esquema `AnalisisNotas`.
  2. **Rol 2 (`src/gemini_agent.py`):** Redacción de borradores SMS/WhatsApp (`DraftRecordatorio`) con `temperature=0.2`, sujeta a guardrails determinísticos y fallback a templates aprobados.

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
