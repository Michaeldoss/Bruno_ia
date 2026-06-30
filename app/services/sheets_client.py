import requests
import csv
import io
import asyncio
import logging
from typing import List, Dict, Optional
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _normalizar_chave(chave: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", chave).encode("ascii", "ignore").decode("ascii").upper().strip()


def _get_col(row: Dict, *nomes: str) -> str:
    for nome in nomes:
        val = row.get(nome)
        if val is not None:
            return str(val).strip()
    row_norm = {_normalizar_chave(k): v for k, v in row.items()}
    for nome in nomes:
        val = row_norm.get(_normalizar_chave(nome))
        if val is not None:
            return str(val).strip()
    return ""


class SheetsClient:
    def __init__(self):
        self.csv_url_equipamentos = getattr(settings, "GOOGLE_SHEET_CSV_URL", "stub")
        self.csv_url_suprimentos = getattr(settings, "GOOGLE_SHEET_SUPRIMENTOS_URL", "stub")

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

    async def get_supplies_catalog(self) -> Dict[str, Dict]:
        rows = await self.get_supplies()
        catalog = {}
        ignorar = ("SUPRIMENTO", "TINTAS DTF", "TINTA DGTEX", "TINTAS ECOSOLVENTE", "TINTAS UV", "")
        for row in rows:
            codigo = _get_col(row, "CÓDIGO", "CODIGO")
            nome = _get_col(row, "PRODUTO")
            preco = _get_col(row, " VALOR 1UN./PDV", "VALOR 1UN./PDV")
            if not codigo or not nome:
                continue
            if codigo in ignorar:
                continue
            try:
                int(codigo)
            except ValueError:
                continue
            catalog[codigo] = {"nome": nome, "preco_pdv": preco}
        return catalog

    async def find_codigo_by_name(self, search_term: str) -> Optional[str]:
        catalog = await self.get_supplies_catalog()
        search_lower = search_term.lower()
        for codigo, data in catalog.items():
            if search_lower in data["nome"].lower():
                return codigo
        return None

    async def find_codigo_by_phrase(self, phrase: str) -> Optional[str]:
        """
        Busca o produto cujo nome tem o MAIOR numero de palavras da frase
        em comum. Resolve ambiguidade quando ha multiplas palavras soltas
        (ex: 'tinta dgtex premium black' deve achar BLACK PREMIUM, nao CYAN).
        """
        catalog = await self.get_supplies_catalog()
        palavras_busca = set(w for w in phrase.lower().split() if len(w) > 2)
        if not palavras_busca:
            return None

        melhor_codigo = None
        melhor_score = 0
        for codigo, data in catalog.items():
            nome_lower = data["nome"].lower()
            palavras_nome = set(nome_lower.split())
            score = len(palavras_busca & palavras_nome)
            if score > melhor_score:
                melhor_score = score
                melhor_codigo = codigo

        return melhor_codigo if melhor_score >= 2 else None

    async def find_familia_by_phrase(self, phrase: str) -> List[Dict]:
        catalog = await self.get_supplies_catalog()
        palavras_busca = set(w for w in phrase.lower().split() if len(w) > 2)
        if not palavras_busca:
            return []

        scored = []
        for codigo, data in catalog.items():
            palavras_nome = set(data["nome"].lower().split())
            score = len(palavras_busca & palavras_nome)
            if score >= 2:
                scored.append((codigo, data["nome"], score))

        if not scored:
            return []

        max_score = max(s[2] for s in scored)
        empatados = [s for s in scored if s[2] == max_score]
        return [{"codigo": c, "nome": n} for c, n, _ in empatados[:6]]

    async def build_tabela_precos(self) -> str:
        equipamentos = await self.get_machines()
        suprimentos = await self.get_supplies()

        tabela = "TABELA DE PRECOS OFICIAL DOSS GROUP (tempo real)\n"
        tabela += "=" * 55 + "\n\n"

        if equipamentos:
            tabela += "EQUIPAMENTOS:\n"
            for row in equipamentos:
                status = _get_col(row, "STATUS")
                nome = _get_col(row, "EQUIPAMENTOS A VENDA", "MODELO")
                preco = _get_col(row, "PREÇO SUJERIDO", "PRECO SUJERIDO", "PREO SUJERIDO")
                condicoes = _get_col(row, "CONDIÇÕES", "CONDICOES", "CONDIES")
                tecnologia = _get_col(row, "PLACAS / TECNOLOGIA", "TECNOLOGIA")
                estoque = _get_col(row, "Estoque", "ESTOQUE")
                if not nome:
                    continue
                linha = f"  [{status}] {nome}"
                if tecnologia:
                    linha += f" | {tecnologia}"
                if preco:
                    linha += f" - {preco}"
                if condicoes:
                    linha += f" | {condicoes}"
                if estoque and estoque != "0":
                    linha += f" | Estoque: {estoque}"
                tabela += linha + "\n"
            tabela += "\n"
        else:
            tabela += "EQUIPAMENTOS: indisponivel\n\n"

        if suprimentos:
            tabela += "SUPRIMENTOS / TINTAS:\n"
            for row in suprimentos:
                descricao = _get_col(row, "PRODUTO", "DESCRICAO", "Nome")
                preco = _get_col(row, " VALOR 1UN./PDV", "VALOR 1UN./PDV", "PREÇO SUJERIDO", "PRECO")
                obs = _get_col(row, "OBSERVAÇÃO OU NEGOCIAÇÕES", "OBS", "OBSERVACAO")
                if not descricao or not preco:
                    continue
                linha = f"  {descricao} - {preco} (PDV)"
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
