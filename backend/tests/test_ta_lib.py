import math

from services import ta_lib


def test_sequence_helpers_treat_nan_as_missing():
    values = [1.0, math.nan, 3.0, 4.0]

    assert ta_lib.ma(values, 2) == [None, None, None, 3.5]
    assert ta_lib.rolling_sum(values, 2) == [None, None, None, 7.0]
    assert ta_lib.barscount([None, math.nan, 3.0, 4.0]) == [0, 0, 0, 1]


def test_condition_helpers_treat_nan_as_false():
    condition = [False, True, math.nan, False, True]

    assert ta_lib.barslast(condition) == [None, 0, 1, 2, 0]
    assert ta_lib.barslastcount(condition) == [0, 1, 0, 0, 1]
    assert ta_lib.count(condition, 2) == [None, 1, 1, 0, 1]
