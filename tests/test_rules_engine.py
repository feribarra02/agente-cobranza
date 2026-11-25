"""Pruebas unitarias del motor de reglas, conciliador y guardrails de borradores."""

from datetime import date
from decimal import Decimal
from typing import Optional

from src.gemini_agent import generar_template_fallback, validar_guardrail_draft
from src.models import AnalisisNotas, CuentaNormalizada, RegistroHistorialPago
from src.note_analyzer import extraer_notas_deterministico
from src.reconciler import conciliar_cuenta
from src.rules_engine import determinar_segmento_draft, evaluar_cuenta

FECHA_CORTE = date(2026, 11, 24)


def _cuenta_base(**overrides: object) -> CuentaNormalizada:
    data = {
        "cuenta_id": "C-9000",
        "cliente": "Cliente Prueba",
        "telefono_raw": "3312345678",
        "telefono_normalizado": "3312345678",
        "telefono_valido": True,
        "canal_preferido": "WhatsApp",
        "producto": "Crédito personal",
        "monto_vencido": Decimal("5000.00"),
        "dias_atraso": 10,
        "fecha_vencimiento": date(2026, 11, 14),
        "fecha_ultimo_pago": date(2026, 10, 15),
        "pagos_atrasados_12m": 0,
        "promesas_incumplidas": 0,
        "fecha_promesa_pago": None,
        "monto_promesa": None,
        "fecha_ultimo_contacto": date(2026, 11, 10),
        "notas": None,
    }
    data.update(overrides)
    return CuentaNormalizada(**data)


def _historial_bueno(cuenta_id: str, con_pendiente: bool = True) -> list[RegistroHistorialPago]:
    regs = [
        RegistroHistorialPago(
            cuenta_id=cuenta_id,
            fecha_vencimiento=date(2026, 10, 14),
            fecha_pago=date(2026, 10, 15),  # +1 día pero marcado "a tiempo"
            monto_pagado=Decimal("2500.00"),
            estatus="a tiempo",
        ),
        RegistroHistorialPago(
            cuenta_id=cuenta_id,
            fecha_vencimiento=date(2026, 9, 14),
            fecha_pago=date(2026, 9, 14),
            monto_pagado=Decimal("2500.00"),
            estatus="a tiempo",
        ),
    ]
    if con_pendiente:
        regs.append(
            RegistroHistorialPago(
                cuenta_id=cuenta_id,
                fecha_vencimiento=date(2026, 11, 14),
                fecha_pago=None,
                monto_pagado=Decimal("0.00"),
                estatus="pendiente",
            )
        )
    return regs


def _evaluar(
    cuenta: CuentaNormalizada,
    historial: Optional[list[RegistroHistorialPago]] = None,
    fecha_corte: date = FECHA_CORTE,
):
    hist = historial if historial is not None else _historial_bueno(cuenta.cuenta_id)
    analisis = extraer_notas_deterministico(cuenta.notas, fecha_corte)
    rec = conciliar_cuenta(cuenta, hist, analisis, fecha_corte)
    return evaluar_cuenta(cuenta, analisis, rec, fecha_corte)


def test_frontera_monto_50000_vs_50000_01() -> None:
    d_exacto = _evaluar(_cuenta_base(monto_vencido=Decimal("50000.00"), dias_atraso=40))
    assert d_exacto.prioridad == "P4_GENERAR_DRAFT"

    d_supera = _evaluar(_cuenta_base(monto_vencido=Decimal("50000.01"), dias_atraso=40))
    assert d_supera.prioridad == "P3_ESCALAR_GERENTE"
    assert d_supera.ficha_gerente is not None
    assert "$50,000.01 > $50,000.00" in d_supera.ficha_gerente.regla_activada


def test_frontera_dias_60_vs_61() -> None:
    d_60 = _evaluar(_cuenta_base(dias_atraso=60))
    assert d_60.prioridad == "P4_GENERAR_DRAFT"
    assert determinar_segmento_draft(60, True) == "Formal + llamada"

    d_61 = _evaluar(_cuenta_base(dias_atraso=61))
    assert d_61.prioridad == "P3_ESCALAR_GERENTE"
    assert "61 > 60" in d_61.ficha_gerente.regla_activada  # type: ignore[union-attr]


def test_frontera_contacto_reciente_5_vs_6_dias() -> None:
    d_5 = _evaluar(_cuenta_base(fecha_ultimo_contacto=date(2026, 11, 19)))
    assert d_5.prioridad == "P1_NO_CONTACTAR"

    d_6 = _evaluar(_cuenta_base(fecha_ultimo_contacto=date(2026, 11, 18)))
    assert d_6.prioridad == "P4_GENERAR_DRAFT"


def test_frontera_buen_historial_15_vs_16_dias() -> None:
    assert determinar_segmento_draft(15, buen_historial=True) == "Amistoso"
    assert determinar_segmento_draft(16, buen_historial=True) == "Firme + opciones de pago"
    assert determinar_segmento_draft(7, buen_historial=False) == "Amistoso"
    assert determinar_segmento_draft(8, buen_historial=False) == "Firme + opciones de pago"


def test_promesa_vigente_retiene_en_p1_aunque_supere_umbrales_gerente() -> None:
    cuenta = _cuenta_base(
        monto_vencido=Decimal("75000.00"),
        dias_atraso=70,
        fecha_promesa_pago=date(2026, 11, 24),  # fecha_corte == fecha_promesa_pago
    )
    decision = _evaluar(cuenta)
    assert decision.prioridad == "P1_NO_CONTACTAR"
    assert decision.alerta_secundaria_gerente is not None
    assert "$75,000.00" in decision.alerta_secundaria_gerente


