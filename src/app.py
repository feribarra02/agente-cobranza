"""Interfaz Streamlit Human-in-the-Loop para el Agente de Cobranza Preventiva."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import streamlit as st

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.normalizer import format_mxn  # noqa: E402
from src.note_analyzer import get_gemini_api_key  # noqa: E402
from src.pipeline import DEFAULT_CUENTAS_PATH, DEFAULT_FECHA_CORTE, DEFAULT_HISTORIAL_PATH, ejecutar_pipeline  # noqa: E402

st.set_page_config(
    page_title="Agente de Cobranza Preventiva (HITL)",
    page_icon="💳",
    layout="wide",
)


def _init_state() -> None:
    if "reporte" not in st.session_state:
        st.session_state.reporte = None
    if "draft_actions" not in st.session_state:
        st.session_state.draft_actions = {}
    if "editing_id" not in st.session_state:
        st.session_state.editing_id = None


def _run_and_store(fecha_corte: date, use_mock: bool) -> None:
    reporte = ejecutar_pipeline(
        cuentas_path=DEFAULT_CUENTAS_PATH,
        historial_path=DEFAULT_HISTORIAL_PATH,
        fecha_corte=fecha_corte,
        use_mock=use_mock,
        output_path="output/reporte_cobranza.json",
    )
    st.session_state.reporte = reporte
    st.session_state.draft_actions = {
        d["cuenta_id"]: {
            "estado": d["draft"]["estado_aprobacion"],
            "mensaje": d["draft"]["mensaje"],
        }
        for d in reporte["decisiones"]
        if d.get("draft") is not None
    }
    st.session_state.editing_id = None


def _render_notas_box(d: dict) -> None:
    nota_raw = d["cuenta"].get("notas")
    origen = d.get("origen_analisis_notas", "sin_notas")
    an = d["analisis_notas"]
    with st.expander(f"Análisis de notas (`{origen}`)", expanded=bool(nota_raw)):
        st.write(f"**Nota original:** {nota_raw or '— Sin notas —'}")
        flags = [
            k
            for k in (
                "menciona_queja_o_disputa",
                "menciona_pago_o_comprobante",
                "menciona_convenio_o_acuerdo",
                "menciona_promesa_pago",
                "nota_inconsistente_o_sensible",
            )
            if an.get(k)
        ]
        st.write(f"**Flags activos:** {', '.join(flags) if flags else 'Ninguno'}")
        if an.get("fecha_promesa_en_nota"):
            st.write(f"**Fecha detectada en nota:** `{an['fecha_promesa_en_nota']}`")
        if an.get("evidencia_literal"):
            st.caption(f"Evidencia literal: \"{an['evidencia_literal']}\"")


def _render_account_header(d: dict) -> None:
    c = d["cuenta"]
    mv = format_mxn(Decimal(str(c["monto_vencido"]))) if c["monto_vencido"] is not None else "N/A"
    st.markdown(
        f"### `{d['cuenta_id']}` — {d['cliente']}  \n"
        f"**Monto vencido:** {mv} | **Días atraso:** {c['dias_atraso']} | "
        f"**Vencimiento:** {c.get('fecha_vencimiento') or c.get('fecha_vencimiento_raw') or 'N/A'} | "
        f"**Canal:** {c['canal_preferido']} | **Buen historial:** {'Sí' if d['buen_historial'] else 'No'}"
    )
    st.info(f"**Motivo de clasificación:** {d['motivo_clasificacion']}")
    if d.get("alerta_secundaria_gerente"):
        st.warning(f"⚠️ **Alerta secundaria gerente:** {d['alerta_secundaria_gerente']}")


def main() -> None:
    _init_state()

    st.title("Agente de Cobranza Preventiva — Human-in-the-Loop")
    st.caption(
        "Motor de reglas 100% determinístico + IA acotada (`gemini-3.5-flash-lite`). "
        "Ningún mensaje se envía automáticamente sin aprobación humana."
    )

    with st.sidebar:
        st.header("Configuración de ejecución")
        fecha_corte = st.date_input("Fecha de corte", value=DEFAULT_FECHA_CORTE)
        api_disponible = get_gemini_api_key() is not None
        modo_label = st.radio(
            "Modo de análisis y redacción",
            options=["Modo Mock Determinístico", "Gemini API (gemini-3.5-flash-lite)"],
            index=0,
            help="Si no hay API key o falla Gemini, el sistema usa fallback determinístico automáticamente.",
        )
        use_mock = modo_label == "Modo Mock Determinístico"
        if not use_mock and not api_disponible:
            st.warning("No se detectó GEMINI_API_KEY en el entorno; se aplicará fallback automático.")

        if st.button("Ejecutar análisis de cartera", type="primary", use_container_width=True):
            with st.spinner("Procesando cuentas e historial de pagos..."):
                _run_and_store(fecha_corte, use_mock)

    if st.session_state.reporte is None:
        _run_and_store(DEFAULT_FECHA_CORTE, use_mock=True)

    reporte = st.session_state.reporte
    res = reporte["resumen"]
    conteo = res["conteo_prioridades"]
    decisiones = reporte["decisiones"]

    # Tarjetas KPI superiores
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total cuentas", res["total_cuentas"])
    k2.metric("Drafts por Aprobar (P4)", conteo["P4_GENERAR_DRAFT"])
    k3.metric("Revisión Especialista (P2)", conteo["P2_REVISION_ESPECIALISTA"])
    k4.metric("Escalamientos Gerente (P3)", conteo["P3_ESCALAR_GERENTE"])
    k5.metric("No Contactar / Espera (P1)", conteo["P1_NO_CONTACTAR"])

    if res.get("duplicados_consolidados"):
        st.caption(f"Duplicados consolidados en limpieza: {', '.join(res['duplicados_consolidados'])}")

    tab_drafts, tab_esp, tab_ger, tab_p1 = st.tabs(
        [
            f"Borradores por Aprobar ({conteo['P4_GENERAR_DRAFT']})",
            f"Revisión Especialista ({conteo['P2_REVISION_ESPECIALISTA']})",
            f"Escalamientos Gerente ({conteo['P3_ESCALAR_GERENTE']})",
            f"No Contactar / En Espera ({conteo['P1_NO_CONTACTAR']})",
        ]
    )

    with tab_drafts:
        p4 = [d for d in decisiones if d["prioridad"] == "P4_GENERAR_DRAFT"]
        if not p4:
            st.info("No hay borradores pendientes.")
        for d in p4:
            cid = d["cuenta_id"]
            draft = d["draft"]
            state = st.session_state.draft_actions.get(
                cid,
                {"estado": draft["estado_aprobacion"], "mensaje": draft["mensaje"]},
            )
            with st.container(border=True):
                _render_account_header(d)
                st.write(
                    f"**Segmento:** `{draft['segmento']}` | "
                    f"**`origen_borrador`:** `{draft['origen_borrador']}` | "
                    f"**Requiere llamada cobrador:** `{'Sí' if draft['requiere_llamada_cobrador'] else 'No'}` | "
                    f"**Estado HITL:** `{state['estado'].upper()}`"
                )
                if st.session_state.editing_id == cid:
                    nuevo_msg = st.text_area(
                        "Editar borrador",
                        value=state["mensaje"],
                        key=f"txt_{cid}",
                        max_chars=400,
                    )
                    col_save, col_cancel = st.columns(2)
                    if col_save.button("Guardar edición", key=f"save_{cid}", type="primary"):
                        st.session_state.draft_actions[cid] = {
                            "estado": "editado",
                            "mensaje": nuevo_msg,
                        }
                        st.session_state.editing_id = None
                        st.rerun()
                    if col_cancel.button("Cancelar", key=f"cancel_{cid}"):
                        st.session_state.editing_id = None
                        st.rerun()
                else:
                    st.success(state["mensaje"])
                    b1, b2, b3, _ = st.columns([1, 1, 1, 3])
                    if b1.button("Aprobar", key=f"ok_{cid}", type="primary"):
                        st.session_state.draft_actions[cid]["estado"] = "aprobado"
                        st.rerun()
                    if b2.button("Editar", key=f"ed_{cid}"):
                        st.session_state.editing_id = cid
                        st.rerun()
                    if b3.button("Rechazar", key=f"rej_{cid}"):
                        st.session_state.draft_actions[cid]["estado"] = "rechazado"
                        st.rerun()
                _render_notas_box(d)

    with tab_esp:
        p2 = [d for d in decisiones if d["prioridad"] == "P2_REVISION_ESPECIALISTA"]
        for d in p2:
            with st.container(border=True):
                _render_account_header(d)
                if d["reconciliacion"]["motivos_inconsistencia"]:
                    st.write("**Inconsistencias detectadas:**")
                    for m in d["reconciliacion"]["motivos_inconsistencia"]:
                        st.write(f"- {m}")
                _render_notas_box(d)

    with tab_ger:
        p3 = [d for d in decisiones if d["prioridad"] == "P3_ESCALAR_GERENTE"]
        for d in p3:
            with st.container(border=True):
                _render_account_header(d)
                ficha = d.get("ficha_gerente")
                if ficha:
                    st.write(f"**Regla exacta activada (FichaGerente):** `{ficha['regla_activada']}`")
                _render_notas_box(d)

    with tab_p1:
        p1 = [d for d in decisiones if d["prioridad"] == "P1_NO_CONTACTAR"]
        for d in p1:
            with st.container(border=True):
                _render_account_header(d)
                _render_notas_box(d)


if __name__ == "__main__":
    main()
