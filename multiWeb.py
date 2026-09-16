# =============================================================================
# ZEARCH WEB - ZW-1 PROFESSIONAL
# Servidor Flask para deploy no Render
# =============================================================================

import os
import re
import json
import time
import psutil
from datetime import datetime
from urllib.parse import quote_plus

from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# =============================================================================
# CONFIGURAÇÃO GERAL
# =============================================================================

# Flask app (o Render vai procurar por `app` no módulo)
app = Flask(__name__)

# Porta padrão do Render é 10000, mas cai pra 5000 em dev local
PORT = int(os.environ.get("PORT", 5000))

# Em produção o browser roda sempre invisível
HEADLESS = True

# URL base da pesquisa no Bing (q = query)
URL_TEMPLATE = "https://www.bing.com/search?FORM=HDRSC1&q={q}"

# User-Agent mobile (Pixel 7 / Android 14 / Chrome 152)
UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Mobile Safari/537.36"
)

# Cookies fixos injetados no contexto (mantém sessão do Bing)
COOKIES = {
    "MUID": "1A41BE0E6A7362A90231A9916B8863E3",
    "MUIDB": "1A41BE0E6A7362A90231A9916B8863E3",
    "_EDGE_S": "SID=3B3D3BB40694616A20B32C61074E6057",
    "SRCHD": "AF=MA13QZ",
    "SRCHUID": "V=2&GUID=0BD21B52D09540DA843F3ED871C644F5&dmnchg=1",
    "SRCHUSR": "DOB=20260915&DS=1",
    "_SS": "PC=U645&SID=3B3D3BB40694616A20B32C61074E6057&R=6&RB=0&GB=0&RG=200&RP=3",
    "SRCHS": "PC=U645",
    "USRLOC": "HS=1&ELOC=LAT=-23.96164894104004|LON=-46.350807189941406|N=Santos%2C%20S%C3%A3o%20Paulo|ELT=4|",
}

# Banner curto de identificação (aparece na rota raiz)
BANNER = "ZEARCH WEB • ZW-1 • Professional"


# =============================================================================
# JS — ESTRATÉGIAS DE EXTRAÇÃO (executado dentro da página)
# =============================================================================
# Ordem:
#   1) Copilot     → #b_mcw / #copans_container → #ca_main
#   2) Sports      → .answer_container → #b_wpt_container → cards de jogo
#   3) Página toda → body.cloneNode(true)
# =============================================================================

