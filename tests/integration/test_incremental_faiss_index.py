from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import faiss
import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import create_sqlite_engine, init_db
from app.core.database.readonly import create_readonly_sqlite_engine
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
import app.modules.query.model  # noqa: F401
from src.base_estruturada.pipelines.pipeline_indexacao_faiss import (
    auditar_cobertura,
    iterar_chunks_pendentes,
    main as pipeline_main,
)
from src.index import (
    ARQUIVO_ESTADO,
    ARQUIVO_IDS,
    ARQUIVO_INDICE,
    ProgressoIndexacaoFaiss,
    auditar_indice_faiss,
    carregar_indice_faiss,
    compactar_checkpoints,
    indexar_chunks_incrementalmente,
    preparar_staging,
    restaurar_estado_compactacao,
    _lock_escrita,
)


class IncrementalFaissIndexTests(unittest.TestCase):
    def test_segunda_execucao_embeda_somente_chunk_novo(self):
        chamadas: list[str] = []

        def embedding(texto: str) -> list[float]:
            chamadas.append(texto)
            return [1.0, 0.0] if "alpha" in texto else [0.0, 1.0]

        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            _, primeiro = indexar_chunks_incrementalmente(
                [("c1", "alpha"), ("c2", "beta")],
                embedding,
                diretorio,
                tamanho_lote=1,
                modelo_embedding="fake-v1",
                origem="lote_001",
            )
            _, segundo = indexar_chunks_incrementalmente(
                [("c1", "alpha"), ("c2", "beta"), ("c3", "beta novo")],
                embedding,
                diretorio,
                tamanho_lote=1,
                modelo_embedding="fake-v1",
                origem="lote_002",
            )
            indice = carregar_indice_faiss(diretorio)

        self.assertEqual(primeiro.novos, 2)
        self.assertEqual(segundo.ja_indexados, 2)
        self.assertEqual(segundo.novos, 1)
        self.assertEqual(chamadas, ["alpha", "beta", "beta novo"])
        self.assertEqual(indice.chunk_ids, ["c1", "c2", "c3"])
        self.assertEqual(indice.search([0.0, 1.0], top_k=1)[0][0], "c2")

    def test_estaging_preserva_indice_legado_e_seus_vetores(self):
        with TemporaryDirectory() as pasta:
            origem = Path(pasta) / "legacy"
            staging = Path(pasta) / "staging"
            origem.mkdir()
            indice_legacy = faiss.IndexFlatIP(2)
            indice_legacy.add(np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))
            faiss.write_index(indice_legacy, str(origem / ARQUIVO_INDICE))
            (origem / ARQUIVO_IDS).write_text(
                json.dumps(["old-1", "old-2"]), encoding="utf-8"
            )
            (origem / ARQUIVO_ESTADO).write_text(
                json.dumps(
                    {
                        "modeloEmbedding": "fake-v1",
                        "dimensao": 2,
                        "totalVetores": 2,
                        "origens": ["lote-antigo"],
                    }
                ),
                encoding="utf-8",
            )
            antes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in origem.iterdir()
            }

            preparar_staging(origem, staging)
            indexar_chunks_incrementalmente(
                [("new-1", "novo")],
                lambda _: [0.7, 0.7],
                staging,
                modelo_embedding="fake-v1",
            )

            depois = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in origem.iterdir()
            }
            carregado = carregar_indice_faiss(staging)
            self.assertEqual(antes, depois)
            self.assertEqual(carregado.chunk_ids, ["old-1", "old-2", "new-1"])
            self.assertEqual(carregado.search([1.0, 0.0], 1)[0][0], "old-1")

    def test_indice_rejeita_modelo_dimensao_e_vetor_invalidos(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indexar_chunks_incrementalmente(
                [("c1", "texto")],
                lambda _: [1.0, 0.0],
                diretorio,
                modelo_embedding="fake-v1",
            )
            embeddings_chamados: list[str] = []
            with self.assertRaisesRegex(RuntimeError, "dimensão configurada"):
                indexar_chunks_incrementalmente(
                    [("c2", "outro")],
                    lambda texto: embeddings_chamados.append(texto) or [0.0, 1.0, 0.0],
                    diretorio,
                    modelo_embedding="fake-v1",
                    dimensao_embedding=3,
                )
            self.assertEqual(embeddings_chamados, [])
            with self.assertRaisesRegex(RuntimeError, "modelo de embedding"):
                indexar_chunks_incrementalmente(
                    [("c2", "outro")],
                    lambda _: [0.0, 1.0],
                    diretorio,
                    modelo_embedding="fake-v2",
                )
            with self.assertRaisesRegex(RuntimeError, "dimensão"):
                indexar_chunks_incrementalmente(
                    [("c2", "outro")],
                    lambda _: [1.0, 0.0, 0.0],
                    diretorio,
                    modelo_embedding="fake-v1",
                )
            with self.assertRaisesRegex(ValueError, "finitos"):
                indexar_chunks_incrementalmente(
                    [("c2", "outro")],
                    lambda _: [float("nan"), 0.0],
                    diretorio,
                    modelo_embedding="fake-v1",
                )

    def test_mudanca_de_conteudo_com_mesmo_id_e_detectada(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indexar_chunks_incrementalmente(
                [("c1", "antes")],
                lambda _: [1.0, 0.0],
                diretorio,
                modelo_embedding="fake-v1",
            )
            with self.assertRaisesRegex(RuntimeError, "conteúdo de c1 mudou"):
                indexar_chunks_incrementalmente(
                    [("c1", "depois")],
                    lambda _: [0.0, 1.0],
                    diretorio,
                    modelo_embedding="fake-v1",
                )

    def test_ids_repetidos_com_conteudo_divergente_sao_rejeitados(self):
        with TemporaryDirectory() as pasta:
            with self.assertRaisesRegex(ValueError, "chunk_id duplicado"):
                indexar_chunks_incrementalmente(
                    [("c1", "um"), ("c1", "outro")],
                    lambda _: [1.0, 0.0],
                    Path(pasta),
                    modelo_embedding="fake-v1",
                )

    def test_retoma_checkpoint_orfao_sem_perder_ids(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            salvar = __import__("src.index", fromlist=["_salvar_json_atomico"])._salvar_json_atomico

            def falhar_ao_publicar_estado(caminho, valor):
                if caminho.name == ARQUIVO_ESTADO and valor.get("totalVetores") == 1:
                    raise OSError("falha simulada após publicar checkpoint")
                salvar(caminho, valor)

            with patch(
                "src.index._salvar_json_atomico",
                side_effect=falhar_ao_publicar_estado,
            ):
                with self.assertRaisesRegex(OSError, "falha simulada"):
                    indexar_chunks_incrementalmente(
                        [("c1", "texto")],
                        lambda _: [1.0, 0.0],
                        diretorio,
                        modelo_embedding="fake-v1",
                    )

            resultado, resumo = indexar_chunks_incrementalmente(
                [("c1", "texto"), ("c2", "novo")],
                lambda _: [0.0, 1.0],
                diretorio,
                modelo_embedding="fake-v1",
                tamanho_lote=1,
            )
            auditoria = auditar_indice_faiss(diretorio)
            self.assertEqual(resumo.novos, 1)
            self.assertEqual(resumo.checkpoints_recuperados, 1)
            self.assertEqual(resumo.checkpoints, 2)
            self.assertEqual(resultado.chunk_ids, ["c1", "c2"])
            self.assertEqual(auditoria.inconsistencias, [])

    def test_falha_de_embedding_preserva_checkpoints_confirmados_e_permite_retomar(self):
        chunks = [(f"c{i}", f"texto {i}") for i in range(5)]
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)

            def falhar_no_terceiro(texto: str) -> list[float]:
                if texto == "texto 2":
                    raise RuntimeError("falha simulada no embedding")
                return [1.0, 0.0]

            with self.assertRaisesRegex(RuntimeError, "falha simulada"):
                indexar_chunks_incrementalmente(
                    chunks,
                    falhar_no_terceiro,
                    diretorio,
                    tamanho_checkpoint=2,
                    modelo_embedding="fake-v1",
                )
            apos_falha = carregar_indice_faiss(diretorio)
            self.assertEqual(apos_falha.chunk_ids, ["c0", "c1"])

            chamadas: list[str] = []

            def embedding_retomada(texto: str) -> list[float]:
                chamadas.append(texto)
                return [0.0, 1.0]

            final, resumo = indexar_chunks_incrementalmente(
                chunks,
                embedding_retomada,
                diretorio,
                tamanho_checkpoint=2,
                modelo_embedding="fake-v1",
            )

        self.assertEqual(chamadas, ["texto 2", "texto 3", "texto 4"])
        self.assertEqual(resumo.novos, 3)
        self.assertEqual(len(final.chunk_ids), 5)
        self.assertEqual(len(set(final.chunk_ids)), 5)

    def test_interrupcao_durante_geracao_preserva_checkpoint_confirmado(self):
        eventos: list[ProgressoIndexacaoFaiss] = []
        chunks = [(f"c{i}", f"texto {i}") for i in range(4)]

        def interromper_no_segundo_checkpoint(texto: str) -> list[float]:
            if texto == "texto 2":
                raise KeyboardInterrupt
            return [1.0, 0.0]

        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            with self.assertRaises(KeyboardInterrupt):
                indexar_chunks_incrementalmente(
                    chunks,
                    interromper_no_segundo_checkpoint,
                    diretorio,
                    tamanho_lote=2,
                    tamanho_checkpoint=2,
                    modelo_embedding="fake-v1",
                    progresso_detalhado=eventos.append,
                    total_esperado=4,
                )

            carregado = carregar_indice_faiss(diretorio)

        self.assertEqual(carregado.chunk_ids, ["c0", "c1"])
        self.assertEqual(eventos[-1].estagio, "geracao")
        self.assertEqual(eventos[-1].embeddings_gerados, 2)
        self.assertEqual(eventos[-1].vetores_persistidos, 2)

    def test_interrupcao_durante_persistencia_recupera_shard_sem_reembeddings(self):
        eventos: list[ProgressoIndexacaoFaiss] = []
        chunks = [(f"c{i}", f"texto {i}") for i in range(4)]
        salvar = __import__("src.index", fromlist=["_salvar_json_atomico"])._salvar_json_atomico

        def interromper_publicacao(caminho, valor):
            if caminho.name == ARQUIVO_ESTADO and valor.get("totalVetores") == 4:
                raise KeyboardInterrupt
            salvar(caminho, valor)

        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            with patch("src.index._salvar_json_atomico", side_effect=interromper_publicacao):
                with self.assertRaises(KeyboardInterrupt):
                    indexar_chunks_incrementalmente(
                        chunks,
                        lambda _: [1.0, 0.0],
                        diretorio,
                        tamanho_lote=2,
                        tamanho_checkpoint=2,
                        modelo_embedding="fake-v1",
                        progresso_detalhado=eventos.append,
                        total_esperado=4,
                    )

            confirmados_antes = carregar_indice_faiss(diretorio)
            self.assertEqual(confirmados_antes.chunk_ids, ["c0", "c1"])
            gerados_antes = eventos[-1].embeddings_gerados
            persistidos_antes = eventos[-1].vetores_persistidos
            embeddings_regerados: list[str] = []
            final, resumo = indexar_chunks_incrementalmente(
                chunks,
                lambda texto: embeddings_regerados.append(texto) or [0.0, 1.0],
                diretorio,
                tamanho_lote=2,
                tamanho_checkpoint=2,
                modelo_embedding="fake-v1",
                total_esperado=4,
            )
            auditoria = auditar_indice_faiss(diretorio)

        self.assertEqual(gerados_antes, 4)
        self.assertEqual(persistidos_antes, 2)
        self.assertEqual(embeddings_regerados, [])
        self.assertEqual(resumo.checkpoints_recuperados, 1)
        self.assertEqual(set(final.chunk_ids), {"c0", "c1", "c2", "c3"})
        self.assertEqual(auditoria.inconsistencias, [])

    def test_interrupcao_apos_publicacao_contabiliza_vetores_confirmados(self):
        eventos: list[ProgressoIndexacaoFaiss] = []
        salvar = __import__("src.index", fromlist=["_salvar_json_atomico"])._salvar_json_atomico

        def interromper_apos_publicar(caminho, valor):
            salvar(caminho, valor)
            if caminho.name == ARQUIVO_ESTADO and valor.get("totalVetores") == 2:
                raise KeyboardInterrupt

        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            with patch("src.index._salvar_json_atomico", side_effect=interromper_apos_publicar):
                with self.assertRaises(KeyboardInterrupt):
                    indexar_chunks_incrementalmente(
                        [("c0", "texto 0"), ("c1", "texto 1")],
                        lambda _: [1.0, 0.0],
                        diretorio,
                        tamanho_checkpoint=2,
                        modelo_embedding="fake-v1",
                        progresso_detalhado=eventos.append,
                        total_esperado=2,
                    )
            carregado = carregar_indice_faiss(diretorio)

        self.assertEqual(carregado.chunk_ids, ["c0", "c1"])
        self.assertEqual(eventos[-1].estagio, "persistencia")
        self.assertEqual(eventos[-1].embeddings_gerados, 2)
        self.assertEqual(eventos[-1].vetores_persistidos, 2)

    def test_progresso_por_lote_embedding_com_checkpoint_500(self):
        eventos: list[ProgressoIndexacaoFaiss] = []
        chunks = [(f"c{i}", f"texto {i}") for i in range(500)]
        with TemporaryDirectory() as pasta:
            _, resumo = indexar_chunks_incrementalmente(
                chunks,
                lambda _: [1.0, 0.0],
                Path(pasta),
                tamanho_lote=50,
                tamanho_checkpoint=500,
                modelo_embedding="fake-v1",
                progresso_detalhado=eventos.append,
                total_esperado=500,
            )

        geracao = [evento for evento in eventos if evento.estagio == "geracao"]
        self.assertEqual(
            [evento.embeddings_gerados for evento in geracao],
            list(range(50, 501, 50)),
        )
        self.assertTrue(all(evento.vetores_persistidos == 0 for evento in geracao))
        evento_persistido = [
            evento
            for evento in eventos
            if evento.estagio == "persistencia" and evento.vetores_persistidos == 500
        ]
        self.assertEqual(len(evento_persistido), 1)
        self.assertEqual(resumo.embeddings_gerados, 500)

    def test_tamanho_checkpoint_reduz_shards_sem_alterar_lotes_de_embedding(self):
        chamadas: list[str] = []
        chunks = [(f"c{i}", f"texto {i}") for i in range(11)]

        def embedding(texto: str) -> list[float]:
            chamadas.append(texto)
            return [1.0, 0.0]

        with TemporaryDirectory() as pasta:
            indice, resumo = indexar_chunks_incrementalmente(
                chunks,
                embedding,
                Path(pasta),
                tamanho_lote=2,
                tamanho_checkpoint=5,
                modelo_embedding="fake-v1",
            )
            estado = json.loads(
                (Path(pasta) / ARQUIVO_ESTADO).read_text(encoding="utf-8")
            )

        self.assertEqual(len(chamadas), 11)
        self.assertEqual(resumo.novos, 11)
        self.assertEqual(resumo.checkpoints, 3)
        self.assertEqual(len(estado["checkpoints"]), 3)
        self.assertEqual(indice.chunk_ids, [chunk_id for chunk_id, _ in chunks])

    def test_compactacao_preserva_vetores_e_estado_anterior_para_rollback(self):
        chunks = [(f"c{i}", f"texto {i}") for i in range(10)]
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indexar_chunks_incrementalmente(
                chunks,
                lambda texto: [float(int(texto.rsplit(" ", 1)[1]) + 1), 1.0],
                diretorio,
                tamanho_checkpoint=2,
                modelo_embedding="fake-v1",
            )
            estado_antes = json.loads(
                (diretorio / ARQUIVO_ESTADO).read_text(encoding="utf-8")
            )
            indice_antes = carregar_indice_faiss(diretorio)
            vetores_antes = indice_antes.index.reconstruct_n(0, indice_antes.index.ntotal)

            compactados = compactar_checkpoints(diretorio)

            estado_depois = json.loads(
                (diretorio / ARQUIVO_ESTADO).read_text(encoding="utf-8")
            )
            snapshot_existe = (
                diretorio / estado_depois["snapshotsEstado"][0]
            ).is_file()
            indice_depois = carregar_indice_faiss(diretorio)
            vetores_depois = indice_depois.index.reconstruct_n(0, indice_depois.index.ntotal)
            auditoria = auditar_indice_faiss(diretorio)
            checkpoints_restaurados = restaurar_estado_compactacao(diretorio)
            estado_restaurado = json.loads(
                (diretorio / ARQUIVO_ESTADO).read_text(encoding="utf-8")
            )
            indice_restaurado = carregar_indice_faiss(diretorio)
            auditoria_restaurada = auditar_indice_faiss(diretorio)

        self.assertEqual(compactados, 5)
        self.assertEqual(len(estado_antes["checkpoints"]), 5)
        self.assertEqual(len(estado_depois["checkpoints"]), 1)
        self.assertEqual(len(estado_depois["checkpointsArquivados"]), 5)
        self.assertTrue(snapshot_existe)
        self.assertEqual(indice_antes.chunk_ids, indice_depois.chunk_ids)
        np.testing.assert_array_equal(vetores_antes, vetores_depois)
        self.assertEqual(auditoria.inconsistencias, [])
        self.assertEqual(checkpoints_restaurados, 5)
        self.assertEqual(len(estado_restaurado["checkpoints"]), 5)
        self.assertEqual(indice_antes.chunk_ids, indice_restaurado.chunk_ids)
        np.testing.assert_array_equal(vetores_antes, indice_restaurado.index.reconstruct_n(0, 10))
        self.assertEqual(auditoria_restaurada.inconsistencias, [])

    def test_retomada_descarta_apenas_compactacao_orfa_equivalente(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indexar_chunks_incrementalmente(
                [(f"c{i}", f"texto {i}") for i in range(6)],
                lambda _: [1.0, 0.0],
                diretorio,
                tamanho_checkpoint=2,
                modelo_embedding="fake-v1",
            )
            compactar_checkpoints(diretorio)
            restaurar_estado_compactacao(diretorio)
            estado_path = diretorio / ARQUIVO_ESTADO
            estado = json.loads(estado_path.read_text(encoding="utf-8"))
            compactado = next(
                item
                for item in estado["checkpointsArquivados"]
                if item["nome"].startswith("compact-")
            )
            estado["checkpointsArquivados"].remove(compactado)
            salvar = __import__("src.index", fromlist=["_salvar_json_atomico"])._salvar_json_atomico
            salvar(estado_path, estado)

            resultado, resumo = indexar_chunks_incrementalmente(
                [("novo", "conteúdo novo")],
                lambda _: [0.0, 1.0],
                diretorio,
                modelo_embedding="fake-v1",
            )
            auditoria = auditar_indice_faiss(diretorio)

        self.assertEqual(resumo.checkpoints_recuperados, 1)
        self.assertIn("novo", resultado.chunk_ids)
        self.assertEqual(auditoria.inconsistencias, [])

    def test_retomada_rejeita_checkpoint_orfao_corrompido_antes_do_embedding(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            salvar = __import__("src.index", fromlist=["_salvar_json_atomico"])._salvar_json_atomico

            def falhar_ao_publicar_estado(caminho, valor):
                if caminho.name == ARQUIVO_ESTADO and valor.get("totalVetores") == 1:
                    raise OSError("falha simulada")
                salvar(caminho, valor)

            with patch("src.index._salvar_json_atomico", side_effect=falhar_ao_publicar_estado):
                with self.assertRaises(OSError):
                    indexar_chunks_incrementalmente(
                        [("c1", "texto")],
                        lambda _: [1.0, 0.0],
                        diretorio,
                        modelo_embedding="fake-v1",
                    )
            checkpoint = next((diretorio / "checkpoints").glob("cp-*"))
            (checkpoint / ARQUIVO_IDS).write_text(json.dumps(["adulterado"]), encoding="utf-8")
            embeddings_chamados: list[str] = []

            with self.assertRaisesRegex(RuntimeError, "não pode ser recuperado"):
                indexar_chunks_incrementalmente(
                    [("c2", "novo")],
                    lambda texto: embeddings_chamados.append(texto) or [0.0, 1.0],
                    diretorio,
                    modelo_embedding="fake-v1",
                )

        self.assertEqual(embeddings_chamados, [])

    def test_auditoria_sinaliza_duplicidade_no_indice_legado(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indice = faiss.IndexFlatIP(2)
            indice.add(np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))
            faiss.write_index(indice, str(diretorio / ARQUIVO_INDICE))
            (diretorio / ARQUIVO_IDS).write_text(
                json.dumps(["duplicado", "duplicado"]), encoding="utf-8"
            )
            auditoria = auditar_indice_faiss(diretorio)
            self.assertTrue(any("IDs duplicados" in item for item in auditoria.inconsistencias))
            with self.assertRaisesRegex(RuntimeError, "inconsistente"):
                carregar_indice_faiss(diretorio)

    def test_checksum_do_checkpoint_detecta_troca_de_id_com_mesma_contagem(self):
        with TemporaryDirectory() as pasta:
            diretorio = Path(pasta)
            indexar_chunks_incrementalmente(
                [("c1", "conteudo")],
                lambda _: [1.0, 0.0],
                diretorio,
                modelo_embedding="fake-v1",
            )
            state = json.loads((diretorio / ARQUIVO_ESTADO).read_text(encoding="utf-8"))
            checkpoint = diretorio / "checkpoints" / state["checkpoints"][0]["nome"]
            (checkpoint / ARQUIVO_IDS).write_text(
                json.dumps(["id-adulterado"]), encoding="utf-8"
            )
            auditoria = auditar_indice_faiss(diretorio)
            self.assertTrue(
                any("Checksum inválido" in item for item in auditoria.inconsistencias)
            )
            with self.assertRaisesRegex(RuntimeError, "corrompido|inconsistente"):
                carregar_indice_faiss(diretorio)

    def test_lock_impede_escritores_concorrentes(self):
        with TemporaryDirectory() as pasta:
            with _lock_escrita(Path(pasta)):
                with self.assertRaisesRegex(RuntimeError, "Já existe um escritor"):
                    with _lock_escrita(Path(pasta)):
                        pass


class FaissReadOnlyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "test.db"
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        self.engine = create_sqlite_engine(self.database_url)
        init_db(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        corpus_a = CorpusVersion(version="v1", status="ready")
        corpus_b = CorpusVersion(version="v2", status="ready")
        documents = [
            Document(
                corpus_version=corpus_a,
                source_file="pncp-1",
                title="PNCP",
                base_id="pncp_rj_estruturada",
                doc_type="contrato",
                sensitivity="publico",
            ),
            Document(
                corpus_version=corpus_a,
                source_file="norma-1",
                title="Norma",
                base_id="editais_normativo",
                doc_type="norma",
                sensitivity="publico",
            ),
            Document(
                corpus_version=corpus_b,
                source_file="pncp-v2",
                title="PNCP v2",
                base_id="pncp_rj_estruturada",
                doc_type="contrato",
                sensitivity="publico",
            ),
        ]
        self.db.add_all(
            [
                DocumentChunk(document=documents[0], chunk_id="pncp-1", content="pncp existente"),
                DocumentChunk(document=documents[0], chunk_id="pncp-2", content="pncp pendente"),
                DocumentChunk(document=documents[1], chunk_id="norma-1", content="texto normativo"),
                DocumentChunk(document=documents[2], chunk_id="pncp-v2", content="outra versão"),
            ]
        )
        self.db.commit()

    def test_selecao_aplica_filtros_e_limite_depois_de_ids_indexados(self):
        resultado = list(
            iterar_chunks_pendentes(
                self.db,
                ids_indexados={"pncp-1"},
                base_id="pncp_rj_estruturada",
                tipo="contrato",
                corpus_version_id=1,
                limite_chunks_novos=1,
            )
        )
        self.assertEqual(resultado, [("pncp-2", "pncp pendente")])

    def test_selecao_normativa_e_jsonl_respeita_filtro_de_base(self):
        arquivo = Path(self.temporary.name) / "ids.jsonl"
        arquivo.write_text(
            "\n".join(
                json.dumps({"chunk_id": chunk_id})
                for chunk_id in ("pncp-2", "norma-1")
            ),
            encoding="utf-8",
        )
        resultado = list(
            iterar_chunks_pendentes(
                self.db,
                ids_indexados=set(),
                arquivo_chunks=arquivo,
                base_id="editais_normativo",
                tipo="norma",
            )
        )
        self.assertEqual(resultado, [("norma-1", "texto normativo")])

    def test_cli_ate_concluir_processa_todos_os_pendentes_apenas_da_base(self):
        from types import SimpleNamespace

        with TemporaryDirectory() as pasta:
            indice_dir = Path(pasta) / "staging"
            embedding_fake = lambda _: [1.0] + [0.0] * 1023
            modulo_embedding = SimpleNamespace(
                EMBEDDING_MODEL="test-model",
                gerar_embedding=embedding_fake,
            )
            saida = io.StringIO()
            hash_antes = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
            argumentos = [
                "pipeline_indexacao_faiss",
                "--indice-dir",
                str(indice_dir),
                "--base-id",
                "pncp_rj_estruturada",
                "--ate-concluir",
                "--limite-chunks-novos",
                "2",
                "--tamanho-lote",
                "2",
                "--tamanho-checkpoint",
                "2",
            ]
            with (
                patch.object(settings, "database_url", self.database_url),
                patch.dict(sys.modules, {"llm_ecoa": modulo_embedding}),
                patch.object(sys, "argv", argumentos),
                redirect_stdout(saida),
            ):
                pipeline_main()
            self.assertEqual(len(carregar_indice_faiss(indice_dir).chunk_ids), 2)
            argumentos.remove("--limite-chunks-novos")
            argumentos.remove("2")
            with (
                patch.object(settings, "database_url", self.database_url),
                patch.dict(sys.modules, {"llm_ecoa": modulo_embedding}),
                patch.object(sys, "argv", argumentos),
                redirect_stdout(saida),
            ):
                pipeline_main()
            resultado = carregar_indice_faiss(indice_dir)
            hash_depois = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
            self.assertEqual(set(resultado.chunk_ids), {"pncp-1", "pncp-2", "pncp-v2"})
            self.assertNotIn("norma-1", resultado.chunk_ids)
            self.assertIn("novos=2", saida.getvalue())
            self.assertIn("novos=1", saida.getvalue())
            self.assertEqual(hash_depois, hash_antes)

    def test_cli_keyboard_interrupt_sai_com_codigo_130(self):
        from types import SimpleNamespace

        with TemporaryDirectory() as pasta:
            modulo_embedding = SimpleNamespace(
                EMBEDDING_MODEL="test-model",
                gerar_embedding=lambda _: [1.0] + [0.0] * 1023,
            )
            with (
                patch.object(settings, "database_url", self.database_url),
                patch.dict(sys.modules, {"llm_ecoa": modulo_embedding}),
                patch(
                    "src.base_estruturada.pipelines.pipeline_indexacao_faiss.indexar_chunks_incrementalmente",
                    side_effect=KeyboardInterrupt,
                ),
                patch.object(
                    sys,
                    "argv",
                    [
                        "pipeline_indexacao_faiss",
                        "--indice-dir",
                        str(Path(pasta) / "staging"),
                        "--ate-concluir",
                    ],
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as interrupcao:
                    pipeline_main()

        self.assertEqual(interrupcao.exception.code, 130)

    def test_cli_registra_tipo_e_estagio_sem_mensagem_da_excecao(self):
        from types import SimpleNamespace

        with TemporaryDirectory() as pasta:
            def embedding_com_erro(_: str) -> list[float]:
                raise RuntimeError("sensitive-provider-response")

            modulo_embedding = SimpleNamespace(
                EMBEDDING_MODEL="test-model",
                gerar_embedding=embedding_com_erro,
            )
            with (
                patch.object(settings, "database_url", self.database_url),
                patch.dict(sys.modules, {"llm_ecoa": modulo_embedding}),
                patch.object(
                    sys,
                    "argv",
                    [
                        "pipeline_indexacao_faiss",
                        "--indice-dir",
                        str(Path(pasta) / "staging"),
                        "--ate-concluir",
                    ],
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertLogs(
                    "src.base_estruturada.pipelines.pipeline_indexacao_faiss",
                    level="ERROR",
                ) as logs,
            ):
                with self.assertRaises(SystemExit) as falha:
                    pipeline_main()

        self.assertEqual(falha.exception.code, 1)
        mensagem_log = "\n".join(logs.output)
        self.assertIn("stage=geracao", mensagem_log)
        self.assertIn("error_type=RuntimeError", mensagem_log)
        self.assertNotIn("sensitive-provider-response", mensagem_log)

    def test_cobertura_classifica_documentos_e_bases(self):
        with TemporaryDirectory() as pasta:
            indexar_chunks_incrementalmente(
                [("pncp-1", "pncp existente"), ("norma-1", "texto normativo")],
                lambda texto: [1.0, 0.0] if "pncp" in texto else [0.0, 1.0],
                Path(pasta),
                modelo_embedding="fake-v1",
            )
            cobertura = auditar_cobertura(self.db, Path(pasta))
        self.assertEqual(cobertura["bases"]["editais_normativo"]["chunks_pendentes"], 0)
        self.assertEqual(
            cobertura["bases"]["pncp_rj_estruturada"]["chunks_pendentes"], 2
        )
        self.assertEqual(
            cobertura["bases"]["pncp_rj_estruturada"]["documentos_parciais"], 1
        )
        self.assertEqual(
            cobertura["bases"]["pncp_rj_estruturada"]["documentos_sem_vetores"], 1
        )

    def test_auditoria_detecta_conteudo_alterado_de_chunk_indexado(self):
        with TemporaryDirectory() as pasta:
            indexar_chunks_incrementalmente(
                [("pncp-1", "pncp existente")],
                lambda _: [1.0, 0.0],
                Path(pasta),
                modelo_embedding="fake-v1",
            )
            chunk = self.db.scalar(
                select(DocumentChunk).where(DocumentChunk.chunk_id == "pncp-1")
            )
            chunk.content = "texto alterado após indexação"
            self.db.commit()
            cobertura = auditar_cobertura(self.db, Path(pasta))
        self.assertEqual(cobertura["conteudos_alterados"], 1)
        self.assertTrue(
            any("mudaram de conteúdo" in item for item in cobertura["inconsistencias"])
        )

    def test_conexao_readonly_impede_ddl_e_dml(self):
        ro_engine = create_readonly_sqlite_engine(self.database_url)
        self.addCleanup(ro_engine.dispose)
        with Session(ro_engine) as readonly_db:
            self.assertEqual(readonly_db.execute(text("PRAGMA query_only")).scalar_one(), 1)
            with self.assertRaises(Exception):
                readonly_db.execute(text("CREATE TABLE forbidden (id INTEGER)"))
        with self.engine.connect() as connection:
            tables = connection.execute(
                text("SELECT name FROM sqlite_master WHERE name='forbidden'")
            ).all()
        self.assertEqual(tables, [])

    def test_comando_auditar_emite_json_sem_inicializar_banco(self):
        with TemporaryDirectory() as pasta:
            indice_dir = Path(pasta) / "index"
            indexar_chunks_incrementalmente(
                [("pncp-1", "pncp existente")],
                lambda _: [1.0, 0.0],
                indice_dir,
                modelo_embedding="fake-v1",
            )
            saida = io.StringIO()
            with (
                patch.object(settings, "database_url", self.database_url),
                patch.object(
                    sys,
                    "argv",
                    [
                        "pipeline_indexacao_faiss",
                        "--auditar",
                        "--indice-dir",
                        str(indice_dir),
                    ],
                ),
                redirect_stdout(saida),
            ):
                pipeline_main()
            relatorio = json.loads(saida.getvalue())
        self.assertEqual(relatorio["total_vetores_faiss"], 1)
        self.assertEqual(
            relatorio["bases"]["pncp_rj_estruturada"]["chunks_pendentes"], 2
        )

    def test_cli_subprocess_registra_todos_os_modelos_sem_escrever_sqlite(self):
        with TemporaryDirectory() as pasta:
            indice_dir = Path(pasta) / "index"
            indexar_chunks_incrementalmente(
                [("pncp-1", "pncp existente")],
                lambda _: [1.0, 0.0],
                indice_dir,
                modelo_embedding="fake-v1",
            )
            hash_antes = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path.cwd())
            environment["DATABASE_URL"] = self.database_url
            comando = [
                sys.executable,
                "-m",
                "src.base_estruturada.pipelines.pipeline_indexacao_faiss",
                "--auditar",
                "--indice-dir",
                str(indice_dir),
            ]
            resultado = subprocess.run(
                comando,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=60,
            )
            hash_depois = hashlib.sha256(self.database_path.read_bytes()).hexdigest()

        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        relatorio = json.loads(resultado.stdout)
        self.assertEqual(relatorio["total_vetores_faiss"], 1)
        self.assertIn("editais_normativo", relatorio["bases"])
        self.assertEqual(hash_depois, hash_antes)


if __name__ == "__main__":
    unittest.main()