def test_promesa_vencida_suma_mas_uno_y_escala_con_dos() -> None:
    cuenta = _cuenta_base(
        promesas_incumplidas=1,
        fecha_promesa_pago=date(2026, 11, 19),
        dias_atraso=18,
    )
    decision = _evaluar(cuenta)
    assert decision.promesas_incumplidas_efectivas == 2
    assert decision.prioridad == "P3_ESCALAR_GERENTE"
    assert decision.ficha_gerente is not None
    assert "2 promesas incumplidas" in decision.ficha_gerente.regla_activada


def test_consistencia_promesa_nota_mas_2_dias_y_conflicto() -> None:
    # Caso tipo C-1031: nota 20/11, fecha_promesa_pago 2026-11-22 -> consistente, sin P2
    c_ok = _cuenta_base(
        cuenta_id="C-1031",
        dias_atraso=22,
        fecha_promesa_pago=date(2026, 11, 22),
        notas="Prometió pago por teléfono el 20/11/2026",
    )
    d_ok = _evaluar(c_ok)
    assert not d_ok.reconciliacion.conflicto_promesa_nota
    assert d_ok.prioridad == "P4_GENERAR_DRAFT"

    # Contradicción explícita en fecha de pago comprometida -> P2
    c_bad = _cuenta_base(
        dias_atraso=22,
        fecha_promesa_pago=date(2026, 11, 26),
        notas="Prometió pagar exactamente el 10/11/2026",
    )
    d_bad = _evaluar(c_bad)
    assert d_bad.reconciliacion.conflicto_promesa_nota
    assert d_bad.prioridad == "P2_REVISION_ESPECIALISTA"


def test_salto_cuota_y_desfase_bancario_no_son_inconsistencia() -> None:
    # Cuota habitual $2,500, monto_vencido $3,750 (1.5x) y fecha_ultimo_pago banco != cajero
    cuenta = _cuenta_base(
        monto_vencido=Decimal("3750.00"),
        dias_atraso=5,
        fecha_ultimo_pago=date(2026, 10, 18),  # 3 días después del cajero (2026-10-15)
    )
    decision = _evaluar(cuenta)
    assert not decision.reconciliacion.inconsistencia_critica
    assert decision.prioridad == "P4_GENERAR_DRAFT"


def test_pagos_mas_1_dia_a_tiempo_cuentan_como_buen_historial() -> None:
    cuenta = _cuenta_base(dias_atraso=12)
    decision = _evaluar(cuenta, _historial_bueno(cuenta.cuenta_id))
    assert decision.buen_historial is True
    assert "Amistoso" in decision.motivo_clasificacion


def test_patron_c1007_sin_pendiente_va_a_p2() -> None:
    cuenta = _cuenta_base(
        cuenta_id="C-1007",
        monto_vencido=Decimal("0.00"),
        dias_atraso=6,
        notas="Pago aplicado 22/11/2026 por $2,450.00",
    )
    decision = _evaluar(cuenta, _historial_bueno("C-1007", con_pendiente=False))
    assert decision.reconciliacion.patron_c1007_sin_pendiente is True
    assert decision.prioridad == "P2_REVISION_ESPECIALISTA"


def test_queja_abierta_con_monto_mayor_50k_se_queda_en_p2() -> None:
    cuenta = _cuenta_base(
        cuenta_id="C-1050",
        monto_vencido=Decimal("58000.00"),
        dias_atraso=20,
        notas="20/11 respondió SMS: 'no estoy de acuerdo con los intereses, hablaré con el gerente'",
    )
    decision = _evaluar(cuenta)
    assert decision.prioridad == "P2_REVISION_ESPECIALISTA"
    assert decision.alerta_secundaria_gerente is not None
    assert "$58,000.00" in decision.alerta_secundaria_gerente


def test_convenio_cancelado_y_queja_resuelta_no_bloquean() -> None:
    c_conv = _cuenta_base(
        dias_atraso=28,
        notas="Convenio de pago cancelado el 02/11/2026 por incumplimiento de la primera mensualidad",
    )
    assert _evaluar(c_conv).prioridad == "P4_GENERAR_DRAFT"

    c_queja = _cuenta_base(
        dias_atraso=12,
        notas="Queja Q-1980 por comisión cerrada y resuelta el 10/11/2026; cliente conforme",
    )
    assert _evaluar(c_queja).prioridad == "P4_GENERAR_DRAFT"


def test_guardrail_draft_sucursal_spei_y_llamada_en_formal() -> None:
    cuenta = _cuenta_base(dias_atraso=45)
    draft_formal = generar_template_fallback(cuenta, "Formal + llamada")
    assert draft_formal.guardrail_aprobado is True
    assert draft_formal.requiere_llamada_cobrador is True
    assert "sucursal" in draft_formal.mensaje.lower()
    assert "spei" in draft_formal.mensaje.lower()

    # Mensaje sin SPEI ni aviso de llamada debe fallar guardrail
    errores = validar_guardrail_draft(
        mensaje="Cliente Prueba, pague $5,000.00 del 14/11/2026 en sucursal.",
        requiere_llamada_cobrador=False,
        cuenta=cuenta,
        segmento="Formal + llamada",
    )
    assert any("SPEI" in e for e in errores)
    assert any("llamar" in e for e in errores)
    assert any("requiere_llamada_cobrador" in e for e in errores)
