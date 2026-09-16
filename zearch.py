# =============================================================================
# ZEARCH WEB - ZW-1 PROFESSIONAL (Flash)
# Servidor Flask async + Playwright mobile + Render-ready
# =============================================================================

import os
import re
import sys
import time
import uuid
import base64
import json as _json
import asyncio
import logging
import threading
import psutil
from datetime import datetime
from urllib.parse import quote_plus
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from playwright.async_api import async_playwright

# =============================================================================
# 1. CONFIGURAÇÃO
# =============================================================================

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 5000))
HEADLESS = False

URL_TEMPLATE = "https://www.bing.com/search?FORM=HDRSC1&q={q}"

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Mobile Safari/537.36"
)

VIEWPORT = {"width": 412, "height": 915}
DEVICE_SCALE = 2.625

PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "./photos"))
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

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

BANNER = "ZEARCH WEB • ZW-1 • Professional (Flash)"


# =============================================================================
# 2. LOGGING COLORIDO
# =============================================================================


class ColorFormatter(logging.Formatter):
    CINZA = "\033[90m"
    AZUL = "\033[94m"
    VERDE = "\033[92m"
    AMARELO = "\033[93m"
    VERMELHO = "\033[91m"
    CIANO = "\033[96m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    def format(self, record):
        ts = self.formatTime(record, "%H:%M:%S")
        nivel = record.levelname
        cores = {
            "DEBUG": self.CINZA,
            "INFO": self.AZUL,
            "WARNING": self.AMARELO,
            "ERROR": self.VERMELHO,
            "CRITICAL": self.VERMELHO + self.BOLD,
        }
        cor = cores.get(nivel, self.RESET)
        msg = record.getMessage()
        msg = re.sub(r"\bREQ\b", f"{self.CIANO}REQ{self.RESET}", msg)
        msg = re.sub(r"\bRES\b", f"{self.VERDE}RES{self.RESET}", msg)
        msg = re.sub(r"\bERR\b", f"{self.VERMELHO}ERR{self.RESET}", msg)
        msg = re.sub(r"\bBOOT\b", f"{self.MAGENTA}BOOT{self.RESET}", msg)
        msg = re.sub(r"\bWARM\b", f"{self.MAGENTA}WARM{self.RESET}", msg)
        return f"{self.CINZA}[{ts}]{self.RESET} {cor}{nivel:<5}{self.RESET} {msg}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter())
log = logging.getLogger("zearch")
log.handlers = [handler]
log.setLevel(logging.INFO)
log.propagate = False


def ip_do_cliente() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "?"


# =============================================================================
# 3. JS — TRÊS ESTRATÉGIAS
#    1) IA (Markdown)  →  se achar #b_content com bloco de resposta
#    2) Jogo  (JSON)   →  se achar #b_wpt_container com cards de partidas
#    3) Página (txt)   →  fallback bruto de toda a página
# =============================================================================

