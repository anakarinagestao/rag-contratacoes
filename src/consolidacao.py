"""Migração conservadora e consolidação offline; não altera configuração do RAG.

python -m src.consolidacao --dry-run --relatorio consolidacao_auditoria.json
--executar exige autorização operacional posterior; nunca publica em produção.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

import faiss
import numpy as np

from src.index import (
    ARQUIVO_ESTADO, ESQUEMA_CHECKPOINTS, _gravar_checkpoint,
    _salvar_json_atomico, auditar_indice_faiss, carregar_indice_faiss,
    preparar_staging,
)

BASE = "editais_normativo"


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def readonly(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def check(db: sqlite3.Connection) -> None:
    if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("Falha de integridade SQLite")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Chaves estrangeiras inválidas")


def snapshot(db: sqlite3.Connection, path: Path) -> None:
    # Nome exclusivo; nunca abre um backup já existente para escrita.
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(path)) as dest:
        db.backup(dest)
        check(dest)


def counts(db: sqlite3.Connection) -> dict:
    return {t: dict(db.execute(f"SELECT base_id,count(*) FROM {t} GROUP BY base_id"))
            for t in ("documents", "document_chunks")}


def schema(db: sqlite3.Connection) -> dict:
    objects = [dict(r) for r in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    tables = {}
    for r in objects:
        if r["type"] != "table":
            continue
        t = quote(r["name"])
        tables[r["name"]] = {
            "count": db.execute(f"SELECT count(*) FROM {t}").fetchone()[0],
            "columns": [dict(x) for x in db.execute(f"PRAGMA table_info({t})")],
            "foreign_keys": [dict(x) for x in db.execute(f"PRAGMA foreign_key_list({t})")],
            "indexes": [{**dict(x), "columns": [dict(y) for y in db.execute(
                f"PRAGMA index_xinfo({quote(x['name'])})")]} for x in db.execute(f"PRAGMA index_list({t})")],
        }
    return {"objects": objects, "tables": tables, "counts": counts(db)}


def rows(db: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(r) for r in db.execute(f"SELECT * FROM {quote(table)} ORDER BY id")]


def database_digest(db: sqlite3.Connection) -> str:
    """Detecta alterações concorrentes sem expor conteúdo ou credenciais."""
    digest = hashlib.sha256()
    for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'document_chunks_fts%' ORDER BY name"):
        digest.update(repr((name,sql)).encode())
        for row in db.execute(f"SELECT * FROM {quote(name)} ORDER BY rowid"):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def migrate(source: sqlite3.Connection, dest: sqlite3.Connection, *, expected_digest: str | None = None) -> dict:
    """Uma transação; IDs SQL remapeados, chunk_id textual preservado.

    O chamador deve gerar os dois backups antes de chamar esta função.
    Relações desconhecidas com dados são recusadas para evitar perda silenciosa.
    """
    check(source)
    check(dest)
    docs = [r for r in rows(source, "documents") if r["base_id"] == BASE]
    doc_ids = {r["id"] for r in docs}
    chunks = [r for r in rows(source, "document_chunks") if r["document_id"] in doc_ids]
    if any(r["base_id"] not in (None, BASE) for r in chunks):
        raise ValueError("Base do chunk diverge do documento")
    # Nenhuma relação de origem populada pode ser descartada implicitamente.
    for table, in source.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if table in ("documents", "document_chunks") or table.startswith("document_chunks_fts"):
            continue
        refs = list(source.execute(f"PRAGMA foreign_key_list({quote(table)})"))
        if any(r[2] in ("documents", "document_chunks") for r in refs):
            if source.execute(f"SELECT 1 FROM {quote(table)} LIMIT 1").fetchone():
                raise ValueError(f"Relação populada exige mapeamento explícito: {table}")
    dest.execute("BEGIN IMMEDIATE")
    before = counts(dest)
    # Preserva todas as linhas pré-existentes, inclusive raw_asset e usuários.
    original = {t: rows(dest, t) for t in ("corpus_versions", "documents", "document_chunks")}
    maps = {t: {} for t in original}
    inserted = {t: 0 for t in original}
    try:
        if expected_digest is not None and database_digest(dest) != expected_digest:
            raise ValueError("Destino mudou desde o backup; refazer diagnóstico e backups")
        # Evolução aditiva necessária: app.db antigo não contém documents.title.
        source_cols = {r[1] for r in source.execute("PRAGMA table_info(documents)")}
        dest_cols = {r[1] for r in dest.execute("PRAGMA table_info(documents)")}
        if "title" in source_cols and "title" not in dest_cols:
            dest.execute("ALTER TABLE documents ADD COLUMN title VARCHAR")

        def put(table: str, record: dict, key: dict) -> int:
            cols = {r[1] for r in dest.execute(f"PRAGMA table_info({quote(table)})")}
            if set(record) - cols:
                raise ValueError(f"Colunas sem destino: {table}: {set(record) - cols}")
            found = dest.execute(f"SELECT * FROM {quote(table)} WHERE " +
                                 " AND ".join(f"{quote(k)} IS ?" for k in key), tuple(key.values())).fetchall()
            if len(found) > 1:
                raise ValueError(f"Chave estável ambígua: {table} {key}")
            if found:
                # Datas de criação não fazem parte da identidade de corpus compartilhado.
                ignored = {"created_at"} if table == "corpus_versions" else set()
                if any(found[0][k] != v for k, v in record.items() if k not in ignored):
                    raise ValueError(f"Conteúdo/metadados conflitantes: {table} {key}")
                return found[0]["id"]
            fields = list(record)
            cur = dest.execute(f"INSERT INTO {quote(table)} ({','.join(map(quote, fields))}) "
                               f"VALUES ({','.join('?' for _ in fields)})", tuple(record.values()))
            inserted[table] += 1
            return cur.lastrowid

        versions = {r["id"]: r for r in rows(source, "corpus_versions")}
        new_docs = []
        preserved_differences = []
        reused_versions: dict[int, set[int]] = {}
        # A versão de corpus é procedência local, não identidade do documento.
        # Não criar outra versão/documento antes de procurar a identidade estável.
        for doc in docs:
            found = dest.execute(
                "SELECT * FROM documents WHERE base_id=? AND source_file=?",
                (doc["base_id"], doc["source_file"]),
            ).fetchall()
            if len(found) > 1:
                raise ValueError(f"Chave estável ambígua: documents {doc['source_file']}")
            if not found:
                new_docs.append(doc)
                continue
            existing = dict(found[0])
            local_fields = {"id", "corpus_version_id", "title", "created_at"}
            if any(k not in existing or existing[k] != v
                   for k, v in doc.items() if k not in local_fields):
                raise ValueError(f"Conteúdo/metadados conflitantes: documents {doc['source_file']}")
            maps["documents"][doc["id"]] = existing["id"]
            reused_versions.setdefault(doc["corpus_version_id"], set()).add(existing["corpus_version_id"])
            differences = {k: {"source": doc.get(k), "target": existing.get(k)}
                           for k in ("title", "created_at") if doc.get(k) != existing.get(k)}
            target_version = dest.execute(
                "SELECT version FROM corpus_versions WHERE id=?", (existing["corpus_version_id"],)
            ).fetchone()[0]
            source_version = versions[doc["corpus_version_id"]]["version"]
            if source_version != target_version:
                differences["corpus_version"] = {"source": source_version, "target": target_version}
            if differences:
                preserved_differences.append({"source_document_id": doc["id"],
                                              "target_document_id": existing["id"],
                                              "fields": differences})
        # Só versões necessárias para documentos efetivamente ausentes são inseridas.
        for old in sorted({r["corpus_version_id"] for r in new_docs}):
            r = {k: v for k, v in versions[old].items() if k != "id"}
            maps["corpus_versions"][old] = put("corpus_versions", r, {"version": r["version"]})
        for old, targets in reused_versions.items():
            if old not in maps["corpus_versions"] and len(targets) == 1:
                maps["corpus_versions"][old] = next(iter(targets))
        for doc in new_docs:
            r = {k: v for k, v in doc.items() if k != "id"}
            r["corpus_version_id"] = maps["corpus_versions"][doc["corpus_version_id"]]
            key = {k: r[k] for k in ("corpus_version_id", "base_id", "source_file")}
            legacy = r["source_file"].removeprefix(BASE+"|")
            if legacy != r["source_file"] and dest.execute(
                "SELECT 1 FROM documents WHERE corpus_version_id=? AND base_id=? AND source_file=?",
                (r["corpus_version_id"],BASE,legacy)).fetchone():
                raise ValueError("Documento com chave legada exige reconciliação explícita")
            maps["documents"][doc["id"]] = put("documents", r, key)
        for chunk in chunks:
            r = {k: v for k, v in chunk.items() if k != "id"}
            r["document_id"] = maps["documents"][chunk["document_id"]]
            maps["document_chunks"][chunk["id"]] = put("document_chunks", r, {"chunk_id": r["chunk_id"]})
        # FTS é derivado; criar/backfill somente se ausente, dentro da transação.
        exists = dest.execute("SELECT 1 FROM sqlite_master WHERE name='document_chunks_fts'").fetchone()
        if not exists:
            dest.execute("CREATE VIRTUAL TABLE document_chunks_fts USING fts5(content, content='document_chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
        from src.fts import _criar_triggers
        class Adapter:
            def exec_driver_sql(self, sql):
                return dest.execute(sql)
        triggers = {r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        missing_triggers = not {"document_chunks_fts_ai", "document_chunks_fts_ad", "document_chunks_fts_au"} <= triggers
        if not exists or missing_triggers or inserted["document_chunks"]:
            _criar_triggers(Adapter())
            dest.execute("INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')")
            dest.execute("INSERT INTO document_chunks_fts(document_chunks_fts,rank) VALUES ('integrity-check',1)")
        for table, records in original.items():
            for r in records:
                actual = dest.execute(f"SELECT * FROM {quote(table)} WHERE id=?", (r["id"],)).fetchone()
                if any(actual[k] != v for k, v in r.items()):
                    raise ValueError(f"Registro preexistente alterado: {table}/{r['id']}")
        check(dest)
        after = counts(dest)
        dest.commit()
    except BaseException:
        dest.rollback()
        raise
    return {"before": before, "after": after, "inserted": inserted, "id_maps": maps,
            "reused": {"documents": len(docs) - inserted["documents"],
                       "document_chunks": len(chunks) - inserted["document_chunks"]},
            "preserved_document_differences": preserved_differences}


def index_audit(path: Path, db: sqlite3.Connection) -> dict:
    a = auditar_indice_faiss(path)
    idx = carregar_indice_faiss(path)
    ids = {r[0] for r in db.execute("SELECT chunk_id FROM document_chunks")}
    normalized, metrics = True, set()
    # Verifica os shards reais: o loader gerenciado sempre monta IndexFlatIP.
    state = a.estado
    shards = ([path / "base/chunks.faiss"] if state.get("base") else []) + [
        path / "checkpoints" / cp["nome"] / "chunks.faiss" for cp in state.get("checkpoints", [])]
    if not shards:
        shards = [path / "chunks.faiss"]
    for shard in shards:
        raw = faiss.read_index(str(shard))
        metrics.add(raw.metric_type)
        for start in range(0, raw.ntotal, 8192):
            v = raw.reconstruct_n(start, min(8192, raw.ntotal-start))
            normalized &= bool(np.allclose(np.linalg.norm(v, axis=1), 1, atol=2e-6))
    contents = dict(db.execute("SELECT chunk_id,content FROM document_chunks"))
    mismatches = [k for k, h in a.hashes_conteudo.items() if k in contents and
                  h != hashlib.sha256(contents[k].encode()).hexdigest()]
    return {"path": str(path.resolve()), "vectors": a.total_vetores,
            "model": a.modelo_embedding, "dimension": a.dimensao,
            "metrics": sorted(metrics), "normalized": normalized,
            "duplicates": len(a.chunk_ids)-len(set(a.chunk_ids)),
            "orphans": len(set(a.chunk_ids)-ids), "hash_mismatches": mismatches,
            "inconsistencies": a.inconsistencias, "state": state,
            "files": [{"path": str(p.relative_to(path)), "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(path.rglob('*')) if p.is_file()]}


def validate_pair(pncp: Path, normative: Path, db: sqlite3.Connection) -> tuple[dict, dict]:
    a, b = index_audit(pncp, db), index_audit(normative, db)
    for report in (a, b):
        if (report["orphans"] or report["duplicates"] or report["inconsistencies"]
                or report["hash_mismatches"]):
            raise ValueError(f"Consolidação bloqueada: {report['path']}: órfãos={report['orphans']}, inconsistências={report['inconsistencies']}")
        if report["metrics"] != [faiss.METRIC_INNER_PRODUCT] or not report["normalized"]:
            raise ValueError("Métrica/normalização incompatível com o RAG")
    if not a["model"] or (a["model"], a["dimension"]) != (b["model"], b["dimension"]):
        raise ValueError("Modelos/dimensões incompatíveis; utilizar estratégia B")
    norm = auditar_indice_faiss(normative)
    expected = {r[0] for r in db.execute("SELECT c.chunk_id FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE d.base_id=?", (BASE,))}
    if set(norm.chunk_ids) != expected or set(norm.hashes_conteudo) != expected:
        raise ValueError("Cobertura normativa/ hashes incompletos; utilizar estratégia B")
    return a, b


def _validate_chunk_mapping(chunk_ids: list[str], db: sqlite3.Connection,
                            chunk_mapping: list[dict] | None) -> None:
    if chunk_mapping is None:
        return
    by_id = {r["chunk_id"]: r for r in chunk_mapping}
    if len(by_id) != len(chunk_mapping) or set(by_id) != set(chunk_ids):
        raise ValueError("Mapeamento normativo incompleto ou duplicado")
    for chunk_id in chunk_ids:
        mapped = by_id[chunk_id]
        actual = db.execute("SELECT chunk_id,document_id FROM document_chunks WHERE id=?",
                            (mapped["target_sql_id"],)).fetchone()
        if actual is None or actual["chunk_id"] != chunk_id or actual["document_id"] != mapped["target_document_id"]:
            raise ValueError(f"Mapeamento SQL/FAISS inválido: {chunk_id}")


def plan_vector_reuse(pncp: Path, normative: Path, db: sqlite3.Connection,
                      chunk_mapping: list[dict]) -> dict:
    """Simula em memória; não escreve staging, checkpoints ou embeddings."""
    baseline = carregar_indice_faiss(pncp)
    extra = carregar_indice_faiss(normative)
    _validate_chunk_mapping(extra.chunk_ids, db, chunk_mapping)
    positions = {k: n for n, k in enumerate(baseline.chunk_ids)}
    selected = []
    for n, key in enumerate(extra.chunk_ids):
        if key in positions:
            if not np.array_equal(baseline.index.reconstruct(positions[key]), extra.index.reconstruct(n)):
                raise ValueError(f"Vetores conflitantes para {key}")
        else:
            selected.append((n, key))
    combined = faiss.clone_index(baseline.index)
    if selected:
        combined.add(np.asarray([extra.index.reconstruct(n) for n, _ in selected]))
    ids = baseline.chunk_ids + [key for _, key in selected]
    valid_ids = {r[0] for r in db.execute("SELECT chunk_id FROM document_chunks")}
    for n in range(0, baseline.index.ntotal, 8192):
        count = min(8192, baseline.index.ntotal - n)
        if not np.array_equal(baseline.index.reconstruct_n(n, count), combined.reconstruct_n(n, count)):
            raise ValueError("Vetores PNCP alterados na simulação")
    for offset, (n, _) in enumerate(selected, start=baseline.index.ntotal):
        if not np.array_equal(extra.index.reconstruct(n), combined.reconstruct(offset)):
            raise ValueError("Vetor normativo alterado na simulação")
    return {"total_vectors": int(combined.ntotal), "new_normative_vectors": len(selected),
            "already_indexed_normative_vectors": len(extra.chunk_ids) - len(selected),
            "duplicates": len(ids) - len(set(ids)), "orphans": len(set(ids) - valid_ids),
            "pncp_vectors_preserved_exactly": True, "normative_vectors_preserved_exactly": True,
            "persisted": False}


def consolidate(pncp: Path, normative: Path, dest: Path, db: sqlite3.Connection,
                *, chunk_mapping: list[dict] | None = None) -> dict:
    """Estratégia A: novo staging exclusivo, byte a byte para os vetores PNCP."""
    validate_pair(pncp, normative, db)
    # Proíbe destinos dentro de qualquer origem, inclusive caminhos ancestrais.
    dest, pncp, normative = dest.resolve(), pncp.resolve(), normative.resolve()
    for original in (pncp, normative):
        if dest == original or dest in original.parents or original in dest.parents:
            raise ValueError("Staging deve estar separado das origens")
    if dest.exists():
        raise FileExistsError(dest)
    baseline = carregar_indice_faiss(pncp)
    extra = carregar_indice_faiss(normative)
    _validate_chunk_mapping(extra.chunk_ids, db, chunk_mapping)
    contents = dict(db.execute("SELECT chunk_id,content FROM document_chunks"))
    normative_ids = {r[0] for r in db.execute("SELECT c.chunk_id FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE d.base_id=?", (BASE,))}
    if set(extra.chunk_ids) != normative_ids:
        raise ValueError("Índice normativo não cobre exatamente os chunks migrados")
    overlap = set(baseline.chunk_ids) & set(extra.chunk_ids)
    positions = {k: n for n, k in enumerate(baseline.chunk_ids)}
    for n, k in enumerate(extra.chunk_ids):
        if k in overlap and not np.array_equal(baseline.index.reconstruct(positions[k]), extra.index.reconstruct(n)):
            raise ValueError(f"Vetores conflitantes para {k}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name('.' + dest.name + '.' + uuid.uuid4().hex + '.tmp')
    state = auditar_indice_faiss(pncp).estado
    if state.get("formato") == ESQUEMA_CHECKPOINTS:
        shutil.copytree(pncp, temp)
    else:
        preparar_staging(pncp, temp)
    # O diretório só se torna revisável após auditoria completa e rename atômico.
    state = json.loads((temp / ARQUIVO_ESTADO).read_text(encoding="utf-8"))
    selected = [(n, k) for n, k in enumerate(extra.chunk_ids) if k not in overlap]
    if selected:
        _gravar_checkpoint(temp, state, [k for _, k in selected],
                           [contents[k] for _, k in selected],
                           np.asarray([extra.index.reconstruct(n) for n, _ in selected]), BASE)
    combined = carregar_indice_faiss(temp)
    if combined.chunk_ids[:len(baseline.chunk_ids)] != baseline.chunk_ids:
        raise ValueError("Mapa PNCP alterado")
    for n in range(0, baseline.index.ntotal, 8192):
        count = min(8192, baseline.index.ntotal-n)
        if not np.array_equal(baseline.index.reconstruct_n(n,count), combined.index.reconstruct_n(n,count)):
            raise ValueError("Vetores PNCP alterados")
    report = index_audit(temp, db)
    if report["inconsistencies"] or report["orphans"] or report["duplicates"]:
        raise ValueError("Staging reprovado; mantido como temporário para inspeção")
    # Nunca substitui staging existente, mesmo se vazio.
    if dest.exists():
        raise FileExistsError(dest)
    os.rename(temp, dest)
    report["path"] = str(dest)
    report["new_normative_vectors"] = len(selected)
    return report


def discover(root: Path, db: sqlite3.Connection) -> list[dict]:
    """Audita todos os candidatos; base/ é parte do índice pai, não versão."""
    result = []
    for state in root.rglob(ARQUIVO_ESTADO):
        if state.parent.name == "base" or "archive" in state.parts:
            continue
        try:
            result.append(index_audit(state.parent, db))
        except (OSError, ValueError, RuntimeError) as exc:
            result.append({"path": str(state.parent.resolve()), "error": str(exc)})
    return result


def local_evidence(db: sqlite3.Connection) -> dict:
    """Evidências locais; não apresenta busca lexical como resposta gerada por LLM."""
    from src.fts import preparar_consulta_fts5
    result = {"method": "SQL/FTS5 local; sem LLM e sem embeddings de consulta remotos"}
    questions = ["O que determina o artigo 86 da Lei 14.133/2021?",
                 "Quais obrigações de fiscalização estão previstas nos documentos normativos?"]
    for question in questions:
        found = db.execute("""SELECT c.chunk_id,d.base_id,d.source_file,d.source_url,
            substr(c.content,1,1400) AS excerpt,bm25(document_chunks_fts) AS score
            FROM document_chunks_fts JOIN document_chunks c ON c.id=document_chunks_fts.rowid
            JOIN documents d ON d.id=c.document_id
            WHERE document_chunks_fts MATCH ? AND d.base_id=? ORDER BY score LIMIT 5""",
            (preparar_consulta_fts5(question),BASE)).fetchall()
        result[question] = [dict(r) for r in found]
    result["article86_exact"] = [dict(r) for r in db.execute("""SELECT c.chunk_id,d.base_id,
        d.source_file,d.source_url,c.content FROM document_chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.chunk_id='editais_normativo:planalto_l14133:86:p1'""")]
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_asset'").fetchone()
    if exists:
        # Apenas uma versão por identidade; payloads históricos não são somados.
        purchases = db.execute("""WITH latest AS (
            SELECT *,row_number() OVER (PARTITION BY source,resource_type,external_id
            ORDER BY fetched_at DESC,created_at DESC,id DESC) AS pos FROM raw_asset
            WHERE resource_type='contratacao')
            SELECT external_id,payload FROM latest WHERE pos=1""").fetchall()
        records = []
        for row in purchases:
            p = json.loads(row["payload"])
            records.append({"external_id":row["external_id"],"numeroControlePNCP":p.get("numeroControlePNCP"),
                            "anoCompra":p.get("anoCompra"),"modalidadeNome":p.get("modalidadeNome"),
                            "valorTotalEstimado":p.get("valorTotalEstimado"),"objetoCompra":p.get("objetoCompra"),
                            "base_id":"pncp_rj_estruturada","source_table":"raw_asset"})
        direct = [r for r in records if str(r["anoCompra"])=='2026' and 'dispensa' in str(r["modalidadeNome"]).lower()]
        ranked = sorted([r for r in records if isinstance(r["valorTotalEstimado"],(int,float))],
                        key=lambda r:(-r["valorTotalEstimado"],str(r["external_id"])))[:10]
        result["Quais contratações por dispensa existem em 2026?"] = {"count":len(direct),"sample":direct[:10],"scope":"anoCompra=2026, modalidadeNome contém dispensa; corpus local"}
        result["Quais são as dez contratações de maior valor estimado?"] = {"records":ranked,"scope":"contratacao, última versão por identidade, corpus local sem filtro de ano"}
    return result


def run(source: Path, target: Path, pncp: Path | None, normative: Path,
        *, execute: bool = False, staging: Path | None = None, backup_dir: Path | None = None) -> dict:
    if source.resolve() == target.resolve():
        raise ValueError("Origem e destino devem ser distintos")
    with closing(readonly(source)) as src, closing(readonly(target)) as dst:
        check(src)
        check(dst)
        report = {"mode": "executar" if execute else "dry-run", "source": schema(src), "target": schema(dst)}
        report["candidates"] = discover(target.resolve().parent / "data/index/faiss", dst)
        if pncp is None:
            candidates = [a for a in report["candidates"] if not a.get("error") and not a["inconsistencies"]
                          and "pncp" in " ".join(a["state"].get("origens", []))]
            # Origens antigas usam nomes de lote, sem o prefixo PNCP.
            if not candidates:
                candidates = [a for a in report["candidates"] if not a.get("error") and not a["inconsistencies"]]
            if not candidates:
                raise ValueError("Nenhum índice PNCP íntegro encontrado")
            chosen = max(candidates, key=lambda a: a["state"].get("atualizadoEm", ""))
            pncp = Path(chosen["path"])
        report["selected_pncp"] = str(pncp.resolve())
        report["numeric_id_collisions"] = {
            table: len({r[0] for r in src.execute(f"SELECT id FROM {table}")} &
                       {r[0] for r in dst.execute(f"SELECT id FROM {table}")})
            for table in ("corpus_versions", "documents", "document_chunks")}
        with tempfile.TemporaryDirectory(prefix="consolidacao-") as tmp:
            backups = backup_dir if execute else Path(tmp)
            if execute and (backups is None or staging is None):
                raise ValueError("--executar exige --backup-dir e --staging novos")
            if execute:
                backups.mkdir(parents=True, exist_ok=False)
            snapshot(src, backups / "source.db")
            snapshot(dst, backups / "target.db")
            # Planejar na cópia ANTES de qualquer escrita no banco principal.
            working = Path(tmp) / "working.db"
            with closing(readonly(backups / "target.db")) as saved:
                saved_digest = database_digest(saved)
                snapshot(saved, working)
            with closing(readonly(backups / "source.db")) as frozen_source, closing(sqlite3.connect(working)) as copy:
                copy.row_factory = sqlite3.Row
                copy.execute("PRAGMA foreign_keys=ON")
                report["migration"] = migrate(frozen_source, copy)
                migration = report["migration"]
                chunk_mapping = [
                    {"chunk_id": r["chunk_id"], "source_sql_id": r["id"],
                     "target_sql_id": migration["id_maps"]["document_chunks"][r["id"]],
                     "source_document_id": r["document_id"],
                     "target_document_id": migration["id_maps"]["documents"][r["document_id"]]}
                    for r in rows(frozen_source, "document_chunks")
                    if r["id"] in migration["id_maps"]["document_chunks"]]
                report["normative_chunk_mapping"] = chunk_mapping
                report["evidence"] = local_evidence(copy)
                report["pncp"] = index_audit(pncp, copy)
                report["normative"] = index_audit(normative, copy)
                try:
                    validate_pair(pncp, normative, copy)
                    vector_plan = plan_vector_reuse(pncp, normative, copy, chunk_mapping)
                    if vector_plan["duplicates"] or vector_plan["orphans"]:
                        raise ValueError("IDs duplicados/órfãos na simulação consolidada")
                    report["consolidation"] = {"status": "ready", "strategy": "A", **vector_plan}
                except ValueError as exc:
                    report["consolidation"] = {"status": "blocked", "reason": str(exc), "strategy": "A condicionada à reconciliação SQLite/PNCP"}
                if execute:
                    if report["consolidation"]["status"] != "ready":
                        raise ValueError(report["consolidation"]["reason"])
                    if staging.exists():
                        staging = staging.with_name(staging.name + "_" + uuid.uuid4().hex[:10])
                    report["staging"] = consolidate(pncp, normative, staging, copy,
                                                     chunk_mapping=chunk_mapping)
                    # Revalidar o destino sob lock; não sobrescrever o banco.
                    with closing(sqlite3.connect(target)) as real:
                        real.row_factory = sqlite3.Row
                        real.execute("PRAGMA foreign_keys=ON")
                        report["migration"] = migrate(frozen_source, real, expected_digest=saved_digest)
            report["backups"] = str(backups) if execute else "snapshots temporários via sqlite3.backup; removidos ao concluir"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--executar", action="store_true")
    parser.add_argument("--origem", type=Path, default=Path("app_normativa.db"))
    parser.add_argument("--destino", type=Path, default=Path("app.db"))
    parser.add_argument("--pncp", type=Path, help="omitir para auditar e escolher o candidato íntegro mais recente")
    parser.add_argument("--normativo", type=Path, default=Path("indice/faiss_normativo_794"))
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--relatorio", type=Path)
    args = parser.parse_args()
    report = run(args.origem, args.destino, args.pncp, args.normativo,
                 execute=args.executar, staging=args.staging, backup_dir=args.backup_dir)
    if args.relatorio:
        if args.relatorio.exists():
            raise FileExistsError(args.relatorio)
        with args.relatorio.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({"migration": report["migration"]["inserted"],
                      "consolidation": report["consolidation"]}, ensure_ascii=False, indent=2))
    return 2 if report["consolidation"]["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