JS_CAPTURAR = r"""
async () => {
  // ---------------------------------------------------------------------------
  // 1) Copilot — resposta gerada por IA
  // ---------------------------------------------------------------------------
  async function tryCopilot() {
    const wrapper =
        document.querySelector('#b_mcw') ||
        document.querySelector('#copans_container');
    if (!wrapper) return null;

    const caMain =
        wrapper.querySelector('#ca_main') ||
        wrapper.querySelector('.ca_main') ||
        wrapper;
    if (!caMain || (caMain.innerText || '').trim().length < 100) return null;

    // Só considera pronto quando o feedback (Curtir/Não gosto) já apareceu
    const feedbackPronto =
        !!wrapper.querySelector('.gs_secctrl, acf-thumbs-up-down-feedback, .ca_action_btn');
    if (!feedbackPronto) return null;

    // Se por acaso o wrapper contém bloco de esportes, não é Copilot
    if (caMain.querySelector('.bsp_mgz_schedule, .bsp_mgz_standings, #b_wpt_container')) {
      return null;
    }

    const clone = caMain.cloneNode(true);
    clone.querySelectorAll([
      'script', 'style', 'noscript', 'iframe',
      '.b_ad', '.sb_ad', 'footer', '.rms_img',
      '.gs_infobbl', '#gs_infobbl',
      '.gs_ai_disclaimer',
      '.gs_secctrl', '.gs_secctrl_items',
      'acf-thumbs-up-down-feedback',
      'acf-button-standard',
      '.b_acf_answer_expansion_control',
      '.b_module_expansion_control',
      '.b_gs_top_gradient', '.b_gs_bottom_cover',
      '.gs_sm_cit', '.gs_sup_cit',
    ].join(',')).forEach(el => el.remove());

    let texto = (clone.innerText || clone.textContent || '').trim();

    const lixos = [
      /Este resumo foi gerado pela IA[^\n]*/gi,
      /Localize os links de origem[^\n]*/gi,
      /Saiba mais sobre os resultados[^\n]*/gi,
      /como o\s+Bing entrega os resultados da pesquisa/gi,
      /Curtir\s*Não gosto/gi,
      /Com base em fontes/gi,
      /^\s*Saiba mais\s*$/gim,
    ];
    for (const r of lixos) texto = texto.replace(r, '');
    texto = texto.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();

    const links = Array.from(wrapper.querySelectorAll('a'))
      .filter(a => a.href && a.href.startsWith('http') && !a.href.includes('bing.com'))
      .map(a => a.href);
    const sources = [...new Set(links)];

    return { modo: 'copilot', text: texto, sources: sources, jogos: [] };
  }

  // ---------------------------------------------------------------------------
  // 2) Sports — jogos (futebol, etc)
  // ---------------------------------------------------------------------------
  async function trySports() {
    const ac = document.querySelector('.answer_container');
    if (!ac) return null;
    const container = ac.querySelector('#b_wpt_container') || ac;
    if (!container) return null;

    const jogos = [];
    const cards = container.querySelectorAll('.bsp_schedule_mtch_crd .bsp_match_card');

    cards.forEach(card => {
      try {
        const compEl = card.querySelector('.bsp_mtc_tps div');
        const competicao = compEl ? (compEl.getAttribute('title') || compEl.innerText.trim()) : '';

        const times = card.querySelectorAll('.bsp_team');
        if (times.length < 2) return;

        const time_casa = (times[0].querySelector('.team-name-ellipsis')?.innerText || '').trim();
        const time_fora = (times[1].querySelector('.team-name-ellipsis')?.innerText || '').trim();

        const scores = card.querySelectorAll('.bsp_team_scr');
        const placar_casa = scores[0] ? scores[0].innerText.trim() : '';
        const placar_fora = scores[1] ? scores[1].innerText.trim() : '';

        const status = (card.querySelector('.bsp_game_info > div:first-child')?.innerText || '').trim();
        const data = (card.querySelector('.bsp_game_time')?.innerText || '').trim();

        jogos.push({
          competicao,
          time_casa,
          placar: `${placar_casa} - ${placar_fora}`,
          time_fora,
          status,
          data,
          horario: 'N/A'
        });
      } catch (e) {}
    });

    // Fallback via linhas de texto
    if (jogos.length === 0) {
      const rawLines = container.innerText.split('\n')
          .map(s => s.trim()).filter(s => s !== '');
      if (rawLines.length < 10) return null;
      let i = 0;
      while (i < rawLines.length) {
        const linha = rawLines[i];
        if (linha.includes('·') || linha.includes('Semana') || linha.includes('final')) {
          try {
            const temHorario = rawLines[i + 7] && rawLines[i + 7].includes(':');
            jogos.push({
              competicao: linha,
              time_casa: rawLines[i + 1],
              time_fora: rawLines[i + 2],
              placar: `${rawLines[i + 3]} - ${rawLines[i + 4]}`,
              status: rawLines[i + 5],
              data: rawLines[i + 6],
              horario: temHorario ? rawLines[i + 7] : 'N/A'
            });
            i += temHorario ? 8 : 7;
          } catch (e) { i++; }
        } else { i++; }
      }
    }

    if (jogos.length === 0) return null;

    return {
      modo: 'sports',
      text: container.innerText.trim(),
      sources: [],
      jogos: jogos
    };
  }

  // ---------------------------------------------------------------------------
  // 3) Último recurso — página inteira
  // ---------------------------------------------------------------------------
  async function tryWholePage() {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll([
      'script', 'style', 'noscript', 'iframe', 'svg', 'head', 'meta',
      'link', 'template', 'object', 'embed', 'canvas',
      '.b_ad', '.sb_ad', '.b_adTop', '.b_adBottom',
      '.rms_img', 'img', 'video', 'audio',
      '#b_header', '#b_footer', 'header', 'nav', 'footer',
      '.b_hide', '[aria-hidden="true"]',
    ].join(',')).forEach(el => el.remove());

    let texto = (clone.innerText || clone.textContent || '').trim();

    const lixos = [
      /Este resumo foi gerado pela IA[^\n]*/gi,
      /Localize os links de origem[^\n]*/gi,
      /Saiba mais sobre os resultados[^\n]*/gi,
      /como o\s+Bing entrega os resultados da pesquisa/gi,
      /Curtir\s*Não gosto/gi,
      /Com base em fontes/gi,
      /^\s*Saiba mais\s*$/gim,
      /^\s*Veja mais\s*$/gim,
      /^\s*Ver mais\s*$/gim,
      /^\s*Feedback\s*$/gim,
    ];
    for (const r of lixos) texto = texto.replace(r, '');

    texto = texto.replace(/[ \t]+/g, ' ')
                 .replace(/\n[ \t]*\n[ \t]*\n+/g, '\n\n')
                 .trim();

    if (texto.length < 50) return null;

    const links = Array.from(document.body.querySelectorAll('a'))
      .filter(a => a.href && a.href.startsWith('http') && !a.href.includes('bing.com'))
      .map(a => a.href);
    const sources = [...new Set(links)];

    return { modo: 'pagina', text: texto, sources: sources, jogos: [] };
  }

  // ---------------------------------------------------------------------------
  // Loop de tentativas: Copilot (10s) → Sports → Página inteira
  // ---------------------------------------------------------------------------
  return await new Promise((resolve) => {
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;

      const c = await tryCopilot();
      if (c) { clearInterval(interval); resolve(c); return; }

      if (attempts > 20) {
        const s = await trySports();
        if (s) { clearInterval(interval); resolve(s); return; }

        const p = await tryWholePage();
        clearInterval(interval);
        resolve(p || { modo: 'nenhum', text: '', sources: [], jogos: [] });
      }
    }, 500);
  });
}
"""


