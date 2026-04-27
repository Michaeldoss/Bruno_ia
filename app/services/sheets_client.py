import requests
import csv
import io
import asyncio
import logging
from typing import List, Dict
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _normalizar_chave(chave: str) -> str:
    """Remove acentos e normaliza espaços para comparação de colunas."""
    import unicodedata
    return unicodedata.normalize("NFKD", chave).encode("ascii", "ignore").decode("ascii").upper().strip()


def _get_col(row: Dict, *nomes: str) -> str:
    """
    Busca uma coluna no dict tentando os nomes fornecidos,
    com fallback para versão normalizada (sem acento).
    """
    # Tenta exato primeiro
    for nome in nomes:
        val = row.get(nome)
        if val is not None:
            return str(val).strip()
    # Tenta normalizado
    row_norm = {_normalizar_chave(k): v for k, v in row.items()}
    for nome in nomes:
        val = row_norm.get(_normalizar_chave(nome))
        if val is not None:
            return str(val).strip()
    return ""


class SheetsClient:
    def __init__(self):
        self.csv_url_equipamentos = getattr(settings, "GOOGLE_SHEET_CSV_URL", "stub")
        self.csv_url_suprimentos  = getattr(settings, "GOOGLE_SHEET_SUPRIMENTOS_URL", "stub")

    async def _fetch_csv(self, url: str, label: str) -> List[Dict]:
        if url in ("stub", "", None):
            logger.warning(f"URL nao configurada: {label}")
            return []
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(requests.get, url, timeout=8),
                timeout=10.0
            )
            response.raise_for_status()
            content = response.content.decode("utf-8")
            rows = list(csv.DictReader(io.StringIO(content)))
            logger.info(f"{label}: {len(rows)} linhas carregadas")
            return rows
        except asyncio.TimeoutError:
            logger.warning(f"{label}: timeout")
            return []
        except Exception as e:
            logger.error(f"{label}: erro {e}")
            return []

    async def get_machines(self) -> List[Dict]:
        return await self._fetch_csv(self.csv_url_equipamentos, "EQUIPAMENTOS")

    async def get_supplies(self) -> List[Dict]:
        return await self._fetch_csv(self.csv_url_suprimentos, "SUPRIMENTOS")

    async def build_tabela_precos(self) -> str:
        equipamentos = await self.get_machines()
        suprimentos  = await self.get_supplies()

        tabela = "TABELA DE PRECOS OFICIAL DOSS GROUP (tempo real)\n"
        tabela += "=" * 55 + "\n\n"

        if equipamentos:
            tabela += "EQUIPAMENTOS:\n"
            for row in equipamentos:
                status    = _get_col(row, "STATUS")
                nome      = _get_col(row, "EQUIPAMENTOS A VENDA", "MODELO")
                modelo    = _get_col(row, "MODELO")
                preco     = _get_col(row, "PREÇO SUJERIDO", "PRECO SUJERIDO", "PREO SUJERIDO")
                condicoes = _get_col(row, "CONDIÇÕES", "CONDICOES", "CONDIES")
                tecnologia = _get_col(row, "PLACAS / TECNOLOGIA", "TECNOLOGIA")
                if not nome:
                    continue
                linha = f"  [{status}] {nome}"
                if tecnologia:
                    linha += f" | {tecnologia}"
                if preco:
                    linha += f" - {preco}"
                if condicoes:
                    linha += f" | {condicoes}"
                tabela += linha + "\n"
            tabela += "\n"
        else:
            tabela += "EQUIPAMENTOS: indisponivel\n\n"

        if suprimentos:
            tabela += "SUPRIMENTOS / TINTAS:\n"
            for row in suprimentos:
                descricao = _get_col(row, "DESCRICAO", "PRODUTO", "Nome", "EQUIPAMENTOS A VENDA")
                preco     = _get_col(row, "PREÇO SUJERIDO", "PRECO SUJERIDO", "PRECO", "VALOR")
                obs       = _get_col(row, "OBS", "OBSERVACAO", "CONDIÇÕES", "CONDIES")
                if not descricao:
                    continue
                linha = f"  {descricao}"
                if preco:
                    linha += f" - {preco}"
                if obs:
                    linha += f" | {obs}"
                tabela += linha + "\n"
            tabela += "\n"
        else:
            tabela += "SUPRIMENTOS: indisponivel\n\n"

        tabela += "=" * 55 + "\n"
        tabela += "CONDICOES GERAIS:\n"
        tabela += "  Padrao: 40% entrada + ate 10x sem juros\n"
        tabela += "  DG 1908: 40% entrada + 18x sem juros (negociavel ate 36x)\n"
        tabela += "  Cartao: ate 12x (juros da operadora)\n"
        tabela += "  Tintas: a vista ou 07/15/30/45/60 dias\n"

        return tabela


sheets_service = SheetsClient()
