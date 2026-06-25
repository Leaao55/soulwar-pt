"""
Rubinot Soul War Check
Verifica, para os personagens cadastrados no Soulwar PT Manager, quais já
passaram pelo bazaar (histórico de leilões) E têm a quest Soul War completa.

Dependências: pip install requests playwright playwright-stealth
              python -m playwright install chromium
Uso:
    python rubinot_soulwar_check.py                 -> todos os servidores
    python rubinot_soulwar_check.py "Tenebrium"      -> só um servidor
"""

import requests
from playwright.sync_api import sync_playwright, Page
from playwright_stealth import Stealth
import logging
import sys
import json
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIGURACAO
# ---------------------------------------------------------------------------

SHEETS_API_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwBhHkqkgtJoWDU-R6ge89TBpmBkLeBhLTdrsos3xVWonY-xJP8pIhgHt7YPfbTy2fy/exec"
)

BAZAAR_HISTORY_URL = "https://rubinot.com.br/bazaar/history"
BAZAAR_API_URL = "https://rubinot.com.br/api/bazaar/history"
BAZAAR_CHAR_BASE = "https://rubinot.com.br/bazaar"
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soulwar_results.json")

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "rubinot_soulwar_check.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CARREGAR PERSONAGENS DO SHEETS (Soulwar PT Manager)
# ---------------------------------------------------------------------------

