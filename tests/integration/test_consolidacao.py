"""Testes exclusivamente temporários, sem embeddings remotos."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import create_sqlite_engine, init_db
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from src.consolidacao import (BASE, check, consolidate, database_digest, index_audit, migrate,
                              readonly, rows, run, snapshot)
from src.faiss_errors import FaissIndexCorruptError
from src.index import carregar_indice_faiss, indexar_chunks_incrementalmente
from src.query.schemas import KnowledgeBase
from src.retrieve import retrieve_hibrido


def embedding(text):
    return [1., 0.] if "dispensa" in text.lower() else [0., 1.]


@pytest.fixture
def corpus(tmp_path):
    for filename, base, text, chunk in [
        ("source.db", BASE, "Artigo 86 Lei 14133 fiscalização obrigações", "norm:86"),
        ("target.db", "pncp_rj_estruturada", "Contratação dispensa 2026 valor estimado", "pncp:1"),
    ]:
        engine = create_sqlite_engine(f"sqlite:///{(tmp_path/filename).as_posix()}")
        init_db(engine)
        with Session(engine) as session:
            doc = Document(corpus_version=CorpusVersion(version=base, status="ready"),
                           source_file=base+"|fonte", title="Título "+base,
                           base_id=base, doc_type="lei" if base == BASE else "contrato",
                           source_url="https://fonte.test/"+base, sensitivity="publico")
            session.add(DocumentChunk(document=doc, chunk_id=chunk, content=text, base_id=base))
            session.commit()
        engine.dispose()
        indexar_chunks_incrementalmente([(chunk, text)], embedding,
            tmp_path / ("normative" if base == BASE else "pncp"), modelo_embedding="test")
    return tmp_path


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def test_migration_preserves_maps_relationships_and_is_idempotent(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as dst:
        before = rows(dst, "document_chunks")
        snapshot(src, corpus/"source.backup.db")
        snapshot(dst, corpus/"target.backup.db")
        result = migrate(src,dst)
        assert result["inserted"] == {"corpus_versions":1,"documents":1,"document_chunks":1}
        assert result["id_maps"]["documents"][1] == 2
        assert rows(dst,"document_chunks")[0] == before[0]
        assert rows(dst,"document_chunks")[1]["document_id"] == 2
        assert rows(dst,"documents")[1]["source_url"] == "https://fonte.test/"+BASE
        check(dst)
        assert not any(migrate(src,dst)["inserted"].values())


def test_conflict_rolls_back_every_insert(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as dst:
        dst.execute("UPDATE document_chunks SET chunk_id='norm:86'")
        dst.commit()
        before = rows(dst,"documents")
        with pytest.raises(ValueError, match="conflitantes"):
            migrate(src,dst)
        assert rows(dst,"documents") == before
        assert len(rows(dst,"corpus_versions")) == 1


def test_additive_title_and_rollback_schema(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as dst:
        dst.execute("ALTER TABLE documents DROP COLUMN title")
        dst.commit()
        migrate(src,dst)
        assert rows(dst,"documents")[0]["title"] is None
        assert rows(dst,"documents")[1]["title"] == "Título "+BASE


def test_consolidation_preserves_vectors_and_maps(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as dst:
        migrate(src,dst)
        before = carregar_indice_faiss(corpus/"pncp")
        report = consolidate(corpus/"pncp",corpus/"normative",corpus/"combined",dst)
        combined = carregar_indice_faiss(corpus/"combined")
        assert combined.chunk_ids == ["pncp:1","norm:86"]
        np.testing.assert_array_equal(before.index.reconstruct(0),combined.index.reconstruct(0))
        np.testing.assert_array_equal(carregar_indice_faiss(corpus/"normative").index.reconstruct(0),combined.index.reconstruct(1))
        assert (report["orphans"],report["duplicates"],report["inconsistencies"]) == (0,0,[])
        with pytest.raises(FileExistsError):
            consolidate(corpus/"pncp",corpus/"normative",corpus/"combined",dst)


def test_hybrid_both_bases_and_citations(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as dst:
        migrate(src,dst)
        consolidate(corpus/"pncp",corpus/"normative",corpus/"combined",dst)
    engine = create_engine(f"sqlite:///{(corpus/'target.db').as_posix()}")
    with Session(engine) as db:
        for query, base, expected in [
            ("Quais contratações por dispensa existem em 2026?",KnowledgeBase.ESTRUTURADA,"pncp:1"),
            ("Quais são as dez contratações de maior valor estimado?",KnowledgeBase.ESTRUTURADA,"pncp:1"),
            ("O que determina o artigo 86 da Lei 14.133/2021?",KnowledgeBase.NORMATIVA,"norm:86"),
            ("Quais obrigações de fiscalização estão previstas nos documentos normativos?",KnowledgeBase.NORMATIVA,"norm:86"),
        ]:
            found = retrieve_hibrido(db,query,top_k=2,cache_dir=corpus/"combined",embedding_fn=embedding,bases=[base])
            assert [r.chunk_id for r in found] == [expected]
            assert found[0].bm25_rank is not None and found[0].dense_rank is not None
            assert found[0].metadata["urlFontePncp"].startswith("https://fonte.test/")
    engine.dispose()


def test_orphans_block_before_staging(corpus):
    with closing(connect(corpus/"target.db")) as db:
        with pytest.raises(ValueError,match="órfãos"):
            consolidate(corpus/"pncp",corpus/"normative",corpus/"blocked",db)
        assert not (corpus/"blocked").exists()


def test_manifest_corruption_is_rejected(corpus):
    manifest = next((corpus/"pncp/checkpoints").glob("*/manifest.json"))
    manifest.write_text('{}',encoding="utf-8")
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as db:
        migrate(src,db)
        with pytest.raises(FaissIndexCorruptError):
            consolidate(corpus/"pncp",corpus/"normative",corpus/"blocked",db)


def test_dry_run_does_not_modify_files(corpus):
    paths = [p for p in corpus.rglob('*') if p.is_file()]
    before = {p:p.read_bytes() for p in paths}
    report = run(corpus/"source.db",corpus/"target.db",corpus/"pncp",corpus/"normative")
    assert report["consolidation"]["status"] == "ready"
    assert all(p.read_bytes() == content for p,content in before.items())


def test_execute_only_temporary_and_backups_untouched(corpus):
    report = run(corpus/"source.db",corpus/"target.db",corpus/"pncp",corpus/"normative",
                 execute=True,staging=corpus/"combined",backup_dir=corpus/"backups")
    assert report["staging"]["vectors"] == 2
    with closing(readonly(corpus/"backups/target.db")) as db:
        assert len(rows(db,"documents")) == 1
    with closing(readonly(corpus/"target.db")) as db:
        assert len(rows(db,"documents")) == 2


def test_incompatible_model_rejected(corpus):
    state_path = corpus/"normative/estado_indexacao.json"
    state = json.loads(state_path.read_text())
    state["modeloEmbedding"] = "different-model"
    state_path.write_text(json.dumps(state))
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as db:
        migrate(src,db)
        with pytest.raises(ValueError,match="Modelos"):
            consolidate(corpus/"pncp",corpus/"normative",corpus/"blocked",db)


def test_hash_mismatch_rejected(corpus):
    with closing(readonly(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as db:
        migrate(src,db)
        db.execute("UPDATE document_chunks SET content='mudou' WHERE chunk_id='norm:86'")
        db.commit()
        with pytest.raises(ValueError,match="bloqueada"):
            consolidate(corpus/"pncp",corpus/"normative",corpus/"blocked",db)


def test_duplicate_ids_rejected(corpus):
    import faiss
    folder = corpus/"duplicate"
    folder.mkdir()
    idx = faiss.IndexFlatIP(2)
    idx.add(np.array([[1.,0.],[1.,0.]],dtype='float32'))
    faiss.write_index(idx,str(folder/'chunks.faiss'))
    (folder/'chunk_ids.json').write_text(json.dumps(['pncp:1','pncp:1']))
    (folder/'estado_indexacao.json').write_text(json.dumps({'modeloEmbedding':'test','dimensao':2,'totalVetores':2}))
    with closing(connect(corpus/'target.db')) as db:
        with pytest.raises(FaissIndexCorruptError):
            index_audit(folder,db)


def test_nonempty_relationship_refuses_silent_loss(corpus):
    with closing(connect(corpus/"source.db")) as src, closing(connect(corpus/"target.db")) as db:
        src.execute('CREATE TABLE extra(id INTEGER PRIMARY KEY, document_id INTEGER REFERENCES documents(id))')
        src.execute('INSERT INTO extra VALUES(1,1)')
        src.commit()
        with pytest.raises(ValueError,match="mapeamento explícito"):
            migrate(src,db)
        assert len(rows(db,'documents')) == 1


def existing_normative(corpus):
    """Reproduz a normativa sob corpus PNCP, com título e timestamp locais."""
    with closing(readonly(corpus/'source.db')) as src, closing(connect(corpus/'target.db')) as db:
        migrate(src,db)
        db.execute("UPDATE documents SET corpus_version_id=1,title='Título local',created_at='2026-10-12 21:00:35' WHERE base_id=?",(BASE,))
        db.execute("DELETE FROM corpus_versions WHERE id<>1")
        db.commit()


def test_existing_normative_reuses_ids_and_preserves_all_records(corpus):
    existing_normative(corpus)
    with closing(readonly(corpus/'source.db')) as src, closing(connect(corpus/'target.db')) as db:
        before = database_digest(db)
        for _ in range(2):
            report = migrate(src,db)
            assert not any(report['inserted'].values())
            assert report['reused'] == {'documents':1,'document_chunks':1}
            assert report['id_maps'] == {'corpus_versions':{1:1},'documents':{1:2},'document_chunks':{1:2}}
            assert set(report['preserved_document_differences'][0]['fields']) == {'title','created_at','corpus_version'}
            assert database_digest(db) == before
        assert rows(db,'documents')[1]['title'] == 'Título local'
        check(db)


def test_existing_normative_dry_run_and_vector_rerun_do_not_duplicate(corpus):
    existing_normative(corpus)
    original = {p:p.read_bytes() for p in corpus.rglob('*') if p.is_file()}
    report = run(corpus/'source.db',corpus/'target.db',corpus/'pncp',corpus/'normative')
    assert report['consolidation']['status'] == 'ready'
    assert report['consolidation']['total_vectors'] == 2
    assert not any(report['migration']['inserted'].values())
    assert all(p.read_bytes() == value for p,value in original.items())
    mapping = report['normative_chunk_mapping']
    assert mapping == [{'chunk_id':'norm:86','source_sql_id':1,'target_sql_id':2,
                        'source_document_id':1,'target_document_id':2}]
    with closing(readonly(corpus/'target.db')) as db:
        first = consolidate(corpus/'pncp',corpus/'normative',corpus/'combined',db,chunk_mapping=mapping)
        second = consolidate(corpus/'combined',corpus/'normative',corpus/'repeated',db,chunk_mapping=mapping)
    assert first['new_normative_vectors'] == 1
    assert second['new_normative_vectors'] == 0
    assert second['vectors'] == 2 and second['duplicates'] == second['orphans'] == 0
    one,two = carregar_indice_faiss(corpus/'combined'),carregar_indice_faiss(corpus/'repeated')
    assert one.chunk_ids == two.chunk_ids
    np.testing.assert_array_equal(one.index.reconstruct_n(0,2),two.index.reconstruct_n(0,2))
    repeated = run(corpus/'source.db',corpus/'target.db',corpus/'combined',corpus/'normative')
    assert repeated['consolidation']['new_normative_vectors'] == 0
    assert repeated['consolidation']['total_vectors'] == 2


def test_existing_normative_with_changed_chunk_metadata_is_rejected(corpus):
    existing_normative(corpus)
    with closing(readonly(corpus/'source.db')) as src, closing(connect(corpus/'target.db')) as db:
        db.execute("UPDATE document_chunks SET orgao='diferente' WHERE chunk_id='norm:86'")
        db.commit()
        before = database_digest(db)
        with pytest.raises(ValueError,match='conflitantes'):
            migrate(src,db)
        assert database_digest(db) == before


def test_partial_normative_adds_only_missing_chunk_to_existing_document(corpus):
    existing_normative(corpus)
    with closing(connect(corpus/'source.db')) as src, closing(connect(corpus/'target.db')) as db:
        src.execute("INSERT INTO document_chunks(document_id,chunk_id,content,base_id) VALUES(1,'norm:87','Artigo 87',?)",(BASE,))
        src.commit()
        report = migrate(src,db)
        assert report['inserted'] == {'corpus_versions':0,'documents':0,'document_chunks':1}
        assert rows(db,'document_chunks')[-1]['document_id'] == 2
        assert len(rows(db,'corpus_versions')) == 1


def test_ambiguous_existing_documents_block_migration(corpus):
    existing_normative(corpus)
    with closing(readonly(corpus/'source.db')) as src, closing(connect(corpus/'target.db')) as db:
        db.execute("INSERT INTO documents(corpus_version_id,source_file,base_id,doc_type,sensitivity) SELECT corpus_version_id,source_file,base_id,doc_type,sensitivity FROM documents WHERE base_id=?",(BASE,))
        db.commit()
        before = database_digest(db)
        with pytest.raises(ValueError,match='ambígua'):
            migrate(src,db)
        assert database_digest(db) == before


def test_incorrect_sql_mapping_blocks_before_staging(corpus):
    existing_normative(corpus)
    bad = [{'chunk_id':'norm:86','target_sql_id':1,'target_document_id':1}]
    with closing(readonly(corpus/'target.db')) as db:
        with pytest.raises(ValueError,match='Mapeamento SQL/FAISS inválido'):
            consolidate(corpus/'pncp',corpus/'normative',corpus/'blocked',db,chunk_mapping=bad)
    assert not (corpus/'blocked').exists()
