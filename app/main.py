from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
from app.config import get_settings
from app.api.webhooks import router as webhook_router
from app.services.followup_service import start_followup_service
from app.models.database import SessionLocal, Conversation, Lead, LeadState
from datetime import datetime, timedelta
from collections import defaultdict
import re

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_followup_service()
    yield


app = FastAPI(
    title="DOSS AI BRAIN",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def health_check():
    return {
        "status": "online",
        "system": "DOSS AI BRAIN",
        "environment": settings.ENVIRONMENT,
    }


# ─────────────────────────────────────────────────────────────────────────────
# API de dados para o dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/dashboard-data")
def dashboard_data():
    db = SessionLocal()
    try:
        agora = datetime.utcnow()
        hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        semana = agora - timedelta(days=7)
        mes = agora - timedelta(days=30)

        leads = db.query(Lead).all()
        states = db.query(LeadState).all()
        states_map = {s.phone: s for s in states}

        # ── KPIs ──────────────────────────────────────────────────────────
        total_leads = len(leads)
        leads_hoje = sum(1 for l in leads if l.id and _created_today(db, l.phone, hoje))
        leads_semana = sum(1 for l in leads if _created_after(db, l.phone, semana))
        leads_mes = sum(1 for l in leads if _created_after(db, l.phone, mes))

        qualificados = sum(1 for s in states if s.stage == "closed")
        ativos = sum(1 for s in states if s.stage == "active")
        aguardando_cnpj = sum(1 for s in states if s.stage in ("awaiting_cnpj", "cnpj_received"))
        followup_ativo = sum(1 for s in states if s.followup_step and s.followup_step > 0 and s.stage not in ("closed", "followup_closed"))

        taxa_qualificacao = round((qualificados / total_leads * 100), 1) if total_leads > 0 else 0

        # ── Leads por dia (últimos 30 dias) ───────────────────────────────
        leads_por_dia = defaultdict(int)
        for lead in leads:
            primeira = db.query(Conversation).filter(
                Conversation.phone == lead.phone
            ).order_by(Conversation.created_at.asc()).first()
            if primeira and primeira.created_at >= mes:
                dia = primeira.created_at.strftime("%d/%m")
                leads_por_dia[dia] += 1

        # Gera os últimos 30 dias em ordem
        dias_labels = []
        dias_valores = []
        for i in range(29, -1, -1):
            d = (agora - timedelta(days=i)).strftime("%d/%m")
            dias_labels.append(d)
            dias_valores.append(leads_por_dia.get(d, 0))

        # ── Distribuição de stages ────────────────────────────────────────
        stage_count = defaultdict(int)
        for s in states:
            stage_count[s.stage] += 1

        stage_labels = ["Ativo", "Aguard. CNPJ", "CNPJ Recebido", "Qualificado", "Follow-up Encerrado"]
        stage_valores = [
            stage_count.get("active", 0),
            stage_count.get("awaiting_cnpj", 0),
            stage_count.get("cnpj_received", 0),
            stage_count.get("closed", 0),
            stage_count.get("followup_closed", 0),
        ]

        # ── Produtos mais procurados ──────────────────────────────────────
        PRODUTO_MAP = {
            "1908": "DG 1908i", "3204": "DG 3204i", "3202": "DG 3202i",
            "1904": "DG 1904i", "1802": "DG 1802i", "1801": "DG 1801i",
            "dtf uv": "DTF UV", "dtf 60": "DTF 6002", "dtf 30": "DTF 3002",
            "dtf": "DTF Têxtil", "flatbed": "Flatbed UV", "laser": "Laser",
            "sublimacao": "Sublimática", "eco solvente": "Eco Solvente",
        }
        produto_count = defaultdict(int)
        convs_all = db.query(Conversation).filter(
            Conversation.role == "user",
            Conversation.created_at >= mes,
            ~Conversation.content.like("[%")
        ).all()

        for c in convs_all:
            txt = c.content.lower()
            for kw, nome in PRODUTO_MAP.items():
                if kw in txt:
                    produto_count[nome] += 1
                    break

        produtos_sorted = sorted(produto_count.items(), key=lambda x: x[1], reverse=True)[:8]
        produto_labels = [p[0] for p in produtos_sorted]
        produto_valores = [p[1] for p in produtos_sorted]

        # ── Origem dos leads ──────────────────────────────────────────────
        origem_count = defaultdict(int)
        for lead in leads:
            primeira = db.query(Conversation).filter(
                Conversation.phone == lead.phone,
                Conversation.role == "user",
                ~Conversation.content.like("[%")
            ).order_by(Conversation.created_at.asc()).first()

            if not primeira:
                origem_count["WhatsApp Direto"] += 1
                continue

            txt = primeira.content.lower()
            if any(k in txt for k in ["instagram", "insta", "@"]):
                origem_count["Instagram"] += 1
            elif any(k in txt for k in ["facebook", " fb "]):
                origem_count["Facebook"] += 1
            elif "google" in txt:
                origem_count["Google"] += 1
            elif "site" in txt:
                origem_count["Site"] += 1
            elif any(k in txt for k in ["indicacao", "indicado", "indicação"]):
                origem_count["Indicação"] += 1
            else:
                campanha = db.query(Conversation).filter(
                    Conversation.phone == lead.phone,
                    Conversation.content.like("[CAMPANHA:%")
                ).first()
                if campanha:
                    origem_count["Campanha"] += 1
                else:
                    origem_count["WhatsApp Direto"] += 1

        # ── Atividade por hora do dia ─────────────────────────────────────
        hora_count = defaultdict(int)
        convs_recentes = db.query(Conversation).filter(
            Conversation.role == "user",
            Conversation.created_at >= semana,
            ~Conversation.content.like("[%")
        ).all()
        for c in convs_recentes:
            # Converte UTC para Brasília (UTC-3)
            hora_brasilia = (c.created_at - timedelta(hours=3)).hour
            hora_count[hora_brasilia] += 1

        hora_labels = [f"{h:02d}h" for h in range(24)]
        hora_valores = [hora_count.get(h, 0) for h in range(24)]

        # ── Follow-up performance ─────────────────────────────────────────
        fu_responderam = 0
        fu_ignoraram = 0
        for s in states:
            if (s.followup_step or 0) > 0:
                if s.stage == "active":
                    fu_responderam += 1
                else:
                    fu_ignoraram += 1

        # ── Média de mensagens por conversa ───────────────────────────────
        total_msgs = 0
        count_convs = 0
        for lead in leads:
            n = db.query(Conversation).filter(
                Conversation.phone == lead.phone,
                Conversation.role == "user",
                ~Conversation.content.like("[%")
            ).count()
            if n > 0:
                total_msgs += n
                count_convs += 1
        media_msgs = round(total_msgs / count_convs, 1) if count_convs > 0 else 0

        # ── Leads recentes para tabela ────────────────────────────────────
        leads_recentes = []
        for lead in sorted(leads, key=lambda l: l.id or 0, reverse=True)[:15]:
            s = states_map.get(lead.phone)
            primeira = db.query(Conversation).filter(
                Conversation.phone == lead.phone
            ).order_by(Conversation.created_at.asc()).first()

            ultima = db.query(Conversation).filter(
                Conversation.phone == lead.phone
            ).order_by(Conversation.created_at.desc()).first()

            diff = agora - ultima.created_at if ultima else timedelta(0)
            mins = int(diff.total_seconds() // 60)
            tempo_str = f"{mins}min" if mins < 60 else f"{mins//60}h"

            # Detecta produto
            prod = ""
            if s:
                convs_lead = db.query(Conversation).filter(
                    Conversation.phone == lead.phone,
                    Conversation.role == "user",
                    ~Conversation.content.like("[%")
                ).all()
                txt = " ".join(c.content.lower() for c in convs_lead)
                for kw, nome in PRODUTO_MAP.items():
                    if kw in txt:
                        prod = nome
                        break

            leads_recentes.append({
                "phone": lead.phone[-4:],  # só últimos 4 dígitos para privacidade
                "nome": lead.name or "—",
                "cidade": lead.city or "—",
                "stage": s.stage if s else "—",
                "produto": prod or "—",
                "fu": s.followup_step if s else 0,
                "tempo": tempo_str,
                "tem_cnpj": bool(s and s.cnpj) if s else False,
                "tem_email": bool(s and s.email) if s else False,
            })

        return {
            "kpis": {
                "total_leads": total_leads,
                "leads_hoje": leads_hoje,
                "leads_semana": leads_semana,
                "leads_mes": leads_mes,
                "qualificados": qualificados,
                "ativos": ativos,
                "aguardando_cnpj": aguardando_cnpj,
                "followup_ativo": followup_ativo,
                "taxa_qualificacao": taxa_qualificacao,
                "media_msgs": media_msgs,
                "fu_responderam": fu_responderam,
                "fu_ignoraram": fu_ignoraram,
            },
            "graficos": {
                "leads_por_dia": {"labels": dias_labels, "valores": dias_valores},
                "stages": {"labels": stage_labels, "valores": stage_valores},
                "produtos": {"labels": produto_labels, "valores": produto_valores},
                "origens": {"labels": list(origem_count.keys()), "valores": list(origem_count.values())},
                "horas": {"labels": hora_labels, "valores": hora_valores},
            },
            "leads_recentes": leads_recentes,
            "atualizado_em": agora.strftime("%d/%m/%Y %H:%M:%S"),
        }
    finally:
        db.close()


def _created_today(db, phone: str, hoje: datetime) -> bool:
    c = db.query(Conversation).filter(
        Conversation.phone == phone,
        Conversation.created_at >= hoje
    ).first()
    return c is not None

def _created_after(db, phone: str, after: datetime) -> bool:
    c = db.query(Conversation).filter(
        Conversation.phone == phone,
        Conversation.created_at >= after
    ).first()
    return c is not None


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard visual
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bruno IA — Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;600;700;800&display=swap');

  :root {
    --bg: #0a0a0f;
    --surface: #111118;
    --surface2: #1a1a26;
    --border: #2a2a3a;
    --accent: #00e5ff;
    --accent2: #ff6b35;
    --accent3: #7c3aed;
    --green: #00ff87;
    --yellow: #ffd60a;
    --red: #ff4757;
    --text: #e8e8f0;
    --muted: #6b6b80;
    --font-display: 'Syne', sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Grid noise texture */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(0,229,255,0.02) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,229,255,0.02) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  .container {
    position: relative;
    z-index: 1;
    max-width: 1600px;
    margin: 0 auto;
    padding: 24px;
  }

  /* Header */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--border);
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .logo-dot {
    width: 12px;
    height: 12px;
    background: var(--accent);
    border-radius: 50%;
    box-shadow: 0 0 12px var(--accent);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.6; transform: scale(0.9); }
  }

  .header h1 {
    font-family: var(--font-display);
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: var(--text);
  }

  .header h1 span { color: var(--accent); }

  .header-right {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .badge {
    padding: 4px 12px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }

  .badge-live {
    background: rgba(0,255,135,0.1);
    border: 1px solid rgba(0,255,135,0.3);
    color: var(--green);
  }

  .last-update {
    font-size: 11px;
    color: var(--muted);
  }

  .btn-refresh {
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--accent);
    padding: 6px 16px;
    border-radius: 6px;
    font-family: var(--font-mono);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
  }

  .btn-refresh:hover {
    background: rgba(0,229,255,0.1);
    border-color: var(--accent);
  }

  .btn-monitor {
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--yellow);
    padding: 6px 16px;
    border-radius: 6px;
    font-family: var(--font-mono);
    font-size: 12px;
    cursor: pointer;
    text-decoration: none;
    transition: all 0.2s;
  }

  .btn-monitor:hover {
    background: rgba(255,214,10,0.1);
    border-color: var(--yellow);
  }

  /* KPI Grid */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }

  .kpi-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    position: relative;
    overflow: hidden;
    transition: transform 0.2s, border-color 0.2s;
  }

  .kpi-card:hover {
    transform: translateY(-2px);
    border-color: var(--accent);
  }

  .kpi-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
  }

  .kpi-card.accent::before { background: var(--accent); }
  .kpi-card.green::before { background: var(--green); }
  .kpi-card.orange::before { background: var(--accent2); }
  .kpi-card.purple::before { background: var(--accent3); }
  .kpi-card.yellow::before { background: var(--yellow); }

  .kpi-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 8px;
  }

  .kpi-value {
    font-family: var(--font-display);
    font-size: 36px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 4px;
  }

  .kpi-card.accent .kpi-value { color: var(--accent); }
  .kpi-card.green .kpi-value { color: var(--green); }
  .kpi-card.orange .kpi-value { color: var(--accent2); }
  .kpi-card.purple .kpi-value { color: var(--accent3); }
  .kpi-card.yellow .kpi-value { color: var(--yellow); }

  .kpi-sub {
    font-size: 11px;
    color: var(--muted);
  }

  /* Charts Grid */
  .charts-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }

  .charts-grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }

  .chart-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
  }

  .chart-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .chart-title::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent);
  }

  .chart-container {
    position: relative;
    height: 220px;
  }

  .chart-container-tall {
    position: relative;
    height: 260px;
  }

  /* Tabela */
  .table-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 16px;
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  thead th {
    text-align: left;
    padding: 8px 12px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }

  tbody tr {
    border-bottom: 1px solid rgba(42,42,58,0.5);
    transition: background 0.15s;
  }

  tbody tr:hover { background: var(--surface2); }
  tbody tr:last-child { border-bottom: none; }

  tbody td {
    padding: 10px 12px;
    vertical-align: middle;
  }

  .stage-badge {
    padding: 2px 8px;
    border-radius: 100px;
    font-size: 10px;
    font-weight: 600;
  }

  .stage-active { background: rgba(0,229,255,0.1); color: var(--accent); border: 1px solid rgba(0,229,255,0.2); }
  .stage-closed { background: rgba(0,255,135,0.1); color: var(--green); border: 1px solid rgba(0,255,135,0.2); }
  .stage-cnpj { background: rgba(255,214,10,0.1); color: var(--yellow); border: 1px solid rgba(255,214,10,0.2); }
  .stage-followup { background: rgba(255,107,53,0.1); color: var(--accent2); border: 1px solid rgba(255,107,53,0.2); }
  .stage-other { background: rgba(107,107,128,0.1); color: var(--muted); border: 1px solid rgba(107,107,128,0.2); }

  .check { color: var(--green); }
  .cross { color: var(--red); opacity: 0.4; }

  /* Follow-up row */
  .fu-bar {
    display: flex;
    gap: 3px;
    align-items: center;
  }

  .fu-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--border);
  }

  .fu-dot.active { background: var(--accent2); box-shadow: 0 0 4px var(--accent2); }

  /* Loading */
  .loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 200px;
    color: var(--muted);
    gap: 8px;
  }

  .spinner {
    width: 20px;
    height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* Error */
  .error-msg {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100px;
    color: var(--red);
    font-size: 13px;
  }

  /* Responsive */
  @media (max-width: 1024px) {
    .charts-grid { grid-template-columns: 1fr; }
    .charts-grid-3 { grid-template-columns: 1fr; }
  }

  @media (max-width: 600px) {
    .kpi-grid { grid-template-columns: repeat(2, 1fr); }
    .header { flex-direction: column; gap: 12px; align-items: flex-start; }
  }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div class="header-left">
      <div class="logo-dot"></div>
      <h1>Bruno IA — <span>Dashboard</span></h1>
    </div>
    <div class="header-right">
      <span class="badge badge-live">● Live</span>
      <span class="last-update" id="last-update">Carregando...</span>
      <a href="/monitor" class="btn-monitor">Monitor</a>
      <button class="btn-refresh" onclick="loadData()">↻ Atualizar</button>
    </div>
  </div>

  <!-- KPIs -->
  <div class="kpi-grid" id="kpi-grid">
    <div class="loading"><div class="spinner"></div> Carregando dados...</div>
  </div>

  <!-- Leads por dia + Stages -->
  <div class="charts-grid">
    <div class="chart-card">
      <div class="chart-title">Leads por Dia (últimos 30 dias)</div>
      <div class="chart-container-tall">
        <canvas id="chartLeadsDia"></canvas>
      </div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Funil de Etapas</div>
      <div class="chart-container-tall">
        <canvas id="chartStages"></canvas>
      </div>
    </div>
  </div>

  <!-- Produtos + Origens + Horas -->
  <div class="charts-grid-3">
    <div class="chart-card">
      <div class="chart-title">Produtos Mais Procurados (30d)</div>
      <div class="chart-container">
        <canvas id="chartProdutos"></canvas>
      </div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Origem dos Leads</div>
      <div class="chart-container">
        <canvas id="chartOrigens"></canvas>
      </div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Atividade por Hora (7d, Brasília)</div>
      <div class="chart-container">
        <canvas id="chartHoras"></canvas>
      </div>
    </div>
  </div>

  <!-- Tabela de leads recentes -->
  <div class="table-card">
    <div class="chart-title" style="margin-bottom:16px">Leads Recentes</div>
    <div id="tabela-leads">
      <div class="loading"><div class="spinner"></div></div>
    </div>
  </div>

</div>

<script>
let charts = {};

const CORES = {
  accent: '#00e5ff',
  accent2: '#ff6b35',
  accent3: '#7c3aed',
  green: '#00ff87',
  yellow: '#ffd60a',
  red: '#ff4757',
  muted: '#6b6b80',
};

const PALETTE = [
  '#00e5ff', '#ff6b35', '#7c3aed', '#00ff87',
  '#ffd60a', '#ff4757', '#00b4d8', '#e040fb'
];

Chart.defaults.color = '#6b6b80';
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 11;

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function stageClass(stage) {
  if (stage === 'active') return 'stage-active';
  if (stage === 'closed') return 'stage-closed';
  if (stage === 'awaiting_cnpj' || stage === 'cnpj_received') return 'stage-cnpj';
  if (stage === 'followup_closed') return 'stage-followup';
  return 'stage-other';
}

function stageLabel(stage) {
  const map = {
    active: 'Ativo',
    closed: 'Qualificado',
    awaiting_cnpj: 'Aguard. CNPJ',
    cnpj_received: 'CNPJ OK',
    followup_closed: 'FU Encerrado',
  };
  return map[stage] || stage;
}

function fuDots(step) {
  let html = '<div class="fu-bar">';
  for (let i = 1; i <= 5; i++) {
    html += `<div class="fu-dot ${i <= step ? 'active' : ''}"></div>`;
  }
  html += '</div>';
  return html;
}

async function loadData() {
  try {
    const res = await fetch('/api/dashboard-data');
    const d = await res.json();

    document.getElementById('last-update').textContent = 'Atualizado: ' + d.atualizado_em;

    renderKPIs(d.kpis);
    renderLeadsDia(d.graficos.leads_por_dia);
    renderStages(d.graficos.stages);
    renderProdutos(d.graficos.produtos);
    renderOrigens(d.graficos.origens);
    renderHoras(d.graficos.horas);
    renderTabela(d.leads_recentes);

  } catch(e) {
    console.error(e);
    document.getElementById('kpi-grid').innerHTML = '<div class="error-msg">Erro ao carregar dados. Verifique a conexão.</div>';
  }
}

function renderKPIs(k) {
  document.getElementById('kpi-grid').innerHTML = `
    <div class="kpi-card accent">
      <div class="kpi-label">Total de Leads</div>
      <div class="kpi-value">${k.total_leads}</div>
      <div class="kpi-sub">Todos os tempos</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">Hoje</div>
      <div class="kpi-value">${k.leads_hoje}</div>
      <div class="kpi-sub">${k.leads_semana} esta semana</div>
    </div>
    <div class="kpi-card orange">
      <div class="kpi-label">Este Mês</div>
      <div class="kpi-value">${k.leads_mes}</div>
      <div class="kpi-sub">últimos 30 dias</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">Qualificados</div>
      <div class="kpi-value">${k.qualificados}</div>
      <div class="kpi-sub">card criado no CRM</div>
    </div>
    <div class="kpi-card accent">
      <div class="kpi-label">Taxa Qualif.</div>
      <div class="kpi-value">${k.taxa_qualificacao}%</div>
      <div class="kpi-sub">leads → CRM</div>
    </div>
    <div class="kpi-card yellow">
      <div class="kpi-label">Ativos Agora</div>
      <div class="kpi-value">${k.ativos}</div>
      <div class="kpi-sub">${k.aguardando_cnpj} aguard. CNPJ</div>
    </div>
    <div class="kpi-card orange">
      <div class="kpi-label">Follow-up Ativo</div>
      <div class="kpi-value">${k.followup_ativo}</div>
      <div class="kpi-sub">${k.fu_responderam} responderam</div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">Média Msgs/Conv</div>
      <div class="kpi-value">${k.media_msgs}</div>
      <div class="kpi-sub">mensagens por lead</div>
    </div>
  `;
}

function renderLeadsDia(data) {
  destroyChart('leadsDia');
  const ctx = document.getElementById('chartLeadsDia').getContext('2d');
  charts['leadsDia'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.labels,
      datasets: [{
        label: 'Leads',
        data: data.valores,
        borderColor: CORES.accent,
        backgroundColor: 'rgba(0,229,255,0.08)',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: CORES.accent,
        fill: true,
        tension: 0.4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { color: 'rgba(42,42,58,0.5)' },
          ticks: { maxRotation: 0, maxTicksLimit: 10 }
        },
        y: {
          grid: { color: 'rgba(42,42,58,0.5)' },
          beginAtZero: true,
          ticks: { stepSize: 1 }
        }
      }
    }
  });
}

