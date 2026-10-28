"""
Persistência da base normativa (editais_normativo) no banco.

Lê data/processed/<versao>_editais_normativo.jsonl e grava cada trecho em
documents/document_chunks — as tabelas que o FTS5 indexa por trigger e que a
indexação do FAISS consulta. Enquanto os trechos ficam só no arquivo, uma
pergunta sobre lei ou decreto não encontra nada: o analisador escolhe a rota
documental e a recuperação volta vazia.

A tabela tem um conjunto fixo de colunas e não guarda metadado livre. Os
campos próprios da base normativa (artigo, cláusula, parte, overlap) seguem
no JSONL, que continua sendo a fonte completa, e podem ser derivados do
chunk_id, cujo formato é estável nos 794 trechos:

    editais_normativo:<documento>:<artigo ou cláusula>:p<parte>

O número da norma ou do edital vai em documents.title ("Decreto RJ 48.760",
"Lei 14.133"). A coluna vigencia fica vazia de propósito: ela significa fim
de vigência, e o que a base normativa tem é data de publicação, que já vai na
coluna própria — repetir uma na outra inventaria um dado que não existe.

Idempotente pelo chunk_id: rodar de novo ignora o que já está gravado e
continua de onde parou.

Compatível com o carregador do Luciano (persistir_chunks_jsonl_normativos, em
app/modules/documents/services.py). Os dois gravam os mesmos trechos, então
quem roda um depois do outro não duplica trecho; para não duplicar também o
documento, os dois seguem a mesma convenção:

- chave do documento (documents.source_file): "<base_id>|<arquivo>", por
  exemplo "editais_normativo|planalto_l14133.html". O prefixo evita colisão
  entre bases que tenham arquivos com o mesmo nome;
- documents.source_url: vem do inventário da coleta (data/inventory), campo
  "origem" de cada arquivo;
- as duas rodadas precisam cair na mesma versão de corpus. Por padrão este
  módulo cria a sua ("normativo-v1"); para gravar na versão que o carregador
  do Luciano usa, passe --corpus-version-id.

Bancos carregados antes dessa convenção têm a chave sem prefixo; a primeira
execução renomeia essas chaves (migrar_chaves_antigas) em vez de criar
documentos novos.

Uso:
    python -m app.modules.normative.persistencia --versao v1
"""

import argparse
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, init_db
from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk

BASE_ID = "editais_normativo"
PASTA_PROCESSADOS = Path("data/processed")
PASTA_INVENTARIO = Path("data/inventory")
TAMANHO_COMMIT = 500      # a cada N trechos novos o banco é gravado
STATUS_VERSAO = "ready"

# ---------------------------------------------------------------------------
# 1. Caminhos e nomes
# ---------------------------------------------------------------------------

def arquivo_da_versao(versao: str, pasta: Path = PASTA_PROCESSADOS) -> Path:
    """data/processed/v1_editais_normativo.jsonl"""
    return Path(pasta) / f"{versao}_{BASE_ID}.jsonl"


def nome_da_versao(versao: str) -> str:
    """
    Nome da linha em corpus_versions. Fica separado do corpus do PNCP
    (pncp-2026-08) porque as duas bases são coletadas e versionadas
    de formas diferentes.
    """
    return f"normativo-{versao}"


def inventario_da_versao(versao: str, pasta: Path = PASTA_INVENTARIO) -> Path:
    """data/inventory/v1_editais_normativo.json"""
    return Path(pasta) / f"{versao}_{BASE_ID}.json"


def urls_do_inventario(caminho: Path | None) -> dict[str, str]:
    """
    Lê o inventário da coleta e devolve {nome do arquivo: URL de origem}.

    É por essa URL que o documento fica ligado à fonte oficial (Planalto,
    PNCP). Se o inventário não existir ou vier fora do formato, devolve vazio
    e o carregamento segue sem URL, em vez de falhar: a URL é metadado, o
    texto dos trechos não depende dela.
    """
    if caminho is None or not Path(caminho).is_file():
        return {}
    try:
        registros = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(registros, list):
        return {}
    return {
        Path(str(item["path"])).name: str(item["origem"])
        for item in registros
        if isinstance(item, dict) and item.get("path") and item.get("origem")
    }


