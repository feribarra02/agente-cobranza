"""Motor de reglas 100% determinístico con cascada estricta P1 -> P2 -> P3 -> P4."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from src.models import (
    AnalisisNotas,
    CuentaNormalizada,
    DecisionCobranza,
    FichaGerente,
    ReconciliacionCuenta,
)
from src.normalizer import format_mxn

UMBRAL_MONTO_GERENTE = Decimal("50000.00")
UMBRAL_DIAS_GERENTE = 60
UMBRAL_PROMESAS_GERENTE = 2
DIAS_CONTACTO_RECIENTE = 5


def evaluar_umbrales_gerente(
    cuenta: CuentaNormalizada,
    reconciliacion: ReconciliacionCuenta,
) -> list[str]:
    """Evalúa los 3 criterios estrictos de escalamiento a Gerente (Prioridad 3)."""
    reglas: list[str] = []
    if cuenta.monto_vencido is not None and cuenta.monto_vencido > UMBRAL_MONTO_GERENTE:
        reglas.append(f"monto {format_mxn(cuenta.monto_vencido)} > $50,000.00")

    if cuenta.dias_atraso is not None and cuenta.dias_atraso > UMBRAL_DIAS_GERENTE:
        reglas.append(f"dias_atraso {cuenta.dias_atraso} > 60")

    if reconciliacion.promesas_incumplidas_efectivas >= UMBRAL_PROMESAS_GERENTE:
        if reconciliacion.promesa_vencida_sumada and cuenta.fecha_promesa_pago is not None:
            reglas.append(
                f"{reconciliacion.promesas_incumplidas_efectivas} promesas incumplidas "
                f"({cuenta.promesas_incumplidas} previa(s) + 1 vencida el {cuenta.fecha_promesa_pago.isoformat()})"
            )
        else:
            reglas.append(
                f"{reconciliacion.promesas_incumplidas_efectivas} promesas incumplidas (>= 2)"
            )

    return reglas


def determinar_segmento_draft(
    dias_atraso: int,
    buen_historial: bool,
) -> Literal["Amistoso", "Firme + opciones de pago", "Formal + llamada"]:
    """Calcula el segmento de cobranza para cuentas en Prioridad 4 (1 a 60 días)."""
    limite_amistoso = 15 if buen_historial else 7
    if dias_atraso <= limite_amistoso:
        return "Amistoso"
    if dias_atraso <= 30:
        return "Firme + opciones de pago"
    return "Formal + llamada"


def evaluar_cuenta(
    cuenta: CuentaNormalizada,
    analisis_notas: AnalisisNotas,
    reconciliacion: ReconciliacionCuenta,
    fecha_corte: date,
    origen_analisis_notas: Literal["llm", "regex_fallback", "sin_notas"] = "sin_notas",
) -> DecisionCobranza:
    """Aplica la cascada estricta P1 -> P2 -> P3 -> P4 y devuelve la decisión auditada."""
    umbrales_gerente = evaluar_umbrales_gerente(cuenta, reconciliacion)
    alerta_secundaria = (
        f"Umbral gerente superado: {'; '.join(umbrales_gerente)}"
        if umbrales_gerente
        else None
    )

    # ------------------------------------------------------------------
    # PRIORIDAD 1 — NO CONTACTAR (En espera)
    # ------------------------------------------------------------------
    razones_p1: list[str] = []

    if analisis_notas.menciona_convenio_o_acuerdo and not reconciliacion.inconsistencia_critica:
        razones_p1.append("P1.1 Convenio de pago vigente detectado")

    if reconciliacion.promesa_vigente and not reconciliacion.conflicto_promesa_nota:
        razones_p1.append(
            f"P1.2 Promesa de pago vigente hasta {cuenta.fecha_promesa_pago.isoformat()} "  # type: ignore[union-attr]
            f"(fecha_corte={fecha_corte.isoformat()})"
        )

    if (
        analisis_notas.menciona_pago_o_comprobante
        and not reconciliacion.patron_c1007_sin_pendiente
        and not analisis_notas.menciona_queja_o_disputa
        and not reconciliacion.inconsistencia_critica
    ):
        razones_p1.append("P1.3 Pago ya realizado o comprobante pendiente de aplicar en notas")

    if cuenta.fecha_ultimo_contacto is not None:
        dias_desde_contacto = (fecha_corte - cuenta.fecha_ultimo_contacto).days
        if 0 <= dias_desde_contacto <= DIAS_CONTACTO_RECIENTE:
            razones_p1.append(
                f"P1.4 Contacto reciente hace {dias_desde_contacto} día(s) "
                f"(último contacto: {cuenta.fecha_ultimo_contacto.isoformat()}, <= 5 días)"
            )

    if razones_p1:
        return DecisionCobranza(
            cuenta_id=cuenta.cuenta_id,
            cliente=cuenta.cliente,
            prioridad="P1_NO_CONTACTAR",
            categoria_destino="No Contactar / En Espera",
            motivo_clasificacion="; ".join(razones_p1),
            reglas_disparadas=razones_p1,
            alerta_secundaria_gerente=alerta_secundaria,
            buen_historial=reconciliacion.buen_historial,
            promesas_incumplidas_efectivas=reconciliacion.promesas_incumplidas_efectivas,
            cuenta=cuenta,
            analisis_notas=analisis_notas,
            origen_analisis_notas=origen_analisis_notas,
            reconciliacion=reconciliacion,
        )

    # ------------------------------------------------------------------
    # PRIORIDAD 2 — REVISIÓN ESPECIALISTA COBRANZA (NO escala a Gerente)
    # ------------------------------------------------------------------
    razones_p2: list[str] = []

    if analisis_notas.menciona_queja_o_disputa:
        razones_p2.append("P2.1 Queja o disputa abierta detectada en notas (bloqueado antes de LLM redactor)")

    if not cuenta.telefono_valido:
        razones_p2.append(
            f"P2.2 Cuenta sin teléfono válido registrado (valor='{cuenta.telefono_raw or ''}')"
        )

    if reconciliacion.patron_c1007_sin_pendiente or reconciliacion.inconsistencia_critica:
        detalle = "; ".join(reconciliacion.motivos_inconsistencia) or "Datos que no cuadran"
        razones_p2.append(f"P2.3 Datos que no cuadran: {detalle}")

    if razones_p2:
        return DecisionCobranza(
            cuenta_id=cuenta.cuenta_id,
            cliente=cuenta.cliente,
            prioridad="P2_REVISION_ESPECIALISTA",
            categoria_destino="Revisión Especialista Cobranza",
            motivo_clasificacion="; ".join(razones_p2),
            reglas_disparadas=razones_p2,
            alerta_secundaria_gerente=alerta_secundaria,
            buen_historial=reconciliacion.buen_historial,
            promesas_incumplidas_efectivas=reconciliacion.promesas_incumplidas_efectivas,
            cuenta=cuenta,
            analisis_notas=analisis_notas,
            origen_analisis_notas=origen_analisis_notas,
            reconciliacion=reconciliacion,
        )

    # ------------------------------------------------------------------
    # PRIORIDAD 3 — ESCALAR A GERENTE
    # ------------------------------------------------------------------
    if umbrales_gerente:
        regla_txt = "; ".join(umbrales_gerente)
        ficha = FichaGerente(
            cuenta_id=cuenta.cuenta_id,
            cliente=cuenta.cliente,
            monto_vencido=cuenta.monto_vencido or Decimal("0.00"),
            dias_atraso=cuenta.dias_atraso or 0,
            fecha_vencimiento=cuenta.fecha_vencimiento,
            regla_activada=regla_txt,
        )
        return DecisionCobranza(
            cuenta_id=cuenta.cuenta_id,
            cliente=cuenta.cliente,
            prioridad="P3_ESCALAR_GERENTE",
            categoria_destino="Escalamiento a Gerente",
            motivo_clasificacion=f"P3 Escalar a Gerente: {regla_txt}",
            reglas_disparadas=[f"P3 {r}" for r in umbrales_gerente],
            alerta_secundaria_gerente=None,
            buen_historial=reconciliacion.buen_historial,
            promesas_incumplidas_efectivas=reconciliacion.promesas_incumplidas_efectivas,
            cuenta=cuenta,
            analisis_notas=analisis_notas,
            origen_analisis_notas=origen_analisis_notas,
            reconciliacion=reconciliacion,
            ficha_gerente=ficha,
        )

    # ------------------------------------------------------------------
    # PRIORIDAD 4 — GENERAR DRAFT (SMS / WhatsApp — Human-in-the-Loop)
    # ------------------------------------------------------------------
    dias = cuenta.dias_atraso or 1
    segmento = determinar_segmento_draft(dias, reconciliacion.buen_historial)
    motivo_p4 = (
        f"P4 Segmento '{segmento}' (dias_atraso={dias}, "
        f"buen_historial={'Sí' if reconciliacion.buen_historial else 'No'}, "
        f"canal={cuenta.canal_preferido})"
    )

    return DecisionCobranza(
        cuenta_id=cuenta.cuenta_id,
        cliente=cuenta.cliente,
        prioridad="P4_GENERAR_DRAFT",
        categoria_destino="Borrador por Aprobar",
        motivo_clasificacion=motivo_p4,
        reglas_disparadas=[motivo_p4],
        alerta_secundaria_gerente=None,
        buen_historial=reconciliacion.buen_historial,
        promesas_incumplidas_efectivas=reconciliacion.promesas_incumplidas_efectivas,
        cuenta=cuenta,
        analisis_notas=analisis_notas,
        origen_analisis_notas=origen_analisis_notas,
        reconciliacion=reconciliacion,
    )
