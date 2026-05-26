import time
import logging
import asyncio
from typing import Optional, Dict, Any
import httpx
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

UNIPLUS_BASE_URL = settings.UNIPLUS_BASE_URL.rstrip("/")
UNIPLUS_CLIENT_ID = settings.UNIPLUS_CLIENT_ID
UNIPLUS_CLIENT_SECRET = settings.UNIPLUS_CLIENT_SECRET
UNIPLUS_FILIAL = settings.UNIPLUS_FILIAL
UNIPLUS_LOCAL_ESTOQUE = settings.UNIPLUS_LOCAL_ESTOQUE

# ---------------------------------------------------------------------------
# Cache de token OAuth2 — evita gerar novo token a cada chamada
# ---------------------------------------------------------------------------
_token_cache: Dict[str, Any] = {"token": "", "expires_at": 0}


async def _get_token() -> str:
    """Busca token OAuth2 do Uniplus com cache de 50 minutos."""
    agora = time.time()
    if _token_cache["token"] and agora < _token_cache["expires_at"]:
        return _token_cache["token"]

    if not (UNIPLUS_CLIENT_ID and UNIPLUS_CLIENT_SECRET):
        raise Exception("Uniplus OAuth2 não configurado")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{UNIPLUS_BASE_URL}/oauth/token",
            data={"grant_type": "client_credentials", "scope": "public-api"},
            auth=(UNIPLUS_CLIENT_ID, UNIPLUS_CLIENT_SECRET),
        )
        if r.status_code >= 400:
            raise Exception(f"Uniplus OAuth2 falhou: {r.text[:200]}")
        data = r.json()
        token = data.get("access_token") or ""
        expires_in = int(data.get("expires_in") or 3600)
        _token_cache["token"] = token
        _token_cache["expires_at"] = agora + min(expires_in - 60, 3000)
        logger.info("Uniplus: token OAuth2 renovado")
        return token


async def _headers() -> Dict[str, str]:
    token = await _get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Consulta de saldo em estoque
# ---------------------------------------------------------------------------
async def get_saldo_estoque(codigo_produto: str) -> Optional[float]:
    """
    Consulta saldo em estoque de um produto no Uniplus.
    Retorna float com a quantidade ou None em caso de erro.
    """
    try:
        hdrs = await _headers()
        params = {
            "produto": codigo_produto,
            "filial": UNIPLUS_FILIAL,
            "localestoque": UNIPLUS_LOCAL_ESTOQUE,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{UNIPLUS_BASE_URL}/public-api/v1/saldo-estoque",
                headers=hdrs,
                params=params,
            )
            if r.status_code >= 400:
                logger.warning(f"Uniplus estoque {codigo_produto}: {r.status_code} {r.text[:100]}")
                return None
            # API retorna número simples: "43.000"
            try:
                return float(r.text.strip())
            except Exception:
                data = r.json()
                if isinstance(data, list):
                    return sum(float(x.get("saldo") or 0) for x in data)
                return 0.0
    except Exception as e:
        logger.error(f"Uniplus get_saldo_estoque({codigo_produto}): {e}")
        return None


# ---------------------------------------------------------------------------
# Busca produto por código
# ---------------------------------------------------------------------------
async def get_produto(codigo_produto: str) -> Optional[Dict[str, Any]]:
    """
    Busca dados de um produto no Uniplus pelo código.
    Retorna dict com nome, preço, unidade, etc. ou None.
    """
    try:
        hdrs = await _headers()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{UNIPLUS_BASE_URL}/public-api/v1/produtos/{codigo_produto}",
                headers=hdrs,
            )
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                logger.warning(f"Uniplus produto {codigo_produto}: {r.status_code}")
                return None
            p = r.json()
            return {
                "codigo": str(p.get("codigo") or codigo_produto),
                "nome": str(p.get("nome") or ""),
                "descricao": str(p.get("descricaoShop") or p.get("infoShop") or ""),
                "preco": float(p.get("preco") or 0),
                "unidade": str(p.get("unidadeMedida") or "UN"),
                "grupo": str(p.get("nomeGrupoProduto") or ""),
                "ativo": p.get("inativo") == 0,
            }
    except Exception as e:
        logger.error(f"Uniplus get_produto({codigo_produto}): {e}")
        return None


# ---------------------------------------------------------------------------
# Busca produto + estoque em uma só chamada
# ---------------------------------------------------------------------------
async def get_stock_and_price(codigo_produto: str) -> Optional[Dict[str, Any]]:
    """
    Retorna produto com nome, preço e estoque atual.
    Usado pelo Bruno para informar disponibilidade de tintas/suprimentos.
    """
    try:
        produto, saldo = await asyncio.gather(
            get_produto(codigo_produto),
            get_saldo_estoque(codigo_produto),
            return_exceptions=True,
        )
        if isinstance(produto, Exception) or produto is None:
            return None
        estoque = saldo if isinstance(saldo, float) else 0.0
        return {
            "codigo": produto["codigo"],
            "nome": produto["nome"],
            "preco": produto["preco"],
            "unidade": produto["unidade"],
            "estoque": estoque,
            "disponivel": estoque > 0,
        }
    except Exception as e:
        logger.error(f"Uniplus get_stock_and_price({codigo_produto}): {e}")
        return None


# ---------------------------------------------------------------------------
# Busca cliente por telefone
# ---------------------------------------------------------------------------
async def get_customer_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """
    Busca cliente no Uniplus pelo telefone.
    Retorna dict com id, nome ou None se não encontrado.
    """
    try:
        hdrs = await _headers()
        phone_clean = "".join(c for c in phone if c.isdigit())
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{UNIPLUS_BASE_URL}/public-api/v1/contatos",
                headers=hdrs,
                params={"telefone": phone_clean, "limit": "1"},
            )
            if r.status_code >= 400:
                return None
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data.get("id"):
                return data
            return None
    except Exception as e:
        logger.error(f"Uniplus get_customer_by_phone({phone}): {e}")
        return None


# ---------------------------------------------------------------------------
# Lista contas a receber vencidas
# ---------------------------------------------------------------------------
async def list_receivables(days_offset: int = 1) -> list:
    """
    Lista títulos vencidos no Uniplus.
    Usado para alertar o Bruno sobre clientes com débito.
    """
    try:
        hdrs = await _headers()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{UNIPLUS_BASE_URL}/public-api/v1/titulos-receber",
                headers=hdrs,
                params={"vencidos": "true", "limit": "100"},
            )
            if r.status_code >= 400:
                return []
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Uniplus list_receivables: {e}")
        return []


# ---------------------------------------------------------------------------
# Testa conexão
# ---------------------------------------------------------------------------
async def test_connection() -> Dict[str, Any]:
    """Testa OAuth2 e retorna status da conexão."""
    try:
        token = await _get_token()
        return {"ok": True, "conectado": True, "token_preview": token[:20] + "..."}
    except Exception as e:
        return {"ok": False, "conectado": False, "erro": str(e)}


# ---------------------------------------------------------------------------
# Instância compatível com o código atual do Bruno
# ---------------------------------------------------------------------------
class UniplusService:
    async def get_customer_by_phone(self, phone: str):
        return await get_customer_by_phone(phone)

    async def list_receivables(self, days_offset: int = 1):
        return await list_receivables(days_offset)

    async def get_stock_and_price(self, query: str):
        return await get_stock_and_price(query)


uniplus_service = UniplusService()
