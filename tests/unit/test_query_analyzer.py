
import unittest
from unittest.mock import patch

from src.query.analyzer import (
    QueryAnalysisError,
    analisar_pergunta,
    analisar_pergunta_com_contexto,
    analisar_pergunta_estruturada,
)
from src.query.prompts import montar_prompt_analise
from src.query.schemas import (
    KnowledgeBase,
    QueryEntity,
    QueryRoute,
    StructuredField,
    StructuredOperation,
    QueryAnalysis,
    QueryFilters,
    QueryIntent,
)


class QueryAnalyzerTests(unittest.TestCase):
    def _contexto_contratos_2025(self) -> QueryAnalysis:
        return QueryAnalysis(
            route=QueryRoute.STRUCTURED,
            intent=QueryIntent.AGGREGATE,
            entity=QueryEntity.CONTRATO,
            bases=[KnowledgeBase.ESTRUTURADA],
            filters=QueryFilters(ano=2025),
            operations=[StructuredOperation.COUNT],
        )

    def test_acompanhamento_preserva_ano_e_adiciona_esfera(self):
        analysis = analisar_pergunta_com_contexto(
            "E somente os estaduais?", self._contexto_contratos_2025()
        )

        self.assertEqual(analysis.entity, QueryEntity.CONTRATO)
        self.assertEqual(analysis.filters.ano, 2025)
        self.assertEqual(analysis.filters.esfera, "E")

    def test_acompanhamento_substitui_ano_explicito(self):
        analysis = analisar_pergunta_com_contexto(
            "E agora em 2024?", self._contexto_contratos_2025()
        )
        self.assertEqual(analysis.filters.ano, 2024)

    def test_acompanhamento_ambiguo_pede_esclarecimento(self):
        with self.assertRaisesRegex(QueryAnalysisError, "filtro alterado"):
            analisar_pergunta_com_contexto(
                "E os demais?", self._contexto_contratos_2025()
            )

    def test_mudanca_explicita_de_entidade_cria_nova_analise(self):
        analysis = analisar_pergunta_com_contexto(
            "E quantos PCAs existem em 2025?",
            self._contexto_contratos_2025(),
            llm_fn=lambda _prompt: """{
              "route": "structured", "intent": "aggregate", "entity": "pca",
              "bases": ["agregada"], "filters": {"ano": 2025},
              "operations": ["count"], "aggregation_field": null,
              "group_by": [], "order_by": [], "limit": null,
              "semantic_query": null, "needs_document_retrieval": false
            }""",
        )
        self.assertEqual(analysis.entity, QueryEntity.PCA)

    def test_analisa_ata_vigente_com_adesao(self):
        analysis = analisar_pergunta(
            "Quais atas de registro de preços vigentes permitem adesão?",
            llm_fn=lambda _prompt: """{
              "route": "structured",
              "intent": "lookup",
              "entity": "ata_registro_preco",
              "bases": ["conversacional"],
              "filters": {"situacao": "vigente", "possibilidade_adesao": true},
              "operations": ["list"],
              "aggregation_field": null,
              "group_by": [],
              "order_by": [],
              "limit": 20,
              "semantic_query": null,
              "needs_document_retrieval": false
            }""",
        )

        self.assertEqual(analysis.entity, QueryEntity.ATA_REGISTRO_PRECO)
        self.assertTrue(analysis.filters.possibilidade_adesao)

    def test_analisa_total_planejado_em_itens_de_pca(self):
        analysis = analisar_pergunta(
            "Qual o valor total planejado nos itens do PCA de 2025?",
            llm_fn=lambda _prompt: """{
              "route": "structured",
              "intent": "aggregate",
              "entity": "pca_item",
              "bases": ["agregada"],
              "filters": {"ano": 2025, "campo_valor": "valor_planejado"},
              "operations": ["sum"],
              "aggregation_field": "valor_planejado",
              "group_by": [],
              "order_by": [],
              "limit": 20,
              "semantic_query": null,
              "needs_document_retrieval": false
            }""",
        )

        self.assertEqual(analysis.entity, QueryEntity.PCA_ITEM)
        self.assertEqual(analysis.aggregation_field, StructuredField.VALOR_PLANEJADO)

    def test_corrige_media_simples_para_media_ponderada_por_quilo(self):
        analysis = analisar_pergunta(
            "Qual foi o valor médio por quilo de arroz por município em 2025?",
            llm_fn=lambda _prompt: """{
              "route": "structured",
              "intent": "aggregate",
              "entity": "resultado_item",
              "bases": ["estruturada"],
              "filters": {
                "ano": 2025,
                "objeto": "arroz",
                "unidade_medida": "kg"
              },
              "operations": ["avg", "group_by"],
              "aggregation_field": "valor_homologado",
              "group_by": ["municipio"],
              "order_by": [],
              "limit": 20,
              "semantic_query": null,
              "needs_document_retrieval": false
            }""",
        )

        self.assertEqual(
            analysis.operations,
            [StructuredOperation.WEIGHTED_AVG_KG, StructuredOperation.GROUP_BY],
        )
        self.assertEqual(analysis.entity, QueryEntity.RESULTADO_ITEM)
        self.assertEqual(analysis.aggregation_field, StructuredField.VALOR_HOMOLOGADO)
        self.assertIsNone(analysis.filters.unidade_medida)

    def test_nao_converte_media_planejada_de_pca_em_resultado_homologado(self):
        analysis = analisar_pergunta(
            "Qual o valor médio planejado por kg nos itens de PCA de 2025?",
            llm_fn=lambda _prompt: """{
              "route": "structured", "intent": "aggregate",
              "entity": "pca_item", "bases": ["agregada"],
              "filters": {"ano": 2025, "unidade_medida": "kg"},
              "operations": ["avg"],
              "aggregation_field": "valor_unitario",
              "group_by": [], "order_by": [], "limit": 20,
              "semantic_query": null, "needs_document_retrieval": false
            }""",
        )

        self.assertEqual(analysis.entity, QueryEntity.PCA_ITEM)
        self.assertEqual(analysis.operations, [StructuredOperation.AVG])

    def test_prompt_diferencia_item_estimado_de_resultado_homologado(self):
        prompt = montar_prompt_analise(
            "Quais resultados homologados de arroz existem em 2025?"
        )

        self.assertIn('entity "contratacao_item"', prompt)
        self.assertIn('entity "resultado_item"', prompt)
        self.assertIn("Nunca trate valor estimado como valor homologado", prompt)

    def test_analisa_resultado_homologado_com_filtros_tipados(self):
        analysis = analisar_pergunta(
            "Resultados homologados de arroz por órgãos estaduais em maio de 2025",
            llm_fn=lambda _prompt: """{
              "route": "structured",
              "intent": "lookup",
              "entity": "resultado_item",
              "bases": ["estruturada"],
              "filters": {
                "objeto": "arroz",
                "esfera": "E",
                "campo_data": "data_resultado",
                "period_start": "2025-05-01",
                "period_end": "2025-05-31"
              },
              "operations": ["list"],
              "aggregation_field": null,
              "group_by": [],
              "order_by": [],
              "limit": 20,
              "semantic_query": null,
              "needs_document_retrieval": false
            }""",
        )

        self.assertEqual(analysis.entity.value, "resultado_item")
        self.assertEqual(analysis.filters.esfera, "E")
        self.assertEqual(analysis.filters.campo_data.value, "data_resultado")

    def test_prompt_diferencia_valor_individual_de_soma(self):
        prompt = montar_prompt_analise("Qual é o valor do contrato?")

        self.assertIn(
            'Use intent "lookup" e operations ["list"], nunca "sum"',
            prompt,
        )
        self.assertIn(
            'A palavra "valor", isoladamente, não indica uma operação de soma',
            prompt,
        )
        self.assertIn(
            "A identificação do contrato deverá ser esclarecida antes da execução",
            prompt,
        )

    def test_analisa_pergunta_estruturada_com_multiplas_operacoes(self):
        chamadas: list[str] = []

        def llm_fn(prompt: str) -> str:
            chamadas.append(prompt)
            return """{
                "route": "structured",
                "intent": "aggregate",
                "entity": "contrato",
                "bases": ["estruturada"],
                "filters": {
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31"
                },
                "operations": ["sum", "count", "group_by"],
                "aggregation_field": "valor_global",
                "group_by": ["municipio"],
                "order_by": [],
                "limit": null,
                "semantic_query": null,
                "needs_document_retrieval": false
            }"""

        analysis = analisar_pergunta(
            "Qual o valor total e a quantidade de contratos por município?",
            llm_fn=llm_fn,
        )

        self.assertEqual(analysis.route, QueryRoute.STRUCTURED)
        self.assertEqual(
            analysis.operations,
            [
                StructuredOperation.SUM,
                StructuredOperation.COUNT,
                StructuredOperation.GROUP_BY,
            ],
        )
        self.assertIn("Retorne exclusivamente um objeto JSON", chamadas[0])
        self.assertIn("Qual o valor total", chamadas[0])

    def test_aceita_json_em_cerca_markdown(self):
        analysis = analisar_pergunta(
            "Quais documentos de habilitação o edital exige?",
            llm_fn=lambda _prompt: """```json
            {
              "route": "rag",
              "intent": "documental",
              "entity": "documento",
              "bases": ["normativa"],
              "filters": {"tipo_documento": "Edital"},
              "operations": [],
              "aggregation_field": null,
              "group_by": [],
              "order_by": [],
              "limit": null,
              "semantic_query": "documentos exigidos para habilitação",
              "needs_document_retrieval": true
            }
            ```""",
        )

        self.assertEqual(analysis.route, QueryRoute.RAG)
        self.assertEqual(analysis.bases, [KnowledgeBase.NORMATIVA])
        self.assertTrue(analysis.needs_document_retrieval)

    def test_aceita_json_com_texto_antes_e_depois(self):
        analysis = analisar_pergunta(
            "Quantas contratações existem por modalidade em 2026?",
            llm_fn=lambda _prompt: """Plano identificado:\n```json
            {
              "route": "structured",
              "intent": "aggregate",
              "entity": "contratacao",
              "bases": ["estruturada"],
              "filters": {"ano": 2026},
              "operations": ["count", "group_by"],
              "aggregation_field": null,
              "group_by": ["modalidade"],
              "order_by": [],
              "limit": null,
              "semantic_query": null,
              "needs_document_retrieval": false
            }
            ```\nFim do plano.""",
        )

        self.assertEqual(analysis.route, QueryRoute.STRUCTURED)
        self.assertIn(StructuredOperation.COUNT, analysis.operations)
        self.assertIn(StructuredOperation.GROUP_BY, analysis.operations)

    def test_aceita_filtro_de_objeto_documental(self):
        analysis = analisar_pergunta(
            "Quais práticas de sustentabilidade existem para manutenção de elevadores?",
            llm_fn=lambda _prompt: """{
              "route": "rag",
              "intent": "documental",
              "entity": "documento",
              "bases": ["agregada"],
              "filters": {
                "tipo_entidade": "contratacao",
                "objeto": "manutenção de elevadores"
              },
              "operations": [],
              "aggregation_field": null,
              "group_by": [],
              "order_by": [],
              "limit": null,
              "semantic_query": "práticas de sustentabilidade",
              "needs_document_retrieval": true
            }""",
        )

        self.assertEqual(analysis.filters.objeto, "manutenção de elevadores")
        self.assertIn("objeto", montar_prompt_analise("teste"))

    def test_modo_estruturado_obriga_rota_e_base_estruturada(self):
        prompts: list[str] = []

        def llm_fn(prompt: str) -> str:
            prompts.append(prompt)
            return """{
              "route": "structured",
              "intent": "aggregate",
              "entity": "contratacao",
              "bases": ["estruturada"],
              "filters": {"ano": 2026},
              "operations": ["count", "group_by"],
              "aggregation_field": null,
              "group_by": ["modalidade"],
              "order_by": [],
              "limit": null,
              "semantic_query": null,
              "needs_document_retrieval": false
            }"""

        analysis = analisar_pergunta_estruturada(
            "Quantas contratações existem por modalidade em 2026?",
            llm_fn=llm_fn,
        )

        self.assertEqual(analysis.route, QueryRoute.STRUCTURED)
        self.assertIn('route DEVE ser "structured"', prompts[0])
        self.assertIn('bases DEVE ser ["estruturada"]', prompts[0])
        self.assertIn('"quantos", "quantidade" e "número de"', prompts[0])

    def test_modo_estruturado_rejeita_rota_diferente(self):
        with self.assertRaisesRegex(QueryAnalysisError, "endpoint exige 'structured'"):
            analisar_pergunta_estruturada(
                "Quantas contratações existem?",
                llm_fn=lambda _prompt: """{
                  "route": "rag",
                  "intent": "documental",
                  "entity": "documento",
                  "bases": ["agregada"],
                  "filters": {},
                  "operations": [],
                  "aggregation_field": null,
                  "group_by": [],
                  "order_by": [],
                  "limit": null,
                  "semantic_query": "contratações",
                  "needs_document_retrieval": true
                }""",
            )

    def test_aceita_plano_hibrido(self):
        analysis = analisar_pergunta(
            "Quais contratos ultrapassaram o limite previsto no decreto?",
            llm_fn=lambda _prompt: """{
                "route": "hybrid",
                "intent": "comparison",
                "entity": "contrato",
                "bases": ["estruturada", "normativa"],
                "filters": {},
                "operations": ["list"],
                "aggregation_field": null,
                "group_by": [],
                "order_by": [],
                "limit": 20,
                "semantic_query": "limite previsto no decreto",
                "needs_document_retrieval": true
            }""",
        )

        self.assertEqual(analysis.route, QueryRoute.HYBRID)
        self.assertEqual(analysis.operations, [StructuredOperation.LIST])
        self.assertTrue(analysis.needs_document_retrieval)

    def test_normaliza_lookup_em_operations_e_entidade_hibrida(self):
        resposta = """{
          "route": "hybrid",
          "intent": "lookup",
          "entity": "contrato",
          "bases": ["estruturada", "agregada"],
          "filters": {"numero_controle_pncp": "00394452000103-1-015448/2026"},
          "operations": ["lookup", "list"],
          "aggregation_field": "valor_estimado",
          "group_by": [],
          "order_by": [],
          "limit": null,
          "semantic_query": "qual equipamento será adquirido",
          "needs_document_retrieval": true
        }"""
        with patch("src.query.analyzer._registrar_normalizacao") as registrar:
            analysis = analisar_pergunta(
                "Para a contratação 00394452000103-1-015448/2026, informe o valor estimado e o equipamento.",
                llm_fn=lambda _prompt: resposta,
            )

        self.assertEqual(analysis.route, QueryRoute.HYBRID)
        self.assertEqual(analysis.entity.value, "contratacao")
        self.assertEqual(analysis.operations, [StructuredOperation.LIST])
        self.assertIsNone(analysis.aggregation_field)
        registrar.assert_called_once()

    def test_normaliza_rag_com_valor_e_pncp_para_hybrid(self):
        resposta = """{
          "route": "rag",
          "intent": "documental",
          "entity": "contratacao",
          "bases": ["agregada"],
          "filters": {"numero_controle_pncp": "00394502000144-1-009055/2026"},
          "operations": ["list"],
          "aggregation_field": "valor_estimado",
          "group_by": [],
          "order_by": [],
          "limit": null,
          "semantic_query": "serviços de manutenção",
          "needs_document_retrieval": true
        }"""
        with patch("src.query.analyzer._registrar_normalizacao"):
            analysis = analisar_pergunta(
                "Na contratação 00394502000144-1-009055/2026, informe o valor estimado e os serviços previstos no edital.",
                llm_fn=lambda _prompt: resposta,
            )

        self.assertEqual(analysis.route, QueryRoute.HYBRID)
        self.assertEqual(analysis.entity.value, "contratacao")
        self.assertEqual(analysis.bases[0], KnowledgeBase.ESTRUTURADA)
        self.assertEqual(analysis.operations, [StructuredOperation.LIST])
        self.assertIsNone(analysis.aggregation_field)

    def test_normaliza_operacoes_residuais_em_rag_documental(self):
        resposta = """{
          "route": "rag",
          "intent": "documental",
          "entity": "contratacao",
          "bases": ["agregada"],
          "filters": {
            "numero_controle_pncp": "00394502000144-1-009055/2026",
            "objeto": "manutenção dos elevadores"
          },
          "operations": ["count", "group_by"],
          "aggregation_field": null,
          "group_by": ["modalidade"],
          "order_by": [],
          "limit": null,
          "semantic_query": "serviços de manutenção dos elevadores",
          "needs_document_retrieval": true
        }"""
        with patch("src.query.analyzer._registrar_normalizacao") as registrar:
            analysis = analisar_pergunta(
                "No edital da contratação 00394502000144-1-009055/2026, quais serviços de manutenção dos elevadores estão previstos?",
                llm_fn=lambda _prompt: resposta,
            )

        self.assertEqual(analysis.route, QueryRoute.RAG)
        self.assertEqual(analysis.operations, [])
        self.assertIsNone(analysis.filters.objeto)
        self.assertTrue(analysis.needs_document_retrieval)
        registrar.assert_called_once()

    def test_normaliza_operacao_group_by_omitida(self):
        resposta = """{
          "route": "structured",
          "intent": "aggregate",
          "entity": "contratacao",
          "bases": ["estruturada"],
          "filters": {"ano": 2026, "modalidade": "Dispensa"},
          "operations": ["count"],
          "aggregation_field": null,
          "group_by": ["modalidade"],
          "order_by": [],
          "limit": null,
          "semantic_query": null,
          "needs_document_retrieval": false
        }"""
        analysis = analisar_pergunta(
            "Quantas contratações por dispensa existem em 2026?",
            llm_fn=lambda _prompt: resposta,
        )

        self.assertIn(StructuredOperation.GROUP_BY, analysis.operations)

    def test_normaliza_operacao_order_by_omitida(self):
        resposta = """{
          "route": "structured",
          "intent": "lookup",
          "entity": "contratacao",
          "bases": ["estruturada"],
          "filters": {"ano": 2026},
          "operations": ["list"],
          "aggregation_field": null,
          "group_by": [],
          "order_by": [{"field": "valor_estimado", "direction": "desc"}],
          "limit": 10,
          "semantic_query": null,
          "needs_document_retrieval": false
        }"""
        analysis = analisar_pergunta(
            "Liste as 10 contratações de 2026 com maior valor estimado.",
            llm_fn=lambda _prompt: resposta,
        )

        self.assertIn(StructuredOperation.ORDER_BY, analysis.operations)

    def test_normaliza_agrupamento_por_maior_quantidade_e_limiar_de_valor(self):
        resposta = """{
          "route": "structured",
          "intent": "aggregate",
          "entity": "contratacao",
          "bases": ["estruturada"],
          "filters": {"ano": 2026, "valor_estimado": 500000},
          "operations": ["group_by"],
          "aggregation_field": null,
          "group_by": ["municipio"],
          "order_by": [{"field": "count", "direction": "desc"}],
          "limit": null,
          "semantic_query": null,
          "needs_document_retrieval": false
        }"""
        analysis = analisar_pergunta(
            "Quais municípios possuem contratações com valor estimado acima de R$ 500.000 em 2026?",
            llm_fn=lambda _prompt: resposta,
        )

        self.assertEqual(analysis.filters.valor_minimo, 500000)
        self.assertEqual(analysis.filters.campo_valor.value, "valor_estimado")
        self.assertIsNone(analysis.filters.valor_maximo)
        self.assertIn(StructuredOperation.COUNT, analysis.operations)
        self.assertIn(StructuredOperation.GROUP_BY, analysis.operations)
        self.assertIn(StructuredOperation.ORDER_BY, analysis.operations)
        self.assertEqual(analysis.order_by[0].field.value, "count")

    def test_normalizacao_nao_converte_erro_de_tipo_em_erro_interno(self):
        resposta = """{
          "route": "rag",
          "intent": "documental",
          "entity": "documento",
          "bases": ["agregada"],
          "filters": {"numero_controle_pncp": "00394502000144-1-009055/2026"},
          "operations": [{"operacao": "list"}],
          "aggregation_field": {"campo": "valor_estimado"},
          "group_by": [],
          "order_by": [],
          "limit": null,
          "semantic_query": "serviços de manutenção",
          "needs_document_retrieval": true
        }"""
        with patch("src.query.analyzer._registrar_falha_validacao"):
            with self.assertRaises(QueryAnalysisError) as contexto:
                analisar_pergunta(
                    "Na contratação 00394502000144-1-009055/2026, quais serviços existem?",
                    llm_fn=lambda _prompt: resposta,
                )

        self.assertEqual(contexto.exception.erros_validacao[0]["campo"], "operations.0")

    def test_rejeita_json_invalido(self):
        with self.assertRaisesRegex(QueryAnalysisError, "JSON válido"):
            analisar_pergunta("Teste", llm_fn=lambda _prompt: "não é JSON")

    def test_rejeita_operacao_desconhecida_na_rota_rag(self):
        with patch("src.query.analyzer._registrar_falha_validacao"):
            with self.assertRaisesRegex(QueryAnalysisError, "plano inválido") as contexto:
                analisar_pergunta(
                    "Teste",
                    llm_fn=lambda _prompt: """{
                    "route": "rag",
                    "intent": "documental",
                    "entity": "documento",
                    "bases": ["normativa"],
                    "filters": {},
                    "operations": ["operacao_inexistente"],
                    "aggregation_field": null,
                    "group_by": [],
                    "order_by": [],
                    "limit": null,
                    "semantic_query": "teste",
                    "needs_document_retrieval": true
                    }""",
                )
        self.assertEqual(contexto.exception.erros_validacao[0]["campo"], "operations.0")
        self.assertIn(
            "Input should be",
            contexto.exception.erros_validacao[0]["mensagem"],
        )

    def test_expoe_diagnostico_publico_sem_resposta_bruta_da_llm(self):
        erro = QueryAnalysisError(
            "A LLM retornou um plano inválido.",
            erros_validacao=[
                {
                    "campo": "needs_document_retrieval",
                    "mensagem": "A rota hybrid exige needs_document_retrieval igual a true.",
                    "tipo": "value_error",
                }
            ],
        )

        self.assertEqual(
            erro.detalhe_publico(),
            {
                "mensagem": "A LLM retornou um plano inválido.",
                "errosValidacao": [
                    {
                        "campo": "needs_document_retrieval",
                        "mensagem": "A rota hybrid exige needs_document_retrieval igual a true.",
                        "tipo": "value_error",
                    }
                ],
            },
        )

    def test_rejeita_pergunta_vazia_sem_chamar_llm(self):
        def llm_fn(_prompt: str) -> str:
            raise AssertionError("A LLM não deveria ser chamada")

        with self.assertRaisesRegex(QueryAnalysisError, "não pode estar vazia"):
            analisar_pergunta("   ", llm_fn=llm_fn)

    def test_pergunta_catalogo_e_deterministica_e_nao_chama_llm(self):
        def llm_fn(_prompt: str) -> str:
            raise AssertionError("A LLM não deveria ser chamada para catálogo")

        analysis = analisar_pergunta(
            "Qual é a URL do edital listado para a contratação "
            "00394502000144-1-009055/2026?",
            llm_fn=llm_fn,
        )

        self.assertEqual(analysis.route, QueryRoute.CATALOG)
        self.assertEqual(analysis.entity.value, "documento")
        self.assertEqual(analysis.filters.tipo_documento, "Edital")
        self.assertFalse(analysis.needs_document_retrieval)


if __name__ == "__main__":
    unittest.main()
