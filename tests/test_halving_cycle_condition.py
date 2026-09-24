"""Regla 'halving_cycle': True mientras la vela caiga en la ventana de días
transcurridos desde el Halving más reciente (día 0 = fecha del Halving)."""
import numpy as np
import pandas as pd
import pytest

from strategy_engine.conditions import ConditionEvaluator

H4_DATE = pd.Timestamp("2024-04-19", tz="UTC")
H3_DATE = pd.Timestamp("2020-05-11", tz="UTC")


def _df_around(anchor: pd.Timestamp, days_before: int, days_after: int):
    idx = pd.date_range(anchor - pd.Timedelta(days=days_before), anchor + pd.Timedelta(days=days_after), freq="D", tz="UTC")
    return pd.DataFrame({"close": np.linspace(100.0, 200.0, len(idx))}, index=idx)


def test_between_is_true_only_inside_the_window():
    df = _df_around(H4_DATE, days_before=10, days_after=1000)
    rule = {"type": "halving_cycle", "condition": "between", "min_days": 30, "max_days": 900}
    res = ConditionEvaluator.evaluate_rule(df, rule)

    assert bool(res.loc[H4_DATE - pd.Timedelta(days=1)]) is False   # antes del halving: sin ciclo vigente
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=29)]) is False  # justo antes del minimo
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=30)]) is True   # borde inferior inclusive
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=887)]) is True  # dentro de la ventana
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=900)]) is True  # borde superior inclusive
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=901)]) is False


def test_above_condition_is_a_hard_time_stop():
    df = _df_around(H4_DATE, days_before=0, days_after=1000)
    rule = {"type": "halving_cycle", "condition": "above", "min_days": 950}
    res = ConditionEvaluator.evaluate_rule(df, rule)

    assert bool(res.loc[H4_DATE + pd.Timedelta(days=949)]) is False
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=951)]) is True


def test_below_condition():
    df = _df_around(H4_DATE, days_before=0, days_after=100)
    rule = {"type": "halving_cycle", "condition": "below", "max_days": 50}
    res = ConditionEvaluator.evaluate_rule(df, rule)

    assert bool(res.loc[H4_DATE + pd.Timedelta(days=49)]) is True
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=50)]) is False


def test_uses_the_most_recent_past_halving():
    # Un rango que atraviesa el Halving H3 (2020) y llega hasta despues del H4 (2024):
    # los dias transcurridos deben resetearse a 0 en la fecha de cada Halving.
    idx = pd.date_range(H3_DATE + pd.Timedelta(days=100), H4_DATE + pd.Timedelta(days=100), freq="30D", tz="UTC")
    df = pd.DataFrame({"close": np.linspace(100.0, 200.0, len(idx))}, index=idx)
    rule = {"type": "halving_cycle", "condition": "between", "min_days": 0, "max_days": 60}
    res = ConditionEvaluator.evaluate_rule(df, rule)

    # Justo tras el H3 (dia 100) no debe estar en ventana [0,60] respecto al H3...
    assert bool(res.iloc[0]) is False
    # ...pero la primera vela tras el H4 (dia ~70-100 desde H4) puede volver a caer dentro
    # de una ventana [0,60] relativa al NUEVO halving mas reciente en algun punto cercano.
    near_h4 = res.index[res.index >= H4_DATE]
    assert len(near_h4) > 0


def test_non_datetime_index_returns_all_false():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    rule = {"type": "halving_cycle", "condition": "between", "min_days": 0, "max_days": 900}
    res = ConditionEvaluator.evaluate_rule(df, rule)
    assert not res.any()


def test_default_condition_is_between():
    df = _df_around(H4_DATE, days_before=0, days_after=100)
    rule = {"type": "halving_cycle", "min_days": 10, "max_days": 20}
    res = ConditionEvaluator.evaluate_rule(df, rule)
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=15)]) is True
    assert bool(res.loc[H4_DATE + pd.Timedelta(days=25)]) is False