JS_CAPTURAR = r"""
() => {

  // ===========================================================================
  // 3.1) IA — Resposta do Copilot convertida em Markdown
  // ===========================================================================
  function tryIA() {
    const content = document.querySelector('#b_content');
    if (!content) return null;

    const firstItem = content.querySelector(
      'div[id^="cplt_frame_"], li.b_ans, #ca_main, .answer_container'
    );
    if (!firstItem) return null;

    // Evita pegar Sports por engano — Sports tem #b_wpt_container
    if (firstItem.querySelector('#b_wpt_container')) return null;

    let source = firstItem;
    const iframe = firstItem.querySelector('iframe');
    if (iframe) {
      try { source = iframe.contentDocument.body; } catch (e) {}
    }

    const clone = source.cloneNode(true);
    clone.querySelectorAll([
      '.md_citlink', 'sup', '.b_cits', '.ca_action_menu', '.b_attribution',
      '.gs_cit', '.gs_cits', '.gs_cit_wrapper', '.gs_cit_cont',
      '.gs_cit_panel', '.gs_cit_panel_content', '.gs_cit_panel_header',
      '.bsp_cit_cont', '.b_genserp_citation_hover_md', '.cit_exp_cont',
      '.gs_cit_exp', '.gs_cit_exp_text', '.gs_cit_src', '.gs_cit_title',
      '.gs_cit_snippet', '.gs_cit_siteurl', '.gs_cit_title_text',
      '.gs_mdlink', '.gs_cit_txt', '.gs_sm_cit', '.gs_sup_cit',
      '.gs_readMoreFullBtn',
      '.gs_infobbl', '#gs_infobbl', '.gs_ai_disclaimer',
      '.gs_secctrl', '.gs_secctrl_items', '.acf_fdbk_ph',
      'acf-thumbs-up-down-feedback', 'acf-button-standard',
      '.b_acf_answer_expansion_control', '.b_module_expansion_control',
      '.b_acf_expansion_gradient_overlay', '.b_btnContainer',
      '.bsp_seemore', '.mag_st_header',
      '.b_wpt_header', '.bsp_mgz_header', '.bsp_mgzhdr_btns',
      '.b_wpt_attr', '.b_wpt_footer', '.b_gs_top_gradient', '.b_gs_bottom_cover',
      'script', 'style', 'noscript', 'iframe',
      '.b_ad', '.sb_ad',
      '#b_header', '#b_footer', 'header', 'nav', 'footer',
    ].join(',')).forEach(el => el.remove());

    let markdown = "";
    clone.querySelectorAll('h1, h2, h3, p, li, br').forEach(el => {
      let text = el.innerHTML || '';
      text = text.replace(/<strong[^>]*>(.*?)<\/strong>/gis, '**$1**');
      text = text.replace(/<b[^>]*>(.*?)<\/b>/gis, '**$1**');
      text = text.replace(/<em[^>]*>(.*?)<\/em>/gis, '*$1*');
      text = text.replace(/<i[^>]*>(.*?)<\/i>/gis, '*$1*');
      text = text.replace(/<[^>]*>/g, '');
      text = text.replace(/&nbsp;|›|»|&amp;/g, ' ');
      text = text.replace(/\s+/g, ' ').trim();
      if (!text) return;

      const tag = el.tagName;
      if (tag === 'H1') markdown += `# ${text}\n\n`;
      else if (tag === 'H2') markdown += `## ${text}\n\n`;
      else if (tag === 'H3') markdown += `### ${text}\n\n`;
      else if (tag === 'P' || tag === 'BR') markdown += `${text}\n\n`;
      else if (tag === 'LI') markdown += `* ${text}\n`;
    });

    markdown = markdown.replace(/\n{3,}/g, '\n\n').trim();
    if (!markdown || markdown.length < 50) return null;

    const fontes = [];
    firstItem.querySelectorAll('a[href^="http"]').forEach(a => {
      const h = a.getAttribute('href') || '';
      if (h.startsWith('http') && !h.includes('bing.com')) fontes.push(h);
    });

    return {
      modo: 'ia',
      text: markdown,
      fontes: [...new Set(fontes)],
      fontes_general: [...new Set(fontes)],
      jogos: [],
      estrutura: null
    };
  }

  // ===========================================================================
  // 3.2) JOGO — Arena de Esportes (JSON estruturado)
  // ===========================================================================
   function tryJogo() {
    const container = document.querySelector('#b_wpt_container');
    if (!container) return null;

    const data = {
      time: (container.querySelector('.b_entityTitle')?.innerText
          || container.querySelector('.bsp_magazine_title')?.innerText
          || '').trim() || null,
      competicao: (container.querySelector('.b_entitySubTitle')?.innerText
          || container.querySelector('.bsp_subttl')?.innerText
          || '').trim() || null,
      partidas: [],
      classificacao: []
    };

    // -------------------------------------------------------------------------
    // 1) PARTIDAS — múltiplos seletores, um card por partida
    // -------------------------------------------------------------------------
    const seletoresCards = [
      '.bsp_schedule_mtch_crd .bsp_match_card',      // layout novo (já funcionava)
      '.bsp_match_card',                              // genérico
      '.b_mtcctnr',                                   // container interno
      '[role="listitem"]',                            // slides
    ];

    const cardsVistos = new Set();
    const cards = [];

    for (const sel of seletoresCards) {
      container.querySelectorAll(sel).forEach(c => {
        if (!cardsVistos.has(c)) {
          cardsVistos.add(c);
          cards.push(c);
        }
      });
    }

    cards.forEach(card => {
      try {
        // Time da casa — múltiplos seletores
        const timeCasaEl = card.querySelector('.bsp_team:first-of-type .team-name-ellipsis')
                       || card.querySelectorAll('.bsp_team')[0]?.querySelector('.team-name-ellipsis')
                       || card.querySelectorAll('.bsp-team-name .team-name-ellipsis')[0];
        const timeForaEl = card.querySelector('.bsp_team:last-of-type .team-name-ellipsis')
                       || card.querySelectorAll('.bsp_team')[1]?.querySelector('.team-name-ellipsis')
                       || card.querySelectorAll('.bsp-team-name .team-name-ellipsis')[1];

        const time_casa = (timeCasaEl?.innerText || '').trim();
        const time_fora = (timeForaEl?.innerText || '').trim();

        // Placar
        const scores = card.querySelectorAll('.bsp_team_scr, .bsp_mag_score > div');
        const placar_casa = scores[0] ? scores[0].innerText.trim() : '';
        const placar_fora = scores[1] ? scores[1].innerText.trim() : '';

        // Competição
        const compEl = card.querySelector('.bsp_mtc_tps div')
                    || card.querySelector('[title*="·"]');
        const torneio = compEl
          ? (compEl.getAttribute('title') || compEl.innerText.trim())
          : '';

        // Status / data
        const status = (card.querySelector('.bsp_game_info > div:first-child')?.innerText
                    || card.querySelector('.game-info > div:first-child')?.innerText
                    || '').trim();
        const data_jogo = (card.querySelector('.bsp_game_time')?.innerText
                       || card.querySelector('.game-time')?.innerText
                       || '').trim();

        // Só adiciona se tem pelo menos time_casa e time_fora
        if (time_casa && time_fora) {
          data.partidas.push({
            torneio,
            time_casa,
            time_fora,
            placar: `${placar_casa} x ${placar_fora}`,
            status,
            data: data_jogo,
            horario: 'N/A'
          });
        } else {
          // Fallback: parsing por linhas (o jeito antigo)
          const linhas = card.innerText.split('\n')
            .map(l => l.trim()).filter(l => l.length > 0);
          for (let i = 0; i < linhas.length; i++) {
            if (linhas[i] && linhas[i+1] && linhas[i+2]
                && linhas[i+3] !== undefined && linhas[i+4] !== undefined) {
              data.partidas.push({
                torneio: linhas[i],
                time_casa: linhas[i+1],
                time_fora: linhas[i+2],
                placar: `${linhas[i+3]} x ${linhas[i+4]}`,
                status: linhas[i+5] || '',
                data: linhas[i+6] || '',
                horario: 'N/A'
              });
              i += 6;
            }
          }
        }
      } catch (e) {}
    });

    // Dedup por (time_casa + time_fora + placar)
    const unicos = new Set();
    data.partidas = data.partidas.filter(p => {
      const k = `${p.time_casa}|${p.time_fora}|${p.placar}`;
      if (unicos.has(k)) return false;
      unicos.add(k);
      return true;
    });

    // -------------------------------------------------------------------------
    // 2) CLASSIFICAÇÃO — múltiplos seletores
    // -------------------------------------------------------------------------
    const seletoresClass = [
      '.bsp_mgz_standings .bsp_row_item',   // layout novo
      '.bsp_std_list tr',
      '.b_snippet li',
      'tr',
    ];

    const rowsVistas = new Set();
    const rows = [];

    for (const sel of seletoresClass) {
      container.querySelectorAll(sel).forEach(r => {
        if (!rowsVistas.has(r)) {
          rowsVistas.add(r);
          rows.push(r);
        }
      });
    }

    rows.forEach(row => {
      try {
        // Modo A: estrutura com classes
        const posEl = row.querySelector('.bsp_row_rank');
        const timeEl = row.querySelector('.bsp_row_teamname, .team-name-ellipsis');
        const ptsEl = row.querySelector('.bsp_col_pts');

        if (posEl && timeEl && ptsEl) {
          const posicao = posEl.innerText.trim();
          const time = timeEl.getAttribute('title') || timeEl.innerText.trim();
          const pontos = ptsEl.innerText.replace(/PTS/gi, '').trim();
          if (posicao && time) {
            data.classificacao.push({ posicao, time, pontos });
            return;
          }
        }

        // Modo B: parsing por texto
        const text = row.innerText.split('\n').map(t => t.trim()).filter(t => t.length > 0);
        if (text.length >= 3 && !isNaN(text[0])) {
          data.classificacao.push({
            posicao: text[0],
            time: text[1],
            pontos: text[text.length - 1]
          });
        }
      } catch (e) {}
    });

    // Dedup por posição
    const posVistas = new Set();
    data.classificacao = data.classificacao.filter(c => {
      if (posVistas.has(c.posicao)) return false;
      posVistas.add(c.posicao);
      return true;
    });

    // -------------------------------------------------------------------------
    // 3) VALIDAÇÃO — só retorna se achou algo
    // -------------------------------------------------------------------------
    if (data.partidas.length === 0 && data.classificacao.length === 0) return null;

    // Texto plano (fallback legível)
    const linhas = [];
    if (data.time) linhas.push(`# ${data.time}`);
    if (data.competicao) linhas.push(`_${data.competicao}_`);
    linhas.push('');

    if (data.partidas.length) {
      linhas.push('## Partidas');
      data.partidas.forEach(p => {
        linhas.push(`- ${p.torneio}: ${p.time_casa} ${p.placar} ${p.time_fora} (${p.status}, ${p.data})`);
      });
      linhas.push('');
    }

    if (data.classificacao.length) {
      linhas.push('## Classificação');
      data.classificacao.forEach(c => {
        linhas.push(`- ${c.posicao}º ${c.time}: ${c.pontos} pts`);
      });
    }

    return {
      modo: 'jogo',
      text: linhas.join('\n').trim(),
      fontes: [],
      fontes_general: [],
      jogos: data.partidas,
      estrutura: data
    };
  }

  // ===========================================================================
  // 3.3) PÁGINA — fallback bruto (texto puro, sem links)
  // ===========================================================================
  function tryPagina() {
    const root =
      document.querySelector('#b_content main') ||
      document.querySelector('#b_content') ||
      document.body;

    const clone = root.cloneNode(true);
    clone.querySelectorAll([
      'script', 'style', 'noscript', 'iframe', 'svg', 'head', 'meta',
      'link', 'template', 'object', 'embed', 'canvas',
      '.b_ad', '.sb_ad', '.b_adTop', '.b_adBottom',
      '.rms_img', 'img', 'video', 'audio',
      '#b_header', '#b_footer', 'header', 'nav', 'footer',
      '.b_hide', '[aria-hidden="true"]',
      '.gs_cit', '.gs_cits', '.gs_cit_wrapper', '.gs_cit_cont',
      '.gs_cit_panel', '.bsp_cit_cont', '.b_genserp_citation_hover_md',
      '.gs_mdlink', '.gs_cit_txt', '.gs_sm_cit', '.gs_sup_cit',
      '.gs_readMoreFullBtn',
      'acf-button-standard', 'acf-thumbs-up-down-feedback',
      '.gs_secctrl', '.gs_secctrl_items', '.acf_fdbk_ph',
      '.b_acf_answer_expansion_control', '.b_module_expansion_control',
      '.gs_ai_disclaimer', '.gs_infobbl', '#gs_infobbl',
      '.bsp_seemore', '.mag_st_header', '.b_wpt_header',
      '.bsp_mgz_header', '.bsp_mgzhdr_btns',
      '.b_wpt_attr', '.b_wpt_footer', '.b_gs_top_gradient', '.b_gs_bottom_cover',
    ].join(',')).forEach(el => el.remove());

    let markdown = "";
    clone.querySelectorAll('h1, h2, h3, p, li').forEach(el => {
      let text = el.innerHTML || '';
      text = text.replace(/<strong[^>]*>(.*?)<\/strong>/gis, '**$1**');
      text = text.replace(/<b[^>]*>(.*?)<\/b>/gis, '**$1**');
      text = text.replace(/<em[^>]*>(.*?)<\/em>/gis, '*$1*');
      text = text.replace(/<i[^>]*>(.*?)<\/i>/gis, '*$1*');
      text = text.replace(/<[^>]*>/g, '');
      text = text.replace(/&nbsp;|›|»|&amp;/g, ' ');
      text = text.replace(/\s+/g, ' ').trim();
      if (!text) return;

      const tag = el.tagName;
      if (tag === 'H1') markdown += `# ${text}\n\n`;
      else if (tag === 'H2') markdown += `## ${text}\n\n`;
      else if (tag === 'H3') markdown += `### ${text}\n\n`;
      else if (tag === 'P') markdown += `${text}\n\n`;
      else if (tag === 'LI') markdown += `* ${text}\n`;
    });

    const cortes = [
      /\nExibir tudo\s*\d*\s*Fontes/i,
      /\nContinuar explorando/i,
      /\nTodas as fontes/i,
      /\nMostrar tudo\s*Referências/i,
      /\nNova pesquisa/i,
      /\nExperimente a Pesquisa Visual/i,
    ];
    for (const c of cortes) markdown = markdown.split(c)[0];

    markdown = markdown.replace(/\n{3,}/g, '\n\n').trim();
    if (markdown.length < 50) return null;

    const fontesTodas = [];
    root.querySelectorAll('a[href^="http"]').forEach(a => {
      const h = a.getAttribute('href') || '';
      if (h.startsWith('http') && !h.includes('bing.com')) fontesTodas.push(h);
    });

    return {
      modo: 'pagina',
      text: markdown,
      fontes: [],
      fontes_general: [...new Set(fontesTodas)],
      jogos: [],
      estrutura: null
    };
  }

  // ===========================================================================
  // 3.4) Cascata: IA → Jogo → Página
  // ===========================================================================
  const ia = tryIA();
  if (ia) return ia;

  const jogo = tryJogo();
  if (jogo) return jogo;

  const pagina = tryPagina();
  if (pagina) return pagina;

  return { modo: 'nenhum', text: '', fontes: [], fontes_general: [], jogos: [], estrutura: null };
}
"""


