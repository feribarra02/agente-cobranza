"""Pipeline CLI de Cobranza Preventiva (orquestación determinística + IA acotada)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src.gemini_agent import redactar_draft
from src.models import DecisionCobranza
from src.normalizer import cargar_cuentas, cargar_historial_pagos, format_mxn, parse_fecha
from src.note_analyzer import analizar_notas, is_mock_llm_enabled
from src.reconciler import conciliar_cuenta, indexar_historial_por_cuenta
from src.rules_engine import determinar_segmento_draft, evaluar_cuenta

DEFAULT_FECHA_CORTE = date(2026, 11, 24)
DEFAULT_CUENTAS_PATH = Path("data/cuentas.csv")
DEFAULT_HISTORIAL_PATH = Path("data/historial_pagos.csv")
DEFAULT_OUTPUT_PATH = Path("output/reporte_cobranza.json")


def ejecutar_pipeline(
    cuentas_path: str | Path = DEFAULT_CUENTAS_PATH,
    historial_path: str | Path = DEFAULT_HISTORIAL_PATH,
    fecha_corte: date = DEFAULT_FECHA_CORTE,
    use_mock: Optional[bool] = None,
    output_path: Optional[str | Path] = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Ejecuta el pipeline completo sobre las cuentas y genera el reporte estructurado."""
    modo_mock = is_mock_llm_enabled(use_mock)
    historial = cargar_historial_pagos(historial_path)
    hist_index = indexar_historial_por_cuenta(historial)
    cuentas, auditoria_duplicados = cargar_cuentas(
        cuentas_path,
        historial_ids=hist_index.keys(),
        deduplicar=True,
    )

    decisiones: list[DecisionCobranza] = []
    for cuenta in cuentas:
        analisis, origen_notas = analizar_notas(
            cuenta.notas,
            fecha_corte=fecha_corte,
            use_mock=modo_mock,
        )
        reconciliacion = conciliar_cuenta(
            cuenta=cuenta,
            historial_cuenta=hist_index.get(cuenta.cuenta_id, []),
            analisis_notas=analisis,
            fecha_corte=fecha_corte,
        )
        decision = evaluar_cuenta(
            cuenta=cuenta,
            analisis_notas=analisis,
            reconciliacion=reconciliacion,
            fecha_corte=fecha_corte,
            origen_analisis_notas=origen_notas,  # type: ignore[arg-type]
        )

        if decision.prioridad == "P4_GENERAR_DRAFT":
            segmento = determinar_segmento_draft(
                cuenta.dias_atraso or 1,
                reconciliacion.buen_historial,
            )
            decision.draft = redactar_draft(
                cuenta=cuenta,
                segmento=segmento,
                use_mock=modo_mock,
            )

        decisiones.append(decision)

    conteo = Counter(d.prioridad for d in decisiones)
    resumen = {
        "fecha_corte": fecha_corte.isoformat(),
        "modo_llm": "mock_deterministico" if modo_mock else "gemini-2.5-flash",
        "total_cuentas": len(decisiones),
        "duplicados_consolidados": auditoria_duplicados,
        "conteo_prioridades": {
            "P1_NO_CONTACTAR": conteo.get("P1_NO_CONTACTAR", 0),
            "P2_REVISION_ESPECIALISTA": conteo.get("P2_REVISION_ESPECIALISTA", 0),
            "P3_ESCALAR_GERENTE": conteo.get("P3_ESCALAR_GERENTE", 0),
            "P4_GENERAR_DRAFT": conteo.get("P4_GENERAR_DRAFT", 0),
        },
        "alertas_secundarias_gerente": sum(
            1 for d in decisiones if d.alerta_secundaria_gerente is not None
        ),
    }

    reporte = {
        "resumen": resumen,
        "decisiones": [d.model_dump(mode="json") for d in decisiones],
    }

    if output_path is not None:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    return reporte


def _imprimir_resumen(reporte: dict[str, Any]) -> None:
    res = reporte["resumen"]
    c = res["conteo_prioridades"]
    print("=" * 72)
    print(f"REPORTE COBRANZA PREVENTIVA | fecha_corte={res['fecha_corte']} | modo={res['modo_llm']}")
    print("=" * 72)
    print(f"Total cuentas únicas procesadas : {res['total_cuentas']}")
    if res["duplicados_consolidados"]:
        print(f"Duplicados consolidados        : {', '.join(res['duplicados_consolidados'])}")
    print(f"  P1 - No Contactar / En Espera: {c['P1_NO_CONTACTAR']}")
    print(f"  P2 - Revisión Especialista   : {c['P2_REVISION_ESPECIALISTA']}")
    print(f"  P3 - Escalar a Gerente       : {c['P3_ESCALAR_GERENTE']}")
    print(f"  P4 - Drafts por Aprobar      : {c['P4_GENERAR_DRAFT']}")
    print(f"  Alertas secundarias gerente  : {res['alertas_secundarias_gerente']}")
    print("-" * 72)
    print(f"{'ID':<8} {'Cliente':<28} {'Prioridad':<24} {'Monto':>12} {'Días':>5}")
    print("-" * 72)
    for d in reporte["decisiones"]:
        mv = d["cuenta"]["monto_vencido"]
        mv_txt = format_mxn(parse_monto_str(mv)) if mv is not None else "N/A"
        da = d["cuenta"]["dias_atraso"]
        da_txt = str(da) if da is not None else "N/A"
        print(f"{d['cuenta_id']:<8} {d['cliente'][:27]:<28} {d['prioridad']:<24} {mv_txt:>12} {da_txt:>5}")
    print("=" * 72)


def parse_monto_str(val: Any):  # helper para impresión limpia
    from decimal import Decimal
    return Decimal(str(val))


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline Agente de Cobranza Preventiva")
    parser.add_argument(
        "--fecha-corte",
        default=DEFAULT_FECHA_CORTE.isoformat(),
        help="Fecha de corte YYYY-MM-DD (default: 2026-11-24)",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Forzar modo determinístico/mock sin llamadas a Gemini API",
    )
    parser.add_argument(
        "--cuentas",
        default=str(DEFAULT_CUENTAS_PATH),
        help="Ruta al archivo cuentas.csv",
    )
    parser.add_argument(
        "--historial",
        default=str(DEFAULT_HISTORIAL_PATH),
        help="Ruta al archivo historial_pagos.csv",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Ruta de salida JSON",
    )
    args = parser.parse_args()

    fecha_corte = parse_fecha(args.fecha_corte)
    if fecha_corte is None:
        raise ValueError("fecha_corte no puede ser vacía")

    reporte = ejecutar_pipeline(
        cuentas_path=args.cuentas,
        historial_path=args.historial,
        fecha_corte=fecha_corte,
        use_mock=True if args.mock_llm else None,
        output_path=args.output,
    )
    _imprimir_resumen(reporte)
    print(f"Reporte guardado en: {args.output}")


if __name__ == "__main__":
    main()
