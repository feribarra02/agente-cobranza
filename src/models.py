"""Modelos Pydantic para el pipeline híbrido de cobranza preventiva."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CuentaNormalizada(BaseModel):
    """Representa una fila normalizada de cuentas.csv con trazabilidad de errores."""

    cuenta_id: str
    cliente: str
    telefono_raw: Optional[str] = None
    telefono_normalizado: Optional[str] = None
    telefono_valido: bool = False
    canal_preferido: Literal["SMS", "WhatsApp"] = "WhatsApp"
    producto: str = ""
    monto_vencido: Optional[Decimal] = None
    dias_atraso: Optional[int] = None
    fecha_vencimiento: Optional[date] = None
    fecha_vencimiento_raw: Optional[str] = None
    fecha_ultimo_pago: Optional[date] = None
    pagos_atrasados_12m: int = 0
    promesas_incumplidas: int = 0
    fecha_promesa_pago: Optional[date] = None
    monto_promesa: Optional[Decimal] = None
    fecha_ultimo_contacto: Optional[date] = None
    notas: Optional[str] = None
    errores_parseo: list[str] = Field(default_factory=list)
    registros_duplicados_fusionados: list[str] = Field(default_factory=list)


class RegistroHistorialPago(BaseModel):
    """Registro normalizado de historial_pagos.csv."""

    cuenta_id: str
    fecha_vencimiento: Optional[date] = None
    fecha_pago: Optional[date] = None
    monto_pagado: Optional[Decimal] = None
    estatus: str


class AnalisisNotas(BaseModel):
    """Salida estructurada del extractor semántico del campo `notas`."""

    menciona_queja_o_disputa: bool = False
    menciona_pago_o_comprobante: bool = False
    menciona_convenio_o_acuerdo: bool = False
    menciona_promesa_pago: bool = False
    fecha_promesa_en_nota: Optional[date] = None
    nota_inconsistente_o_sensible: bool = False
    evidencia_literal: str = ""


class ReconciliacionCuenta(BaseModel):
    """Resultado determinístico del cruce cuentas <-> historial_pagos."""

    cuenta_id: str
    tiene_historial: bool = True
    total_periodos_historial: int = 0
    pagos_a_tiempo: int = 0
    pagos_tarde_12m: int = 0
    tiene_pendiente_en_historial: bool = False
    buen_historial: bool = False
    fecha_ultimo_pago_real: Optional[date] = None
    promesas_incumplidas_efectivas: int = 0
    promesa_vigente: bool = False
    promesa_vencida_sumada: bool = False
    patron_c1007_sin_pendiente: bool = False
    conflicto_promesa_nota: bool = False
    inconsistencia_critica: bool = False
    motivos_inconsistencia: list[str] = Field(default_factory=list)


class FichaGerente(BaseModel):
    """Ficha obligatoria para cuentas escaladas a Gerente (Prioridad 3)."""

    cuenta_id: str
    cliente: str
    monto_vencido: Decimal
    dias_atraso: int
    fecha_vencimiento: Optional[date] = None
    regla_activada: str


class DraftLLMOutput(BaseModel):
    """Esquema acotado que devuelve Gemini al redactar el borrador."""

    mensaje: str
    requiere_llamada_cobrador: bool = False


class DraftRecordatorio(BaseModel):
    """Borrador validado por guardrails para aprobación Human-in-the-Loop."""

    cuenta_id: str
    cliente: str
    canal: Literal["SMS", "WhatsApp"]
    segmento: Literal["Amistoso", "Firme + opciones de pago", "Formal + llamada"]
    mensaje: str
    requiere_llamada_cobrador: bool = False
    origen_borrador: Literal["llm", "template_fallback"] = "llm"
    estado_aprobacion: Literal["pendiente", "aprobado", "editado", "rechazado"] = "pendiente"
    guardrail_aprobado: bool = True
    errores_guardrail: list[str] = Field(default_factory=list)


class DecisionCobranza(BaseModel):
    """Decisión final auditada del motor de reglas para una cuenta."""

    cuenta_id: str
    cliente: str
    prioridad: Literal[
        "P1_NO_CONTACTAR",
        "P2_REVISION_ESPECIALISTA",
        "P3_ESCALAR_GERENTE",
        "P4_GENERAR_DRAFT",
    ]
    categoria_destino: Literal[
        "No Contactar / En Espera",
        "Revisión Especialista Cobranza",
        "Escalamiento a Gerente",
        "Borrador por Aprobar",
    ]
    motivo_clasificacion: str
    reglas_disparadas: list[str] = Field(default_factory=list)
    alerta_secundaria_gerente: Optional[str] = None
    buen_historial: bool = False
    promesas_incumplidas_efectivas: int = 0
    cuenta: CuentaNormalizada
    analisis_notas: AnalisisNotas
    origen_analisis_notas: Literal["llm", "regex_fallback", "sin_notas"] = "sin_notas"
    reconciliacion: ReconciliacionCuenta
    ficha_gerente: Optional[FichaGerente] = None
    draft: Optional[DraftRecordatorio] = None
