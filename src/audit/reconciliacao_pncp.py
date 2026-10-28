"""Diagnóstico somente leitura de SQLite/FAISS/JSONL e snapshots do Git.

Extrai blobs Git exclusivamente em TemporaryDirectory; nunca aplica stash.
Não importa inicialização de banco, não migra, não indexa e não gera embeddings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from src.consolidacao import readonly
from src.index import auditar_indice_faiss


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8').strip()


def canonical_id(item: dict) -> str:
    m = item['metadata']
    identity = m.get('caminhoRelativo', '')
    if m.get('caminhoInternoPacote'):
        identity = m.get('identificadorDocumentoFonte', identity)
    key = '|'.join([str(m.get('numeroControlePNCP', '')),
                    str(m.get('sequencialDocumento', '')), str(identity),
                    str(m['indiceChunk']), item['content']])
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def sqlite_report(path: Path, vector_ids: set, source_hashes: dict,
                  checkpoint_hashes: dict, current: dict) -> dict:
    with closing(readonly(path)) as db:
        tables = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        report = {
            'bytes': path.stat().st_size, 'sha256': sha(path),
            'integrity': [r[0] for r in db.execute('PRAGMA integrity_check')],
            'foreign_key_errors': [tuple(r) for r in db.execute('PRAGMA foreign_key_check')],
            'tables': {name: {'count': db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0],
                             'columns': [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')]}
                       for name in tables},
        }
        if 'document_chunks' not in tables:
            return report
        report['documents_by_base'] = dict(db.execute('SELECT base_id,count(*) FROM documents GROUP BY base_id'))
        report['chunks_by_base'] = dict(db.execute('SELECT base_id,count(*) FROM document_chunks GROUP BY base_id'))
        report['versions'] = [dict(r) for r in db.execute('SELECT * FROM corpus_versions')]
        ids, hashes, doc_ids, mismatches, cp_mismatches = set(), {}, set(), [], []
        known_current = set()
        for r in db.execute('SELECT chunk_id,content,document_id FROM document_chunks'):
            k = r['chunk_id']
            ids.add(k)
            h = hashlib.sha256(r['content'].encode()).hexdigest()
            hashes[k] = h
            if k in vector_ids:
                doc_ids.add(r['document_id'])
            if k in source_hashes and source_hashes[k] != h:
                mismatches.append(k)
            if k in checkpoint_hashes and checkpoint_hashes[k] != h:
                cp_mismatches.append(k)
            if k in current:
                known_current.add(k)
        current_hashes = {hashlib.sha256(r['content'].encode()).hexdigest() for r in current.values()}
        report.update({
            'vectors_matched': len(ids & vector_ids), 'vectors_orphan': len(vector_ids-ids),
            'chunks_without_vectors': len(ids-vector_ids), 'indexed_documents': len(doc_ids),
            'jsonl_matched': len(ids & source_hashes.keys()), 'jsonl_content_mismatches': mismatches,
            'checkpoint_hash_mismatches': cp_mismatches,
            'current_chunks_matched_by_id': len(known_current),
            'current_contents_present': len(current_hashes & set(hashes.values())),
            'document_versions': [dict(r) for r in db.execute('''SELECT d.base_id,cv.version,count(*) AS count
                FROM documents d JOIN corpus_versions cv ON cv.id=d.corpus_version_id GROUP BY d.base_id,cv.version''')],
        })
        if 'raw_asset' in tables:
            report['raw_by_type'] = dict(db.execute('SELECT resource_type,count(*) FROM raw_asset GROUP BY resource_type'))
        return report


def diagnose(root: Path, include_git: bool = True) -> dict:
    root = root.resolve()
    protected = [p for p in root.rglob('*') if p.is_file() and
                 (p.name in ('app.db','app_normativa.db','app.db.backup') or
                  p.is_relative_to(root/'data/index/faiss') or
                  p.is_relative_to(root/'indice/faiss_normativo_794') or
                  p.is_relative_to(root/'data/chunks')) and
                 not p.is_relative_to(root/'.git')]
    protected += [root/'app/core/config.py']
    before = {str(p.relative_to(root)): sha(p) for p in protected}
    a = auditar_indice_faiss(root/'data/index/faiss/staging/v2')
    vector_ids = set(a.chunk_ids)
    with closing(readonly(root/'app.db')) as db:
        current = {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM document_chunks')}
        current_docs = {r['source_file']: dict(r) for r in db.execute('SELECT * FROM documents')}
    source_hashes, per_file, id_file, documents = {}, [], {}, {}
    for path in sorted((root/'data/chunks').rglob('*.jsonl')):
        count, matched, conflicts, bad = 0, 0, 0, 0
        groups, indexed_groups = set(), set()
        for line in path.open(encoding='utf-8'):
            if not line.strip():
                continue
            item = json.loads(line)
            k, m = item['chunk_id'], item['metadata']
            h = hashlib.sha256(item['content'].encode()).hexdigest()
            key = (m.get('tipoEntidade'), m.get('numeroControlePNCP'), m.get('sequencialDocumento'))
            count += 1
            matched += k in vector_ids
            groups.add(key)
            if k in vector_ids:
                indexed_groups.add(key)
            conflicts += k in source_hashes and source_hashes[k] != h
            bad += canonical_id(item) != k
            source_hashes[k], id_file[k], documents[k] = h, str(path.relative_to(root)), key
        per_file.append({'path':str(path.relative_to(root)), 'sha256':sha(path), 'rows':count,
                         'vector_matches':matched,'documents':len(groups),
                         'indexed_documents':len(indexed_groups),'content_conflicts':conflicts,
                         'canonical_id_mismatches':bad})
    report = {'index': {'path':'data/index/faiss/staging/v2','vectors':a.total_vetores,
              'duplicates':len(a.chunk_ids)-len(vector_ids),'issues':a.inconsistencias,
              'model':a.modelo_embedding,'dimension':a.dimensao,'state':a.estado,
              'checkpoints':len(a.estado.get('checkpoints',[])),
              'checkpoint_content_hashes':len(a.hashes_conteudo)},
              'jsonl':per_file,'jsonl_unique_ids':len(source_hashes),
              'vectors_missing_jsonl':len(vector_ids-source_hashes.keys()),
              'checkpoint_jsonl_hash_mismatches':sum(source_hashes.get(k)!=h for k,h in a.hashes_conteudo.items()),
              'local_databases':[], 'git_snapshots':[]}
    # Todos os arquivos com nomes usuais, inclusive backups ignorados pelo Git.
    files = []
    for p in root.rglob('*'):
        if any(part in {'.git','.venv','.pytest_tmp','__pycache__'} for part in p.relative_to(root).parts):
            continue
        if p.is_file() and ('.db' in p.name.lower() or '.sqlite' in p.name.lower() or
                            p.suffix.lower() in {'.bak','.backup'}):
            files.append(p)
    for p in files:
        with p.open('rb') as file:
            header = file.read(16)
        if header == b'SQLite format 3\x00':
            report['local_databases'].append({'path':str(p.relative_to(root)),
                **sqlite_report(p,vector_ids,source_hashes,a.hashes_conteudo,current)})
    if include_git:
        refs = git('stash','list','--format=%H').splitlines()
        refs += git('log','--all','--format=%H','--','app.db').splitlines()
        blobs = {}
        for ref in dict.fromkeys(refs):
            # Inclui estado do índice do stash e eventuais arquivos não rastreados.
            for rev in [ref, ref+'^2', ref+'^3']:
                found = subprocess.run(['git','ls-tree','-r','-l',rev],capture_output=True,text=True)
                for line in found.stdout.splitlines():
                    info, name = line.split('\t',1)
                    if '.db' not in name.lower() and '.sqlite' not in name.lower():
                        continue
                    fields = info.split()
                    if fields[1] != 'blob':
                        continue
                    blob = fields[2]
                    blobs.setdefault(blob, {'blob':blob,'bytes':int(fields[3]),'refs':[]})['refs'].append(rev+':'+name)
        with tempfile.TemporaryDirectory(prefix='pncp-readonly-audit-') as tmp:
            path = Path(tmp)/'snapshot.db'
            for blob, evidence in blobs.items():
                if evidence['bytes'] == 0:
                    report['git_snapshots'].append({**evidence,'empty':True})
                    continue
                with path.open('wb') as output:
                    subprocess.run(['git','cat-file','blob',blob],stdout=output,check=True)
                with path.open('rb') as file:
                    header = file.read(16)
                if header != b'SQLite format 3\x00':
                    report['git_snapshots'].append({**evidence,'not_sqlite':True})
                    continue
                detail = sqlite_report(path,vector_ids,source_hashes,a.hashes_conteudo,current)
                report['git_snapshots'].append({**evidence,**detail})
                print('snapshot',blob,'matches',detail.get('vectors_matched',0),flush=True)
                if detail.get('vectors_matched') == len(vector_ids):
                    with closing(readonly(path)) as db:
                        docs = [dict(r) for r in db.execute('SELECT * FROM documents')]
                        by_key = {r['source_file']:r for r in docs}
                        detail['current_document_keys_present'] = sum(k in by_key for k in current_docs)
                        detail['current_document_differences'] = {
                            key: {col:{'current':value,'saved':by_key[key].get(col)} for col,value in doc.items()
                                  if col not in ('id','created_at') and by_key[key].get(col)!=value}
                            for key,doc in current_docs.items() if key in by_key}
                        detail['indexed_base_counts'] = dict(Counter(
                            r['base_id'] for r in db.execute('SELECT chunk_id,base_id FROM document_chunks')
                            if r['chunk_id'] in vector_ids))
                        with closing(readonly(root/'app_normativa.db')) as norm:
                            normative = {r['chunk_id']:dict(r) for r in norm.execute('SELECT * FROM document_chunks')}
                        saved_norm = {r['chunk_id']:dict(r) for r in db.execute(
                            "SELECT * FROM document_chunks WHERE base_id='editais_normativo'")}
                        detail['normative_source_comparison'] = {
                            'matched_ids':len(normative.keys() & saved_norm.keys()),
                            'field_differences':dict(Counter(col for k,r in normative.items() if k in saved_norm
                                for col,v in r.items() if col not in ('id','document_id') and saved_norm[k].get(col)!=v))}
                        detail['proposed_union'] = {'pncp_chunks':detail['chunks_by_base'].get('pncp_rj_estruturada',0)+len(current.keys()-source_hashes.keys()),
                            'current_55_retained':55,'vectors_unchanged':len(vector_ids),
                            'orphans_after_id_union':len(vector_ids-(source_hashes.keys() | current.keys()))}
                    report['git_snapshots'][-1].update(detail)
    report['git_evidence'] = {
        'current_blob':git('hash-object','app.db'), 'backup_blob':git('hash-object','app.db.backup'),
        'head_app_db':git('ls-tree','HEAD','--','app.db'),
        'stashes':git('stash','list','--format=%H %ad %gs','--date=iso'),
        'reflog':git('reflog','-18','--date=iso'),
    }
    after = {str(p.relative_to(root)):sha(p) for p in protected}
    if before != after:
        raise RuntimeError('Arquivos de origem mudaram durante a auditoria')
    report['preservation'] = {'files':len(protected),'all_sha256_unchanged':True,'sha256':after}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--skip-git',action='store_true')
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    report = diagnose(args.root,not args.skip_git)
    with args.report.open('x',encoding='utf-8') as output:
        json.dump(report,output,ensure_ascii=False,indent=2)
    print('Relatório:',args.report)


if __name__ == '__main__':
    main()