# =============================================================================
# HELPERS
# =============================================================================

def limpar_texto(texto: str) -> str:
    """Remove linhas vazias duplicadas e normaliza espaços em branco."""
    linhas, vazias = [], 0
    for linha in texto.splitlines():
        linha = linha.rstrip()
        if not linha.strip():
            vazias += 1
            if vazias <= 1:
                linhas.append("")
        else:
            vazias = 0
            linhas.append(linha)
    return "\n".join(linhas).strip()


def ram_atual_mb() -> float:
    """RAM do próprio processo Python em MB."""
    return psutil.Process().memory_info().rss / 1024 / 1024


def ram_sistema_mb():
    """RAM usada/total do sistema em MB."""
    vm = psutil.virtual_memory()
    return vm.used / 1024 / 1024, vm.total / 1024 / 1024


def ram_browsers_mb() -> float:
    """Soma a RAM de todos os processos Chromium/Chrome em MB."""
    total = 0.0
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            nome = (p.info["name"] or "").lower()
            if "chrome" in nome or "chromium" in nome:
                total += p.info["memory_info"].rss / 1024 / 1024
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def abrir_browser(p):
    """
    Abre um Chromium headless (invisível) configurado para mobile.
    Retorna (browser, context, page).
    """
    browser = p.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-gpu",                 # sem GPU (servidor não tem)
            "--no-sandbox",                  # necessário em containers
            "--disable-dev-shm-usage",       # evita erro de /dev/shm pequeno
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--no-first-run",
            "--disable-features=Translate,BackForwardCache",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ],
    )
    context = browser.new_context(
        user_agent=UA_MOBILE,
        viewport={"width": 412, "height": 915},   # Pixel 7
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        locale="pt-BR",
        java_script_enabled=True,
    )

    # Cookies injetados no domínio .bing.com
    context.add_cookies([
        {"name": k, "value": v, "domain": ".bing.com", "path": "/"}
        for k, v in COOKIES.items()
    ])

    page = context.new_page()
    return browser, context, page


# =============================================================================
# NÚCLEO DA PESQUISA
# =============================================================================

