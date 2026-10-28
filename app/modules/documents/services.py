from __future__ import annotations

import json
import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterable

from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.text_normalization import normalizar_para_busca
from app.modules.documents.model import (
    Document,
    DocumentCatalogEntry,
    DocumentCatalogInventory,
    DocumentCatalogInventoryMember,
    DocumentCatalogState,
    DocumentChunk,
    DocumentProcessingTask,
    DocumentProcessingTaskStatus,
)
from src.base_estruturada.chunking import (
    caminho_relativo_portavel,
    gerar_chunk_id,
    gerar_chunks,
    montar_metadata_base,
    selecionar_configuracao,
)
from src.base_estruturada.pipelines.pipeline_chunks import _indice_por_controle


SENSIBILIDADE_PADRAO = "publico"
_STATUS_TAREFA_ATIVA = {
    DocumentProcessingTaskStatus.PENDENTE.value,
    DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
}


def _data(valor: Any) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _decimal(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _document_source_file(metadata: dict[str, Any]) -> str:
    # Bases não-PNCP (por exemplo, a normativa) já possuem um nome de origem
    # estável. Nos chunks PNCP, a chave de negócio continua sendo o controle
    # do processo + sequência do documento.
    identificador = metadata.get("identificadorDocumentoFonte")
    # O identificador de filho só é adotado quando o extrator informou a
    # entrada real do pacote. Sem isso, preservamos a chave v1 para que
    # reprocessamentos de lotes atuais não criem Documents paralelos.
    if (
        metadata.get("caminhoInternoPacote")
        and isinstance(identificador, str)
        and identificador.strip()
    ):
        return "|".join(
            [
                str(metadata.get("baseId") or metadata.get("base_id") or ""),
                identificador.strip(),
            ]
        )

    source_file = metadata.get("source_file")
    if isinstance(source_file, str) and source_file.strip():
        return "|".join(
            [
                str(metadata.get("base_id") or metadata.get("baseId") or ""),
                source_file.strip(),
            ]
        )
    return "|".join(
        [
            str(metadata.get("baseId", "")),
            str(metadata.get("tipoEntidade", "")),
            str(metadata.get("numeroControlePNCP", "")),
            str(metadata.get("sequencialDocumento", "")),
        ]
    )


def _atualizar_origem_documento(
    documento: Document,
    metadata: dict[str, Any],
) -> None:
    """Sincroniza os identificadores de origem quando o JSONL é reprocessado."""

    titulo = (
        metadata.get("tituloDocumento")
        or metadata.get("numero_norma")
        or metadata.get("numero_edital")
        or metadata.get("source_file")
    )
    if isinstance(titulo, str) and titulo.strip():
        documento.title = titulo.strip()

    url = metadata.get("urlFontePncp") or metadata.get("source_url")
    if isinstance(url, str) and url.strip():
        documento.source_url = url.strip()


def _obter_ou_criar_documento(
    db: Session,
    corpus_version_id: int,
    metadata: dict[str, Any],
) -> tuple[Document, bool]:
    source_file = _document_source_file(metadata)
    documento = db.scalar(
        select(Document).where(
            Document.corpus_version_id == corpus_version_id,
            Document.source_file == source_file,
        )
    )
    if documento is not None:
        _atualizar_origem_documento(documento, metadata)
        return documento, False

    documento = Document(
        corpus_version_id=corpus_version_id,
        source_file=source_file,
        title=None,
        base_id=str(metadata.get("baseId") or metadata.get("base_id") or ""),
        doc_type=str(metadata.get("tipoEntidade") or metadata.get("doc_type") or ""),
        source_url=None,
        sensitivity=SENSIBILIDADE_PADRAO,
    )
    _atualizar_origem_documento(documento, metadata)
    db.add(documento)
    db.flush()
    return documento, True


def _criar_chunk(
    documento: Document,
    chunk_id: str,
    conteudo: str,
    metadata: dict[str, Any],
) -> DocumentChunk:
    valor = metadata.get("valorGlobal", metadata.get("valorTotalEstimado"))
    data_publicacao = metadata.get(
        "dataAssinatura",
        metadata.get("dataAberturaProposta", metadata.get("data_publicacao")),
    )
    vigencia = metadata.get("dataVigenciaFim", metadata.get("dataEncerramentoProposta"))
    return DocumentChunk(
        document_id=documento.id,
        chunk_id=chunk_id,
        content=conteudo,
        doc_type=documento.doc_type,
        base_id=documento.base_id,
        sensitivity=documento.sensitivity,
        orgao=metadata.get("orgao"),
        modalidade=metadata.get("modalidade"),
        municipio=metadata.get("municipio"),
        fornecedor=metadata.get("fornecedor"),
        objeto=metadata.get("objeto"),
        valor=_decimal(valor),
        ano=metadata.get("ano"),
        data_publicacao=_data(data_publicacao),
        vigencia=_data(vigencia),
    )


def _data_hora(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def _agora_utc_sem_fuso() -> datetime:
    """SQLite persiste os timestamps do catálogo em UTC sem offset."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_json(valor: Any) -> str:
    serializado = json.dumps(
        valor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _identidade_inventario(
    manifestos: list[tuple[str, str, Path]],
) -> tuple[str, str]:
    descritores = []
    for tipo_entidade, periodo_coleta, caminho in sorted(
        manifestos, key=lambda item: (item[0], item[1], item[2].as_posix())
    ):
        if tipo_entidade not in {"contrato", "contratacao"}:
            raise ValueError(f"Tipo de entidade inválido: {tipo_entidade}")
        if not caminho.is_file():
            raise FileNotFoundError(f"Manifesto não encontrado: {caminho}")
        descritores.append(
            {
                "tipoEntidade": tipo_entidade,
                "periodoColeta": periodo_coleta,
                "sha256": hashlib.sha256(caminho.read_bytes()).hexdigest(),
            }
        )
    if not descritores:
        raise ValueError("Informe ao menos um manifesto para reconciliar o catálogo.")
    digest = _hash_json(descritores)
    escopo = json.dumps(
        {
            "tiposEntidade": sorted({item["tipoEntidade"] for item in descritores}),
            "periodos": sorted({item["periodoColeta"] for item in descritores}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return digest, escopo


def _valores_catalogo(
    tipo_entidade: str,
    periodo_coleta: str,
    caminho: Path,
    entidade: dict[str, Any],
    documento: dict[str, Any],
) -> dict[str, Any]:
    return {
        "cnpj_orgao": str(
            documento.get("cnpj") or entidade.get("cnpjOrgao") or ""
        ) or None,
        "ano": documento.get("anoCompra") or entidade.get(
            "anoContrato" if tipo_entidade == "contrato" else "anoCompra"
        ),
        "sequencial_entidade": documento.get("sequencialCompra") or entidade.get(
            "sequencialContrato" if tipo_entidade == "contrato" else "sequencialCompra"
        ),
        "tipo_documento": documento.get("tipoDocumentoNome"),
        "titulo": documento.get("titulo"),
        "url_download": documento.get("url") or documento.get("uri"),
        "uri_original": documento.get("uri"),
        "data_publicacao_pncp": _data_hora(documento.get("dataPublicacaoPncp")),
        "status_ativo": documento.get("statusAtivo"),
        "periodo_coleta": periodo_coleta,
        "manifesto_origem": caminho.as_posix(),
    }


def _fingerprint_catalogo(
    tipo_entidade: str,
    controle: str,
    sequencial_documento: int,
    valores: dict[str, Any],
) -> str:
    return _hash_json(
        {
            "tipoEntidade": tipo_entidade,
            "numeroControlePNCP": controle,
            "sequencialDocumento": sequencial_documento,
            **{
                campo: valor.isoformat() if isinstance(valor, datetime) else valor
                for campo, valor in valores.items()
                if campo not in {"periodo_coleta", "manifesto_origem"}
            },
        }
    )


def persistir_catalogo_documentos_pncp(
    db: Session,
    manifestos: Iterable[tuple[str, str, Path]],
    tamanho_commit: int = 2_000,
    *,
    authoritative_full_snapshot: bool = False,
) -> dict[str, int | str]:
    """Persiste manifestos originais do PNCP como catálogo, sem baixar nada.

    Cada tupla é ``(tipo_entidade, periodo_coleta, caminho_manifesto)``. A
    idempotência usa ``tipo + numeroControlePNCP + sequencialDocumento``. Em
    uma nova listagem, título, URL e demais informações são atualizados, mas
    nenhum arquivo é consultado na rede.
    """

    if tamanho_commit <= 0 or tamanho_commit > 10_000:
        raise ValueError("tamanho_commit deve estar entre 1 e 10000.")

    manifestos = list(manifestos)
    inventory_id, scope = _identidade_inventario(manifestos)
    agora = _agora_utc_sem_fuso()
    inventario = db.get(DocumentCatalogInventory, inventory_id)
    if inventario is None:
        inventario = DocumentCatalogInventory(
            id=inventory_id,
            manifest_set_hash=inventory_id,
            scope=scope,
            authoritative_full_snapshot=authoritative_full_snapshot,
            status="RUNNING",
            manifest_count=len(manifestos),
            last_reconciled_at=agora,
        )
        db.add(inventario)
    else:
        inventario.scope = scope
        inventario.authoritative_full_snapshot = (
            inventario.authoritative_full_snapshot or authoritative_full_snapshot
        )
        inventario.status = "RUNNING"
        inventario.error_code = None
        inventario.last_reconciled_at = agora
    db.commit()

    # Uma interrupção nunca invalida os lotes já confirmados. Ao retomar o
    # mesmo inventário, ignoramos suas chaves antes de carregar objetos ORM e
    # seguimos exatamente do primeiro membro ainda ausente.
    chaves_processadas = set(
        db.execute(
            select(
                DocumentCatalogEntry.tipo_entidade,
                DocumentCatalogEntry.numero_controle_pncp,
                DocumentCatalogEntry.sequencial_documento,
            )
            .join(
                DocumentCatalogInventoryMember,
                DocumentCatalogInventoryMember.catalog_entry_id
                == DocumentCatalogEntry.id,
            )
            .join(
                DocumentCatalogState,
                DocumentCatalogState.catalog_entry_id == DocumentCatalogEntry.id,
            )
            .where(
                DocumentCatalogInventoryMember.inventory_id == inventory_id,
                DocumentCatalogState.last_inventory_id == inventory_id,
                DocumentCatalogState.availability_status == "AVAILABLE",
            )
        ).all()
    )

    resumo = {
        "inventoryId": inventory_id,
        "manifestosLidos": 0,
        "entidadesLidas": 0,
        "documentosLidos": 0,
        "catalogados": 0,
        "atualizados": 0,
        "inalterados": 0,
        "metadadosAlterados": 0,
        "reativados": 0,
        "ausentes": 0,
        "ignoradosInvalidos": 0,
    }
    lote: list[tuple[str, str, int, dict[str, Any], str]] = []

    def persistir_lote() -> None:
        if not lote:
            return
        chaves = {(tipo, controle, sequencial) for tipo, controle, sequencial, _, _ in lote}
        existentes = {
            (item.tipo_entidade, item.numero_controle_pncp, item.sequencial_documento): item
            for item in db.scalars(
                select(DocumentCatalogEntry).where(
                    tuple_(
                        DocumentCatalogEntry.tipo_entidade,
                        DocumentCatalogEntry.numero_controle_pncp,
                        DocumentCatalogEntry.sequencial_documento,
                    ).in_(chaves)
                )
            ).all()
        }
        itens: list[tuple[DocumentCatalogEntry, str]] = []
        for tipo, controle, sequencial, valores, fingerprint in lote:
            chave = (tipo, controle, sequencial)
            item = existentes.get(chave)
            if item is None:
                item = DocumentCatalogEntry(
                    tipo_entidade=tipo,
                    numero_controle_pncp=controle,
                    sequencial_documento=sequencial,
                    **valores,
                )
                db.add(item)
                existentes[chave] = item
                resumo["catalogados"] += 1
            else:
                alterado = any(getattr(item, campo) != valor for campo, valor in valores.items())
                for campo, valor in valores.items():
                    setattr(item, campo, valor)
                resumo["atualizados" if alterado else "inalterados"] += 1
            itens.append((item, fingerprint))
        db.flush()

        ids = [item.id for item, _ in itens]
        estados = {
            estado.catalog_entry_id: estado
            for estado in db.scalars(
                select(DocumentCatalogState).where(
                    DocumentCatalogState.catalog_entry_id.in_(ids)
                )
            ).all()
        }
        membros_existentes = set(
            db.scalars(
                select(DocumentCatalogInventoryMember.catalog_entry_id).where(
                    DocumentCatalogInventoryMember.inventory_id == inventory_id,
                    DocumentCatalogInventoryMember.catalog_entry_id.in_(ids),
                )
            ).all()
        )
        for item, fingerprint in itens:
            estado = estados.get(item.id)
            if estado is None:
                db.add(
                    DocumentCatalogState(
                        catalog_entry_id=item.id,
                        availability_status="AVAILABLE",
                        current_fingerprint=fingerprint,
                        first_seen_at=agora,
                        last_seen_at=agora,
                        missing_since=None,
                        last_inventory_id=inventory_id,
                    )
                )
            else:
                if estado.current_fingerprint != fingerprint:
                    resumo["metadadosAlterados"] += 1
                if estado.availability_status == "REMOVED":
                    resumo["reativados"] += 1
                estado.availability_status = "AVAILABLE"
                estado.current_fingerprint = fingerprint
                estado.last_seen_at = agora
                estado.missing_since = None
                estado.last_inventory_id = inventory_id
            if item.id not in membros_existentes:
                db.add(
                    DocumentCatalogInventoryMember(
                        inventory_id=inventory_id,
                        catalog_entry_id=item.id,
                        metadata_fingerprint=fingerprint,
                        observed_at=agora,
                    )
                )
        db.commit()
        chaves_processadas.update(
            (item.tipo_entidade, item.numero_controle_pncp, item.sequencial_documento)
            for item, _ in itens
        )
        lote.clear()

    try:
        for tipo_entidade, periodo_coleta, caminho in manifestos:
            try:
                entidades = json.loads(caminho.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Manifesto JSON inválido: {caminho}") from exc
            if not isinstance(entidades, list):
                raise ValueError(f"Manifesto deve conter uma lista: {caminho}")

            resumo["manifestosLidos"] += 1
            for entidade in entidades:
                if not isinstance(entidade, dict):
                    resumo["ignoradosInvalidos"] += 1
                    continue
                resumo["entidadesLidas"] += 1
                controle = entidade.get("numeroControlePNCP")
                documentos = entidade.get("documentos") or []
                if not isinstance(controle, str) or not controle or not isinstance(documentos, list):
                    resumo["ignoradosInvalidos"] += 1
                    continue

                for documento in documentos:
                    if not isinstance(documento, dict):
                        resumo["ignoradosInvalidos"] += 1
                        continue
                    sequencial_documento = documento.get("sequencialDocumento")
                    if not isinstance(sequencial_documento, int):
                        resumo["ignoradosInvalidos"] += 1
                        continue
                    resumo["documentosLidos"] += 1
                    chave = (
                        tipo_entidade,
                        controle,
                        sequencial_documento,
                    )
                    if chave in chaves_processadas:
                        resumo["inalterados"] += 1
                        continue
                    valores = _valores_catalogo(
                        tipo_entidade, periodo_coleta, caminho, entidade, documento
                    )
                    fingerprint = _fingerprint_catalogo(
                        tipo_entidade, controle, sequencial_documento, valores
                    )
                    lote.append(
                        (
                            tipo_entidade,
                            controle,
                            sequencial_documento,
                            valores,
                            fingerprint,
                        )
                    )
                    if len(lote) >= tamanho_commit:
                        persistir_lote()
        persistir_lote()

        if authoritative_full_snapshot:
            ausentes = db.execute(
                update(DocumentCatalogState)
                .where(
                    DocumentCatalogState.last_inventory_id != inventory_id,
                    DocumentCatalogState.availability_status == "AVAILABLE",
                )
                .values(availability_status="REMOVED", missing_since=agora)
            )
            resumo["ausentes"] = int(ausentes.rowcount or 0)

        inventario = db.get(DocumentCatalogInventory, inventory_id)
        assert inventario is not None
        inventario.status = "COMPLETED"
        inventario.documents_seen = int(resumo["documentosLidos"])
        inventario.documents_cataloged = int(resumo["documentosLidos"])
        inventario.documents_updated = int(resumo["atualizados"])
        inventario.documents_missing = int(resumo["ausentes"])
        inventario.completed_at = _agora_utc_sem_fuso()
        inventario.error_code = None
        db.commit()
        return resumo
    except Exception as exc:
        db.rollback()
        inventario = db.get(DocumentCatalogInventory, inventory_id)
        if inventario is not None:
            inventario.status = "FAILED"
            inventario.error_code = type(exc).__name__[:128]
            inventario.completed_at = _agora_utc_sem_fuso()
            db.commit()
        raise


def _url_canonica_pncp_catalogo(item: DocumentCatalogEntry) -> str | None:
    """Monta uma URL PNCP estável sem reaproveitar portas temporárias da API."""

    if not (
        item.cnpj_orgao
        and item.ano is not None
        and item.sequencial_entidade is not None
    ):
        return item.url_download
    recurso = "contratos" if item.tipo_entidade == "contrato" else "compras"
    return (
        "https://pncp.gov.br/pncp-api/v1/orgaos/"
        f"{item.cnpj_orgao}/{recurso}/{item.ano}/"
        f"{item.sequencial_entidade}/arquivos/{item.sequencial_documento}"
    )


def _status_processamento_catalogo(
    db: Session,
    item: DocumentCatalogEntry,
) -> tuple[str, bool, int]:
    """Calcula o estado local do arquivo anunciado pelo PNCP.

    A associação não usa caminho do computador: a origem persistida dos
    documentos PNCP contém ``tipo_entidade|numero_controle|sequencial``. Este
    status é somente de leitura e não cria tarefa, download ou indexação.
    """

    sufixo_origem = (
        f"|{item.tipo_entidade}|{item.numero_controle_pncp}|"
        f"{item.sequencial_documento}"
    )
    documentos, chunks = db.execute(
        select(
            func.count(func.distinct(Document.id)),
            func.count(DocumentChunk.id),
        )
        .select_from(Document)
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(
            or_(
                Document.source_file.like(f"%{sufixo_origem}"),
                Document.source_file.like(f"%{sufixo_origem}|%"),
            )
        )
    ).one()
    documento_persistido = bool(documentos)
    chunks_persistidos = int(chunks or 0)
    if chunks_persistidos:
        return "PROCESSADO", documento_persistido, chunks_persistidos
    if documento_persistido:
        return "INCOMPLETO", documento_persistido, 0
    return "PENDENTE", False, 0


def serializar_catalogo_documento(
    item: DocumentCatalogEntry,
    *,
    status_processamento: str = "PENDENTE",
    documento_persistido: bool = False,
    chunks_persistidos: int = 0,
) -> dict[str, Any]:
    """Formato público do catálogo usado pelo endpoint e pelo fallback RAG."""

    estado = item.catalog_state
    return {
        "id": item.id,
        "tipoEntidade": item.tipo_entidade,
        "numeroControlePNCP": item.numero_controle_pncp,
        "cnpjOrgao": item.cnpj_orgao,
        "ano": item.ano,
        "sequencialEntidade": item.sequencial_entidade,
        "sequencialDocumento": item.sequencial_documento,
        "tipoDocumento": item.tipo_documento,
        "titulo": item.titulo,
        "urlDownload": _url_canonica_pncp_catalogo(item),
        "urlOriginal": item.url_download,
        "dataPublicacaoPncp": (
            item.data_publicacao_pncp.isoformat()
            if item.data_publicacao_pncp is not None
            else None
        ),
        "statusAtivo": item.status_ativo,
        "periodoColeta": item.periodo_coleta,
        "availabilityStatus": (
            estado.availability_status if estado is not None else "UNKNOWN"
        ),
        "firstSeenAt": (
            estado.first_seen_at.isoformat() if estado is not None else None
        ),
        "lastSeenAt": (
            estado.last_seen_at.isoformat() if estado is not None else None
        ),
        "missingSince": (
            estado.missing_since.isoformat()
            if estado is not None and estado.missing_since is not None
            else None
        ),
        "inventoryId": estado.last_inventory_id if estado is not None else None,
        "statusProcessamento": status_processamento,
        "documentoPersistido": documento_persistido,
        "chunksPersistidos": chunks_persistidos,
    }


def consultar_catalogo_documentos_pncp(
    db: Session,
    *,
    numero_controle_pncp: str | None = None,
    tipo_entidade: str | None = None,
    tipo_documento: str | None = None,
    titulo: str | None = None,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """Consulta localmente o catálogo PNCP, sem acionar a API externa."""

    if not any([numero_controle_pncp, tipo_entidade, tipo_documento, titulo]):
        raise ValueError("Informe ao menos um filtro para consultar o catálogo.")
    if not 1 <= limite <= 100:
        raise ValueError("limite deve estar entre 1 e 100.")

    consulta = select(DocumentCatalogEntry)
    if numero_controle_pncp:
        consulta = consulta.where(
            DocumentCatalogEntry.numero_controle_pncp == numero_controle_pncp.strip()
        )
    if tipo_entidade:
        consulta = consulta.where(DocumentCatalogEntry.tipo_entidade == tipo_entidade)
    for coluna, valor in (
        (DocumentCatalogEntry.tipo_documento, tipo_documento),
        (DocumentCatalogEntry.titulo, titulo),
    ):
        if valor and valor.strip():
            consulta = consulta.where(
                func.normalizar_busca(coluna).like(
                    f"%{normalizar_para_busca(valor)}%"
                )
            )

    consulta = consulta.order_by(
        DocumentCatalogEntry.periodo_coleta.desc(),
        DocumentCatalogEntry.sequencial_documento.asc(),
    ).limit(limite)
    documentos: list[dict[str, Any]] = []
    for item in db.scalars(consulta):
        status_processamento, documento_persistido, chunks_persistidos = (
            _status_processamento_catalogo(db, item)
        )
        documentos.append(
            serializar_catalogo_documento(
                item,
                status_processamento=status_processamento,
                documento_persistido=documento_persistido,
                chunks_persistidos=chunks_persistidos,
            )
        )
    return documentos


def solicitar_processamento_catalogo(
    db: Session,
    catalog_entry_id: int,
    *,
    motivo: str = "solicitacao_usuario",
) -> tuple[DocumentProcessingTask, bool]:
    """Cria uma tarefa pendente ou reaproveita uma tarefa ativa existente.

    Esta função não baixa arquivos, não chama Docling e não altera índices.
    Ela apenas registra, de forma idempotente, o trabalho que será consumido
    pelo worker na próxima etapa.
    """

    item = db.get(DocumentCatalogEntry, catalog_entry_id)
    if item is None:
        raise ValueError("Documento do catálogo não encontrado.")

    status_processamento, _, _ = _status_processamento_catalogo(db, item)
    if status_processamento == "PROCESSADO":
        raise ValueError("O documento já está processado e não exige nova tarefa.")

    existente = db.scalar(
        select(DocumentProcessingTask)
        .where(
            DocumentProcessingTask.catalog_entry_id == catalog_entry_id,
            DocumentProcessingTask.status.in_(_STATUS_TAREFA_ATIVA),
        )
        .order_by(DocumentProcessingTask.id.desc())
    )
    if existente is not None:
        return existente, False

    tarefa = DocumentProcessingTask(
        catalog_entry_id=catalog_entry_id,
        status=DocumentProcessingTaskStatus.PENDENTE.value,
        tentativas=0,
        motivo=motivo.strip()[:200] if motivo.strip() else None,
    )
    db.add(tarefa)
    try:
        db.commit()
    except IntegrityError:
        # A restrição parcial resolve a corrida entre duas requisições que
        # tentem criar simultaneamente a tarefa do mesmo documento.
        db.rollback()
        concorrente = db.scalar(
            select(DocumentProcessingTask)
            .where(
                DocumentProcessingTask.catalog_entry_id == catalog_entry_id,
                DocumentProcessingTask.status.in_(_STATUS_TAREFA_ATIVA),
            )
            .order_by(DocumentProcessingTask.id.desc())
        )
        if concorrente is None:
            raise
        return concorrente, False
    db.refresh(tarefa)
    return tarefa, True


def consultar_tarefa_processamento(
    db: Session,
    tarefa_id: int,
) -> DocumentProcessingTask:
    """Retorna uma tarefa persistida sem tentar processá-la."""

    tarefa = db.get(DocumentProcessingTask, tarefa_id)
    if tarefa is None:
        raise ValueError("Tarefa de processamento não encontrada.")
    return tarefa


def retentar_tarefa_processamento(
    db: Session,
    tarefa_id: int,
) -> DocumentProcessingTask:
    """Reabre uma tarefa que falhou, sem criar outro documento ou outro chunk.

    É útil para falhas transitórias (PNCP indisponível, índice FAISS ainda não
    preparado). A execução continua idempotente: o worker pode reaproveitar
    chunks já persistidos e o FAISS ignora IDs que já estejam indexados.
    """

    tarefa = db.get(DocumentProcessingTask, tarefa_id)
    if tarefa is None:
        raise ValueError("Tarefa de processamento não encontrada.")
    if tarefa.status != DocumentProcessingTaskStatus.ERRO.value:
        raise ValueError("Somente tarefas com erro podem ser retentadas.")
    tarefa.status = DocumentProcessingTaskStatus.PENDENTE.value
    tarefa.erro = None
    tarefa.error_code = None
    tarefa.lease_owner = None
    tarefa.lease_token = None
    tarefa.lease_expires_at = None
    tarefa.iniciado_em = None
    tarefa.concluido_em = None
    db.commit()
    db.refresh(tarefa)
    return tarefa


def reservar_proxima_tarefa_processamento(
    db: Session,
    *,
    worker_id: str = "document-worker",
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> DocumentProcessingTask | None:
    """Reserva uma tarefa pendente de modo seguro para um worker local.

    A atualização condicional impede que dois workers que leram a mesma linha
    iniciem o mesmo processamento. Para o MVP, um worker é suficiente, mas a
    regra continua válida se o processo for iniciado duas vezes por engano.
    """

    if lease_seconds <= 0:
        raise ValueError("lease_seconds deve ser maior que zero.")
    instante = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    expiracao = instante + timedelta(seconds=lease_seconds)
    recuperavel = or_(
        DocumentProcessingTask.status == DocumentProcessingTaskStatus.PENDENTE.value,
        and_(
            DocumentProcessingTask.status
            == DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
            DocumentProcessingTask.lease_expires_at.is_not(None),
            DocumentProcessingTask.lease_expires_at <= instante,
        ),
    )
    candidata = db.scalar(
        select(DocumentProcessingTask)
        .where(recuperavel)
        .order_by(DocumentProcessingTask.id.asc())
        .limit(1)
    )
    if candidata is None:
        return None

    token = str(uuid.uuid4())
    reservado = db.execute(
        update(DocumentProcessingTask)
        .where(
            DocumentProcessingTask.id == candidata.id,
            recuperavel,
        )
        .values(
            status=DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
            tentativas=DocumentProcessingTask.tentativas + 1,
            erro=None,
            error_code=None,
            lease_owner=worker_id.strip()[:200] or "document-worker",
            lease_token=token,
            lease_expires_at=expiracao,
            iniciado_em=instante,
            concluido_em=None,
        )
    )
    if reservado.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(DocumentProcessingTask, candidata.id)


def renovar_lease_tarefa_processamento(
    db: Session,
    tarefa_id: int,
    lease_token: str,
    *,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> DocumentProcessingTask:
    """Renova somente o lease que ainda pertence ao worker solicitante."""

    if lease_seconds <= 0:
        raise ValueError("lease_seconds deve ser maior que zero.")
    instante = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    renovado = db.execute(
        update(DocumentProcessingTask)
        .where(
            DocumentProcessingTask.id == tarefa_id,
            DocumentProcessingTask.status
            == DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
            DocumentProcessingTask.lease_token == lease_token,
            DocumentProcessingTask.lease_expires_at > instante,
        )
        .values(lease_expires_at=instante + timedelta(seconds=lease_seconds))
    )
    if renovado.rowcount != 1:
        db.rollback()
        raise ValueError("Lease ausente, expirado ou pertencente a outro worker.")
    db.commit()
    return db.get(DocumentProcessingTask, tarefa_id)


def concluir_tarefa_processamento(
    db: Session,
    tarefa_id: int,
    lease_token: str,
    *,
    now: datetime | None = None,
) -> DocumentProcessingTask:
    """Marca como concluída somente uma tarefa que estava em execução."""

    instante = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    concluida = db.execute(
        update(DocumentProcessingTask)
        .where(
            DocumentProcessingTask.id == tarefa_id,
            DocumentProcessingTask.status
            == DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
            DocumentProcessingTask.lease_token == lease_token,
            DocumentProcessingTask.lease_expires_at > instante,
        )
        .values(
            status=DocumentProcessingTaskStatus.CONCLUIDO.value,
            erro=None,
            error_code=None,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            concluido_em=instante,
        )
    )
    if concluida.rowcount != 1:
        db.rollback()
        raise ValueError("A tarefa perdeu o lease e não pode ser concluída.")
    db.commit()
    return db.get(DocumentProcessingTask, tarefa_id)


def falhar_tarefa_processamento(
    db: Session,
    tarefa_id: int,
    error_code: str,
    lease_token: str | None = None,
) -> DocumentProcessingTask:
    """Registra somente um código seguro, nunca a mensagem bruta da exceção."""

    codigo = error_code.strip().casefold()
    if not codigo or len(codigo) > 100 or any(
        caractere not in "abcdefghijklmnopqrstuvwxyz0123456789_" for caractere in codigo
    ):
        codigo = "processing_failed"
    instante = datetime.now(timezone.utc).replace(tzinfo=None)
    criterios = [DocumentProcessingTask.id == tarefa_id]
    if lease_token is not None:
        criterios.extend(
            [
                DocumentProcessingTask.status
                == DocumentProcessingTaskStatus.EM_PROCESSAMENTO.value,
                DocumentProcessingTask.lease_token == lease_token,
                DocumentProcessingTask.lease_expires_at > instante,
            ]
        )
    falhou = db.execute(
        update(DocumentProcessingTask)
        .where(*criterios)
        .values(
            status=DocumentProcessingTaskStatus.ERRO.value,
            erro=None,
            error_code=codigo,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            concluido_em=instante,
        )
    )
    if falhou.rowcount != 1:
        db.rollback()
        raise ValueError("A tarefa perdeu o lease e não pode ser alterada.")
    db.commit()
    return db.get(DocumentProcessingTask, tarefa_id)


def persistir_chunks_jsonl_normativos(
    db: Session,
    corpus_version_id: int,
    arquivo_chunks: Path,
    urls_por_arquivo: dict[str, str] | None = None,
    tamanho_commit: int = 500,
) -> dict[str, int]:
    """Persiste o JSONL da base normativa no SQLite, sem FAISS/embeddings."""

    if not arquivo_chunks.is_file():
        raise FileNotFoundError(f"Arquivo de chunks não encontrado: {arquivo_chunks}")
    urls_por_arquivo = urls_por_arquivo or {}
    resumo = {
        "linhasLidas": 0,
        "linhasInvalidas": 0,
        "documentosCriados": 0,
        "documentosReutilizados": 0,
        "chunksCriados": 0,
        "chunksReutilizados": 0,
    }
    chunk_ids_vistos = set(db.scalars(select(DocumentChunk.chunk_id)).all())
    documentos_cache: dict[str, Document] = {}
    pendentes_commit = 0

    with arquivo_chunks.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            if not linha.strip():
                continue
            try:
                item = json.loads(linha)
            except json.JSONDecodeError:
                resumo["linhasInvalidas"] += 1
                continue
            if not isinstance(item, dict):
                resumo["linhasInvalidas"] += 1
                continue
            conteudo = item.get("texto")
            metadata = item.get("metadata")
            if not isinstance(conteudo, str) or not isinstance(metadata, dict):
                resumo["linhasInvalidas"] += 1
                continue
            chunk_id = metadata.get("chunk_id")
            source_file = metadata.get("source_file")
            if not isinstance(chunk_id, str) or not chunk_id or not isinstance(source_file, str):
                resumo["linhasInvalidas"] += 1
                continue
            resumo["linhasLidas"] += 1
            metadata = dict(metadata)
            metadata["source_url"] = urls_por_arquivo.get(source_file)
            chave_documento = _document_source_file(metadata)
            documento = documentos_cache.get(chave_documento)
            if documento is None:
                documento, criado = _obter_ou_criar_documento(
                    db, corpus_version_id, metadata
                )
                documentos_cache[chave_documento] = documento
                resumo["documentosCriados" if criado else "documentosReutilizados"] += 1
            if chunk_id in chunk_ids_vistos:
                resumo["chunksReutilizados"] += 1
                continue
            db.add(_criar_chunk(documento, chunk_id, conteudo, metadata))
            chunk_ids_vistos.add(chunk_id)
            resumo["chunksCriados"] += 1
            pendentes_commit += 1
            if pendentes_commit >= tamanho_commit:
                db.commit()
                pendentes_commit = 0
    db.commit()
    return resumo


def persistir_textos_ativos_pncp(
    db: Session,
    corpus_version_id: int,
    manifestos: dict[str, list[dict[str, Any]]],
    bases: dict[str, list[dict[str, Any]]],
    limite_textos: int | None = None,
    usar_langchain: bool = True,
    raiz_projeto: Path | None = None,
) -> dict[str, int]:
    """Persiste Document/DocumentChunk a partir de textoAtivo dos manifestos PNCP."""

    raiz = raiz_projeto or Path.cwd()
    indices = {
        origem: _indice_por_controle(registros)
        for origem, registros in bases.items()
    }
    resumo = {
        "textosLidos": 0,
        "documentosCriados": 0,
        "documentosReutilizados": 0,
        "chunksCriados": 0,
        "chunksReutilizados": 0,
        "textosSemArquivo": 0,
        "textosSemRegistroEstruturado": 0,
    }
    chunk_ids_vistos = set(db.scalars(select(DocumentChunk.chunk_id)).all())

    for origem, manifesto in manifestos.items():
        tipo_entidade = "contrato" if origem == "contratos" else "contratacao"
        for entidade_manifesto in manifesto:
            if not isinstance(entidade_manifesto, dict):
                continue

            controle = str(entidade_manifesto.get("numeroControlePNCP") or "")
            entidade = indices.get(origem, {}).get(controle)
            if entidade is None:
                resumo["textosSemRegistroEstruturado"] += 1
                continue

            for documento_pncp in entidade_manifesto.get("documentos") or []:
                if not isinstance(documento_pncp, dict):
                    continue

                documento_db: Document | None = None
                for registro_texto in documento_pncp.get("processamentoTexto") or []:
                    ativo = registro_texto.get("textoAtivo") or {}
                    caminho_texto = Path(str(ativo.get("caminho") or ""))
                    if not caminho_texto.is_file():
                        if ativo:
                            resumo["textosSemArquivo"] += 1
                        continue
                    if limite_textos is not None and resumo["textosLidos"] >= limite_textos:
                        db.commit()
                        return resumo

                    texto = caminho_texto.read_text(encoding="utf-8", errors="replace")
                    configuracao = selecionar_configuracao(
                        documento_pncp.get("tipoDocumentoNome"),
                        caminho_texto,
                    )
                    metadata = montar_metadata_base(
                        entidade=entidade,
                        documento=documento_pncp,
                        registro_texto=registro_texto,
                        tipo_entidade=tipo_entidade,
                        caminho_relativo=caminho_relativo_portavel(caminho_texto, raiz),
                    )

                    if documento_db is None:
                        documento_db, criado = _obter_ou_criar_documento(
                            db,
                            corpus_version_id,
                            metadata,
                        )
                        if criado:
                            resumo["documentosCriados"] += 1
                        else:
                            resumo["documentosReutilizados"] += 1

                    for indice, conteudo in enumerate(
                        gerar_chunks(texto, configuracao, usar_langchain=usar_langchain)
                    ):
                        chunk_id = gerar_chunk_id(metadata, indice, conteudo)
                        if chunk_id in chunk_ids_vistos:
                            resumo["chunksReutilizados"] += 1
                            continue

                        db.add(_criar_chunk(documento_db, chunk_id, conteudo, metadata))
                        chunk_ids_vistos.add(chunk_id)
                        resumo["chunksCriados"] += 1

                    resumo["textosLidos"] += 1

    db.commit()
    return resumo


def persistir_chunks_jsonl_pncp(
    db: Session,
    corpus_version_id: int,
    arquivo_chunks: Path,
    tipos_entidade: set[str] | None = None,
    limite_chunks_novos: int | None = None,
    tamanho_commit: int = 500,
    progresso: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Persiste o JSONL canônico de chunks sem extrair ou dividir texto outra vez.

    O arquivo JSONL é a fronteira entre o processamento documental e o banco.
    A idempotência é baseada no ``chunk_id``: uma nova execução percorre o
    arquivo novamente, ignora os chunks existentes e continua nos pendentes.
    """

    if not arquivo_chunks.is_file():
        raise FileNotFoundError(f"Arquivo de chunks não encontrado: {arquivo_chunks}")
    if tamanho_commit <= 0:
        raise ValueError("tamanho_commit deve ser maior que zero.")

    # O JSONL é percorrido duas vezes: primeiro para informar o total na barra
    # e depois para persistir. A segunda passagem continua sendo a fonte de
    # verdade; não carregamos todos os chunks em memória.
    with arquivo_chunks.open(encoding="utf-8") as arquivo:
        total_linhas = sum(1 for linha in arquivo if linha.strip())
    if progresso is not None:
        progresso(0, total_linhas)

    resumo = {
        "linhasLidas": 0,
        "linhasInvalidas": 0,
        "linhasIgnoradasTipo": 0,
        "documentosCriados": 0,
        "documentosReutilizados": 0,
        "chunksCriados": 0,
        "chunksReutilizados": 0,
    }
    chunk_ids_vistos = set(db.scalars(select(DocumentChunk.chunk_id)).all())
    documentos_cache: dict[str, Document] = {}
    novos_desde_commit = 0
    linhas_processadas = 0

    with arquivo_chunks.open(encoding="utf-8") as arquivo:
        for numero_linha, linha in enumerate(arquivo, start=1):
            if not linha.strip():
                continue
            linhas_processadas += 1
            try:
                item = json.loads(linha)
            except json.JSONDecodeError:
                resumo["linhasInvalidas"] += 1
                if progresso is not None:
                    progresso(linhas_processadas, total_linhas)
                continue

            if not isinstance(item, dict):
                resumo["linhasInvalidas"] += 1
                if progresso is not None:
                    progresso(linhas_processadas, total_linhas)
                continue
            chunk_id = item.get("chunk_id")
            conteudo = item.get("content")
            metadata = item.get("metadata")
            if (
                not isinstance(chunk_id, str)
                or not chunk_id
                or not isinstance(conteudo, str)
                or not isinstance(metadata, dict)
            ):
                resumo["linhasInvalidas"] += 1
                if progresso is not None:
                    progresso(linhas_processadas, total_linhas)
                continue

            resumo["linhasLidas"] += 1
            tipo_entidade = str(metadata.get("tipoEntidade") or "")
            if tipos_entidade is not None and tipo_entidade not in tipos_entidade:
                resumo["linhasIgnoradasTipo"] += 1
                if progresso is not None:
                    progresso(linhas_processadas, total_linhas)
                continue
            if (
                limite_chunks_novos is not None
                and resumo["chunksCriados"] >= limite_chunks_novos
            ):
                break

            chave_documento = _document_source_file(metadata)
            documento = documentos_cache.get(chave_documento)
            if documento is None:
                documento, criado = _obter_ou_criar_documento(
                    db,
                    corpus_version_id,
                    metadata,
                )
                documentos_cache[chave_documento] = documento
                if criado:
                    resumo["documentosCriados"] += 1
                else:
                    resumo["documentosReutilizados"] += 1

            if chunk_id in chunk_ids_vistos:
                resumo["chunksReutilizados"] += 1
                if progresso is not None:
                    progresso(linhas_processadas, total_linhas)
                continue

            db.add(_criar_chunk(documento, chunk_id, conteudo, metadata))
            chunk_ids_vistos.add(chunk_id)
            resumo["chunksCriados"] += 1
            novos_desde_commit += 1
            if novos_desde_commit >= tamanho_commit:
                db.commit()
                novos_desde_commit = 0

            if progresso is not None:
                progresso(linhas_processadas, total_linhas)

    db.commit()
    return resumo