def chave_do_documento(metadata: dict) -> str:
    """
    Chave que identifica o documento em documents.source_file:
    "<base_id>|<arquivo>".

    É a mesma chave do carregador do Luciano (_document_source_file). Se as
    duas fossem diferentes, rodar um carregador depois do outro criaria um
    segundo documento para cada arquivo, sem nenhum trecho ligado a ele.
    """
    return f"{metadata['base_id']}|{metadata['source_file'].strip()}"


# ---------------------------------------------------------------------------
# 2. O que dá para derivar do chunk_id
# ---------------------------------------------------------------------------

def partes_do_chunk_id(chunk_id: str) -> dict | None:
    """
    Quebra o chunk_id nos quatro pedaços que ele carrega. Serve para montar a
    citação ("Art. 86 do Decreto 48.760") sem precisar de coluna no banco.

    Devolve None se o formato não for o esperado, para quem chama decidir o
    que fazer em vez de receber um pedaço errado em silêncio.
    """
    pedacos = chunk_id.split(":")
    if len(pedacos) != 4:
        return None
    base_id, documento, trecho, parte = pedacos
    if not parte.startswith("p") or not parte[1:].isdigit():
        return None
    return {
        "base_id": base_id,
        "documento": documento,
        "trecho": trecho,
        "parte": int(parte[1:]),
    }


# ---------------------------------------------------------------------------
# 3. Leitura do JSONL
# ---------------------------------------------------------------------------

def _data(texto) -> date | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def _titulo(metadata: dict) -> str | None:
    """
    Leis e decretos têm numero_norma; editais têm numero_edital.
    Os dois são o título do documento.
    """
    return metadata.get("numero_norma") or metadata.get("numero_edital")


def linha_valida(item) -> bool:
    """
    Uma linha só entra no banco se tiver o texto e os metadados obrigatórios
    do enunciado. O que não tiver é contado como inválido e o arquivo segue.
    """
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if not isinstance(item.get("texto"), str) or not item["texto"].strip():
        return False
    obrigatorios = ("source_file", "doc_type", "chunk_id", "sensitivity", "base_id")
    return all(isinstance(metadata.get(campo), str) and metadata[campo] for campo in obrigatorios)


# ---------------------------------------------------------------------------
# 4. Banco
# ---------------------------------------------------------------------------

def obter_ou_criar_versao(db: Session, versao: str) -> CorpusVersion:
    nome = nome_da_versao(versao)
    encontrada = db.scalar(select(CorpusVersion).where(CorpusVersion.version == nome))
    if encontrada is not None:
        return encontrada
    nova = CorpusVersion(version=nome, status=STATUS_VERSAO)
    db.add(nova)
    db.flush()
    return nova


def _criar_documento(
    corpus_version_id: int,
    metadata: dict,
    url: str | None = None,
) -> Document:
    return Document(
        corpus_version_id=corpus_version_id,
        source_file=chave_do_documento(metadata),
        title=_titulo(metadata),
        base_id=metadata["base_id"],
        doc_type=metadata["doc_type"],
        source_url=url,
        sensitivity=metadata["sensitivity"],
    )


def _criar_chunk(documento: Document, texto: str, metadata: dict) -> DocumentChunk:
    return DocumentChunk(
        document_id=documento.id,
        chunk_id=metadata["chunk_id"],
        content=texto,
        doc_type=documento.doc_type,
        base_id=documento.base_id,
        sensitivity=documento.sensitivity,
        orgao=metadata.get("orgao"),
        ano=metadata.get("ano"),
        data_publicacao=_data(metadata.get("data_publicacao")),
        vigencia=None,
    )


# ---------------------------------------------------------------------------
# 5. Persistência
# ---------------------------------------------------------------------------