def executar_pesquisa(query: str) -> dict:
    """
    Faz a pesquisa completa:
      - abre Chromium headless
      - navega até a URL de busca
      - roda o JS de extração (Copilot → Sports → Página inteira)
      - fecha o browser
      - retorna dict com modo/texto/fontes/jogos/métricas
    """
    url = URL_TEMPLATE.format(q=quote_plus(query))

    # Métricas iniciais
    ram_ini_script = ram_atual_mb()
    sys_ini, sys_total = ram_sistema_mb()
    t_ini = time.time()

    p = sync_playwright().start()
    browser, context, page = abrir_browser(p)

    try:
        # Navega para a página de busca (com timeout folgado pra Render)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Aguarda + executa o JS de extração
        resultado = page.evaluate(JS_CAPTURAR)
    except Exception as e:
        resultado = {
            "modo": "erro",
            "text": f"Falha na extração: {e}",
            "sources": [],
            "jogos": [],
        }
    finally:
        # Sempre fecha browser para não vazar processo
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    # Métricas finais
    t_total = time.time() - t_ini
    ram_fim_script = ram_atual_mb()
    sys_fim, _ = ram_sistema_mb()
    ram_chrome = ram_browsers_mb()

    # Normaliza texto
    modo = (resultado or {}).get("modo", "nenhum")
    texto = limpar_texto((resultado or {}).get("text", "") or "")
    fontes = (resultado or {}).get("sources", []) or []
    jogos = (resultado or {}).get("jogos", []) or []

    return {
        "query": query,
        "url": url,
        "modo": modo,
        "texto": texto,
        "fontes": fontes,
        "jogos": jogos,
        "metricas": {
            "tempo_s": round(t_total, 3),
            "ram_script_mb_ini": round(ram_ini_script, 1),
            "ram_script_mb_fim": round(ram_fim_script, 1),
            "ram_script_delta_mb": round(ram_fim_script - ram_ini_script, 1),
            "ram_sistema_mb_ini": round(sys_ini, 1),
            "ram_sistema_mb_fim": round(sys_fim, 1),
            "ram_sistema_total_mb": round(sys_total, 1),
            "ram_chromium_mb_fim": round(ram_chrome, 1),
        },
    }


# =============================================================================
# ROTAS FLASK
# =============================================================================

@app.route("/", methods=["GET"])
def raiz():
    """Rota de status — confirma que o servidor está no ar."""
    return jsonify({
        "status": "ok",
        "service": BANNER,
        "endpoints": {
            "GET /": "status",
            "GET /search?q=<termo>": "faz a pesquisa e devolve JSON",
        },
    })


@app.route("/search", methods=["GET"])
def rota_search():
    """
    Pesquisa via query string.
    Exemplo: /search?q=nobru
    """
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({
            "error": "Parâmetro 'q' é obrigatório",
            "example": "/search?q=nobru",
        }), 400

    dados = executar_pesquisa(query)

    # Resposta padrão: campo "Result" com o texto extraído
    return jsonify({
        "Result": dados["texto"],
        "Meta": {
            "query": dados["query"],
            "url": dados["url"],
            "modo": dados["modo"],
            "chars": len(dados["texto"]),
            "fontes": dados["fontes"],
            "jogos": dados["jogos"],
            "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metricas": dados["metricas"],
        },
    })


@app.route("/search", methods=["POST"])
def rota_search_post():
    """
    Pesquisa via JSON no corpo.
    Exemplo: POST /search  {"q": "nobru"}
    """
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or payload.get("query") or "").strip()

    if not query:
        return jsonify({
            "error": "Campo 'q' (ou 'query') é obrigatório no JSON",
            "example": {"q": "nobru"},
        }), 400

    dados = executar_pesquisa(query)

    return jsonify({
        "Result": dados["texto"],
        "Meta": {
            "query": dados["query"],
            "url": dados["url"],
            "modo": dados["modo"],
            "chars": len(dados["texto"]),
            "fontes": dados["fontes"],
            "jogos": dados["jogos"],
            "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metricas": dados["metricas"],
        },
    })


# =============================================================================
# ENTRYPOINT
# =============================================================================
# Render usa `gunicorn app:app` (ou similar) e define $PORT.
# Aqui só rodamos localmente se executado direto.
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)