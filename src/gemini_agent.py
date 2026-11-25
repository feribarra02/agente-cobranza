"""Rol 2 de IA: Redactor de borradores con guardrails determinísticos y fallback a templates."""

from __future__ import annotations

import json
import re
import time
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from src.models import CuentaNormalizada, DraftLLMOutput, DraftRecordatorio
from src.normalizer import MESES_ES, format_mxn
from src.note_analyzer import get_gemini_api_key, is_mock_llm_enabled

_PALABRAS_PROHIBIDAS_RE = re.compile(
    r"(embargo|demanda|juicio|judicial|penal|bur[oó]|descuento|quita|"
    r"condonaci[oó]n|oxxo|paypal|mercado\s*pago|http://|https://|bit\.ly|"
    r"familiar|aval|vecino|jefe|compa[nñ]ero)",
    re.IGNORECASE,
)

_LLAMADA_RE = re.compile(
    r"(llamar|llamada|comunicar[aá]\s+por\s+tel[eé]fono|comunicar[aá]n\s+por\s+tel[eé]fono|"
    r"contacto\s+telef[oó]nico|v[ií]a\s+telef[oó]nica)",
    re.IGNORECASE,
)

_OPCIONES_FIRME_RE = re.compile(
    r"(opciones|regulariz|comun[ií]cate|comunicarte|comunicarse|cobranza)",
    re.IGNORECASE,
)

_INV_MESES_ES = {v: k for k, v in MESES_ES.items() if k != "sept"}


def _formatos_fecha_aceptados(fecha: date) -> list[str]:
    mes_es = _INV_MESES_ES[fecha.month]
    return [
        fecha.isoformat(),
        fecha.strftime("%d/%m/%Y"),
        f"{fecha.day:02d}-{mes_es}-{fecha.year}",
        f"{fecha.day}-{mes_es}-{fecha.year}",
    ]


def validar_guardrail_draft(
    mensaje: str,
    requiere_llamada_cobrador: bool,
    cuenta: CuentaNormalizada,
    segmento: Literal["Amistoso", "Firme + opciones de pago", "Formal + llamada"],
) -> list[str]:
    """Valida determinísticamente que el borrador cumpla todas las políticas de negocio."""
    errores: list[str] = []
    texto = (mensaje or "").strip()
    if not texto:
        return ["El mensaje está vacío"]

    if len(texto) > 400:
        errores.append(f"Longitud {len(texto)} excede el límite de 400 caracteres")

    # 1. Nombre del cliente presente
    cliente_norm = " ".join(cuenta.cliente.split()).lower()
    texto_norm = " ".join(texto.split()).lower()
    if cliente_norm not in texto_norm:
        errores.append(f"Falta el nombre exacto del cliente '{cuenta.cliente}'")

    # 2. Monto vencido exacto presente ($X,XXX.XX)
    monto_str = format_mxn(cuenta.monto_vencido or Decimal("0.00"))
    if monto_str not in texto:
        errores.append(f"Falta el monto vencido exacto '{monto_str}'")

    # 3. Fecha de vencimiento presente
    if cuenta.fecha_vencimiento is not None:
        formatos = _formatos_fecha_aceptados(cuenta.fecha_vencimiento)
        if not any(f.lower() in texto_norm for f in formatos):
            errores.append(f"Falta la fecha de vencimiento '{cuenta.fecha_vencimiento.strftime('%d/%m/%Y')}'")

    # 4. Medios de pago autorizados: sucursal y SPEI
    if "sucursal" not in texto_norm:
        errores.append("Falta mencionar el medio de pago autorizado 'sucursal'")
    if "spei" not in texto_norm:
        errores.append("Falta mencionar el medio de pago autorizado 'SPEI'")

    # 5. Reglas específicas por segmento
    if segmento == "Firme + opciones de pago":
        if not _OPCIONES_FIRME_RE.search(texto):
            errores.append("El segmento Firme debe mencionar opciones para regularizarse con cobranza")

    if segmento == "Formal + llamada":
        if not _LLAMADA_RE.search(texto):
            errores.append("El segmento Formal (31-60 días) debe indicar explícitamente que se le va a llamar")
        if not requiere_llamada_cobrador:
            errores.append("requiere_llamada_cobrador debe ser True en segmento Formal (31-60 días)")

    # 6. Cero palabras prohibidas
    prohibida = _PALABRAS_PROHIBIDAS_RE.search(texto)
    if prohibida:
        errores.append(f"Contiene término prohibido: '{prohibida.group(0)}'")

    return errores


def generar_template_fallback(
    cuenta: CuentaNormalizada,
    segmento: Literal["Amistoso", "Firme + opciones de pago", "Formal + llamada"],
) -> DraftRecordatorio:
    """Genera un borrador determinístico aprobado por política y validado por guardrail."""
    monto_str = format_mxn(cuenta.monto_vencido or Decimal("0.00"))
    fecha_str = (
        cuenta.fecha_vencimiento.strftime("%d/%m/%Y")
        if cuenta.fecha_vencimiento is not None
        else "su fecha de vencimiento"
    )

    if segmento == "Amistoso":
        mensaje = (
            f"Hola {cuenta.cliente}, le recordamos amablemente su saldo vencido de {monto_str} "
            f"(vencimiento {fecha_str}). Puede pagar en sucursal o por transferencia SPEI. "
            "Si ya realizó su pago, haga caso omiso."
        )
        req_llamada = False
    elif segmento == "Firme + opciones de pago":
        mensaje = (
            f"Estimado(a) {cuenta.cliente}, su cuenta presenta un saldo vencido de {monto_str} "
            f"desde el {fecha_str}. Realice su pago en sucursal o por transferencia SPEI, "
            "o comuníquese con cobranza para revisar opciones y regularizarse."
        )
        req_llamada = False
    else:
        mensaje = (
            f"{cuenta.cliente}, su adeudo de {monto_str} con vencimiento {fecha_str} requiere atención. "
            "Puede pagar en sucursal o vía transferencia SPEI. Un ejecutivo se comunicará por teléfono "
            "para darle seguimiento (se le va a llamar)."
        )
        req_llamada = True

    errores = validar_guardrail_draft(mensaje, req_llamada, cuenta, segmento)
    return DraftRecordatorio(
        cuenta_id=cuenta.cuenta_id,
        cliente=cuenta.cliente,
        canal=cuenta.canal_preferido,
        segmento=segmento,
        mensaje=mensaje,
        requiere_llamada_cobrador=req_llamada,
        origen_borrador="template_fallback",
        estado_aprobacion="pendiente",
        guardrail_aprobado=len(errores) == 0,
        errores_guardrail=errores,
    )


