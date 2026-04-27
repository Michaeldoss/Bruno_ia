import logging
import requests
import asyncio
import time
from typing import Optional, List, Dict
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class UniplusClient:
    def __init__(self):
        self.base_url = settings.UNIPLUS_BASE_URL
        self.account  = settings.UNIPLUS_ACCOUNT
        self.auth_code = settings.UNIPLUS_AUTH_CODE  # Base64 de conta:access_key
        self._token = None
        self._token_expires = 0

    def _is_configured(self) -> bool:
        return self.auth_code not in ("stub", "", None) and self.account not in ("stub", "", None)

    def _get_token_sync(self) -> Optional[str]:
        """
        Obtem token OAuth2 do Uniplus.
        Endpoint: POST /oauth/token
        Auth: Basic ${codigo_de_autorizacao}
        """
        if not self._is_configured():
            return None

        # Reutiliza token se ainda valido (margem de 5 minutos)
        if self._token and time.time() < self._token_expires - 300:
            return self._token

        try:
            url = self.base_url + "/oauth/token"
            headers = {
                "Authorization": f"Basic {self.auth_code}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            data = "grant_type=client_credentials&scope=public-api"

            r = requests.post(url, headers=headers, data=data, timeout=8)

            if r.status_code == 200:
                token_data = r.json()
                self._token = token_data.get("access_token")
                # Token valido por 60 minutos
                self._token_expires = time.time() + 3600
                logger.info("Uniplus: token OAuth2 obtido com sucesso")
                return self._token
            else:
                logger.error(f"Uniplus OAuth2 erro: {r.status_code} | {r.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Uniplus OAuth2 excecao: {e}")
            return None

    def _get_headers(self, token: str) -> Dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    async def _get_token(self) -> Optional[str]:
        return await asyncio.to_thread(self._get_token_sync)

    async def get_customer_by_phone(self, phone: str) -> Optional[Dict]:
        """Busca cliente pelo telefone no Uniplus."""
        if not self._is_configured():
            return None
        try:
            token = await asyncio.wait_for(self._get_token(), timeout=6.0)
            if not token:
                return None

            # Normaliza telefone (remove +55 e caracteres especiais)
            phone_clean = phone.replace("+55", "").replace("+", "").replace("-", "").replace(" ", "")

            url = f"{self.base_url}/public-api/v1/entidades"
            params = {"pesquisa": phone_clean, "limit": 1}

            response = await asyncio.wait_for(
                asyncio.to_thread(requests.get, url, headers=self._get_headers(token), params=params, timeout=5),
                timeout=6.0
            )
            if response.status_code == 200:
                data = response.json()
                items = data if isinstance(data, list) else data.get("content", [])
                return items[0] if items else None
            return None
        except asyncio.TimeoutError:
            logger.warning("Uniplus get_customer_by_phone: timeout")
            return None
        except Exception as e:
            logger.error(f"Uniplus get_customer_by_phone: {e}")
            return None

    async def get_stock_and_price(self, product_query: str) -> Optional[Dict]:
        """Busca produto por descricao no Uniplus."""
        if not self._is_configured():
            return None
        try:
            token = await asyncio.wait_for(self._get_token(), timeout=6.0)
            if not token:
                return None

            url = f"{self.base_url}/public-api/v1/produtos"
            params = {"pesquisa": product_query, "limit": 5}

            response = await asyncio.wait_for(
                asyncio.to_thread(requests.get, url, headers=self._get_headers(token), params=params, timeout=5),
                timeout=6.0
            )
            if response.status_code == 200:
                data = response.json()
                items = data if isinstance(data, list) else data.get("content", [])
                if not items:
                    return None
                item = items[0]
                return {
                    "nome": item.get("descricao") or item.get("nome"),
                    "estoque": item.get("saldoEstoque", 0),
                    "preco": item.get("precoVenda", 0),
                    "codigo": item.get("codigo")
                }
            return None
        except asyncio.TimeoutError:
            logger.warning(f"Uniplus get_stock_and_price timeout: {product_query}")
            return None
        except Exception as e:
            logger.error(f"Uniplus get_stock_and_price: {e}")
            return None

    async def list_receivables(self, days_offset: int = 0) -> List[Dict]:
        """Busca contas a receber vencidas."""
        if not self._is_configured():
            return []
        try:
            token = await asyncio.wait_for(self._get_token(), timeout=6.0)
            if not token:
                return []

            from datetime import datetime, timedelta
            target_date = (datetime.now() - timedelta(days=days_offset)).strftime("%Y-%m-%d")

            url = f"{self.base_url}/public-api/v1/contas-receber"
            params = {
                "vencimento.le": target_date,
                "situacao.eq": "ABERTO",
                "limit": 50
            }

            response = await asyncio.wait_for(
                asyncio.to_thread(requests.get, url, headers=self._get_headers(token), params=params, timeout=5),
                timeout=6.0
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, list) else data.get("content", [])
            return []
        except asyncio.TimeoutError:
            logger.warning("Uniplus list_receivables: timeout")
            return []
        except Exception as e:
            logger.error(f"Uniplus list_receivables: {e}")
            return []


uniplus_service = UniplusClient()
