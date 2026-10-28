"""Worker local e durável para tarefas de indexação sob demanda.

Execute em processo separado da API::

    python -m app.modules.documents.worker --watch

Ele consome uma tarefa por vez do SQLite. A API continua responsiva enquanto
download, Docling, chunking e FAISS ocorrem em segundo plano.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import resolver_caminho_indice_faiss
from app.core.database import SessionLocal, init_db
from app.modules.documents.model import DocumentCatalogEntry
from app.modules.documents.services import (
    concluir_tarefa_processamento,
    falhar_tarefa_processamento,
    persistir_chunks_jsonl_pncp,
    reservar_proxima_tarefa_processamento,
    renovar_lease_tarefa_processamento,
)
from src.base_estruturada.chunking import (
    caminho_relativo_portavel,
    gerar_chunk_id,
    gerar_chunks,
    montar_metadata_base,
    selecionar_configuracao,
)
from src.base_estruturada.pncp.repository import (
    extrair_zip_com_seguranca,
    extrair_zips_aninhados_com_seguranca,
)
from src.index import DEFAULT_CHECKPOINT_SIZE, indexar_chunks_incrementalmente


PASTA_TAREFAS = Path("data/adaptive_tasks")
LIMITE_DOWNLOAD_BYTES = 100 * 1024 * 1024
TAMANHO_LOTE_EMBEDDING = 25
FORMATOS_DOCLING_DIRETOS = {".pdf", ".docx"}

ProcessorFn = Callable[[int, Session], dict[str, int]]


class LeasePerdidoError(RuntimeError):
    """O worker não possui mais autorização para finalizar a tarefa."""


def normalizar_url_download_pncp(url: str | None) -> str:
    """Valida URL do PNCP e remove a porta explícita do catálogo.

    Alguns registros do PNCP trazem ``pncp.gov.br:porta``. A rota pública
    equivalente responde em HTTPS padrão (443); remover a porta evita que o
    worker dependa de uma porta alternativa indisponível na rede local.
    """

    if not url:
        raise RuntimeError("Documento catalogado não possui URL de download.")
    destino = urlparse(url)
    if destino.scheme != "https" or destino.hostname != "pncp.gov.br":
        raise RuntimeError("A URL do catálogo não pertence ao domínio seguro do PNCP.")
    return destino._replace(netloc="pncp.gov.br").geturl()


def _nome_seguro(nome: str) -> str:
    return "".join("_" if caractere in '<>:"/\\|?*' else caractere for caractere in nome)


def _baixar_documento(url: str, destino: Path) -> Path:
    """Baixa somente URL PNCP, com tamanho máximo e escrita atômica."""

    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(f"{destino.suffix}.part")
    total = 0
    with requests.get(url, stream=True, timeout=(15, 120), allow_redirects=False) as resposta:
        if resposta.status_code != 200:
            raise RuntimeError(f"Download PNCP retornou HTTP {resposta.status_code}.")
        tamanho = resposta.headers.get("Content-Length")
        if tamanho and int(tamanho) > LIMITE_DOWNLOAD_BYTES:
            raise RuntimeError("Documento excede o limite de download configurado.")
        with temporario.open("wb") as arquivo:
            for bloco in resposta.iter_content(chunk_size=64 * 1024):
                if not bloco:
                    continue
                total += len(bloco)
                if total > LIMITE_DOWNLOAD_BYTES:
                    arquivo.close()
                    temporario.unlink(missing_ok=True)
                    raise RuntimeError("Documento excede o limite de download configurado.")
                arquivo.write(bloco)
    if total == 0:
        temporario.unlink(missing_ok=True)
        raise RuntimeError("O PNCP retornou um arquivo vazio.")
    temporario.replace(destino)
    return destino


def _arquivos_processaveis(
    caminho_original: Path,
    pasta_expandida: Path,
    tarefa_id: int,
) -> list[tuple[Path, str | None]]:
    """Devolve PDFs/DOCX e, quando aplicável, sua entrada estável no ZIP.

    DOCX é um ZIP internamente; por isso a extensão de um arquivo direto é
    avaliada antes de perguntar se ele é um pacote ZIP a ser expandido.
    """

    if caminho_original.suffix.casefold() in FORMATOS_DOCLING_DIRETOS:
        return [(caminho_original, None)]

    with caminho_original.open("rb") as arquivo:
        assinatura = arquivo.read(4)
    if assinatura == b"%PDF":
        return [(caminho_original, None)]
    if not zipfile.is_zipfile(caminho_original):
        tipo = mimetypes.guess_type(caminho_original.name)[0] or "formato desconhecido"
        raise RuntimeError(f"Formato ainda não suportado pelo worker local: {tipo}.")

    extraidos = extrair_zip_com_seguranca(
        caminho_original,
        pasta_expandida,
        sufixo_colisao=f"tarefa-{tarefa_id}",
    )
    aninhados = extrair_zips_aninhados_com_seguranca(
        [arquivo for arquivo in extraidos if zipfile.is_zipfile(arquivo)],
        sufixo_colisao=f"tarefa-{tarefa_id}",
    )
    todos = [*extraidos, *aninhados["arquivos"]]
    documentos = [
        arquivo
        for arquivo in todos
        if arquivo.suffix.casefold() in FORMATOS_DOCLING_DIRETOS
    ]
    if not documentos:
        raise RuntimeError("O pacote não contém PDFs ou DOCX processáveis.")
    return [
        (arquivo, arquivo.relative_to(pasta_expandida).as_posix())
        for arquivo in sorted(set(documentos))
    ]


def _converter_documento_docling(caminho: Path) -> str:
    """Converte um PDF ou DOCX local sem derrubar a API em caso de falha."""

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling não está instalado no ambiente do worker local."
        ) from exc
    resultado = DocumentConverter().convert(caminho, raises_on_error=False)
    if resultado.document is None:
        raise RuntimeError(f"Docling não extraiu documento: {resultado.status}")
    markdown = resultado.document.export_to_markdown().strip()
    if not markdown:
        raise RuntimeError("Docling retornou Markdown vazio.")
    return markdown


def _dados_entidade(item: DocumentCatalogEntry) -> tuple[dict[str, Any], dict[str, Any], str]:
    tipo = item.tipo_entidade
    entidade: dict[str, Any] = {
        "numeroControlePNCP": item.numero_controle_pncp,
        "orgaoEntidade": {"cnpj": item.cnpj_orgao},
        "unidadeOrgao": {},
    }
    if tipo == "contrato":
        entidade.update({"anoContrato": item.ano, "sequencialContrato": item.sequencial_entidade})
    else:
        entidade.update({"anoCompra": item.ano, "sequencialCompra": item.sequencial_entidade})
    documento = {
        "sequencialDocumento": item.sequencial_documento,
        "tipoDocumentoNome": item.tipo_documento,
        "titulo": item.titulo,
        "url": item.url_download,
    }
    return entidade, documento, tipo


def processar_tarefa_local(tarefa_id: int, db: Session, *, corpus_version_id: int) -> dict[str, int]:
    """Executa pipeline local completo de uma tarefa já reservada pelo worker."""

    from app.modules.documents.model import DocumentProcessingTask

    tarefa = db.get(DocumentProcessingTask, tarefa_id)
    if tarefa is None:
        raise RuntimeError("Tarefa não encontrada durante o processamento.")
    item = db.get(DocumentCatalogEntry, tarefa.catalog_entry_id)
    if item is None:
        raise RuntimeError("Item do catálogo não encontrado durante o processamento.")

    pasta_tarefa = PASTA_TAREFAS / str(tarefa.id)
    pasta_original = pasta_tarefa / "original"
    pasta_expandida = pasta_tarefa / "expanded"
    pasta_markdown = pasta_tarefa / "markdown"
    nome = _nome_seguro(item.titulo or f"documento_{item.sequencial_documento}")
    if not Path(nome).suffix:
        nome = f"{nome}.bin"
    url_download = normalizar_url_download_pncp(item.url_download)
    original = _baixar_documento(url_download, pasta_original / nome)
    fontes = _arquivos_processaveis(original, pasta_expandida, tarefa.id)
    entidade, documento, tipo_entidade = _dados_entidade(item)
    linhas: list[dict[str, Any]] = []

    for numero, (pdf, caminho_interno) in enumerate(fontes, start=1):
        markdown = _converter_documento_docling(pdf)
        destino_md = pasta_markdown / f"{numero:03d}_{_nome_seguro(pdf.stem)}.md"
        destino_md.parent.mkdir(parents=True, exist_ok=True)
        destino_md.write_text(markdown, encoding="utf-8")
        registro = {
            "textoAtivo": {"origem": "docling_local", "caminho": str(destino_md)},
            "tipoFonte": (
                f"{pdf.suffix.casefold().removeprefix('.')}_expandido_de_zip"
                if caminho_interno
                else f"{pdf.suffix.casefold().removeprefix('.')}_original"
            ),
            "arquivoPacote": original.name if caminho_interno else None,
            "arquivoOriginal": pdf.name,
            "caminhoInternoPacote": caminho_interno,
        }
        metadata = montar_metadata_base(
            entidade,
            documento,
            registro,
            tipo_entidade,
            caminho_relativo_portavel(destino_md, Path.cwd()),
        )
        configuracao = selecionar_configuracao(item.tipo_documento, destino_md)
        for indice, conteudo in enumerate(gerar_chunks(markdown, configuracao, usar_langchain=True)):
            linhas.append(
                {
                    "chunk_id": gerar_chunk_id(metadata, indice, conteudo),
                    "content": conteudo,
                    "metadata": {**metadata, "indiceChunk": indice, "configuracaoChunk": configuracao.nome},
                }
            )

    if not linhas:
        raise RuntimeError("Nenhum chunk foi gerado para o documento.")
    caminho_jsonl = pasta_tarefa / "chunks.jsonl"
    caminho_jsonl.write_text(
        "".join(json.dumps(linha, ensure_ascii=False) + "\n" for linha in linhas),
        encoding="utf-8",
    )
    persistencia = persistir_chunks_jsonl_pncp(
        db, corpus_version_id, caminho_jsonl, tipos_entidade={tipo_entidade}
    )

    from llm_ecoa import EMBEDDING_MODEL, gerar_embedding

    _, resumo_indice = indexar_chunks_incrementalmente(
        ((linha["chunk_id"], linha["content"]) for linha in linhas),
        gerar_embedding,
        resolver_caminho_indice_faiss(),
        tamanho_lote=TAMANHO_LOTE_EMBEDDING,
        tamanho_checkpoint=DEFAULT_CHECKPOINT_SIZE,
        modelo_embedding=EMBEDDING_MODEL,
        origem=f"adaptive_task_{tarefa.id}",
    )
    (pasta_tarefa / "relatorio.json").write_text(
        json.dumps(
            {
                "tarefaId": tarefa.id,
                "catalogEntryId": item.id,
                "pdfsProcessados": len(fontes),
                "chunksGerados": len(linhas),
                "persistencia": persistencia,
                "vetoresNovos": resumo_indice.novos,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"pdfs": len(fontes), "chunks": len(linhas), "vetores": resumo_indice.novos}


def executar_uma_tarefa(
    fabrica_sessao: sessionmaker[Session],
    processador: ProcessorFn,
    *,
    worker_id: str | None = None,
    lease_seconds: int = 900,
) -> bool:
    """Reserva e executa uma tarefa; retorna falso quando não há pendências."""

    with fabrica_sessao() as db:
        tarefa = reservar_proxima_tarefa_processamento(
            db,
            worker_id=worker_id or f"{socket.gethostname()}:{os.getpid()}",
            lease_seconds=lease_seconds,
        )
        if tarefa is None:
            return False
        tarefa_id = tarefa.id
        lease_token = tarefa.lease_token
        if not lease_token:
            raise RuntimeError("A reserva da tarefa não gerou lease token.")
    parar_heartbeat = threading.Event()
    lease_perdido = threading.Event()

    def heartbeat() -> None:
        intervalo = max(1.0, lease_seconds / 3)
        while not parar_heartbeat.wait(intervalo):
            try:
                with fabrica_sessao() as db:
                    renovar_lease_tarefa_processamento(
                        db,
                        tarefa_id,
                        lease_token,
                        lease_seconds=lease_seconds,
                    )
            except Exception:
                lease_perdido.set()
                return

    thread_heartbeat = threading.Thread(
        target=heartbeat,
        name=f"document-task-{tarefa_id}-heartbeat",
        daemon=True,
    )
    thread_heartbeat.start()
    try:
        with fabrica_sessao() as db:
            processador(tarefa_id, db)
            if lease_perdido.is_set():
                raise LeasePerdidoError("Lease perdido durante o processamento.")
            concluir_tarefa_processamento(db, tarefa_id, lease_token)
    except Exception as exc:
        error_code = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).casefold()
        with fabrica_sessao() as db:
            try:
                falhar_tarefa_processamento(db, tarefa_id, error_code, lease_token)
            except ValueError:
                db.rollback()
        print(f"Tarefa {tarefa_id} ERRO | codigo={error_code}")
        return True
    finally:
        parar_heartbeat.set()
        thread_heartbeat.join(timeout=2)
    print(f"Tarefa {tarefa_id} CONCLUIDA")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker local de indexação sob demanda.")
    parser.add_argument("--watch", action="store_true", help="Fica aguardando novas tarefas.")
    parser.add_argument("--intervalo", type=float, default=3.0)
    parser.add_argument("--corpus-version-id", type=int, default=1)
    args = parser.parse_args()
    if args.intervalo <= 0:
        parser.error("--intervalo deve ser maior que zero.")
    init_db()

    def processador(tarefa_id: int, db: Session) -> dict[str, int]:
        return processar_tarefa_local(tarefa_id, db, corpus_version_id=args.corpus_version_id)

    while True:
        executou = executar_uma_tarefa(SessionLocal, processador)
        if not args.watch:
            return
        if not executou:
            time.sleep(args.intervalo)


if __name__ == "__main__":
    main()
