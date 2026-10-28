from src.query.financial_quality import evaluate_financial_quality


def test_nao_altera_valores_e_nao_alerta_valores_usuais():
    rows = [{"valor_global": 1000.25, "valor_estimado": 1200.50}]

    warnings = evaluate_financial_quality(rows)

    assert warnings == []
    assert rows == [{"valor_global": 1000.25, "valor_estimado": 1200.50}]


def test_sinaliza_negativo_extremo_e_divergencia_sem_expor_registro():
    rows = [
        {"numero_controle_pncp": "identificador", "valor_global": -0.22},
        {"valor_global": 457_322_461_390.80, "valor_estimado": 58_754_997.00},
    ]

    warnings = evaluate_financial_quality(rows)

    assert [warning.code for warning in warnings] == [
        "negative_financial_value",
        "extreme_financial_value",
        "financial_fields_diverge",
    ]
    assert all("identificador" not in warning.message for warning in warnings)


def test_sinaliza_total_agregado_extremo():
    warnings = evaluate_financial_quality(
        [{"sum_valor_estimado": 8_990_000_000_000.00}]
    )

    assert [warning.code for warning in warnings] == ["extreme_financial_value"]


def test_nao_compara_soma_e_media_como_campos_do_mesmo_registro():
    warnings = evaluate_financial_quality(
        [{"sum_valor_global": 1_000_000, "avg_valor_global": 100}]
    )

    assert warnings == []


def test_sinaliza_data_invalida_e_linhas_exatamente_repetidas():
    rows = [
        {"numero_controle_pncp": "x", "data_assinatura": "2025-99-40"},
        {"numero_controle_pncp": "x", "data_assinatura": "2025-99-40"},
    ]

    warnings = evaluate_financial_quality(rows)

    assert [warning.code for warning in warnings] == [
        "invalid_date_value",
        "possible_duplicate_rows",
    ]
    assert warnings[0].affected_rows == 2
    assert warnings[1].affected_rows == 2