def migrar_chaves_antigas(db: Session, corpus_version_id: int) -> dict:
    """
    Renomeia as chaves antigas ("planalto_l14133.html") para a convenção
    atual ("editais_normativo|planalto_l14133.html").

    Antes do alinhamento com o carregador do Luciano, a chave era só o nome do
    arquivo. Sem esta migração, um banco já carregado ficaria com os trechos
    presos ao documento de chave antiga, e a primeira execução nova criaria
    um segundo documento, vazio, para cada arquivo.

    Só renomeia dentro da versão de corpus informada e só quando a chave nova
    ainda não existe. Se já existir, o documento é deixado como está e contado
    em "em_conflito": apagar ou juntar documentos é decisão de uma pessoa, não
    de uma rotina de carga.
    """
    resultado = {"migradas": 0, "em_conflito": 0}
    prefixo = f"{BASE_ID}|"

    antigos = db.scalars(
        select(Document).where(
            Document.corpus_version_id == corpus_version_id,
            Document.base_id == BASE_ID,
            ~Document.source_file.startswith(prefixo, autoescape=True),
        )
    ).all()

    for documento in antigos:
        nova = f"{prefixo}{documento.source_file}"
        ja_existe = db.scalar(
            select(Document.id).where(
                Document.corpus_version_id == corpus_version_id,
                Document.source_file == nova,
            )
        )
        if ja_existe is not None:
            resultado["em_conflito"] += 1
            continue
        documento.source_file = nova
        resultado["migradas"] += 1

    if resultado["migradas"]:
        db.flush()
    return resultado


def _sincronizar_documento(
    db: Session,
    documentos: dict[str, Document],
    corpus_version_id: int,
    metadata: dict,
    url: str | None,
) -> dict:
    """
    Completa o título e a URL do documento quando estão vazios e o JSONL ou o
    inventário já trazem o valor. Devolve {"titulo": bool, "url": bool}.

    Só preenche o que está vazio: um valor já gravado não é sobrescrito, para
    o carregamento não desfazer correção feita à mão no banco. É a diferença
    de política em relação ao carregador do Luciano, que sempre sobrescreve;
    o que os dois gravam por cima de um banco vazio é igual.
    """
    feito = {"titulo": False, "url": False}
    titulo = _titulo(metadata)
    if not titulo and not url:
        return feito

    chave = chave_do_documento(metadata)
    documento = documentos.get(chave)
    if documento is None:
        documento = db.scalar(
            select(Document).where(
                Document.corpus_version_id == corpus_version_id,
                Document.source_file == chave,
            )
        )
        if documento is None:
            return feito
        documentos[chave] = documento

    if titulo and not documento.title:
        documento.title = titulo
        feito["titulo"] = True
    if url and not documento.source_url:
        documento.source_url = url
        feito["url"] = True
    return feito


def persistir_jsonl(
    db: Session,
    corpus_version_id: int,
    arquivo: Path,
    tamanho_commit: int = TAMANHO_COMMIT,
    urls_por_arquivo: dict[str, str] | None = None,
) -> dict:
    """
    Percorre o JSONL e grava o que ainda não está no banco.

    A idempotência é pelo chunk_id: uma segunda execução lê o arquivo inteiro
    de novo, pula o que já existe e grava só o que falta.

    urls_por_arquivo liga cada arquivo à sua URL de origem (veja
    urls_do_inventario). Sem ela, o documento é gravado sem URL.
    """
    arquivo = Path(arquivo)
    if not arquivo.is_file():
        raise FileNotFoundError(f"JSONL da base normativa não encontrado: {arquivo}")
    if tamanho_commit <= 0:
        raise ValueError("tamanho_commit deve ser maior que zero.")
    urls_por_arquivo = urls_por_arquivo or {}

    migracao = migrar_chaves_antigas(db, corpus_version_id)
    resumo = {
        "linhas": 0,
        "invalidas": 0,
        "chaves_migradas": migracao["migradas"],
        "chaves_em_conflito": migracao["em_conflito"],
        "documentos_criados": 0,
        "documentos_reaproveitados": 0,
        "titulos_preenchidos": 0,
        "urls_preenchidas": 0,
        "chunks_criados": 0,
        "chunks_ja_existentes": 0,
    }

    ja_no_banco = set(db.scalars(select(DocumentChunk.chunk_id)).all())
    documentos: dict[str, Document] = {}
    desde_o_commit = 0

    with arquivo.open(encoding="utf-8") as entrada:
        for linha in entrada:
            if not linha.strip():
                continue
            try:
                item = json.loads(linha)
            except json.JSONDecodeError:
                resumo["invalidas"] += 1
                continue
            if not linha_valida(item):
                resumo["invalidas"] += 1
                continue

            resumo["linhas"] += 1
            metadata = item["metadata"]
            chunk_id = metadata["chunk_id"]

            url = urls_por_arquivo.get(metadata["source_file"])

            # Título e URL são conferidos em toda passada, inclusive nos
            # trechos que já estão gravados: quando a extração melhora e passa
            # a achar o número do documento, ou quando o inventário passa a
            # existir, o campo que ficou vazio é preenchido sem apagar nada.
            feito = _sincronizar_documento(
                db, documentos, corpus_version_id, metadata, url
            )
            resumo["titulos_preenchidos"] += feito["titulo"]
            resumo["urls_preenchidas"] += feito["url"]

            if chunk_id in ja_no_banco:
                resumo["chunks_ja_existentes"] += 1
                continue

            chave = chave_do_documento(metadata)
            documento = documentos.get(chave)
            if documento is None:
                documento = db.scalar(
                    select(Document).where(
                        Document.corpus_version_id == corpus_version_id,
                        Document.source_file == chave,
                    )
                )
                if documento is None:
                    documento = _criar_documento(corpus_version_id, metadata, url)
                    db.add(documento)
                    db.flush()
                    resumo["documentos_criados"] += 1
                else:
                    resumo["documentos_reaproveitados"] += 1
                documentos[chave] = documento

            db.add(_criar_chunk(documento, item["texto"], metadata))
            ja_no_banco.add(chunk_id)
            resumo["chunks_criados"] += 1
            desde_o_commit += 1
            if desde_o_commit >= tamanho_commit:
                db.commit()
                desde_o_commit = 0

    db.commit()
    return resumo


