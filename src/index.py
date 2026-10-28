"""Índice FAISS incremental, com checkpoints imutáveis e leitor legado."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

import faiss
import numpy as np

from src.embedding_identity import identidades_embedding_compativeis

from src.faiss_errors import (
    FAISS_UNAVAILABLE_ERRORS,
    FaissIndexCorruptError,
    FaissIndexIncompatibleError,
    FaissIndexMissingError,
)


EmbeddingFn = Callable[[str], list[float]]
ProgressFn = Callable[[int, int], None]
RecoveryFn = Callable[[int], None]
IndexingStage = Literal["selecao", "geracao", "validacao", "persistencia"]


@dataclass(frozen=True)
class ProgressoIndexacaoFaiss:
    estagio: IndexingStage
    chunks_selecionados: int
    embeddings_gerados: int
    vetores_persistidos: int
    total_esperado: int | None


DEFAULT_CHECKPOINT_SIZE = 500
logger = logging.getLogger(__name__)

ARQUIVO_INDICE = "chunks.faiss"
ARQUIVO_IDS = "chunk_ids.json"
ARQUIVO_ESTADO = "estado_indexacao.json"
ESQUEMA_INDICE = "faiss_document_chunks_v1"
ESQUEMA_CHECKPOINTS = "faiss_document_chunks_checkpoints_v1"


def _erro_ausente_ou_incompleto(cache_dir: Path) -> Exception:
    """Distingue diretório ausente de um conjunto parcial/corrompido."""

    artefatos = (
        cache_dir / ARQUIVO_INDICE,
        cache_dir / ARQUIVO_IDS,
        cache_dir / ARQUIVO_ESTADO,
        cache_dir / "base",
        cache_dir / "checkpoints",
    )
    if any(artefato.exists() for artefato in artefatos):
        return FaissIndexCorruptError()
    return FaissIndexMissingError()


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _hash_conteudo(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def _vetores_finitos(index: faiss.Index) -> bool:
    for inicio in range(0, int(index.ntotal), 8192):
        vetores = index.reconstruct_n(inicio, min(8192, int(index.ntotal) - inicio))
        if not np.isfinite(np.asarray(vetores)).all():
            return False
    return True


def _adicionar_vetores(destino: faiss.Index, origem: faiss.Index) -> None:
    for inicio in range(0, int(origem.ntotal), 8192):
        vetores = origem.reconstruct_n(
            inicio,
            min(8192, int(origem.ntotal) - inicio),
        )
        destino.add(np.asarray(vetores, dtype="float32"))


def _salvar_json_atomico(caminho: Path, valor: object) -> None:
    temporario = caminho.with_name(f".{caminho.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
            json.dump(valor, arquivo, ensure_ascii=False, indent=2)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
    finally:
        temporario.unlink(missing_ok=True)


def _normalizar(vetores: np.ndarray) -> np.ndarray:
    if vetores.ndim != 2 or not np.isfinite(vetores).all():
        raise ValueError("Embeddings devem ser uma matriz 2D de valores finitos.")
    normas = np.linalg.norm(vetores, axis=1, keepdims=True)
    if np.any(normas == 0):
        raise ValueError("Embeddings com norma zero não são válidos.")
    return vetores / normas


@contextmanager
def _lock_escrita(diretorio: Path) -> Iterator[None]:
    """Lock exclusivo do SO; o lock é liberado automaticamente após crash."""

    diretorio.mkdir(parents=True, exist_ok=True)
    caminho = diretorio / ".writer.lock"
    arquivo = caminho.open("a+b")
    adquirido = False
    try:
        arquivo.seek(0)
        if os.name == "nt":
            import msvcrt

            if caminho.stat().st_size == 0:
                arquivo.write(b"\0")
                arquivo.flush()
            arquivo.seek(0)
            try:
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"Já existe um escritor para {diretorio}.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(f"Já existe um escritor para {diretorio}.") from exc
        adquirido = True
        arquivo.seek(0)
        arquivo.truncate()
        arquivo.write(f"pid={os.getpid()} started={_agora_utc()}\n".encode())
        arquivo.flush()
        yield
    finally:
        try:
            if adquirido:
                if os.name == "nt":
                    import msvcrt

                    arquivo.seek(0)
                    msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        arquivo.close()


@dataclass(frozen=True)
class FaissChunkIndex:
    index: faiss.Index
    chunk_ids: list[str]
    content_hashes: dict[str, str]

    def search(self, query_embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        if not self.chunk_ids or top_k <= 0:
            return []
        consulta = _normalizar(np.asarray([query_embedding], dtype="float32"))
        if consulta.shape[1] != self.index.d:
            raise FaissIndexIncompatibleError()
        scores, indices = self.index.search(consulta, min(top_k, len(self.chunk_ids)))
        return [
            (self.chunk_ids[int(posicao)], float(score))
            for posicao, score in zip(indices[0], scores[0])
            if posicao >= 0
        ]


@dataclass(frozen=True)
class ResumoIndexacaoFaiss:
    selecionados: int
    ja_indexados: int
    novos: int
    total_no_indice: int
    diretorio_indice: str
    checkpoints: int = 0
    checkpoints_recuperados: int = 0
    embeddings_gerados: int = 0


@dataclass(frozen=True)
class AuditoriaIndiceFaiss:
    chunk_ids: list[str]
    hashes_conteudo: dict[str, str]
    total_vetores: int
    modelo_embedding: str | None
    dimensao: int | None
    estado: dict[str, object]
    inconsistencias: list[str]


def _caminhos_legados(cache_dir: Path) -> tuple[Path, Path, Path]:
    return (
        cache_dir / ARQUIVO_INDICE,
        cache_dir / ARQUIVO_IDS,
        cache_dir / ARQUIVO_ESTADO,
    )


def _ler_json(caminho: Path) -> object:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _ler_json_artefato(caminho: Path) -> object:
    """Classifica somente falhas da fronteira de leitura do artefato."""

    try:
        return _ler_json(caminho)
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FaissIndexCorruptError() from exc


def _ler_faiss_artefato(caminho: Path) -> faiss.Index:
    """Converte falhas do parser nativo do FAISS em erro de domínio."""

    try:
        return faiss.read_index(str(caminho))
    except (OSError, RuntimeError) as exc:
        raise FaissIndexCorruptError() from exc


def _inteiro_artefato(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FaissIndexCorruptError() from exc


def _ler_indice_legado(cache_dir: Path, *, verificar: bool) -> AuditoriaIndiceFaiss:
    index_path, ids_path, state_path = _caminhos_legados(cache_dir)
    if not index_path.is_file() or not ids_path.is_file():
        raise _erro_ausente_ou_incompleto(cache_dir)
    estado: dict[str, object] = {}
    problemas: list[str] = []
    try:
        valor_estado = _ler_json(state_path)
        if isinstance(valor_estado, dict):
            estado = valor_estado
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        problemas.append(f"estado_indexacao.json ilegível: {exc}")

    try:
        ids_valor = _ler_json_artefato(ids_path)
    except FileNotFoundError as exc:
        raise _erro_ausente_ou_incompleto(cache_dir) from exc
    if not isinstance(ids_valor, list) or not all(isinstance(item, str) for item in ids_valor):
        raise FaissIndexCorruptError()
    chunk_ids = ids_valor
    index = _ler_faiss_artefato(index_path)
    total_vetores = int(index.ntotal)
    if not _vetores_finitos(index):
        problemas.append("O FAISS contém vetores não finitos.")
    if total_vetores != len(chunk_ids):
        problemas.append(
            f"FAISS tem {total_vetores} vetores e chunk_ids.json tem {len(chunk_ids)} IDs."
        )
    duplicados = len(chunk_ids) - len(set(chunk_ids))
    if duplicados:
        problemas.append(f"chunk_ids.json contém {duplicados} IDs duplicados.")
    if estado.get("totalVetores") is not None and estado["totalVetores"] != total_vetores:
        problemas.append("totalVetores do estado não corresponde ao FAISS.")
    if estado.get("dimensao") is not None and estado["dimensao"] != int(index.d):
        problemas.append("dimensao do estado não corresponde ao FAISS.")
    if verificar and problemas:
        raise RuntimeError("Índice FAISS inconsistente: " + " ".join(problemas))
    return AuditoriaIndiceFaiss(
        chunk_ids=chunk_ids,
        hashes_conteudo={},
        total_vetores=total_vetores,
        modelo_embedding=(
            str(estado["modeloEmbedding"])
            if estado.get("modeloEmbedding") is not None
            else None
        ),
        dimensao=int(index.d),
        estado=estado,
        inconsistencias=problemas,
    )


def _ler_indice_checkpoint(cache_dir: Path, *, verificar: bool) -> AuditoriaIndiceFaiss:
    estado_path = cache_dir / ARQUIVO_ESTADO
    try:
        estado_valor = _ler_json_artefato(estado_path)
    except FileNotFoundError as exc:
        raise _erro_ausente_ou_incompleto(cache_dir) from exc
    if not isinstance(estado_valor, dict) or estado_valor.get("formato") != ESQUEMA_CHECKPOINTS:
        raise FaissIndexCorruptError()
    estado = estado_valor
    problemas: list[str] = []
    ids_completos: list[str] = []
    hashes: dict[str, str] = {}
    faiss_total = 0
    dimensao = _inteiro_artefato(estado.get("dimensao", 0))

    base_nome = estado.get("base")
    if base_nome:
        base_dir = cache_dir / "base"
        manifesto_path = base_dir / "manifest.json"
        try:
            expected_manifest = estado.get("baseManifestSha256")
            if not expected_manifest:
                problemas.append("O estado não referencia o manifesto da base.")
            elif _sha256(manifesto_path) != expected_manifest:
                problemas.append("Checksum inválido no manifesto da base.")
            manifesto = _ler_json_artefato(manifesto_path)
            if not isinstance(manifesto, dict):
                raise FaissIndexCorruptError()
            for nome, digest in manifesto.get("arquivos", {}).items():
                caminho = base_dir / str(nome)
                if not caminho.is_file() or _sha256(caminho) != digest:
                    problemas.append(f"Checksum inválido no arquivo base {nome}.")
            base_index_path = base_dir / ARQUIVO_INDICE
            base_ids_path = base_dir / ARQUIVO_IDS
            base_index = _ler_faiss_artefato(base_index_path)
            base_ids = _ler_json_artefato(base_ids_path)
            if not isinstance(base_ids, list) or not all(isinstance(i, str) for i in base_ids):
                raise FaissIndexCorruptError()
            if base_index.ntotal != len(base_ids):
                problemas.append("Quantidade de vetores/IDs diverge na base do staging.")
            if not _vetores_finitos(base_index):
                problemas.append("A base do staging contém vetores não finitos.")
            if base_index.d != dimensao:
                problemas.append("Dimensão da base diverge do estado do staging.")
            ids_completos.extend(base_ids)
            faiss_total += int(base_index.ntotal)
            base_state = manifesto.get("estado", {})
            if isinstance(base_state, dict):
                legacy_model = base_state.get("modeloEmbedding")
                if legacy_model and legacy_model != estado.get("modeloEmbedding"):
                    problemas.append("Modelo da base diverge do modelo do staging.")
        except FAISS_UNAVAILABLE_ERRORS:
            raise
        except OSError as exc:
            problemas.append(f"Base de staging inválida: {exc}")

    checkpoints = estado.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        problemas.append("Lista de checkpoints inválida.")
        checkpoints = []
    nomes_referenciados = {
        str(item.get("nome"))
        for item in [
            *checkpoints,
            *(
                estado.get("checkpointsArquivados", [])
                if isinstance(estado.get("checkpointsArquivados", []), list)
                else []
            ),
        ]
        if isinstance(item, dict) and item.get("nome")
    }
    checkpoints_dir = cache_dir / "checkpoints"
    if checkpoints_dir.exists():
        orfaos_checkpoint = [
            caminho.name
            for caminho in checkpoints_dir.iterdir()
            if caminho.is_dir()
            and not caminho.name.startswith(".")
            and caminho.name not in nomes_referenciados
        ]
        if orfaos_checkpoint:
            problemas.append(
                f"{len(orfaos_checkpoint)} diretórios de checkpoint sem referência no estado."
            )
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            problemas.append("Entrada de checkpoint inválida.")
            continue
        nome = str(checkpoint.get("nome", ""))
        cp_dir = cache_dir / "checkpoints" / nome
        manifesto_path = cp_dir / "manifest.json"
        try:
            manifesto_bytes = manifesto_path.read_bytes()
            digest_manifesto = hashlib.sha256(manifesto_bytes).hexdigest()
            if digest_manifesto != checkpoint.get("manifestSha256"):
                problemas.append(f"Checksum inválido no manifesto do checkpoint {nome}.")
            manifesto = json.loads(manifesto_bytes)
            for filename, digest in manifesto.get("arquivos", {}).items():
                caminho = cp_dir / str(filename)
                if not caminho.is_file() or _sha256(caminho) != digest:
                    problemas.append(f"Checksum inválido em {nome}/{filename}.")
            parte = _ler_faiss_artefato(cp_dir / "chunks.faiss")
            ids = _ler_json_artefato(cp_dir / "chunk_ids.json")
            cp_hashes = _ler_json_artefato(cp_dir / "content_hashes.json")
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise FaissIndexCorruptError()
            if not isinstance(cp_hashes, dict):
                raise FaissIndexCorruptError()
            if set(cp_hashes) != set(ids):
                problemas.append(f"Hashes de conteúdo e IDs divergem em {nome}.")
            if any(
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in cp_hashes.values()
            ):
                problemas.append(f"Hash de conteúdo inválido em {nome}.")
            if parte.ntotal != len(ids):
                problemas.append(f"Quantidade de vetores/IDs diverge em {nome}.")
            if not _vetores_finitos(parte):
                problemas.append(f"O checkpoint {nome} contém vetores não finitos.")
            if parte.d != dimensao:
                problemas.append(f"Dimensão divergente em {nome}.")
            ids_completos.extend(ids)
            hashes.update({str(key): str(value) for key, value in cp_hashes.items()})
            faiss_total += int(parte.ntotal)
        except FAISS_UNAVAILABLE_ERRORS:
            raise
        except OSError as exc:
            problemas.append(f"Checkpoint {nome} inválido: {exc}")

    checkpoints_arquivados = estado.get("checkpointsArquivados", [])
    if not isinstance(checkpoints_arquivados, list):
        problemas.append("Lista de checkpoints arquivados inválida.")
        checkpoints_arquivados = []
    for checkpoint in checkpoints_arquivados:
        if not isinstance(checkpoint, dict):
            problemas.append("Entrada de checkpoint arquivado inválida.")
            continue
        nome = str(checkpoint.get("nome", ""))
        manifesto_path = cache_dir / "checkpoints" / nome / "manifest.json"
        try:
            if _sha256(manifesto_path) != checkpoint.get("manifestSha256"):
                problemas.append(f"Manifesto do checkpoint arquivado inválido: {nome}.")
        except OSError as exc:
            problemas.append(f"Checkpoint arquivado {nome} ausente: {exc}")

    if len(ids_completos) != len(set(ids_completos)):
        problemas.append(
            f"Índice contém {len(ids_completos) - len(set(ids_completos))} IDs duplicados."
        )
    if estado.get("totalVetores") != faiss_total:
        problemas.append("totalVetores do estado não corresponde aos checkpoints.")
    if faiss_total and not estado.get("modeloEmbedding"):
        problemas.append("O índice checkpoint não registra modelo de embedding.")
    if faiss_total and dimensao <= 0:
        problemas.append("O índice checkpoint não registra uma dimensão válida.")
    if verificar and problemas:
        raise RuntimeError("Índice FAISS inconsistente: " + " ".join(problemas))

    return AuditoriaIndiceFaiss(
        chunk_ids=ids_completos,
        hashes_conteudo=hashes,
        total_vetores=faiss_total,
        modelo_embedding=(
            str(estado["modeloEmbedding"])
            if estado.get("modeloEmbedding") is not None
            else None
        ),
        dimensao=dimensao or None,
        estado=estado,
        inconsistencias=problemas,
    )


def auditar_indice_faiss(cache_dir: Path) -> AuditoriaIndiceFaiss:
    """Inspeciona índice legado ou checkpoint sem alterar seus arquivos."""

    state_path = cache_dir / ARQUIVO_ESTADO
    if state_path.is_file():
        try:
            state = _ler_json(state_path)
        except (OSError, json.JSONDecodeError):
            state = None
        if isinstance(state, dict) and state.get("formato") == ESQUEMA_CHECKPOINTS:
            return _ler_indice_checkpoint(cache_dir, verificar=False)
    return _ler_indice_legado(cache_dir, verificar=False)


def validar_indice_faiss(
    cache_dir: Path,
    *,
    modelo_esperado: str,
) -> AuditoriaIndiceFaiss:
    """Valida somente por leitura existência, integridade, modelo e dimensão."""

    auditoria = auditar_indice_faiss(cache_dir)
    bloqueantes = [
        item
        for item in auditoria.inconsistencias
        if "diretórios de checkpoint sem referência" not in item
    ]
    if bloqueantes:
        raise FaissIndexCorruptError()
    if not identidades_embedding_compativeis(
        auditoria.modelo_embedding, modelo_esperado, auditoria.dimensao
    ):
        raise FaissIndexIncompatibleError()
    if auditoria.dimensao is None or auditoria.dimensao <= 0:
        raise FaissIndexCorruptError()
    return auditoria


def carregar_indice_faiss(
    cache_dir: Path,
    *,
    modelo_esperado: str | None = None,
) -> FaissChunkIndex:
    """Carrega índice gerenciado ou legado; valida checksums e IDs."""

    auditoria = auditar_indice_faiss(cache_dir)
    bloqueantes = [
        item
        for item in auditoria.inconsistencias
        if "diretórios de checkpoint sem referência" not in item
    ]
    if bloqueantes:
        raise FaissIndexCorruptError()
    if modelo_esperado is not None and not identidades_embedding_compativeis(
        auditoria.modelo_embedding, modelo_esperado, auditoria.dimensao
    ):
        raise FaissIndexIncompatibleError()
    if auditoria.dimensao is None or auditoria.dimensao <= 0:
        raise FaissIndexCorruptError()
    state = auditoria.estado
    if state.get("formato") == ESQUEMA_CHECKPOINTS:
        dimension = _inteiro_artefato(auditoria.dimensao or 0)
        index: faiss.Index = faiss.IndexFlatIP(dimension)
        base_name = state.get("base")
        if base_name:
            base_index = _ler_faiss_artefato(cache_dir / "base" / ARQUIVO_INDICE)
            _adicionar_vetores(index, base_index)
        for entry in state.get("checkpoints", []):
            cp_index = _ler_faiss_artefato(
                cache_dir / "checkpoints" / str(entry["nome"]) / "chunks.faiss"
            )
            _adicionar_vetores(index, cp_index)
    else:
        index = _ler_faiss_artefato(cache_dir / ARQUIVO_INDICE)
    return FaissChunkIndex(
        index=index,
        chunk_ids=auditoria.chunk_ids,
        content_hashes=auditoria.hashes_conteudo,
    )


def preparar_staging(
    indice_origem: Path,
    diretorio_staging: Path,
) -> None:
    """Copia artefatos legados para um staging novo, sem alterar a origem."""

    origem = indice_origem.resolve()
    destino = diretorio_staging.resolve()
    if origem == destino or origem in destino.parents:
        raise ValueError("O staging deve estar fora do diretório do índice de origem.")
    if destino.exists() and any(destino.iterdir()):
        raise FileExistsError(f"Diretório de staging não está vazio: {destino}")
    snapshot = auditar_indice_faiss(origem)
    if snapshot.inconsistencias:
        raise RuntimeError("Índice de origem inconsistente: " + " ".join(snapshot.inconsistencias))
    if not snapshot.modelo_embedding:
        raise RuntimeError(
            "O índice legado não registra modelo de embedding; não é seguro acrescentar vetores."
        )
    if not all((origem / nome).is_file() for nome in (ARQUIVO_INDICE, ARQUIVO_IDS)):
        raise ValueError("A preparação de staging requer os três artefatos de um índice legado.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.tmp")
    temporario.mkdir()
    try:
        base = temporario / "base"
        base.mkdir()
        for nome in (ARQUIVO_INDICE, ARQUIVO_IDS, ARQUIVO_ESTADO):
            arquivo = origem / nome
            if arquivo.is_file():
                shutil.copy2(arquivo, base / nome)
        manifest = {
            "arquivos": {
                nome: _sha256(base / nome)
                for nome in (ARQUIVO_INDICE, ARQUIVO_IDS, ARQUIVO_ESTADO)
                if (base / nome).is_file()
            },
            "estado": snapshot.estado,
        }
        _salvar_json_atomico(base / "manifest.json", manifest)
        state = {
            "formato": ESQUEMA_CHECKPOINTS,
            "modeloEmbedding": snapshot.modelo_embedding,
            "dimensao": snapshot.dimensao,
            "totalVetores": snapshot.total_vetores,
            "atualizadoEm": _agora_utc(),
            "origens": list(snapshot.estado.get("origens", [])),
            "base": "base",
            "baseManifestSha256": _sha256(base / "manifest.json"),
            "checkpoints": [],
        }
        _salvar_json_atomico(temporario / ARQUIVO_ESTADO, state)
        if destino.exists():
            destino.rmdir()
        os.replace(temporario, destino)
    except BaseException:
        shutil.rmtree(temporario, ignore_errors=True)
        raise


def _inicializar_vazio(
    cache_dir: Path,
    modelo_embedding: str,
    dimensao: int,
) -> dict[str, object]:
    estado: dict[str, object] = {
        "formato": ESQUEMA_CHECKPOINTS,
        "modeloEmbedding": modelo_embedding,
        "dimensao": dimensao,
        "totalVetores": 0,
        "atualizadoEm": _agora_utc(),
        "origens": [],
        "base": None,
        "checkpoints": [],
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    _salvar_json_atomico(cache_dir / ARQUIVO_ESTADO, estado)
    return estado


def _gravar_checkpoint(
    cache_dir: Path,
    estado: dict[str, object],
    chunk_ids: list[str],
    conteudos: list[str],
    vetores: np.ndarray,
    origem: str | None,
) -> dict[str, object]:
    sequence = len(estado["checkpoints"]) + 1
    nome = f"cp-{sequence:08d}-{uuid.uuid4().hex[:8]}"
    checkpoints_dir = cache_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    temporario = checkpoints_dir / f".{nome}.tmp"
    final = checkpoints_dir / nome
    temporario.mkdir()
    try:
        shard = faiss.IndexFlatIP(int(estado["dimensao"]))
        shard.add(vetores)
        faiss.write_index(shard, str(temporario / "chunks.faiss"))
        (temporario / "chunk_ids.json").write_text(
            json.dumps(chunk_ids, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        content_hashes = {
            chunk_id: _hash_conteudo(content)
            for chunk_id, content in zip(chunk_ids, conteudos)
        }
        (temporario / "content_hashes.json").write_text(
            json.dumps(content_hashes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files = ("chunks.faiss", "chunk_ids.json", "content_hashes.json")
        manifesto = {
            "arquivos": {filename: _sha256(temporario / filename) for filename in files},
            "total": len(chunk_ids),
        }
        manifesto_path = temporario / "manifest.json"
        manifesto_path.write_text(
            json.dumps(manifesto, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifesto_digest = _sha256(manifesto_path)
        os.replace(temporario, final)

        origens = list(estado.get("origens", []))
        if origem and origem not in origens:
            origens.append(origem)
        atualizados = {
            **estado,
            "totalVetores": int(estado["totalVetores"]) + len(chunk_ids),
            "atualizadoEm": _agora_utc(),
            "origens": origens,
            "checkpoints": [
                *estado["checkpoints"],
                {"nome": nome, "manifestSha256": manifesto_digest},
            ],
        }
        _salvar_json_atomico(cache_dir / ARQUIVO_ESTADO, atualizados)
        return atualizados
    except BaseException:
        shutil.rmtree(temporario, ignore_errors=True)
        raise


def _carregar_estado_escrita(cache_dir: Path) -> tuple[dict[str, object] | None, FaissChunkIndex | None]:
    state_path = cache_dir / ARQUIVO_ESTADO
    if not state_path.exists():
        legacy_index, legacy_ids, _ = _caminhos_legados(cache_dir)
        if legacy_index.exists() or legacy_ids.exists():
            raise RuntimeError(
                "Índice legado não pode ser atualizado diretamente. "
                "Prepare um diretório de staging com preparar_staging()."
            )
        return None, None
    state_value = _ler_json(state_path)
    if isinstance(state_value, dict) and state_value.get("formato") == ESQUEMA_CHECKPOINTS:
        return state_value, carregar_indice_faiss(cache_dir)
    raise RuntimeError(
        "Índice legado não pode ser atualizado diretamente. "
        "Prepare um diretório de staging com preparar_staging()."
    )


def _adotar_checkpoints_orfaos(cache_dir: Path) -> int:
    """Adota shards completos publicados antes de uma falha no estado atômico."""

    state_path = cache_dir / ARQUIVO_ESTADO
    if not state_path.is_file():
        return 0
    state = _ler_json(state_path)
    if not isinstance(state, dict) or state.get("formato") != ESQUEMA_CHECKPOINTS:
        return 0

    auditoria = auditar_indice_faiss(cache_dir)
    erros_nao_recuperaveis = [
        item
        for item in auditoria.inconsistencias
        if "diretórios de checkpoint sem referência" not in item
    ]
    if erros_nao_recuperaveis:
        raise RuntimeError(
            "Índice FAISS inconsistente antes da retomada: "
            + " ".join(erros_nao_recuperaveis)
        )

    checkpoints = state.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        raise RuntimeError("Lista de checkpoints inválida antes da retomada.")
    referenciados = {
        str(item.get("nome"))
        for item in [
            *checkpoints,
            *(
                state.get("checkpointsArquivados", [])
                if isinstance(state.get("checkpointsArquivados", []), list)
                else []
            ),
        ]
        if isinstance(item, dict) and item.get("nome")
    }
    diretorio = cache_dir / "checkpoints"
    orfaos = sorted(
        (
            path
            for path in diretorio.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name not in referenciados
        ),
        key=lambda path: path.name,
    ) if diretorio.exists() else []
    if not orfaos:
        return 0

    ids_conhecidos = set(auditoria.chunk_ids)
    novos_registros = []
    total_adotado = 0
    recuperacoes = 0
    for caminho in orfaos:
        manifest_path = caminho / "manifest.json"
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("arquivos"), dict):
                raise ValueError("manifesto inválido")
            for filename, digest in manifest["arquivos"].items():
                arquivo = caminho / str(filename)
                if not arquivo.is_file() or _sha256(arquivo) != digest:
                    raise ValueError(f"checksum inválido em {filename}")
            shard = faiss.read_index(str(caminho / ARQUIVO_INDICE))
            ids = _ler_json(caminho / ARQUIVO_IDS)
            hashes = _ler_json(caminho / "content_hashes.json")
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                raise ValueError("IDs inválidos")
            if (
                not isinstance(hashes, dict)
                or set(hashes) != set(ids)
                or shard.ntotal != len(ids)
                or shard.d != int(state.get("dimensao", 0))
                or not _vetores_finitos(shard)
            ):
                raise ValueError("vetores, IDs, hashes ou dimensão inconsistentes")
            if manifest.get("total") != len(ids):
                raise ValueError("total do manifesto diverge da quantidade de IDs")
            if len(ids) != len(set(ids)):
                raise ValueError("IDs duplicados no checkpoint")
            if ids_conhecidos.intersection(ids):
                if manifest.get("tipo") != "compactacao":
                    raise ValueError(
                        "IDs duplicados em checkpoints publicados e não referenciados"
                    )
                ids_ativos: list[str] = []
                hashes_ativos: dict[str, str] = {}
                vetores_ativos = faiss.IndexFlatIP(int(state["dimensao"]))
                for entry in checkpoints:
                    ativo_dir = diretorio / str(entry["nome"])
                    ids_ativos.extend(_ler_json(ativo_dir / ARQUIVO_IDS))
                    hashes_ativos.update(_ler_json(ativo_dir / "content_hashes.json"))
                    _adicionar_vetores(
                        vetores_ativos,
                        faiss.read_index(str(ativo_dir / ARQUIVO_INDICE)),
                    )
                vetores_orfaos = shard.reconstruct_n(0, int(shard.ntotal))
                if (
                    ids != ids_ativos
                    or hashes != hashes_ativos
                    or not np.array_equal(
                        vetores_orfaos,
                        vetores_ativos.reconstruct_n(0, int(vetores_ativos.ntotal)),
                    )
                ):
                    raise ValueError(
                        "Checkpoint de compactação órfão não corresponde aos shards ativos"
                    )
                shutil.rmtree(caminho)
                recuperacoes += 1
                continue
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Checkpoint órfão {caminho.name} não pode ser recuperado: {exc}"
            ) from exc

        ids_conhecidos.update(ids)
        total_adotado += len(ids)
        recuperacoes += 1
        novos_registros.append(
            {"nome": caminho.name, "manifestSha256": manifest_digest}
        )

    if not novos_registros:
        return recuperacoes
    atualizado = {
        **state,
        "totalVetores": int(state.get("totalVetores", 0)) + total_adotado,
        "checkpoints": [*checkpoints, *novos_registros],
        "atualizadoEm": _agora_utc(),
    }
    _salvar_json_atomico(state_path, atualizado)
    return recuperacoes


def indexar_chunks_incrementalmente(
    chunks: Iterable[tuple[str, str]],
    gerar_embedding: EmbeddingFn,
    cache_dir: Path,
    *,
    tamanho_lote: int = 50,
    tamanho_checkpoint: int = DEFAULT_CHECKPOINT_SIZE,
    modelo_embedding: str,
    dimensao_embedding: int | None = None,
    origem: str | None = None,
    progresso: ProgressFn | None = None,
    progresso_detalhado: Callable[[ProgressoIndexacaoFaiss], None] | None = None,
    recuperacao: RecoveryFn | None = None,
    total_esperado: int | None = None,
) -> tuple[FaissChunkIndex, ResumoIndexacaoFaiss]:
    """Acrescenta IDs ausentes em checkpoints; índices legados exigem staging."""

    if tamanho_lote <= 0:
        raise ValueError("tamanho_lote deve ser maior que zero.")
    if tamanho_checkpoint <= 0:
        raise ValueError("tamanho_checkpoint deve ser maior que zero.")
    if not modelo_embedding:
        raise ValueError("modelo_embedding deve ser informado explicitamente.")
    with _lock_escrita(cache_dir):
        checkpoints_recuperados = _adotar_checkpoints_orfaos(cache_dir)
        if recuperacao is not None and checkpoints_recuperados:
            recuperacao(checkpoints_recuperados)
        estado, indice = _carregar_estado_escrita(cache_dir)
        if estado is not None:
            if not identidades_embedding_compativeis(
                estado.get("modeloEmbedding"), modelo_embedding, indice.index.d
            ):
                raise RuntimeError(
                    "O modelo de embedding difere do índice existente "
                    f"({estado.get('modeloEmbedding')!r} != {modelo_embedding!r})."
                )
            if (
                dimensao_embedding is not None
                and estado.get("dimensao") != dimensao_embedding
            ):
                raise RuntimeError(
                    "A dimensão configurada difere do índice existente "
                    f"({estado.get('dimensao')!r} != {dimensao_embedding!r})."
                )
        existentes = set(indice.chunk_ids) if indice else set()
        hashes_existentes = indice.content_hashes if indice else {}
        vistos: dict[str, str] = {}
        selecionados = 0
        ja_indexados = 0
        novos = 0
        embeddings_gerados = 0
        pendentes_checkpoint: list[tuple[str, str]] = []
        ultimo_estado = estado

        def notificar(estagio: IndexingStage) -> None:
            if progresso_detalhado is not None:
                progresso_detalhado(
                    ProgressoIndexacaoFaiss(
                        estagio=estagio,
                        chunks_selecionados=selecionados,
                        embeddings_gerados=embeddings_gerados,
                        vetores_persistidos=novos,
                        total_esperado=total_esperado,
                    )
                )

        def persistir_checkpoint(lote: list[tuple[str, str]]) -> None:
            nonlocal indice, ultimo_estado, novos, embeddings_gerados
            textos = [conteudo for _, conteudo in lote]
            lotes_vetores = []
            for inicio in range(0, len(textos), tamanho_lote):
                lote_textos = textos[inicio : inicio + tamanho_lote]
                embeddings_lote = []
                try:
                    for texto in lote_textos:
                        embeddings_lote.append(gerar_embedding(texto))
                        embeddings_gerados += 1
                except BaseException:
                    notificar("geracao")
                    raise
                lote_raw = np.asarray(embeddings_lote, dtype="float32")
                notificar("geracao")
                notificar("validacao")
                if (
                    lote_raw.ndim != 2
                    or lote_raw.shape[0] != len(lote_textos)
                    or lote_raw.shape[1] == 0
                ):
                    raise ValueError("O gerador retornou embeddings com formato inválido.")
                if dimensao_embedding is not None and lote_raw.shape[1] != dimensao_embedding:
                    raise ValueError(
                        "A dimensão retornada pelo gerador difere da configuração "
                        f"({lote_raw.shape[1]} != {dimensao_embedding})."
                    )
                lotes_vetores.append(_normalizar(lote_raw))
            raw = np.concatenate(lotes_vetores, axis=0)
            notificar("validacao")
            if raw.ndim != 2 or raw.shape[0] != len(lote) or raw.shape[1] == 0:
                raise ValueError("O gerador retornou embeddings com formato inválido.")
            vetores = raw
            if indice is not None and vetores.shape[1] != indice.index.d:
                raise RuntimeError(
                    "A dimensão dos embeddings novos difere da dimensão do índice. "
                    "Use um novo staging e uma migração explícita de modelo."
                )
            if ultimo_estado is None:
                ultimo_estado = _inicializar_vazio(cache_dir, modelo_embedding, vetores.shape[1])
            elif int(ultimo_estado["dimensao"]) != vetores.shape[1]:
                raise RuntimeError("Dimensão de embedding incompatível com o estado.")
            notificar("persistencia")
            lote_ids = [chunk_id for chunk_id, _ in lote]
            try:
                ultimo_estado = _gravar_checkpoint(
                    cache_dir,
                    ultimo_estado,
                    lote_ids,
                    textos,
                    vetores,
                    origem,
                )
            except KeyboardInterrupt:
                try:
                    estado_publicado = _ler_json(cache_dir / ARQUIVO_ESTADO)
                    ids_publicados: set[str] = set()
                    if (
                        isinstance(estado_publicado, dict)
                        and estado_publicado.get("formato") == ESQUEMA_CHECKPOINTS
                    ):
                        for checkpoint in estado_publicado.get("checkpoints", []):
                            ids_publicados.update(
                                _ler_json(
                                    cache_dir
                                    / "checkpoints"
                                    / str(checkpoint["nome"])
                                    / ARQUIVO_IDS
                                )
                            )
                    if set(lote_ids).issubset(ids_publicados):
                        ultimo_estado = estado_publicado
                        novos += len(lote)
                        existentes.update(lote_ids)
                        notificar("persistencia")
                except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                    logger.warning(
                        "Could not reconcile checkpoint status after interruption "
                        "(error_type=%s); recovery will validate the published state",
                        type(exc).__name__,
                    )
                raise
            novos += len(lote)
            notificar("persistencia")
            if indice is None:
                indice = FaissChunkIndex(
                    index=faiss.IndexFlatIP(vetores.shape[1]),
                    chunk_ids=[],
                    content_hashes={},
                )
            indice.index.add(vetores)
            indice.chunk_ids.extend(lote_ids)
            indice.content_hashes.update(
                {chunk_id: _hash_conteudo(texto) for chunk_id, texto in lote}
            )
            existentes.update(lote_ids)
            if progresso is not None:
                progresso(novos, total_esperado or novos)

        notificar("selecao")
        for chunk_id, conteudo in chunks:
            if not isinstance(chunk_id, str) or not chunk_id or not isinstance(conteudo, str):
                raise ValueError("Cada chunk deve ter chunk_id e conteúdo textual válidos.")
            digest = _hash_conteudo(conteudo)
            if chunk_id in vistos:
                if vistos[chunk_id] != digest:
                    raise ValueError(f"chunk_id duplicado com conteúdo divergente: {chunk_id}")
                continue
            vistos[chunk_id] = digest
            selecionados += 1
            if selecionados == 1 or selecionados % tamanho_lote == 0:
                notificar("selecao")
            if chunk_id in existentes:
                ja_indexados += 1
                digest_existente = hashes_existentes.get(chunk_id)
                if digest_existente is not None and digest_existente != digest:
                    raise RuntimeError(
                        f"O conteúdo de {chunk_id} mudou após a indexação. "
                        "Reindexação requer política explícita."
                    )
                continue
            pendentes_checkpoint.append((chunk_id, conteudo))
            if len(pendentes_checkpoint) >= tamanho_checkpoint:
                persistir_checkpoint(pendentes_checkpoint)
                pendentes_checkpoint = []
        if pendentes_checkpoint:
            persistir_checkpoint(pendentes_checkpoint)

        if indice is None:
            if selecionados == 0:
                raise ValueError("Não há chunks válidos para indexar.")
            raise RuntimeError("Estado do índice não foi inicializado.")
        if estado is not None and not novos:
            _validar_estado_para_leitura(cache_dir)
        return indice, ResumoIndexacaoFaiss(
            selecionados=selecionados,
            ja_indexados=ja_indexados,
            novos=novos,
            total_no_indice=len(indice.chunk_ids),
            diretorio_indice=str(cache_dir),
            checkpoints=len((ultimo_estado or {}).get("checkpoints", [])),
            checkpoints_recuperados=checkpoints_recuperados,
            embeddings_gerados=embeddings_gerados,
        )


def _validar_estado_para_leitura(cache_dir: Path) -> None:
    carregar_indice_faiss(cache_dir)


def compactar_checkpoints(cache_dir: Path) -> int:
    """Consolida shards ativos sem substituir a base nem apagar dados de rollback."""

    with _lock_escrita(cache_dir):
        _adotar_checkpoints_orfaos(cache_dir)
        state, indice = _carregar_estado_escrita(cache_dir)
        if state is None or indice is None:
            raise RuntimeError("Compactação requer um índice checkpoint preparado.")
        checkpoints = state.get("checkpoints", [])
        if not isinstance(checkpoints, list):
            raise RuntimeError("Lista de checkpoints inválida para compactação.")
        if len(checkpoints) <= 1:
            return 0

        dimension = int(state["dimensao"])
        consolidado = faiss.IndexFlatIP(dimension)
        ids: list[str] = []
        hashes: dict[str, str] = {}
        for entry in checkpoints:
            checkpoint_dir = cache_dir / "checkpoints" / str(entry["nome"])
            shard = faiss.read_index(str(checkpoint_dir / ARQUIVO_INDICE))
            _adicionar_vetores(consolidado, shard)
            chunk_ids = _ler_json(checkpoint_dir / ARQUIVO_IDS)
            content_hashes = _ler_json(checkpoint_dir / "content_hashes.json")
            ids.extend(chunk_ids)
            hashes.update(content_hashes)

        checkpoints_dir = cache_dir / "checkpoints"
        nome = f"compact-{uuid.uuid4().hex}"
        temporario = checkpoints_dir / f".{nome}.tmp"
        final = checkpoints_dir / nome
        temporario.mkdir(parents=True, exist_ok=False)
        arquivo_estado_anterior = (
            cache_dir / "archive" / "states" / f"estado-{uuid.uuid4().hex}.json"
        )
        try:
            faiss.write_index(consolidado, str(temporario / ARQUIVO_INDICE))
            (temporario / ARQUIVO_IDS).write_text(
                json.dumps(ids, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (temporario / "content_hashes.json").write_text(
                json.dumps(hashes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            filenames = (ARQUIVO_INDICE, ARQUIVO_IDS, "content_hashes.json")
            manifesto = {
                "arquivos": {
                    filename: _sha256(temporario / filename)
                    for filename in filenames
                },
                "total": len(ids),
                "tipo": "compactacao",
            }
            manifesto_path = temporario / "manifest.json"
            manifesto_path.write_text(
                json.dumps(manifesto, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            manifest_digest = _sha256(manifesto_path)
            os.replace(temporario, final)

            arquivo_estado_anterior.parent.mkdir(parents=True, exist_ok=True)
            _salvar_json_atomico(arquivo_estado_anterior, state)
            antigos_arquivados = state.get("checkpointsArquivados", [])
            snapshots_anteriores = state.get("snapshotsEstado", [])
            if not isinstance(antigos_arquivados, list) or not isinstance(
                snapshots_anteriores, list
            ):
                raise RuntimeError("Metadados de compactação anteriores são inválidos.")
            atualizado = {
                **state,
                "checkpoints": [
                    {"nome": nome, "manifestSha256": manifest_digest}
                ],
                "checkpointsArquivados": [
                    *antigos_arquivados,
                    *checkpoints,
                ],
                "snapshotsEstado": [
                    *snapshots_anteriores,
                    str(arquivo_estado_anterior.relative_to(cache_dir)),
                ],
                "atualizadoEm": _agora_utc(),
            }
            _salvar_json_atomico(cache_dir / ARQUIVO_ESTADO, atualizado)
        except BaseException:
            shutil.rmtree(temporario, ignore_errors=True)
            try:
                current = _ler_json(cache_dir / ARQUIVO_ESTADO)
                published = isinstance(current, dict) and any(
                    item.get("nome") == nome
                    for item in current.get("checkpoints", [])
                    if isinstance(item, dict)
                )
            except (OSError, ValueError, json.JSONDecodeError):
                published = False
            if not published:
                shutil.rmtree(final, ignore_errors=True)
                arquivo_estado_anterior.unlink(missing_ok=True)
            raise
        return len(checkpoints)


def restaurar_estado_compactacao(cache_dir: Path) -> int:
    """Restaura atomicamente o estado anterior, mantendo o estado atual arquivado."""

    with _lock_escrita(cache_dir):
        state, _ = _carregar_estado_escrita(cache_dir)
        if state is None:
            raise RuntimeError("Rollback requer um índice checkpoint preparado.")
        snapshots = state.get("snapshotsEstado", [])
        if not isinstance(snapshots, list) or not snapshots:
            raise RuntimeError("Não há snapshot de estado para restaurar.")
        snapshot_path = cache_dir / str(snapshots[-1])
        snapshot = _ler_json(snapshot_path)
        if not isinstance(snapshot, dict) or snapshot.get("formato") != ESQUEMA_CHECKPOINTS:
            raise RuntimeError(f"Snapshot de estado inválido: {snapshot_path}")

        snapshot_checkpoints = snapshot.get("checkpoints", [])
        if not isinstance(snapshot_checkpoints, list):
            raise RuntimeError("Lista de checkpoints inválida no snapshot.")
        ids_snapshot: set[str] = set()
        vetores_snapshot = 0
        for entry in snapshot_checkpoints:
            if not isinstance(entry, dict) or not entry.get("nome"):
                raise RuntimeError("Entrada inválida de checkpoint no snapshot.")
            checkpoint_dir = cache_dir / "checkpoints" / str(entry["nome"])
            manifest_path = checkpoint_dir / "manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            if hashlib.sha256(manifest_bytes).hexdigest() != entry.get("manifestSha256"):
                raise RuntimeError(f"Manifesto divergente no checkpoint {entry['nome']}.")
            manifest = json.loads(manifest_bytes)
            for filename, digest in manifest.get("arquivos", {}).items():
                arquivo = checkpoint_dir / str(filename)
                if not arquivo.is_file() or _sha256(arquivo) != digest:
                    raise RuntimeError(
                        f"Arquivo inválido no checkpoint {entry['nome']}/{filename}."
                    )
            shard = faiss.read_index(str(checkpoint_dir / ARQUIVO_INDICE))
            ids = _ler_json(checkpoint_dir / ARQUIVO_IDS)
            hashes = _ler_json(checkpoint_dir / "content_hashes.json")
            if (
                not isinstance(ids, list)
                or not all(isinstance(item, str) for item in ids)
                or len(ids) != len(set(ids))
                or ids_snapshot.intersection(ids)
                or not isinstance(hashes, dict)
                or set(hashes) != set(ids)
                or shard.ntotal != len(ids)
                or shard.d != int(snapshot.get("dimensao", 0))
                or not _vetores_finitos(shard)
            ):
                raise RuntimeError(f"Dados inconsistentes no checkpoint {entry['nome']}.")
            ids_snapshot.update(ids)
            vetores_snapshot += int(shard.ntotal)
        base_total = 0
        base_name = snapshot.get("base")
        if base_name:
            base_dir = cache_dir / "base"
            base_index = faiss.read_index(str(base_dir / ARQUIVO_INDICE))
            base_ids = _ler_json(base_dir / ARQUIVO_IDS)
            if ids_snapshot.intersection(base_ids):
                raise RuntimeError("IDs duplicados entre base e snapshot de checkpoints.")
            base_total = int(base_index.ntotal)
        if base_total + vetores_snapshot != snapshot.get("totalVetores"):
            raise RuntimeError("totalVetores diverge no snapshot de estado.")

        arquivo_estado_atual = (
            cache_dir / "archive" / "states" / f"estado-rollback-{uuid.uuid4().hex}.json"
        )
        _salvar_json_atomico(arquivo_estado_atual, state)
        checkpoints_atuais = state.get("checkpoints", [])
        checkpoints_arquivados = snapshot.get("checkpointsArquivados", [])
        if not isinstance(checkpoints_atuais, list) or not isinstance(
            checkpoints_arquivados, list
        ):
            arquivo_estado_atual.unlink(missing_ok=True)
            raise RuntimeError("Metadados de checkpoints inválidos para rollback.")
        restaurado = {
            **snapshot,
            "checkpointsArquivados": [
                *checkpoints_arquivados,
                *checkpoints_atuais,
            ],
            "snapshotsEstado": [
                *snapshots,
                str(arquivo_estado_atual.relative_to(cache_dir)),
            ],
            "atualizadoEm": _agora_utc(),
        }
        _salvar_json_atomico(cache_dir / ARQUIVO_ESTADO, restaurado)
        return len(restaurado.get("checkpoints", []))


# Mantida por compatibilidade com o MVP anterior.
def load_or_build_faiss_index(
    chunks: list[tuple[str, str]],
    gerar_embedding: EmbeddingFn,
    cache_dir: Path,
    *,
    modelo_embedding: str,
) -> FaissChunkIndex:
    indice, _ = indexar_chunks_incrementalmente(
        chunks,
        gerar_embedding,
        cache_dir,
        modelo_embedding=modelo_embedding,
    )
    return indice