# =============================================================================
# 4. HELPERS
# =============================================================================


def limpar_texto(texto: str) -> str:
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
    return psutil.Process().memory_info().rss / 1024 / 1024


# =============================================================================
# 5. EVENT LOOP PERSISTENTE
# =============================================================================


class LoopBackground:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=120):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)


LOOP_BG = LoopBackground()


# =============================================================================
# 6. BROWSER PERSISTENTE
# =============================================================================


class BrowserPool:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _criar(self):
        log.info("BOOT Chromium iniciando...")
        t0 = time.time()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
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
        self._context = await self._browser.new_context(
            user_agent=UA_MOBILE,
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            is_mobile=True,
            has_touch=True,
            locale="pt-BR",
            java_script_enabled=True,
        )
        await self._context.add_cookies(
            [
                {"name": k, "value": v, "domain": ".bing.com", "path": "/"}
                for k, v in COOKIES.items()
            ]
        )
        self._page = await self._context.new_page()
        log.info(f"BOOT Chromium pronto em {round(time.time()-t0, 2)}s")

    async def get_page(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._criar()
            return self._page

    async def warmup(self):
        await self.get_page()


POOL = BrowserPool()


# =============================================================================
# 7. NÚCLEO FLASH
# =============================================================================

PHOTO_MODOS = {"base64", "url", "none"}


async def executar_pesquisa_flash(query: str, photo_modo: str = "url") -> dict:
    """
    Pesquisa em modo flash:
      1. goto
      2. espera 1.5s
      3. extrai com JS em cascata (IA → Jogo → Página)
      4. foto (opcional)
    """
    url = URL_TEMPLATE.format(q=quote_plus(query))
    t_ini = time.time()

    page = await POOL.get_page()
    resultado = None
    foto_arquivo = None
    foto_b64 = None

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        for tentativa in range(1, 3):
            try:
                resultado = await page.evaluate(JS_CAPTURAR)
                break
            except Exception as e:
                msg = str(e)
                if (
                    "Execution context was destroyed" in msg
                    or "navigation" in msg.lower()
                ) and tentativa < 2:
                    await page.wait_for_timeout(1000)
                    continue
                raise

        if photo_modo == "url":
            try:
                nome_foto = f"{uuid.uuid4().hex}.png"
                caminho = PHOTOS_DIR / nome_foto
                await page.screenshot(path=str(caminho), full_page=False, type="png")
                foto_arquivo = nome_foto
            except Exception as e:
                log.warning(f"Falha ao capturar foto (url): {e}")

        elif photo_modo == "base64":
            try:
                shot = await page.screenshot(full_page=False, type="png")
                foto_b64 = base64.b64encode(shot).decode("ascii")
            except Exception as e:
                log.warning(f"Falha ao capturar foto (base64): {e}")

        if resultado is None:
            resultado = {
                "modo": "erro",
                "text": "Sem resultado",
                "fontes": [],
                "fontes_general": [],
                "jogos": [],
                "estrutura": None,
            }

    except Exception as e:
        import traceback

        traceback.print_exc()
        resultado = {
            "modo": "erro",
            "text": f"Falha: {type(e).__name__}: {e}",
            "fontes": [],
            "fontes_general": [],
            "jogos": [],
            "estrutura": None,
        }

    t_total = time.time() - t_ini

    modo = (resultado or {}).get("modo", "nenhum")
    texto = limpar_texto((resultado or {}).get("text", "") or "")
    fontes = (resultado or {}).get("fontes", []) or []
    fontes_general = (resultado or {}).get("fontes_general", []) or []
    jogos = (resultado or {}).get("jogos", []) or []
    estrutura = (resultado or {}).get("estrutura", None)

    return {
        "query": query,
        "url": url,
        "modo": modo,
        "texto": texto,
        "fontes": fontes,
        "fontes_general": fontes_general,
        "jogos": jogos,
        "estrutura": estrutura,
        "foto_arquivo": foto_arquivo,
        "foto_b64": foto_b64,
        "tempo_s": round(t_total, 3),
    }


# =============================================================================
# 8. ROTAS
# =============================================================================


@app.route("/photos/<path:nome>", methods=["GET"])
def servir_foto(nome):
    return send_from_directory(PHOTOS_DIR.resolve(), nome)


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": BANNER,
            "modos": ["ia", "jogo", "pagina"],
            "endpoints": {
                "GET  /": "status",
                "GET  /search?q=<termo>&photo=url|base64|none": "pesquisa flash",
                "POST /search": 'idem, JSON body {"q":"...","photo":"url"}',
                "GET  /warmup": "esquenta o Chromium (após deploy)",
                "GET  /photos/<file>": "baixa a foto capturada",
            },
            "photo_modes": {
                "url": "salva PNG no servidor, devolve /photos/<file>",
                "base64": "devolve string base64 no JSON",
                "none": "não captura foto (mais rápido)",
            },
        }
    )