def persistir(
    versao: str = "v1",
    arquivo: Path | None = None,
    inventario: Path | None = None,
    corpus_version_id: int | None = None,
) -> dict:
    """
    Abre o banco, garante a versão do corpus e grava o JSONL da versão.

    Sem corpus_version_id, usa (ou cria) a versão "normativo-<versao>". Com
    ele, grava numa versão que já existe — por exemplo, a que o carregador do
    Luciano usa — e falha se ela não existir, em vez de criar outra em silêncio.
    """
    caminho = Path(arquivo) if arquivo else arquivo_da_versao(versao)
    urls = urls_do_inventario(inventario or inventario_da_versao(versao))
    init_db()
    with SessionLocal() as db:
        if corpus_version_id is None:
            corpus_id = obter_ou_criar_versao(db, versao).id
        else:
            if db.get(CorpusVersion, corpus_version_id) is None:
                raise ValueError(
                    f"Versão de corpus {corpus_version_id} não existe no banco."
                )
            corpus_id = corpus_version_id
        resumo = persistir_jsonl(db, corpus_id, caminho, urls_por_arquivo=urls)
    resumo["corpus_version_id"] = corpus_id
    resumo["urls_no_inventario"] = len(urls)
    return resumo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Persistência da base normativa no banco")
    parser.add_argument("--versao", default="v1")
    parser.add_argument("--arquivo", default=None)
    parser.add_argument("--inventario", default=None,
                        help="padrão: data/inventory/<versao>_editais_normativo.json")
    parser.add_argument("--corpus-version-id", type=int, default=None,
                        help="grava numa versão de corpus existente (padrão: normativo-<versao>)")
    args = parser.parse_args()
    relatorio = persistir(args.versao, args.arquivo, args.inventario, args.corpus_version_id)
    print(
        f"  [versao]    corpus {relatorio['corpus_version_id']}"
        f" ({nome_da_versao(args.versao) if args.corpus_version_id is None else 'informada'})\n"
        f"  [linhas]    {relatorio['linhas']} lidas · {relatorio['invalidas']} inválidas\n"
        f"  [chave]     {relatorio['chaves_migradas']} migrada(s) para o prefixo · "
        f"{relatorio['chaves_em_conflito']} em conflito\n"
        f"  [documento] {relatorio['documentos_criados']} criados · "
        f"{relatorio['documentos_reaproveitados']} reaproveitados · "
        f"{relatorio['titulos_preenchidos']} título(s) e "
        f"{relatorio['urls_preenchidas']} URL(s) preenchido(s)\n"
        f"  [trecho]    {relatorio['chunks_criados']} gravados · "
        f"{relatorio['chunks_ja_existentes']} já estavam no banco"
    )
