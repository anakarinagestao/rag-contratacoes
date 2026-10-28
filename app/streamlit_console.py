"""Chat local do Adaptive RAG com área técnica preservada.

O cliente chama exclusivamente a FastAPI. Tokens e conversas permanecem no
``st.session_state`` e não são gravados no projeto ou nos bancos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from app.streamlit_chat_state import (
    CHAT_CONVERSATIONS_KEY,
    conversa_ativa,
    criar_conversa,
    inicializar_chat,
    montar_requisicao,
    registrar_pergunta,
    registrar_resposta,
    selecionar_conversa,
)


RAIZ_PROJETO = Path(__file__).resolve().parents[1]
ARQUIVO_CASOS = RAIZ_PROJETO / "tests" / "fixtures" / "query_cases.json"
URL_API_PADRAO = "http://127.0.0.1:8000"


def _casos_fixos() -> list[dict[str, Any]]:
    if not ARQUIVO_CASOS.is_file():
        return []
    conteudo = json.loads(ARQUIVO_CASOS.read_text(encoding="utf-8"))
    return conteudo.get("cases", []) if isinstance(conteudo, dict) else []


def _chamar_api(
    metodo: str,
    url: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    corpo: dict[str, Any] | None = None,
) -> tuple[int | None, dict[str, Any] | str]:
    headers = {"Authorization": f"Bearer {token}"} if token.strip() else {}
    try:
        resposta = requests.request(
            metodo, url, headers=headers, params=params, json=corpo, timeout=120
        )
    except requests.RequestException:
        return None, "Não foi possível alcançar a API. Verifique se a FastAPI está ativa."
    try:
        return resposta.status_code, resposta.json()
    except ValueError:
        return resposta.status_code, resposta.text


def _mensagem_erro(status: int | None, payload: dict[str, Any] | str) -> str:
    if status is None:
        return str(payload)
    if status == 401:
        return "Sua sessão expirou ou o token é inválido. Entre novamente."
    if status == 503:
        return "Um serviço necessário está temporariamente indisponível. Tente novamente mais tarde."
    if status == 422 and isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and detail.get("mensagem"):
            return str(detail["mensagem"])
        if isinstance(detail, str):
            return detail
    return f"A consulta não pôde ser concluída (HTTP {status})."


def _exibir_fontes(payload: dict[str, Any]) -> None:
    sources = payload.get("sources") or []
    external = payload.get("external_documents") or []
    if sources:
        with st.expander(f"Fontes documentais ({len(sources)})"):
            for source in sources:
                metadata = source.get("metadata") or {}
                label = metadata.get("titulo") or metadata.get("source_url") or source.get("chunk_id")
                st.markdown(f"**[{source.get('rank', '-')}] {label}**")
                for key in ("numeroControlePNCP", "numero_controle_pncp", "artigo", "clausula", "url", "source_url"):
                    if metadata.get(key):
                        st.write(f"{key}: {metadata[key]}")
    if external:
        with st.expander(f"Arquivos catalogados no PNCP ({len(external)})"):
            st.caption("Arquivos anunciados pelo PNCP; podem ainda não ter sido processados.")
            st.json(external)


def _exibir_tarefas_processamento(payload: dict[str, Any]) -> None:
    tasks = payload.get("processing_tasks") or []
    if not tasks:
        return
    st.info(
        f"{len(tasks)} documento(s) foi(ram) colocado(s) na fila de "
        "processamento. A resposta documental poderá ser completada depois."
    )
    with st.expander("Acompanhar processamento documental"):
        for task in tasks:
            st.write(
                f"Tarefa {task.get('task_id')} · {task.get('status')} · "
                f"{task.get('status_url')}"
            )


def _exibir_evidencias_estruturadas(payload: dict[str, Any]) -> None:
    structured = payload.get("structured_result")
    if not isinstance(structured, dict):
        return
    for warning in structured.get("warnings") or []:
        message = warning.get("message") if isinstance(warning, dict) else str(warning)
        st.warning(message)
    rows = structured.get("rows") or []
    if rows:
        with st.expander(f"Dados estruturados ({structured.get('row_count', len(rows))})"):
            st.dataframe(rows, use_container_width=True)


def _exibir_resposta_chat(status: int | None, payload: dict[str, Any] | str) -> None:
    if status is None or status >= 400:
        st.error(_mensagem_erro(status, payload))
        if status == 503 and isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict) and detail.get("code"):
                st.caption(f"Código técnico: {detail['code']}")
        return
    if not isinstance(payload, dict):
        st.write(str(payload))
        return
    answer = payload.get("answer")
    if answer:
        st.write(answer)
    elif status == 202 and payload.get("processing_tasks"):
        st.info("Os documentos necessários ainda estão sendo preparados.")
    else:
        st.info("A consulta foi concluída, mas não retornou resposta textual.")
    _exibir_tarefas_processamento(payload)
    _exibir_evidencias_estruturadas(payload)
    _exibir_fontes(payload)
    with st.expander("Detalhes técnicos"):
        st.json({key: payload.get(key) for key in ("route", "analysis", "plan")})


def _barra_lateral(url_api: str) -> tuple[str, int, str]:
    if "_next_access_token" in st.session_state:
        st.session_state["access_token"] = st.session_state.pop("_next_access_token")
    st.session_state.setdefault("access_token", "")

    with st.sidebar:
        st.header("PNCP/RJ")
        area = st.radio("Navegação", ["Chat", "Administração"])
        st.divider()
        st.subheader("Conexão")
        st.text_input("URL da API", value=url_api, key="api_url")

        with st.expander("Entrar com usuário e senha", expanded=not bool(st.session_state["access_token"])):
            with st.form("login_form", clear_on_submit=True):
                username = st.text_input("Usuário")
                password = st.text_input("Senha", type="password")
                login = st.form_submit_button("Entrar", type="primary")
            if login:
                status, payload = _chamar_api(
                    "POST", f"{st.session_state['api_url'].rstrip('/')}/auth/login",
                    token="", corpo={"username": username, "password": password},
                )
                if status == 200 and isinstance(payload, dict) and payload.get("access_token"):
                    st.session_state["_next_access_token"] = payload["access_token"]
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos, ou API indisponível.")

        st.text_input(
            "Access token", type="password", key="access_token",
            help="Fica somente nesta sessão do Streamlit.",
        )
        if st.button("Sair / limpar token"):
            st.session_state["_next_access_token"] = ""
            st.rerun()

        top_k = st.slider("Top K documental", 1, 20, 5)
        return area, top_k, st.session_state["api_url"].rstrip("/")


def _pagina_chat(url_api: str, token: str, top_k: int) -> None:
    inicializar_chat(st.session_state)
    with st.sidebar:
        st.divider()
        if st.button("＋ Nova conversa", type="primary", use_container_width=True):
            criar_conversa(st.session_state)
            st.rerun()
        st.caption("Conversas desta sessão")
        conversations = st.session_state[CHAT_CONVERSATIONS_KEY]
        for conversation_id, conversation in reversed(list(conversations.items())):
            if st.button(
                conversation["title"], key=f"conversation-{conversation_id}",
                use_container_width=True,
            ):
                selecionar_conversa(st.session_state, conversation_id)
                st.rerun()

    conversation = conversa_ativa(st.session_state)
    st.title("Assistente PNCP/RJ")
    st.caption("Pergunte sobre contratações, contratos, itens, atas e PCAs do Rio de Janeiro.")
    if not conversation["messages"]:
        st.info("Exemplo: Quantos contratos foram assinados em 2025?")
    for message in conversation["messages"]:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            else:
                _exibir_resposta_chat(message["status"], message["payload"])

    question = st.chat_input("Faça uma pergunta sobre o PNCP/RJ")
    if question:
        registrar_pergunta(conversation, question)
        with st.spinner("Consultando o Adaptive RAG..."):
            status, payload = _chamar_api(
                "POST", f"{url_api}/query/ask", token=token,
                corpo=montar_requisicao(conversation, question, top_k),
            )
        registrar_resposta(conversation, status, payload)
        st.rerun()


def _pagina_administracao(url_api: str, token: str) -> None:
    st.title("Administração e validação")
    st.caption("Ferramentas técnicas preservadas; nenhuma tela baixa documentos automaticamente.")
    aba_catalogo, aba_casos, aba_historico = st.tabs(
        ["Catálogo PNCP", "Casos de teste", "Histórico da sessão"]
    )
    with aba_catalogo:
        controle = st.text_input("Número de controle PNCP", key="catalogo_controle")
        colunas = st.columns(3)
        tipo_entidade = colunas[0].selectbox("Tipo de entidade", ["", "contrato", "contratacao"])
        tipo_documento = colunas[1].text_input("Tipo de documento")
        titulo = colunas[2].text_input("Título contém")
        if st.button("Consultar catálogo"):
            params = {key: value for key, value in {
                "numero_controle_pncp": controle.strip(),
                "tipo_entidade": tipo_entidade,
                "tipo_documento": tipo_documento.strip(),
                "titulo": titulo.strip(),
            }.items() if value}
            if not params:
                st.warning("Informe ao menos um filtro.")
            else:
                status, payload = _chamar_api(
                    "GET", f"{url_api}/query/catalog/documents", token=token, params=params,
                )
                if status is not None and status < 400:
                    st.json(payload)
                else:
                    st.error(_mensagem_erro(status, payload))
    with aba_casos:
        for case in _casos_fixos():
            with st.expander(f"{case['id']} · {case['expected']['route']}"):
                st.write(case["question"])
                st.json(case["expected"])
                st.caption(case.get("note", ""))
    with aba_historico:
        conversations = st.session_state.get(CHAT_CONVERSATIONS_KEY, {})
        if not conversations:
            st.caption("Nenhuma conversa nesta sessão.")
        for conversation in conversations.values():
            with st.expander(conversation["title"]):
                st.json(conversation["messages"])


def main() -> None:
    st.set_page_config(page_title="Adaptive RAG PNCP/RJ", page_icon="🔎", layout="wide")
    inicializar_chat(st.session_state)
    area, top_k, url_api = _barra_lateral(URL_API_PADRAO)
    token = st.session_state.get("access_token", "")
    if area == "Chat":
        _pagina_chat(url_api, token, top_k)
    else:
        _pagina_administracao(url_api, token)


if __name__ == "__main__":
    main()
