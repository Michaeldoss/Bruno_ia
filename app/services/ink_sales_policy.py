"""Ink-first commercial guidance for Bruno, independent of any one ERP.

Do not infer technical compatibility or live prices from historic conversations.
"""
from dataclasses import dataclass
from enum import Enum


class InkTechnology(str, Enum):
    SUBLIMATION = "sublimation"
    ECO_SOLVENT = "eco_solvent"
    DTF_TEXTILE = "dtf_textile"
    UV = "uv"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InkDiscovery:
    technology: InkTechnology = InkTechnology.UNKNOWN
    machine_brand: str = ""
    machine_model: str = ""
    printhead: str = ""
    current_ink: str = ""
    monthly_consumption_liters: float | None = None
    current_supplier: str = ""
    current_price_per_liter: float | None = None
    feed_system: str = ""
    technical_compatibility_approved: bool = False


def ink_next_question(discovery: InkDiscovery) -> str:
    """Ask one useful question at a time, without forcing a fixed script."""
    if discovery.technology == InkTechnology.UNKNOWN:
        return "Você trabalha com tinta sublimática, eco solvente, DTF ou UV?"
    if not discovery.machine_model:
        return "Qual máquina você usa hoje?"
    if not discovery.current_ink:
        return "Qual tinta você está usando nela atualmente?"
    if discovery.monthly_consumption_liters is None:
        return "Em média, quantos litros você consome por mês?"
    if not discovery.current_supplier:
        return "Você compra essa tinta de qual fornecedor hoje?"
    if discovery.current_price_per_liter is None:
        return "Quanto você paga hoje por litro?"
    return "O que mais pesa hoje na escolha da tinta: estabilidade, qualidade ou custo?"


def can_assert_compatibility(discovery: InkDiscovery) -> bool:
    return bool(
        discovery.technical_compatibility_approved
        and discovery.technology != InkTechnology.UNKNOWN
        and discovery.machine_model.strip()
    )


INK_SALES_INSTRUCTIONS = """
PRIORIDADE COMERCIAL ATUAL DA DOSS: TINTAS E RECOMPRA RECORRENTE.
Máquinas continuam no portfólio; não force venda de máquina a quem busca tinta.
Para tintas, descubra naturalmente tecnologia, equipamento, cabeça de impressão,
tinta atual, volume mensal, fornecedor, preço e principal problema do cliente.
Uma pergunta por mensagem; use o que o cliente já informou e não reinicie cadastro.
Explique diferenciais técnicos verificáveis: consistência de lotes, estabilidade,
perfil de cor, secagem, aderência, comportamento do branco quando pertinente,
manutenção e suporte, sem prometer resultados universais.
Compatibilidade depende de equipamento, cabeça, alimentação, firmware e processo.
NUNCA afirme compatibilidade apenas porque a tecnologia é semelhante.
Não recomende misturar tintas nem trocar fornecedor sem avaliar limpeza,
transição, perfil de cor e procedimentos técnicos aprovados.
Preço por litro e economia devem vir de fonte comercial atual e comparável.
Não invente consumo, custo por metro, durabilidade, rendimento ou estoque.
Se o cliente pedir tinta, mantenha o foco em ajudá-lo a comprar a tinta correta.
"""