def load_chars_from_sheets(server_filter: str | None = None) -> list[dict]:
    """Busca a lista de personagens aprovados do Google Sheets via Apps Script."""
    log.info("Carregando personagens do Soulwar PT Manager...")
    resp = requests.get(SHEETS_API_URL, params={"action": "load", "secret": "rbn_x9k2_leao_2026_soulwar"}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    if not payload.get("ok"):
        raise RuntimeError("Resposta do Apps Script não OK ao carregar dados.")

    raw_chars = payload["data"].get("chars")
    if isinstance(raw_chars, str):
        raw_chars = json.loads(raw_chars) if raw_chars else []
    raw_chars = raw_chars or []

    chars = [c for c in raw_chars if c.get("status") == "approved"]

    if server_filter:
        chars = [c for c in chars if c.get("server") == server_filter]

    log.info("Total de personagens carregados: %d", len(chars))
    return chars


# ---------------------------------------------------------------------------
# HELPERS DO PLAYWRIGHT
# ---------------------------------------------------------------------------

def _wait_for_site(page: Page) -> None:
    """Aguarda a página real do site carregar (sai do Cloudflare challenge)."""
    for _ in range(20):
        page.wait_for_timeout(2000)
        if len(page.content()) > 200_000:
            return
    log.warning("Timeout aguardando carregamento completo da página.")


def _dump_page_snapshot(page: Page, label: str) -> None:
    """Salva snapshot do HTML atual para debug de seletores."""
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"debug_{label}_{datetime.now().strftime('%H%M%S')}.html",
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    log.info("  [debug] snapshot salvo: %s", path)


def _ensure_page_ready(page: Page) -> None:
    """Garante que a página do site esteja carregada (passa Cloudflare se necessário)."""
    if page.url.startswith("https://rubinot.com.br"):
        return
    page.goto(BAZAAR_HISTORY_URL, wait_until="load", timeout=30000)
    _wait_for_site(page)


def search_char_in_history(page: Page, name: str) -> str | None:
    """
    Chama a API interna do site via fetch() (mesmas cookies do browser, bypassa Cloudflare).
    Retorna a URL individual (rubinot.com.br/bazaar/{id}) se encontrado, ou None.
    """
    _ensure_page_ready(page)
    try:
        result = page.evaluate(
            """
            async (args) => {
                const {apiUrl, name} = args;
                const params = new URLSearchParams({
                    page: '1', limit: '25', state: 'finished',
                    sortBy: 'auction_end', sortOrder: 'desc', name: name
                });
                const resp = await fetch(apiUrl + '?' + params.toString(), {
                    headers: {
                        'Accept': 'application/json',
                        'Referer': 'https://rubinot.com.br/bazaar/history'
                    }
                });
                if (!resp.ok) return {error: resp.status};
                return await resp.json();
            }
            """,
            {"apiUrl": BAZAAR_API_URL, "name": name},
        )

        if isinstance(result, dict) and result.get("error"):
            log.warning("  API retornou erro %s para '%s'", result["error"], name)
            return None

        # estrutura esperada: {data: [{id, name, ...}, ...]} ou {auctions: [...]}
        items = (
            result.get("data")
            or result.get("auctions")
            or result.get("items")
            or result.get("results")
            or []
        )
        if isinstance(items, list) and items:
            # filtra pelo nome exato (case-insensitive)
            name_lower = name.lower()
            match = next(
                (i for i in items if str(i.get("name", "")).lower() == name_lower),
                None,
            )
            if match is None:
                # fallback parcial
                match = next(
                    (i for i in items if name_lower in str(i.get("name", "")).lower()),
                    None,
                )
            if match:
                char_id = match.get("id") or match.get("auction_id") or match.get("auctionId")
                if char_id:
                    return f"{BAZAAR_CHAR_BASE}/{char_id}"
                # às vezes o objeto traz a URL diretamente
                url = match.get("url") or match.get("link")
                if url:
                    return url if url.startswith("http") else f"https://rubinot.com.br{url}"

        log.info("  Não encontrado via API (total itens retornados: %d)", len(items) if isinstance(items, list) else -1)
        if isinstance(result, dict):
            log.debug("  API payload keys: %s", list(result.keys()))
        return None

    except Exception as exc:
        log.error("Erro ao buscar '%s' via API: %s", name, exc)
        return None


def check_soul_war_status(page: Page, char_url: str) -> bool:
    """
    Abre a página individual do personagem (bazaar/{id}), navega até a aba
    Quests e retorna True se a quest 'Soul War' estiver marcada como completa.
    """
    try:
        page.goto(char_url, wait_until="load", timeout=30000)
    except Exception as exc:
        log.error("Erro ao navegar para %s: %s", char_url, exc)
        return False

    page.wait_for_timeout(3000)

    # Clica na aba "Quests"
    try:
        quests_tab = page.locator("button:has-text('Quests')").first
        if quests_tab.is_visible(timeout=5000):
            quests_tab.click()
            log.info("  [debug] aba Quests clicada")
            try:
                page.wait_for_selector("td:has-text('Soul War')", timeout=8000)
            except Exception:
                page.wait_for_timeout(4000)
        else:
            log.warning("  [debug] aba Quests não encontrada — salvando snapshot")
            _dump_page_snapshot(page, "no_quests_tab")
    except Exception:
        page.wait_for_timeout(4000)

    # Estrutura real observada:
    # <tr>
    #   <td><svg class="lucide lucide-circle-check-big ... text-[var(--color-success)]"/></td>
    #   <td>Soul War</td>
    # </tr>
    # SVG com "circle-check" na classe = completa; qualquer outro ícone = incompleta.
    completed: bool = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll('td'));
                const hasSoulWar = cells.some(c => c.textContent.trim() === 'Soul War');
                if (!hasSoulWar) continue;
                // procura SVG de check em qualquer célula da linha
                for (const cell of cells) {
                    for (const svg of cell.querySelectorAll('svg')) {
                        const cls = (svg.className && svg.className.baseVal)
                            ? svg.className.baseVal
                            : (svg.getAttribute('class') || '');
                        if (cls.includes('circle-check') || cls.includes('check-big') ||
                            cls.includes('color-success') || cls.includes('lucide-check')) {
                            return true;
                        }
                    }
                }
            }
            return false;
        }
    """)

    return bool(completed)


# ---------------------------------------------------------------------------
# DIAGNÓSTICO RÁPIDO (modo --debug)
# ---------------------------------------------------------------------------

def run_debug(page) -> None:
    """Testa a API do bazaar e inspeciona o payload de um personagem real."""
    log.info("=== MODO DEBUG: testando API do bazaar ===")
    page.goto(BAZAAR_HISTORY_URL, wait_until="load", timeout=30000)
    _wait_for_site(page)

    result = page.evaluate(
        """
        async (args) => {
            const {apiUrl} = args;
            const params = new URLSearchParams({
                page: '1', limit: '5', state: 'finished',
                sortBy: 'auction_end', sortOrder: 'desc'
            });
            const resp = await fetch(apiUrl + '?' + params.toString(), {
                headers: {'Accept': 'application/json', 'Referer': 'https://rubinot.com.br/bazaar/history'}
            });
            const text = await resp.text();
            return {status: resp.status, body: text.slice(0, 3000)};
        }
        """,
        {"apiUrl": BAZAAR_API_URL},
    )
    log.info("Status API: %d", result["status"])
    log.info("Payload (3000 chars):\n%s", result["body"])


# ---------------------------------------------------------------------------
# LOOP PRINCIPAL
# ---------------------------------------------------------------------------

def run_check(server_filter: str | None = None, debug: bool = False) -> None:
    if server_filter:
        log.info("=== Verificando Soul War — servidor: %s ===", server_filter)
    else:
        log.info("=== Verificando Soul War — todos os servidores ===")

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        # carrega o site uma vez para obter cookies Cloudflare
        log.info("Abrindo site para obter cookies Cloudflare...")
        page.goto(BAZAAR_HISTORY_URL, wait_until="load", timeout=30000)
        _wait_for_site(page)

        if debug:
            run_debug(page)
            browser.close()
            return

        chars = load_chars_from_sheets(server_filter)
        if not chars:
            log.warning("Nenhum personagem encontrado para verificar.")
            browser.close()
            return

        with_soul_war: list[dict] = []
        not_found: list[str] = []

        for c in chars:
            name = c.get("name", "")
            if not name:
                continue

            log.info("Buscando '%s' no histórico do bazaar...", name)
            char_url = search_char_in_history(page, name)

            if not char_url:
                log.info("  Não encontrado no histórico (nunca foi ao bazaar).")
                not_found.append(name)
                continue

            log.info("  Encontrado: %s — verificando Soul War...", char_url)
            has_sw = check_soul_war_status(page, char_url)

            if has_sw:
                log.info("  ✅ Soul War COMPLETA.")
                with_soul_war.append({
                    "name": name,
                    "owner": c.get("owner", ""),
                    "vocation": c.get("cls", ""),
                    "level": c.get("level", ""),
                    "server": c.get("server", ""),
                    "url": char_url,
                })
            else:
                log.info("  ❌ Soul War não completa.")

        browser.close()

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(with_soul_war, f, ensure_ascii=False, indent=2)

    log.info("=== RESULTADO ===")
    log.info("Personagens com Soul War completa: %d", len(with_soul_war))
    for c in with_soul_war:
        log.info(
            "  ✅ %s (%s, Lv %s, %s) — dono: %s",
            c["name"], c["vocation"], c["level"], c["server"], c["owner"],
        )
    log.info("Personagens não encontrados no histórico do bazaar: %d", len(not_found))
    for n in not_found:
        log.info("  — %s", n)
    log.info("Resultado salvo em: %s", RESULTS_PATH)


def main() -> None:
    args = sys.argv[1:]
    debug = "--debug" in args
    server_filter = next((a for a in args if not a.startswith("--")), None)
    try:
        run_check(server_filter, debug=debug)
    except Exception as exc:
        log.exception("Erro inesperado na verificação: %s", exc)


if __name__ == "__main__":
    main()
