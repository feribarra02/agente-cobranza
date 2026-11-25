"""Pruebas unitarias para normalización de fechas, montos, teléfonos y carga de CSV."""

from datetime import date
from decimal import Decimal

import pytest

from src.normalizer import (
    cargar_cuentas,
    cargar_historial_pagos,
    format_mxn,
    normalizar_fila_cuenta,
    normalizar_telefono,
    parse_fecha,
    parse_monto,
)


def test_parse_fecha_formatos_mixtos() -> None:
    assert parse_fecha("2026-11-24") == date(2026, 11, 24)
    assert parse_fecha("22/11/2026") == date(2026, 11, 22)
    assert parse_fecha("10/27/2026") == date(2026, 10, 27)
    assert parse_fecha("09/30/2026") == date(2026, 9, 30)
    assert parse_fecha("15-nov-2026") == date(2026, 11, 15)
    assert parse_fecha("08-oct-2026") == date(2026, 10, 8)
    assert parse_fecha("03-dic-2025") == date(2025, 12, 3)
    assert parse_fecha("21-ene-2026") == date(2026, 1, 21)
    assert parse_fecha("19-abr-2026") == date(2026, 4, 19)
    assert parse_fecha("23-ago-2026") == date(2026, 8, 23)
    assert parse_fecha("") is None
    assert parse_fecha(None) is None


def test_parse_fecha_invalida_lanza_error() -> None:
    with pytest.raises(ValueError):
        parse_fecha("2026-11-31")


def test_parse_monto_formatos_heterogeneos() -> None:
    assert parse_monto("4.041,13") == Decimal("4041.13")
    assert parse_monto("$4,041.13") == Decimal("4041.13")
    assert parse_monto("4,041.13 MXN") == Decimal("4041.13")
    assert parse_monto("4041.13") == Decimal("4041.13")
    assert parse_monto("4041") == Decimal("4041.00")
    assert parse_monto("$0.00") == Decimal("0.00")
    assert parse_monto("50,000.01 MXN") == Decimal("50000.01")
    assert format_mxn(Decimal("4041.13")) == "$4,041.13"


def test_normalizar_telefono() -> None:
    assert normalizar_telefono("+52 33 7334 9694") == ("3373349694", True)
    assert normalizar_telefono("(33) 1293-2051") == ("3312932051", True)
    assert normalizar_telefono("3389973267") == ("3389973267", True)
    assert normalizar_telefono("N/D") == (None, False)
    assert normalizar_telefono("") == (None, False)


def test_fila_con_fecha_corrupta_registra_error_sin_abortar() -> None:
    cuenta = normalizar_fila_cuenta(
        {
            "cuenta_id": "C-1033",
            "cliente": "Gabriela Lara Gómez",
            "telefono": "(33) 2530-9725",
            "canal_preferido": "WhatsApp",
            "producto": "Crédito automotriz",
            "monto_vencido": "1481.22",
            "dias_atraso": "18",
            "fecha_vencimiento": "2026-11-31",
        }
    )
    assert cuenta.fecha_vencimiento is None
    assert len(cuenta.errores_parseo) == 1
    assert "2026-11-31" in cuenta.errores_parseo[0]


def test_carga_real_consolida_60_cuentas_unicas() -> None:
    hist = cargar_historial_pagos("data/historial_pagos.csv")
    hist_ids = {r.cuenta_id for r in hist}
    cuentas, duplicados = cargar_cuentas("data/cuentas.csv", historial_ids=hist_ids)
    assert len(cuentas) == 60
    assert len(duplicados) == 3
