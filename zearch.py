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
HEADLESS = True

URL_TEMPLATE = "https://www.bing.com/search?FORM=HDRSC1&q={q}"

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Mobile Safari/537.36"
)

VIEWPORT = {"width": 412, "height": 915}
DEVICE_SCALE = 2.625

# Pasta das fotos (servida em /photos/<arquivo>)
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
# 3. JS DE EXTRAÇÃO
# =============================================================================

JS_CAPTURAR = r"""
async () => {

  // ---------------------------------------------------------------------------
  // 3.1) Copilot — resposta da IA (melhor caso)
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

    if (caMain.querySelector('.bsp_mgz_schedule, .bsp_mgz_standings, #b_wpt_container')) {
      return null;
    }

    // -------------------------------------------------------------------------
    // FLASH: clica em "Ler tudo" se existir (1 clique rápido, sem loop)
    // -------------------------------------------------------------------------
    const lerTudo = Array.from(wrapper.querySelectorAll('button, a, div[role="button"]'))
      .find(el => {
        const t = (el.textContent || '').toLowerCase();
        return (t.includes('ler tudo') || t.includes('read more') || t.includes('ver mais'))
               && el.offsetParent !== null;
      });

    if (lerTudo) {
      try {
        lerTudo.scrollIntoView({ block: 'center' });
        lerTudo.click();
        // 1s pra expandir
        await new Promise(r => setTimeout(r, 1000));
      } catch (e) {}
    }

    // -------------------------------------------------------------------------
    // Fontes do Copilot (citações reais)
    // -------------------------------------------------------------------------
    const fontesCopilot = [];
    wrapper.querySelectorAll('a[data-url]').forEach(a => {
      const u = a.getAttribute('data-url');
      if (u && u.startsWith('http')) fontesCopilot.push(u);
    });

    // -------------------------------------------------------------------------
    // Clona e limpa
    // -------------------------------------------------------------------------
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
      '.gs_mdlink', '.gs_cit_txt',
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
      /^\s*Ler tudo\s*$/gim,
      /^\s*Read more\s*$/gim,
    ];
    for (const r of lixos) texto = texto.replace(r, '');

    texto = texto.split(/Wikipedia\s*›\s*wiki/i)[0].trim();
    texto = texto.split(/Mostrar tudo\s*Referências/i)[0].trim();
    texto = texto.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();

    const fontesTodas = [];
    wrapper.querySelectorAll('a').forEach(a => {
      const h = a.href;
      if (h && h.startsWith('http') && !h.includes('bing.com')) fontesTodas.push(h);
    });

    return {
      modo: 'copilot',
      text: texto,
      fontes: [...new Set(fontesCopilot)],
      fontes_general: [...new Set(fontesTodas)],
      jogos: []
    };
  }

  // ---------------------------------------------------------------------------
  // 3.2) Sports — tabela de jogos
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
        const status = (card.querySelector('.game-info > div:first-child')?.innerText || '').trim();
        const data = (card.querySelector('.game-time')?.innerText || '').trim();
        jogos.push({
          competicao, time_casa, time_fora,
          placar: `${placar_casa} - ${placar_fora}`,
          status, data, horario: 'N/A'
        });
      } catch (e) {}
    });

    if (jogos.length === 0) return null;
    return { modo: 'sports', text: container.innerText.trim(), fontes: [], fontes_general: [], jogos };
  }

  // ---------------------------------------------------------------------------
  // 3.3) Página inteira — LIMPEZA AGRESSIVA
  //     (remove cards de fonte, links citados, botões, tudo que é estrutura)
  // ---------------------------------------------------------------------------
  async function tryWholePage() {
    // Tenta partir do container mais "limpo" disponível
    const root =
      document.querySelector('#ca_main') ||
      document.querySelector('#b_content main') ||
      document.querySelector('#b_content') ||
      document.body;

    const clone = root.cloneNode(true);

    // -------------------------------------------------------------------------
    // 3.3.1) REMOÇÃO ESTRUTURAL
    // Remove blocos inteiros que são lixo (cards, botões, cabeçalhos, etc.)
    // -------------------------------------------------------------------------
    clone.querySelectorAll([
      // Lixo básico
      'script', 'style', 'noscript', 'iframe', 'svg', 'head', 'meta',
      'link', 'template', 'object', 'embed', 'canvas',
      '.b_ad', '.sb_ad', '.b_adTop', '.b_adBottom',
      '.rms_img', 'img', 'video', 'audio',
      '#b_header', '#b_footer', 'header', 'nav', 'footer',
      '.b_hide', '[aria-hidden="true"]',

      // Cards de fonte (contêm os textos "Wikipedia › wiki › Nobru", "forbes.com.br" etc.)
      '.gs_cit', '.gs_cits', '.gs_cit_wrapper', '.gs_cit_cont',
      '.gs_cit_panel', '.gs_cit_panel_content', '.gs_cit_panel_header',
      '.bsp_cit_cont', '.b_genserp_citation_hover_md', '.cit_exp_cont',
      '.gs_cit_exp', '.gs_cit_exp_text', '.gs_cit_src', '.gs_cit_title',
      '.gs_cit_snippet', '.gs_cit_siteurl', '.gs_cit_title_text',

      // Links citados inline no texto (Wikipedia+1, Esports.net, etc.)
      '.gs_mdlink', '.gs_cit_txt', '.gs_sm_cit', '.gs_sup_cit',

      // Botões e feedback
      'acf-button-standard', 'acf-thumbs-up-down-feedback',
      '.gs_secctrl', '.gs_secctrl_items', '.acf_fdbk_ph',
      '.b_acf_answer_expansion_control', '.b_module_expansion_control',
      '.b_acf_expansion_gradient_overlay', '.b_btnContainer',

      // Avisos IA e disclaimers
      '.gs_ai_disclaimer', '.gs_infobbl', '#gs_infobbl',

      // Cabeçalhos de bloco e "ver mais"
      '.bsp_seemore', '.bsp_seemore_start', '.bsp_seemore_cta',
      '.mag_st_header', '.mag_header',
      '.b_wpt_header', '.bsp_mgz_header', '.bsp_mgzhdr_btns',

      // Rodapés do Copilot
      '.b_wpt_attr', '.b_wpt_footer', '.b_gs_top_gradient', '.b_gs_bottom_cover',
    ].join(',')).forEach(el => el.remove());

    // -------------------------------------------------------------------------
    // 3.3.2) REMOÇÃO DE LINKS (mantém só o texto)
    // Substitui cada <a> pelo seu texto puro
    // -------------------------------------------------------------------------
    clone.querySelectorAll('a').forEach(a => {
      const t = (a.innerText || a.textContent || '').trim();
      a.replaceWith(document.createTextNode(t));
    });

    // -------------------------------------------------------------------------
    // 3.3.3) PEGA O TEXTO PURO
    // -------------------------------------------------------------------------
    let texto = (clone.innerText || clone.textContent || '').trim();

    // -------------------------------------------------------------------------
    // 3.3.4) CORTES ESTRUTURAIS (frases-âncora do rodapé)
    // -------------------------------------------------------------------------
    const cortes = [
      /Exibir tudo\s*\d*\s*Fontes/i,
      /Continuar explorando/i,
      /Todas as fontes/i,
      /Mostrar tudo\s*Referências/i,
      /Nova pesquisa/i,
      /Experimente a Pesquisa Visual/i,
      /Referências\s*\d+\s*fonte/i,
      /Fontes\s*\d+/i,
    ];
    for (const c of cortes) {
      texto = texto.split(c)[0];
    }

    // -------------------------------------------------------------------------
    // 3.3.5) LIMPEZA FINA (frases soltas e lixo visual)
    // -------------------------------------------------------------------------
    const lixos = [
      /^\s*Leia mais\s*$/gim,
      /^\s*Ver mais\s*$/gim,
      /^\s*Saiba mais\s*$/gim,
      /^\s*Feedback\s*$/gim,
      /^\s*Exibir tudo\s*$/gim,
      /Este resumo foi gerado pela IA[^\n]*/gi,
      /Localize os links de origem[^\n]*/gi,
      /Saiba mais sobre os resultados[^\n]*/gi,
      /Curtir\s*Não gosto/gi,
      /Com base em fontes/gi,
    ];
    for (const r of lixos) texto = texto.replace(r, '');

    // -------------------------------------------------------------------------
    // 3.3.6) HIGIENIZAÇÃO FINAL
    // Remove linhas em branco duplicadas e normaliza espaços
    // -------------------------------------------------------------------------
    texto = texto
      .split('\n')
      .map(l => l.replace(/[ \t]+/g, ' ').trim())
      .filter((l, i, arr) => l.length > 0 || (i > 0 && arr[i - 1].length > 0))
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

    if (texto.length < 50) return null;

    // Fontes gerais: pega os links externos ANTES de remover (do root original)
    const fontesTodas = [];
    root.querySelectorAll('a').forEach(a => {
      const h = a.href;
      if (h && h.startsWith('http') && !h.includes('bing.com')) fontesTodas.push(h);
    });

    return {
      modo: 'pagina',
      text: texto,
      fontes: [],
      fontes_general: [...new Set(fontesTodas)],
      jogos: []
    };
  }

  // ---------------------------------------------------------------------------
  // 3.4) Loop: Copilot (10s) → Sports → Página
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
        resolve(p || { modo: 'nenhum', text: '', fontes: [], fontes_general: [], jogos: [] });
      }
    }, 500);
  });
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

# Modos válidos para o parâmetro "photo":
#   "base64"  → devolve a imagem em base64 no JSON (string gigante)
#   "url"     → salva PNG no servidor e devolve a URL /photos/xxx.png
#   "none"    → não captura foto (mais rápido)
PHOTO_MODOS = {"base64", "url", "none"}


async def executar_pesquisa_flash(query: str, photo_modo: str = "url") -> dict:
    """
    Executa a pesquisa em modo flash.
    photo_modo: "base64" | "url" | "none"
    """
    url = URL_TEMPLATE.format(q=quote_plus(query))
    t_ini = time.time()

    page = await POOL.get_page()
    resultado = None
    foto_arquivo = None
    foto_b64 = None

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(800)

        # ---------------------------------------------------------------------
        # 1) CLICA EM "LER TUDO" (Playwright clique real)
        #    Bing usa acf-button-standard, que não responde a element.click()
        # ---------------------------------------------------------------------
        clicou_ler_tudo = False
        try:
            for texto in (
                "Ler tudo",
                "Read more",
                "Ver mais",
                "Mostrar mais",
                "Show more",
            ):
                loc = page.locator(
                    f"button:has-text('{texto}'):visible, "
                    f"a:has-text('{texto}'):visible, "
                    f"div[role='button']:has-text('{texto}'):visible"
                ).first

                if await loc.count() > 0:
                    try:
                        await loc.scroll_into_view_if_needed(timeout=1000)
                        await loc.click(timeout=2000)
                        await page.wait_for_timeout(1000)  # 1s expande
                        clicou_ler_tudo = True
                        log.info(f"BOOT Ler tudo clicado ({texto!r})")
                        break
                    except Exception:
                        continue
        except Exception:
            pass

        # ---------------------------------------------------------------------
        # 2) EXTRAI COM O JS (com 2 tentativas em caso de navegação)
        # ---------------------------------------------------------------------
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

        # ---------------------------------------------------------------------
        # 3) FOTO
        # ---------------------------------------------------------------------
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
        }

    t_total = time.time() - t_ini

    modo = (resultado or {}).get("modo", "nenhum")
    texto = limpar_texto((resultado or {}).get("text", "") or "")
    fontes = (resultado or {}).get("fontes", []) or []
    fontes_general = (resultado or {}).get("fontes_general", []) or []
    jogos = (resultado or {}).get("jogos", []) or []

    return {
        "query": query,
        "url": url,
        "modo": modo,
        "texto": texto,
        "fontes": fontes,
        "fontes_general": fontes_general,
        "jogos": jogos,
        "foto_arquivo": foto_arquivo,
        "foto_b64": foto_b64,
        "tempo_s": round(t_total, 3),
    }


# =============================================================================
# 8. ROTAS
# =============================================================================


@app.route("/photos/<path:nome>", methods=["GET"])
def servir_foto(nome):
    """Serve os PNGs salvos em PHOTOS_DIR."""
    return send_from_directory(PHOTOS_DIR.resolve(), nome)


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": BANNER,
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

    # Monta o campo "photo" de acordo com o modo escolhido
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

    return jsonify(
        {
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
    )


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
