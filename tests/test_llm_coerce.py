from app.llm import _coerce


def test_dollar_sign_maps_to_usd_even_if_model_omits_it():
    assert _coerce({}, "Logo for Acme, $400").currency_code == "USD"


def test_euro_sign_maps_to_eur():
    assert _coerce({}, "2 mockups at €150 each").currency_code == "EUR"


def test_explicit_code_beats_symbol():
    assert _coerce({}, "$400 AUD for a logo").currency_code == "AUD"


def test_kes_is_left_for_the_human():
    assert _coerce({"currency_code": "KES"}, "KES 20000 for a website").currency_code is None