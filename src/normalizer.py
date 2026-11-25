"""Normalización determinística de fechas, montos, teléfonos y deduplicación de CSV."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from src.models import CuentaNormalizada, RegistroHistorialPago

MESES_ES: dict[str, int] = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

_TWO_PLACES = Decimal("0.01")


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "n/a", "na", "n/d"}


def parse_fecha(value: object) -> Optional[date]:
    """Parsea fechas YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY (si día>12) o DD-mmm-YYYY en español."""
    if _is_empty(value):
        return None

    raw = str(value).strip()

    # 1. ISO YYYY-MM-DD
    m_iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if m_iso:
        year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        return date(year, month, day)

    # 2. Barras DD/MM/YYYY (con desambiguación MM/DD/YYYY si el segundo grupo > 12)
    m_slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m_slash:
        p1, p2, year = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        if p2 > 12 and p1 <= 12:
            month, day = p1, p2
        else:
            day, month = p1, p2
        return date(year, month, day)

    # 3. Español DD-mmm-YYYY
    m_es = re.fullmatch(r"(\d{1,2})-([a-zA-ZáéíóúÁÉÍÓÚ]{3,4})-(\d{4})", raw)
    if m_es:
        day = int(m_es.group(1))
        mes_txt = m_es.group(2).lower()
        year = int(m_es.group(3))
        if mes_txt not in MESES_ES:
            raise ValueError(f"Mes en español desconocido: '{raw}'")
        return date(year, MESES_ES[mes_txt], day)

    raise ValueError(f"Formato de fecha no reconocido: '{raw}'")


def parse_monto(value: object) -> Optional[Decimal]:
    """Convierte montos heterogéneos (europeo, MXN, $, enteros) a Decimal(0.01)."""
    if _is_empty(value):
        return None

    raw = str(value).strip()
    cleaned = re.sub(r"(?i)\bmxn\b", "", raw)
    cleaned = cleaned.replace("$", "").replace(" ", "").strip()
    if not cleaned:
        raise ValueError(f"Monto vacío tras limpieza: '{raw}'")

    if not re.fullmatch(r"-?[\d.,]+", cleaned):
        raise ValueError(f"Monto con caracteres inválidos: '{raw}'")

    has_comma = "," in cleaned
    has_dot = "." in cleaned

    if has_comma and has_dot:
        # El último separador define los decimales (ej. "4.041,13" vs "4,041.13")
        if cleaned.rfind(",") > cleaned.rfind("."):
            normalized = cleaned.replace(".", "").replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    elif has_comma:
        # Ej. "4041,13" vs "4,041"
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", cleaned):
            normalized = cleaned.replace(",", "")
        else:
            normalized = cleaned.replace(",", ".")
    elif has_dot:
        # Ej. "4.041.000" (miles) vs "4041.13"
        if cleaned.count(".") > 1 and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", cleaned):
            normalized = cleaned.replace(".", "")
        else:
            normalized = cleaned
    else:
        normalized = cleaned

    try:
        dec = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"No se pudo convertir monto '{raw}': {exc}") from exc

    return dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def format_mxn(amount: Decimal) -> str:
    """Formatea un Decimal a '$X,XXX.XX'."""
    quantized = amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return f"${quantized:,.2f}"


def normalizar_telefono(value: object) -> tuple[Optional[str], bool]:
    """Limpia un teléfono mexicano y valida que tenga 10 dígitos útiles."""
    if _is_empty(value):
        return None, False
    raw = str(value).strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("52"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("521"):
        digits = digits[3:]
    valido = len(digits) == 10
    return (digits if digits else None), valido


def _parse_int(value: object, default: Optional[int] = None) -> Optional[int]:
    if _is_empty(value):
        return default
    raw = str(value).strip()
    return int(Decimal(raw))


def _leer_tabla(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def normalizar_fila_cuenta(row: dict[str, object]) -> CuentaNormalizada:
    """Normaliza un diccionario crudo de una fila de cuentas sin abortar ante datos corruptos."""
    errores: list[str] = []
    cuenta_id = str(row.get("cuenta_id", "")).strip()
    cliente = str(row.get("cliente", "")).strip()
    producto = "" if _is_empty(row.get("producto")) else str(row.get("producto")).strip()

    canal_raw = "" if _is_empty(row.get("canal_preferido")) else str(row.get("canal_preferido")).strip()
    canal: str = "SMS" if canal_raw.upper() == "SMS" else "WhatsApp"

    tel_raw = None if _is_empty(row.get("telefono")) else str(row.get("telefono")).strip()
    tel_norm, tel_valido = normalizar_telefono(tel_raw)

    monto_vencido: Optional[Decimal] = None
    try:
        monto_vencido = parse_monto(row.get("monto_vencido"))
    except ValueError as exc:
        errores.append(f"monto_vencido inválido: {exc}")

    dias_atraso: Optional[int] = None
    try:
        dias_atraso = _parse_int(row.get("dias_atraso"))
    except (ValueError, InvalidOperation) as exc:
        errores.append(f"dias_atraso inválido: {exc}")

    fecha_venc_raw = None if _is_empty(row.get("fecha_vencimiento")) else str(row.get("fecha_vencimiento")).strip()
    fecha_vencimiento: Optional[date] = None
    try:
        fecha_vencimiento = parse_fecha(row.get("fecha_vencimiento"))
    except ValueError as exc:
        errores.append(f"fecha_vencimiento inválida ({fecha_venc_raw}): {exc}")

    fecha_ultimo_pago: Optional[date] = None
    try:
        fecha_ultimo_pago = parse_fecha(row.get("fecha_ultimo_pago"))
    except ValueError as exc:
        errores.append(f"fecha_ultimo_pago inválida: {exc}")

    fecha_promesa_pago: Optional[date] = None
    try:
        fecha_promesa_pago = parse_fecha(row.get("fecha_promesa_pago"))
    except ValueError as exc:
        errores.append(f"fecha_promesa_pago inválida: {exc}")

    monto_promesa: Optional[Decimal] = None
    try:
        monto_promesa = parse_monto(row.get("monto_promesa"))
    except ValueError as exc:
        errores.append(f"monto_promesa inválido: {exc}")

    fecha_ultimo_contacto: Optional[date] = None
    try:
        fecha_ultimo_contacto = parse_fecha(row.get("fecha_ultimo_contacto"))
    except ValueError as exc:
        errores.append(f"fecha_ultimo_contacto inválida: {exc}")

    pagos_atrasados_12m = _parse_int(row.get("pagos_atrasados_12m"), default=0) or 0
    promesas_incumplidas = _parse_int(row.get("promesas_incumplidas"), default=0) or 0

    notas_val = row.get("notas")
    notas = None if _is_empty(notas_val) else str(notas_val).strip()

    return CuentaNormalizada(
        cuenta_id=cuenta_id,
        cliente=cliente,
        telefono_raw=tel_raw,
        telefono_normalizado=tel_norm,
        telefono_valido=tel_valido,
        canal_preferido=canal,  # type: ignore[arg-type]
        producto=producto,
        monto_vencido=monto_vencido,
        dias_atraso=dias_atraso,
        fecha_vencimiento=fecha_vencimiento,
        fecha_vencimiento_raw=fecha_venc_raw,
        fecha_ultimo_pago=fecha_ultimo_pago,
        pagos_atrasados_12m=pagos_atrasados_12m,
        promesas_incumplidas=promesas_incumplidas,
        fecha_promesa_pago=fecha_promesa_pago,
        monto_promesa=monto_promesa,
        fecha_ultimo_contacto=fecha_ultimo_contacto,
        notas=notas,
        errores_parseo=errores,
    )


def _calidad_registro(c: CuentaNormalizada) -> tuple[int, int, int, int]:
    """Puntúa precisión para elegir el mejor registro al fusionar duplicados."""
    has_cents = 1 if (c.monto_vencido is not None and c.monto_vencido != c.monto_vencido.to_integral_value()) else 0
    no_errors = 1 if not c.errores_parseo else 0
    name_len = len(c.cliente)
    non_empty_notes = 1 if c.notas else 0
    return (no_errors, has_cents, name_len, non_empty_notes)


def cargar_historial_pagos(path: str | Path) -> list[RegistroHistorialPago]:
    """Carga y normaliza historial_pagos.csv (o Excel)."""
    df = _leer_tabla(Path(path))
    registros: list[RegistroHistorialPago] = []
    for row in df.to_dict(orient="records"):
        cid = str(row.get("cuenta_id", "")).strip()
        estatus = "" if _is_empty(row.get("estatus")) else str(row.get("estatus")).strip().lower()
        try:
            fv = parse_fecha(row.get("fecha_vencimiento"))
        except ValueError:
            fv = None
        try:
            fp = parse_fecha(row.get("fecha_pago"))
        except ValueError:
            fp = None
        try:
            mp = parse_monto(row.get("monto_pagado"))
        except ValueError:
            mp = None
        registros.append(
            RegistroHistorialPago(
                cuenta_id=cid,
                fecha_vencimiento=fv,
                fecha_pago=fp,
                monto_pagado=mp,
                estatus=estatus,
            )
        )
    return registros


def cargar_cuentas(
    path: str | Path,
    historial_ids: Optional[Iterable[str]] = None,
    deduplicar: bool = True,
) -> tuple[list[CuentaNormalizada], list[str]]:
    """Carga cuentas.csv, normaliza campos y consolida filas duplicadas (ID y persona/teléfono sin historial propio)."""
    df = _leer_tabla(Path(path))
    raw_accounts = [normalizar_fila_cuenta(r) for r in df.to_dict(orient="records")]
    if not deduplicar:
        return raw_accounts, []

    auditoria_duplicados: list[str] = []

    # Paso 1: Deduplicar por cuenta_id conservando el registro más completo/preciso
    por_id: dict[str, CuentaNormalizada] = {}
    for acc in raw_accounts:
        if acc.cuenta_id not in por_id:
            por_id[acc.cuenta_id] = acc
        else:
            actual = por_id[acc.cuenta_id]
            if _calidad_registro(acc) > _calidad_registro(actual):
                acc.registros_duplicados_fusionados = actual.registros_duplicados_fusionados + [
                    f"{actual.cuenta_id} (fila duplicada por ID)"
                ]
                por_id[acc.cuenta_id] = acc
            else:
                actual.registros_duplicados_fusionados.append(f"{acc.cuenta_id} (fila duplicada por ID)")
            auditoria_duplicados.append(f"{acc.cuenta_id} (duplicado exacto de ID)")

    unicas_id = list(por_id.values())
    if historial_ids is None:
        return unicas_id, auditoria_duplicados

    hist_set = set(historial_ids)
    # Paso 2: Detectar duplicados con distinto cuenta_id pero idéntica persona/teléfono/monto/vencimiento
    # cuando el duplicado carece de historial propio (ej. C-1061 duplicando a C-1017).
    clave_a_principal: dict[tuple[str, str, Optional[Decimal], Optional[date]], CuentaNormalizada] = {}
    resultado: list[CuentaNormalizada] = []

    # Procesar primero cuentas que sí tienen historial
    ordenadas = sorted(unicas_id, key=lambda c: (0 if c.cuenta_id in hist_set else 1, c.cuenta_id))
    for acc in ordenadas:
        clave = (
            acc.cliente.strip().lower(),
            acc.telefono_normalizado or "",
            acc.monto_vencido,
            acc.fecha_vencimiento,
        )
        if (
            acc.telefono_normalizado
            and clave in clave_a_principal
            and acc.cuenta_id not in hist_set
        ):
            principal = clave_a_principal[clave]
            principal.registros_duplicados_fusionados.append(
                f"{acc.cuenta_id} (duplicado de {principal.cuenta_id} sin historial propio)"
            )
            auditoria_duplicados.append(f"{acc.cuenta_id} -> {principal.cuenta_id}")
        else:
            clave_a_principal[clave] = acc
            resultado.append(acc)

    # Preservar orden original aproximado por cuenta_id
    orden_original = {c.cuenta_id: idx for idx, c in enumerate(unicas_id)}
    resultado.sort(key=lambda c: orden_original.get(c.cuenta_id, 999999))
    return resultado, auditoria_duplicados
