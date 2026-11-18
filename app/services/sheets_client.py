import requests
import csv
import io
import asyncio
import logging
import re
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


_SUPPLY_QUERY_LIMIT = 300
_TECHNOLOGIES = {"dtf", "uv", "subli", "eco"}
_COLOURS = {"white", "black", "cyan", "magenta", "yellow", "verniz"}
_LINES = {"dgtex", "dgeco", "prime", "premium", "lite", "nano", "flex", "dx"}
_VARIANTS = _LINES - {"dgtex", "dgeco"}
_MATERIALS = {"tinta", "filme", "po", "limpeza"}
_SUPPLY_ALIASES = {
    "tintas": "tinta", "branca": "white", "branco": "white", "wht": "white", "withe": "white",
    "preta": "black", "preto": "black", "ciano": "cyan", "cian": "cyan",
    "amarela": "yellow", "amarelo": "yellow", "varnish": "verniz",
    "sublimatica": "subli", "sublimaticas": "subli", "sublimacao": "subli",
    "sublimation": "subli", "ecosolvente": "eco", "ecosolvent": "eco",
    "filmes": "filme", "film": "filme", "powder": "po",
    "cleaner": "limpeza", "cleaning": "limpeza", "flexivel": "flex",
}


def _supply_terms(text: str) -> set:
    normalized = _normalizar_chave(text).lower()
    normalized = re.sub(r"\bdg[\s-]+tex\b", "dgtex", normalized)
    normalized = re.sub(r"\bdg[\s-]+eco\b", "dgeco", normalized)
    normalized = re.sub(r"\beco[\s-]+solvent(?:e)?\b", "eco", normalized)
    normalized = re.sub(r"(?<=\d)\.(?=l\b)", "", normalized)
    terms = {_SUPPLY_ALIASES.get(word, word) for word in re.findall(r"[a-z]+", normalized)}
    if "dgtex" in terms:
        terms.add("subli")
    if "dgeco" in terms:
        terms.add("eco")
    # A size is part of product identity, not a price or consumption estimate.
    for amount, unit in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*(ml|lt|litros?|l|cm)\b", normalized):
        number = float(amount.replace(",", "."))
        if unit in {"l", "lt", "litro", "litros"}:
            number, unit = number * 1000, "ml"
        terms.add(f"size:{number:g}:{unit}")
    return terms


def is_specific_supply_query(query: str) -> bool:
    """Whether a name query identifies more than a broad ink technology.

    This does not prove compatibility or authorize an offer. The caller must
    still require one candidate and verify its identity and price in the ERP.
    """
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= _SUPPLY_QUERY_LIMIT:
        return False
    terms = _supply_terms(query)
    return bool(terms & _TECHNOLOGIES and terms & (_COLOURS | _VARIANTS))


def supply_name_matches_query(query: str, name: str) -> bool:
    """Conservative identity match, shared by discovery and ERP readback.

    No model number is interpreted as a SKU or compatibility approval. Explicit
    technology, colour, line and package constraints must all match the name.
    """
    if (not isinstance(query, str) or not isinstance(name, str)
            or not 1 <= len(query.strip()) <= _SUPPLY_QUERY_LIMIT or not name.strip()):
        return False
    requested, actual = _supply_terms(query), _supply_terms(name)
    constraints = requested & (_TECHNOLOGIES | _COLOURS | _LINES | _MATERIALS)
    constraints |= {term for term in requested if term.startswith("size:")}
    if not constraints or not constraints <= actual:
        return False
    # Textile DTF must not silently resolve to UV DTF, or vice versa.
    technologies = requested & _TECHNOLOGIES
    actual_technologies = actual & _TECHNOLOGIES
    # DTF UV is part of the UV family. A request for UV can find it, but a
    # textile DTF request cannot silently cross into UV just because both names
    # contain the token DTF. An explicit DTF+UV query still requires both tokens.
    if "uv" in technologies:
        technologies.discard("dtf")
    if "uv" in actual_technologies:
        actual_technologies.discard("dtf")
    if technologies and actual_technologies != technologies:
        return False
    if "epson" in requested and not technologies:
        return False
    return True


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

    async def lookup_supplies_candidates(self, query: str, limit: int = 4) -> List[Dict]:
        """Resolve names only; never expose worksheet prices, costs or margins.

        Conflicting rows for the same SKU are excluded before matching, so a
        duplicated code cannot turn a yellow ink request into a white ink quote.
        Multiple matches remain ambiguous; their order is not a recommendation.
        """
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= _SUPPLY_QUERY_LIMIT
                or type(limit) is not int or not 1 <= limit <= 4):
            return []
        rows = await self.get_supplies()
        catalog, fingerprints, conflicts = {}, {}, set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            row = {key: value for key, value in row.items() if isinstance(key, str)}
            codigo = _get_col(row, "CÓDIGO", "CODIGO")
            nome = _get_col(row, "PRODUTO")
            # Excel may serialize an integer identifier as "222353.0".
            # Accept only an exact zero fractional part; never round a SKU.
            numeric_code = re.fullmatch(r"([0-9]{1,20})(?:\.0+)?", codigo)
            if not numeric_code or not nome or len(nome) > 500:
                continue
            codigo = numeric_code.group(1)
            fingerprint = re.sub(r"[^A-Z0-9]+", " ", _normalizar_chave(nome)).strip()
            if codigo in fingerprints and fingerprints[codigo] != fingerprint:
                conflicts.add(codigo)
            fingerprints[codigo] = fingerprint
            catalog[codigo] = {"codigo": codigo, "nome": nome}
        matches = [item for code, item in sorted(catalog.items())
                   if code not in conflicts and supply_name_matches_query(query, item["nome"])]
        # A caller's output limit must never turn ambiguity into one apparent
        # exact match. Return no resolvable candidate if it cannot show choices.
        if limit == 1 and len(matches) > 1:
            return []
        return matches[:limit]

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
        tabela += "\n"
        tabela += "  TINTAS E SUPRIMENTOS -- prazo por faixa de VALOR DO PEDIDO (padrao):\n"
        tabela += "    R$ 600,00  a R$ 750,00   -> 30 dias\n"
        tabela += "    R$ 750,01  a R$ 1.100,00 -> 15/30\n"
        tabela += "    R$ 1.100,01 a R$ 1.700,00 -> 15/30/45\n"
        tabela += "    R$ 1.700,01 a R$ 2.900,00 -> 15/30/45/60\n"
        tabela += "    Acima de R$ 2.900,01      -> 30/60\n"
        tabela += "    Pedidos abaixo de R$ 600,00: a vista.\n"
        tabela += "    Esses prazos podem ser AMPLIADOS conforme volume/recorrencia do\n"
        tabela += "    cliente -- isso e negociado com o time comercial, NUNCA prometa\n"
        tabela += "    prazo maior que a tabela sozinho.\n"

        return tabela


sheets_service = SheetsClient()


async def lookup_supplies_candidates(query: str, limit: int = 4) -> List[Dict]:
    return await sheets_service.lookup_supplies_candidates(query, limit)