function renderStages(data) {
  destroyChart('stages');
  const ctx = document.getElementById('chartStages').getContext('2d');
  charts['stages'] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: data.labels,
      datasets: [{
        data: data.valores,
        backgroundColor: PALETTE,
        borderColor: '#0a0a0f',
        borderWidth: 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { padding: 12, boxWidth: 10, usePointStyle: true }
        }
      },
      cutout: '60%',
    }
  });
}

function renderProdutos(data) {
  destroyChart('produtos');
  if (!data.labels.length) return;
  const ctx = document.getElementById('chartProdutos').getContext('2d');
  charts['produtos'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [{
        label: 'Menções',
        data: data.valores,
        backgroundColor: PALETTE,
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(42,42,58,0.5)' }, beginAtZero: true, ticks: { stepSize: 1 } },
        y: { grid: { display: false } }
      }
    }
  });
}

function renderOrigens(data) {
  destroyChart('origens');
  if (!data.labels.length) return;
  const ctx = document.getElementById('chartOrigens').getContext('2d');
  charts['origens'] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: data.labels,
      datasets: [{
        data: data.valores,
        backgroundColor: PALETTE,
        borderColor: '#0a0a0f',
        borderWidth: 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { padding: 10, boxWidth: 10, usePointStyle: true }
        }
      },
      cutout: '55%',
    }
  });
}

