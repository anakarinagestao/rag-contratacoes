import time
from typing import Any

import requests


class PNCPClient:
    """
    Cliente HTTP responsável pela comunicação com a API do PNCP.

    Responsabilidades:
    - manter uma Session do requests;
    - configurar headers;
    - aplicar timeout;
    - realizar tentativas automáticas em erros transitórios;
    - tratar erros HTTP básicos.

    O cliente NÃO conhece regras de negócio de compras,
    atas, contratos ou documentos.
    """

    STATUS_RETRY = {
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        timeout: int = 30,
        max_tentativas: int = 3,
        backoff: float = 1.0,
    ):
        self.timeout = timeout
        self.max_tentativas = max_tentativas
        self.backoff = backoff

        self.session = requests.Session()

        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "radio-licitacoes/1.0",
            }
        )

    # ========================================================
    # GET
    # ========================================================

    def get(
        self,
        url: str,
        params: dict | None = None,
        stream: bool = False,
        timeout: int | None = None,
    ) -> requests.Response:
        """
        Executa uma requisição GET.

        Faz retry automaticamente para:
        - timeout;
        - erro de conexão;
        - HTTP 429;
        - HTTP 500;
        - HTTP 502;
        - HTTP 503;
        - HTTP 504.

        Erros como 400 e 404 não são repetidos.
        """

        timeout_requisicao = (
            timeout
            if timeout is not None
            else self.timeout
        )

        ultimo_erro = None

        for tentativa in range(
            1,
            self.max_tentativas + 1,
        ):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=timeout_requisicao,
                    stream=stream,
                )

                # --------------------------------------------
                # Erro transitório
                # --------------------------------------------

                if response.status_code in self.STATUS_RETRY:
                    if tentativa < self.max_tentativas:
                        self._aguardar(
                            tentativa=tentativa,
                            response=response,
                        )

                        response.close()
                        continue

                    return response

                # --------------------------------------------
                # Qualquer outro status é devolvido
                # imediatamente.
                #
                # Exemplo:
                # 200
                # 204
                # 400
                # 404
                # --------------------------------------------

                return response

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:

                ultimo_erro = exc

                if tentativa < self.max_tentativas:
                    self._aguardar(tentativa)
                    continue

                raise

        # Proteção adicional.
        # Normalmente este ponto nunca será alcançado.

        if ultimo_erro:
            raise ultimo_erro

        raise RuntimeError(
            "Não foi possível realizar a requisição ao PNCP."
        )

    # ========================================================
    # JSON
    # ========================================================

    def get_json(
        self,
        url: str,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> Any:
        """
        Executa GET e devolve o JSON.

        Retorna None quando o PNCP responder HTTP 204.

        Para respostas de erro HTTP, utiliza raise_for_status().
        """

        response = self.get(
            url=url,
            params=params,
            timeout=timeout,
        )

        if response.status_code == 204:
            return None

        response.raise_for_status()

        return response.json()

    # ========================================================
    # BACKOFF
    # ========================================================

    def _aguardar(
        self,
        tentativa: int,
        response: requests.Response | None = None,
    ) -> None:
        """
        Aguarda antes de uma nova tentativa.

        Para HTTP 429, respeita Retry-After quando o PNCP o informar.
        Para os demais casos, usa backoff exponencial.
        """

        segundos = (
            self.backoff
            * (2 ** (tentativa - 1))
        )

        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")

            if retry_after and retry_after.isdigit():
                segundos = max(segundos, float(retry_after))
            else:
                # O PNCP não informou quanto esperar.
                segundos = max(segundos, 30.0)

        pagina = (
            response.request.url
            if response is not None
            else "sem resposta"
        )

        print(
            f"Nova tentativa em {segundos:.0f}s | "
            f"tentativa={tentativa}/{self.max_tentativas} | "
            f"status={response.status_code if response else 'timeout/conexão'} | "
            f"requisição={pagina}"
        )

        time.sleep(segundos)

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self) -> None:
        """
        Fecha a sessão HTTP.
        """

        self.session.close()


# ============================================================
# CLIENTE PADRÃO
# ============================================================

pncp_client = PNCPClient(
    timeout=60,
    max_tentativas=4,
    backoff=3.0,
)
