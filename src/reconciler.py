"""Motor determinístico de conciliación entre cuentas y historial_pagos."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable, Optional

from src.models import (
    AnalisisNotas,
    CuentaNormalizada,
    ReconciliacionCuenta,
    RegistroHistorialPago,
)

_NOTA_LLAMADA_RE = re.compile(
    r"(por\s+tel[eé]fono\s+el|llam[oó]\s+el|contact[oó]\s+el)",
    re.IGNORECASE,
)


def indexar_historial_por_cuenta(
    historial: Iterable[RegistroHistorialPago],
) -> dict[str, list[RegistroHistorialPago]]:
    """Agrupa los registros del historial de pagos por cuenta_id."""
    indice: dict[str, list[RegistroHistorialPago]] = {}
    for reg in historial:
        indice.setdefault(reg.cuenta_id, []).append(reg)
    return indice


def _validar_conflicto_promesa_nota(
    cuenta: CuentaNormalizada,
    analisis_notas: AnalisisNotas,
) -> Optional[str]:
    """
    Valida consistencia entre fecha_promesa_en_nota y fecha_promesa_pago (que incluye +2 días de gracia).

    Solo reporta conflicto si la fecha en nota contradice claramente tanto a `fecha_promesa_pago`
    como a `fecha_promesa_pago - 2 días` (sin confundir la fecha de llamada telefónica previa
    con la fecha comprometida de pago).
    """
    fecha_nota = analisis_notas.fecha_promesa_en_nota
    fecha_promesa = cuenta.fecha_promesa_pago
    if fecha_nota is None or fecha_promesa is None:
        return None

    fecha_base = fecha_promesa - timedelta(days=2)
    if fecha_nota in (fecha_promesa, fecha_base):
        return None

    # Si la nota registra cuándo llamó el cliente ("Prometió pago por teléfono el DD/MM/YYYY")
    # y esa llamada ocurrió antes o igual que fecha_promesa, es cronológicamente consistente.
    if cuenta.notas and _NOTA_LLAMADA_RE.search(cuenta.notas) and fecha_nota <= fecha_promesa:
        return None

    return (
        f"Fecha promesa en nota ({fecha_nota.isoformat()}) contradice "
        f"fecha_promesa_pago ({fecha_promesa.isoformat()}) y fecha base sin gracia ({fecha_base.isoformat()})"
    )


def conciliar_cuenta(
    cuenta: CuentaNormalizada,
    historial_cuenta: list[RegistroHistorialPago],
    analisis_notas: AnalisisNotas,
    fecha_corte: date,
) -> ReconciliacionCuenta:
    """Ejecuta el cruce determinístico cuentas <-> historial_pagos y evalúa reglas de consistencia."""
    total_periodos = len(historial_cuenta)
    tiene_historial = total_periodos > 0

    pagos_a_tiempo = sum(1 for r in historial_cuenta if r.estatus == "a tiempo")
    pagos_tarde = sum(1 for r in historial_cuenta if r.estatus not in {"a tiempo", "pendiente"})
    tiene_pendiente = any(r.estatus == "pendiente" for r in historial_cuenta)

    if tiene_historial:
        buen_historial = pagos_tarde <= 1
    else:
        buen_historial = cuenta.pagos_atrasados_12m <= 1

    fechas_pago = [r.fecha_pago for r in historial_cuenta if r.estatus != "pendiente" and r.fecha_pago is not None]
    fecha_ultimo_pago_real = max(fechas_pago) if fechas_pago else cuenta.fecha_ultimo_pago

    # Reglas 4 y 5: Promesa de pago (ya incluye +2 días de gracia)
    promesa_vigente = False
    promesa_vencida_sumada = False
    promesas_efectivas = cuenta.promesas_incumplidas

    if cuenta.fecha_promesa_pago is not None:
        if fecha_corte <= cuenta.fecha_promesa_pago:
            promesa_vigente = True
        else:
            pago_posterior = (
                fecha_ultimo_pago_real is not None
                and fecha_ultimo_pago_real >= cuenta.fecha_promesa_pago
            )
            if not pago_posterior:
                promesas_efectivas += 1
                promesa_vencida_sumada = True

    motivos_inconsistencia: list[str] = list(cuenta.errores_parseo)

    # Regla 7 (Patrón C-1007): cuenta activa/vencida sin fila "pendiente" en historial_pagos
    patron_c1007 = False
    es_activa_o_vencida = (
        (cuenta.monto_vencido is not None and cuenta.monto_vencido > 0)
        or (cuenta.dias_atraso is not None and cuenta.dias_atraso > 0)
    )
    if es_activa_o_vencida and tiene_historial and not tiene_pendiente:
        patron_c1007 = True
        motivos_inconsistencia.append(
            "Datos que no cuadran (patrón C-1007): cuenta activa/vencida en cuentas sin periodo 'pendiente' en historial_pagos"
        )

    # Inconsistencias críticas monto vs dias_atraso
    if cuenta.monto_vencido is None:
        motivos_inconsistencia.append("monto_vencido faltante o no parseable")
    if cuenta.dias_atraso is None:
        motivos_inconsistencia.append("dias_atraso faltante o no parseable")

    if cuenta.monto_vencido is not None and cuenta.dias_atraso is not None:
        if cuenta.monto_vencido <= 0 and cuenta.dias_atraso > 0:
            motivos_inconsistencia.append(
                f"Inconsistencia crítica: monto_vencido={cuenta.monto_vencido} <= 0 con dias_atraso={cuenta.dias_atraso} > 0"
            )
        elif cuenta.monto_vencido > 0 and cuenta.dias_atraso <= 0:
            motivos_inconsistencia.append(
                f"Inconsistencia crítica: monto_vencido={cuenta.monto_vencido} > 0 con dias_atraso={cuenta.dias_atraso} <= 0"
            )

    if cuenta.fecha_vencimiento is None and not cuenta.errores_parseo:
        motivos_inconsistencia.append("fecha_vencimiento faltante o no parseable")

    conflicto_msg = _validar_conflicto_promesa_nota(cuenta, analisis_notas)
    conflicto_promesa_nota = conflicto_msg is not None
    if conflicto_msg:
        motivos_inconsistencia.append(conflicto_msg)

    if analisis_notas.nota_inconsistente_o_sensible:
        motivos_inconsistencia.append("Nota marcada como inconsistente o sensible (posible prompt injection)")

    inconsistencia_critica = patron_c1007 or len(motivos_inconsistencia) > 0

    return ReconciliacionCuenta(
        cuenta_id=cuenta.cuenta_id,
        tiene_historial=tiene_historial,
        total_periodos_historial=total_periodos,
        pagos_a_tiempo=pagos_a_tiempo,
        pagos_tarde_12m=pagos_tarde,
        tiene_pendiente_en_historial=tiene_pendiente,
        buen_historial=buen_historial,
        fecha_ultimo_pago_real=fecha_ultimo_pago_real,
        promesas_incumplidas_efectivas=promesas_efectivas,
        promesa_vigente=promesa_vigente,
        promesa_vencida_sumada=promesa_vencida_sumada,
        patron_c1007_sin_pendiente=patron_c1007,
        conflicto_promesa_nota=conflicto_promesa_nota,
        inconsistencia_critica=inconsistencia_critica,
        motivos_inconsistencia=motivos_inconsistencia,
    )