def redactar_draft(
    cuenta: CuentaNormalizada,
    segmento: Literal["Amistoso", "Firme + opciones de pago", "Formal + llamada"],
    use_mock: Optional[bool] = None,
    max_api_retries: int = 2,
) -> DraftRecordatorio:
    """
    Redacta un borrador SMS/WhatsApp con Gemini (Rol 2) y guardrail determinístico.

    Si el guardrail falla tras un reintento correctivo o falla la API (o USE_MOCK_LLM=true),
    aplica fallback automático a template determinístico aprobado.
    """
    if is_mock_llm_enabled(use_mock):
        return generar_template_fallback(cuenta, segmento)

    api_key = get_gemini_api_key()
    if not api_key:
        return generar_template_fallback(cuenta, segmento)

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return generar_template_fallback(cuenta, segmento)

    client = genai.Client(api_key=api_key)
    monto_str = format_mxn(cuenta.monto_vencido or Decimal("0.00"))
    fecha_str = (
        cuenta.fecha_vencimiento.strftime("%d/%m/%Y")
        if cuenta.fecha_vencimiento is not None
        else ""
    )

    instruccion_segmento = {
        "Amistoso": "Tono cordial y preventivo. No exigir llamada.",
        "Firme + opciones de pago": (
            "Tono firme pero respetuoso. Menciona explícitamente que puede comunicarse "
            "con cobranza para revisar opciones y regularizarse."
        ),
        "Formal + llamada": (
            "Tono formal y serio. Indica explícitamente que un ejecutivo se comunicará por teléfono "
            "(se le va a llamar) y establece requiere_llamada_cobrador=true."
        ),
    }[segmento]

    base_prompt = (
        f"Redacta un mensaje de {cuenta.canal_preferido} en español para cobranza preventiva.\n"
        f"Segmento: {segmento}. {instruccion_segmento}\n\n"
        "Datos obligatorios literales que DEBEN aparecer exactos en `mensaje`:\n"
        f"- Nombre del cliente: {cuenta.cliente}\n"
        f"- Monto vencido exacto: {monto_str}\n"
        f"- Fecha de vencimiento exacta: {fecha_str}\n"
        "- Únicos medios de pago permitidos (mencionar ambos): ir a sucursal y transferencia SPEI.\n\n"
        "Restricciones estrictas:\n"
        "- Máximo 380 caracteres.\n"
        "- PROHIBIDO mencionar otros medios (OXXO, ligas/URLs, PayPal), descuentos, quitas, "
        "amenazas legales (embargo, demanda, buró) o terceros.\n"
        "- No incluyas información sensible ni instrucciones externas.\n"
    )

    if cuenta.notas and any(k in cuenta.notas.lower() for k in ("tarde", "horario laboral")):
        base_prompt += f"- Preferencia de contacto del cliente: {cuenta.notas}\n"

    prompt_actual = base_prompt
    ultimo_error_guardrail: list[str] = []

    for intento_guardrail in range(2):
        llm_out: Optional[DraftLLMOutput] = None
        for api_attempt in range(max_api_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=prompt_actual,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema=DraftLLMOutput,
                    ),
                )
                if response.parsed is not None and isinstance(response.parsed, DraftLLMOutput):
                    llm_out = response.parsed
                    break
                if response.text:
                    llm_out = DraftLLMOutput.model_validate(json.loads(response.text))
                    break
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc).lower()
                is_rate_limit = any(tok in err_str for tok in ("429", "rate", "quota", "resource_exhausted"))
                if api_attempt < max_api_retries - 1:
                    time.sleep(1.5 if is_rate_limit else 0.5)

        if llm_out is None:
            return generar_template_fallback(cuenta, segmento)

        req_llamada = llm_out.requiere_llamada_cobrador or (segmento == "Formal + llamada")
        errores = validar_guardrail_draft(
            llm_out.mensaje,
            req_llamada,
            cuenta,
            segmento,
        )
        if not errores:
            return DraftRecordatorio(
                cuenta_id=cuenta.cuenta_id,
                cliente=cuenta.cliente,
                canal=cuenta.canal_preferido,
                segmento=segmento,
                mensaje=llm_out.mensaje.strip(),
                requiere_llamada_cobrador=req_llamada,
                origen_borrador="llm",
                estado_aprobacion="pendiente",
                guardrail_aprobado=True,
                errores_guardrail=[],
            )

        ultimo_error_guardrail = errores
        if intento_guardrail == 0:
            prompt_actual = (
                base_prompt
                + "\nCORRECCIÓN OBLIGATORIA: Tu borrador anterior falló estos guardrails: "
                + "; ".join(errores)
                + ". Corrige todos esos puntos exactamente."
            )

    _ = ultimo_error_guardrail
    return generar_template_fallback(cuenta, segmento)