function renderHoras(data) {
  destroyChart('horas');
  const ctx = document.getElementById('chartHoras').getContext('2d');
  const maxVal = Math.max(...data.valores);
  const colors = data.valores.map(v => {
    const ratio = maxVal > 0 ? v / maxVal : 0;
    if (ratio > 0.7) return CORES.accent2;
    if (ratio > 0.3) return CORES.accent;
    return CORES.muted;
  });
  charts['horas'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [{
        label: 'Msgs',
        data: data.valores,
        backgroundColor: colors,
        borderRadius: 3,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 0, maxTicksLimit: 8 } },
        y: { grid: { color: 'rgba(42,42,58,0.5)' }, beginAtZero: true, ticks: { stepSize: 1 } }
      }
    }
  });
}

function renderTabela(leads) {
  if (!leads.length) {
    document.getElementById('tabela-leads').innerHTML = '<div class="error-msg" style="color: var(--muted)">Nenhum lead encontrado</div>';
    return;
  }

  let html = `
    <table>
      <thead>
        <tr>
          <th>Telefone</th>
          <th>Nome</th>
          <th>Cidade</th>
          <th>Etapa</th>
          <th>Produto</th>
          <th>Follow-up</th>
          <th>Email</th>
          <th>CNPJ</th>
          <th>Última msg</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const l of leads) {
    html += `
      <tr>
        <td style="color:var(--muted)">···${l.phone}</td>
        <td>${l.nome}</td>
        <td>${l.cidade}</td>
        <td><span class="stage-badge ${stageClass(l.stage)}">${stageLabel(l.stage)}</span></td>
        <td style="color:var(--accent)">${l.produto}</td>
        <td>${fuDots(l.fu)}</td>
        <td>${l.tem_email ? '<span class="check">✓</span>' : '<span class="cross">✗</span>'}</td>
        <td>${l.tem_cnpj ? '<span class="check">✓</span>' : '<span class="cross">✗</span>'}</td>
        <td style="color:var(--muted)">${l.tempo} atrás</td>
      </tr>
    `;
  }

  html += '</tbody></table>';
  document.getElementById('tabela-leads').innerHTML = html;
}

// Carrega na inicialização e a cada 60s
loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Monitor original (mantido intacto)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/monitor", response_class=HTMLResponse)
def monitor():
    """Painel de monitoramento de conversas em tempo real."""
    db = SessionLocal()
    try:
        corte = datetime.utcnow() - timedelta(hours=48)
        leads = db.query(Lead).all()

        html = """
        <html><head>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="15">
        <title>Bruno IA — Monitor</title>
        <style>
            body { font-family: monospace; background: #1a1a1a; color: #00ff00; padding: 20px; }
            h1 { color: #00ff00; }
            .lead { border: 1px solid #333; margin: 20px 0; padding: 15px; border-radius: 5px; }
            .lead-header { color: #ffff00; font-size: 14px; margin-bottom: 10px; }
            .msg-user { color: #00bfff; margin: 5px 0; }
            .msg-bruno { color: #00ff00; margin: 5px 0; padding-left: 20px; }
            .time { color: #666; font-size: 11px; }
            .total { color: #ff6600; margin-top: 20px; }
            hr { border-color: #333; }
        </style>
        </head><body>
        """
        html += f"<h1>🤖 Bruno IA — Monitor de Conversas</h1>"
        html += f"<p style='color:#666'>Atualiza a cada 15s | {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p><hr>"

        total = 0
        for lead in leads:
            convs = db.query(Conversation).filter(
                Conversation.phone == lead.phone,
                Conversation.created_at >= corte
            ).order_by(Conversation.created_at.asc()).all()

            if not convs:
                continue

            total += 1
            state = db.query(LeadState).filter(LeadState.phone == lead.phone).first()
            ultima = convs[-1]
            diff = datetime.utcnow() - ultima.created_at
            mins = int(diff.total_seconds() // 60)
            tempo = f"{mins}min atrás" if mins < 60 else f"{mins//60}h atrás"

            nome = lead.name or "Desconhecido"
            cidade = lead.city or "?"
            stage = state.stage if state else "—"
            email = state.email if state and state.email else "—"
            cnpj = state.cnpj if state and state.cnpj else "—"
            fu = f" | FU-{state.followup_step}" if state and state.followup_step else ""

            html += f"""<div class='lead'>
            <div class='lead-header'>
                📱 {lead.phone} | {nome} ({cidade}) | Stage: {stage}{fu}<br>
                Email: {email} | CNPJ: {cnpj} | Última msg: {tempo}
            </div>"""

            PREFIXOS = ("[SISTEMA", "[CAMPANHA", "[FOLLOWUP")
            msgs = [m for m in convs if not any(m.content.startswith(p) for p in PREFIXOS)][-15:]

            for msg in msgs:
                hora = msg.created_at.strftime("%H:%M")
                texto = msg.content.replace("<", "&lt;").replace(">", "&gt;")
                if msg.role == "user":
                    html += f"<div class='msg-user'><span class='time'>[{hora}]</span> 👤 {texto}</div>"
                else:
                    html += f"<div class='msg-bruno'><span class='time'>[{hora}]</span> 🤖 {texto}</div>"

            html += "</div>"

        html += f"<div class='total'>Total de conversas ativas (48h): {total}</div>"
        html += "</body></html>"
        return html
    finally:
        db.close()


app.include_router(webhook_router, prefix="/webhooks", tags=["Webhooks"])
