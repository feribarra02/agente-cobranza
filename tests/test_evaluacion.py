"""Evaluación end-to-end del pipeline sobre datos reales y casos sintéticos (KPIs obligatorios)."""

from datetime import date
from decimal import Decimal

from src.gemini_agent import validar_guardrail_draft
from src.models import CuentaNormalizada
from src.normalizer import format_mxn
from src.pipeline import ejecutar_pipeline
from src.rules_engine import UMBRAL_DIAS_GERENTE, UMBRAL_MONTO_GERENTE, UMBRAL_PROMESAS_GERENTE

FECHA_CORTE = date(2026, 11, 24)


def test_kpis_evaluacion_dataset_real(tmp_path) -> None:
    out_json = tmp_path / "reporte_eval.json"
    reporte = ejecutar_pipeline(
        cuentas_path="data/cuentas.csv",
        historial_path="data/historial_pagos.csv",
        fecha_corte=FECHA_CORTE,
        use_mock=True,
        output_path=out_json,
    )

    decisiones = reporte["decisiones"]
    assert reporte["resumen"]["total_cuentas"] == 60
    assert out_json.exists()

    by_id = {d["cuenta_id"]: d for d in decisiones}

    # Validación explícita de casos emblemáticos reales
    assert by_id["C-1007"]["prioridad"] == "P2_REVISION_ESPECIALISTA"
    assert by_id["C-1031"]["prioridad"] == "P4_GENERAR_DRAFT"
    assert by_id["C-1031"]["draft"]["segmento"] == "Firme + opciones de pago"
    assert by_id["C-1033"]["prioridad"] == "P2_REVISION_ESPECIALISTA"
    assert by_id["C-1050"]["prioridad"] == "P2_REVISION_ESPECIALISTA"
    assert by_id["C-1050"]["alerta_secundaria_gerente"] is not None
    assert by_id["C-1041"]["prioridad"] == "P1_NO_CONTACTAR"
    assert by_id["C-1041"]["alerta_secundaria_gerente"] is not None
    assert by_id["C-1055"]["prioridad"] == "P1_NO_CONTACTAR"
    assert by_id["C-1055"]["alerta_secundaria_gerente"] is not None
    assert by_id["C-1001"]["prioridad"] == "P2_REVISION_ESPECIALISTA"
    assert by_id["C-1058"]["prioridad"] == "P1_NO_CONTACTAR"
    assert by_id["C-1037"]["prioridad"] == "P4_GENERAR_DRAFT"
    assert by_id["C-1038"]["prioridad"] == "P4_GENERAR_DRAFT"
    assert by_id["C-1040"]["prioridad"] == "P4_GENERAR_DRAFT"  # $50,000.00 exactos NO escala
    assert by_id["C-1008"]["prioridad"] == "P3_ESCALAR_GERENTE"  # $50,000.01 sí escala
    assert by_id["C-1018"]["prioridad"] == "P3_ESCALAR_GERENTE"  # 1 + 1 promesa vencida

    # KPI 1: Tasa de Falsos Positivos en Contacto == 0.0%
    # Ninguna cuenta con condición de P1 o P2 (queja, convenio, promesa vigente, pago pendiente,
    # contacto <=5d, teléfono inválido o inconsistencia) puede recibir draft P4.
    falsos_positivos = []
    for d in decisiones:
        if d["prioridad"] == "P4_GENERAR_DRAFT":
            an = d["analisis_notas"]
            rec = d["reconciliacion"]
            c = d["cuenta"]
            if (
                an["menciona_queja_o_disputa"]
                or an["menciona_convenio_o_acuerdo"]
                or an["menciona_pago_o_comprobante"]
                or rec["promesa_vigente"]
                or rec["inconsistencia_critica"]
                or not c["telefono_valido"]
            ):
                falsos_positivos.append(d["cuenta_id"])
    tasa_fp = len(falsos_positivos) / len(decisiones)
    assert tasa_fp == 0.0, f"Falsos positivos en contacto: {falsos_positivos}"

    # KPI 2: Recall en Escalamientos Críticos al Gerente == 100.0%
    esperados_gerente = []
    obtenidos_gerente = []
    for d in decisiones:
        if d["prioridad"] in ("P1_NO_CONTACTAR", "P2_REVISION_ESPECIALISTA"):
            continue
        c = d["cuenta"]
        mv = Decimal(str(c["monto_vencido"]))
        da = int(c["dias_atraso"])
        prom = int(d["promesas_incumplidas_efectivas"])
        supera = (
            mv > UMBRAL_MONTO_GERENTE
            or da > UMBRAL_DIAS_GERENTE
            or prom >= UMBRAL_PROMESAS_GERENTE
        )
        if supera:
            esperados_gerente.append(d["cuenta_id"])
        if d["prioridad"] == "P3_ESCALAR_GERENTE":
            assert d["ficha_gerente"] is not None
            obtenidos_gerente.append(d["cuenta_id"])

    assert set(esperados_gerente) == set(obtenidos_gerente)
    recall_gerente = len(obtenidos_gerente) / len(esperados_gerente)
    assert recall_gerente == 1.0

    # KPI 3: Fidelidad de Datos en Drafts == 100.0%
    drafts = [d for d in decisiones if d["prioridad"] == "P4_GENERAR_DRAFT"]
    assert len(drafts) > 0
    for d in drafts:
        draft = d["draft"]
        assert draft is not None
        cuenta_obj = CuentaNormalizada.model_validate(d["cuenta"])
        errores = validar_guardrail_draft(
            mensaje=draft["mensaje"],
            requiere_llamada_cobrador=draft["requiere_llamada_cobrador"],
            cuenta=cuenta_obj,
            segmento=draft["segmento"],
        )
        assert errores == [], f"Draft inválido en {d['cuenta_id']}: {errores}"
        assert format_mxn(cuenta_obj.monto_vencido) in draft["mensaje"]  # type: ignore[arg-type]