@app.route("/warmup", methods=["GET"])
def warmup():
    try:
        LOOP_BG.run(POOL.warmup())
        return jsonify({"status": "ok", "browser": "warm"})
    except Exception as e:
        return jsonify({"status": "erro", "erro": f"{type(e).__name__}: {e}"}), 500


def _executar_e_responder(query: str, ip: str, t0: float, photo_modo: str):
    try:
        dados = LOOP_BG.run(executar_pesquisa_flash(query, photo_modo))
    except Exception as e:
        log.error(f'ERR ip={ip} q="{query}" {type(e).__name__}: {e}')
        return (
            jsonify(
                {
                    "response": f"Erro: {type(e).__name__}: {e}",
                    "fontes": [],
                    "fontes_general": [],
                    "jogos": [],
                    "estrutura": None,
                    "photo": None,
                    "meta": {"erro": True},
                }
            ),
            500,
        )

    total = round(time.time() - t0, 2)
    log.info(
        f"RES ip={ip} q=\"{query}\" modo={dados['modo']} "
        f"chars={len(dados['texto'])} fontes={len(dados['fontes'])} "
        f"geral={len(dados['fontes_general'])} jogos={len(dados['jogos'])} "
        f"photo={photo_modo} tempo={dados['tempo_s']}s total={total}s"
    )

    photo_field = None
    if photo_modo == "url" and dados["foto_arquivo"]:
        photo_field = {
            "tipo": "url",
            "arquivo": dados["foto_arquivo"],
            "url": f"/photos/{dados['foto_arquivo']}",
        }
    elif photo_modo == "base64" and dados["foto_b64"]:
        photo_field = {
            "tipo": "base64",
            "mime": "image/png",
            "conteudo": dados["foto_b64"],
        }
    elif photo_modo == "none":
        photo_field = {"tipo": "none"}

    # Estrutura: se modo=jogo, devolve o JSON de dados do time
    # Se modo=ia, devolve com "texto" (markdown). Se modo=pagina, devolve texto bruto.
    response_data = {
        "response": dados["texto"],
        "fontes": dados["fontes"],
        "fontes_general": dados["fontes_general"],
        "jogos": dados["jogos"],
        "photo": photo_field,
        "meta": {
            "query": dados["query"],
            "url": dados["url"],
            "modo": dados["modo"],
            "chars": len(dados["texto"]),
            "tempo_s": dados["tempo_s"],
            "ip": ip,
            "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    # No modo jogo, anexa a estrutura JSON bruta (time/competicao/partidas/classificacao)
    if dados["modo"] == "jogo" and dados.get("estrutura"):
        response_data["dados"] = dados["estrutura"]

    return jsonify(response_data)


@app.route("/search", methods=["GET"])
def rota_search_get():
    t0 = time.time()
    ip = ip_do_cliente()
    query = (request.args.get("q") or "").strip()
    photo_modo = (request.args.get("photo") or "url").lower().strip()

    if not query:
        log.warning(f"ERR ip={ip} GET /search sem query")
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400
    if photo_modo not in PHOTO_MODOS:
        photo_modo = "url"

    log.info(f'REQ ip={ip} GET q="{query}" photo={photo_modo}')
    return _executar_e_responder(query, ip, t0, photo_modo)


@app.route("/search", methods=["POST"])
def rota_search_post():
    t0 = time.time()
    ip = ip_do_cliente()
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or payload.get("query") or "").strip()
    photo_modo = (payload.get("photo") or "url").lower().strip()

    if not query:
        log.warning(f"ERR ip={ip} POST /search sem query")
        return jsonify({"error": "Campo 'q' é obrigatório"}), 400
    if photo_modo not in PHOTO_MODOS:
        photo_modo = "url"

    log.info(f'REQ ip={ip} POST q="{query}" photo={photo_modo}')
    return _executar_e_responder(query, ip, t0, photo_modo)


# =============================================================================
# 9. ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
