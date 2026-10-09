"""
selecao_ia.py - clique duplo/triplo em um texto e a IA responde numa janelinha ao lado.

  Clique duplo  -> seleciona uma PALAVRA  -> explica o significado em 1-2 frases
  Clique triplo -> seleciona a LINHA/PARÁGRAFO -> responde a pergunta / explica o trecho

  Cmd+Option+Enter (Mac) / Ctrl+Alt+Enter   envia a seleção atual (ex.: arrastada com o mouse)
  Cmd+Option+P (Mac) / Ctrl+Alt+P (Windows)  pausa/retoma
  Esc ou clicar na janela  fecha a resposta

Rodar:  .venv/bin/python selecao_ia.py
"""

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk

import pyperclip
from pynput import keyboard, mouse

# ---------------- configuração ----------------
MODELO = "claude-sonnet-5-5"
MAX_TOKENS = 120
JANELA_CLIQUE = 0.40      # segundos entre cliques para contar como multi-clique
DIST_MAX = 6              # px que o mouse pode se mover entre cliques
ESPERA_TRIPLO = 0.35      # após o 2º clique, espera isso para ver se vem o 3º
MAX_CHARS = 4000          # ignora seleções gigantes
LARGURA = 180             # largura da janelinha (px)
OPACIDADE = 0.5           # transparência da janelinha (0.0 a 1.0)

SISTEMA = (
    "Você é um assistente de resposta instantânea que aparece numa janela minúscula "
    "ao lado do cursor. Responda em português do Brasil, direto ao ponto, sem "
    "introduções nem markdown. Máximo de ~30 palavras, no máximo 2 frases curtas."
)

# ---------------- DPI (coordenadas do mouse e do Tk iguais) ----------------
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

MAC = sys.platform == "darwin"
TECLA_COPIAR = keyboard.Key.cmd if MAC else keyboard.Key.ctrl
FONTE = ("Helvetica Neue", 11) if MAC else ("Segoe UI", 10)
ATALHO_PAUSA = "<cmd>+<alt>+p" if MAC else "<ctrl>+<alt>+p"
ATALHO_SELECAO = "<cmd>+<alt>+<enter>" if MAC else "<ctrl>+<alt>+<enter>"
# Modificadores que atrapalhariam o Cmd/Ctrl+C simulado se ainda estivessem apertados
MODS_ATRAPALHAM = {
    keyboard.Key.alt, keyboard.Key.alt_r,
    keyboard.Key.shift, keyboard.Key.shift_r,
    keyboard.Key.ctrl, keyboard.Key.ctrl_r,
}

# Com ANTHROPIC_API_KEY usa a API (com streaming); sem ela, usa o `claude` CLI.
client = None
if os.environ.get("ANTHROPIC_API_KEY"):
    import anthropic
    _ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")  # só p/ chaves sem workspace
    client = anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": _ws} if _ws else None
    )
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
if client is None and not os.path.exists(CLAUDE_BIN):
    sys.exit("Defina ANTHROPIC_API_KEY ou instale/logue o Claude Code CLI (`claude`).")
teclado = keyboard.Controller()
eventos: "queue.Queue[tuple]" = queue.Queue()
pausado = threading.Event()
mods_apertados: set = set()
mouse_ctl = mouse.Controller()


# ---------------- detecção de cliques ----------------
class DetectorCliques:
    def __init__(self):
        self.n = 0
        self.t = 0.0
        self.pos = (0, 0)
        self.geracao = 0
        self.timer = None
        self.lock = threading.Lock()

    def on_click(self, x, y, button, pressed):
        if button != mouse.Button.left or pressed:
            return
        eventos.put(("clique", x, y))
        if pausado.is_set():
            return
        agora = time.monotonic()
        with self.lock:
            perto = abs(x - self.pos[0]) <= DIST_MAX and abs(y - self.pos[1]) <= DIST_MAX
            self.n = self.n + 1 if (agora - self.t <= JANELA_CLIQUE and perto) else 1
            self.t, self.pos = agora, (x, y)
            self.geracao += 1
            if self.timer:
                self.timer.cancel()
            if self.n >= 2:
                n = min(self.n, 3)
                espera = ESPERA_TRIPLO if n == 2 else 0.05
                self.timer = threading.Timer(espera, self._disparar, args=(self.geracao, n, x, y))
                self.timer.daemon = True
                self.timer.start()

    def _disparar(self, geracao, n, x, y):
        with self.lock:
            if geracao != self.geracao:  # veio outro clique depois, deixa o próximo timer decidir
                return
            self.n = 0
        eventos.put(("capturar", n, x, y))


# ---------------- captura da seleção ----------------
def capturar_selecao() -> str:
    """Simula Cmd/Ctrl+C e lê o texto, restaurando a área de transferência depois."""
    try:
        antigo = pyperclip.paste()
    except Exception:
        antigo = ""
    marcador = f"__selecao_ia_{time.time()}__"
    try:
        pyperclip.copy(marcador)
    except Exception:
        return ""

    with teclado.pressed(TECLA_COPIAR):
        teclado.press("c")
        teclado.release("c")

    texto = ""
    for _ in range(12):
        time.sleep(0.03)
        try:
            atual = pyperclip.paste()
        except Exception:
            continue
        if atual != marcador:
            texto = atual
            break

    try:
        pyperclip.copy(antigo)
    except Exception:
        pass
    return texto.strip()


def trabalhar(req_id, n, x, y):
    texto = capturar_selecao()
    if not texto or len(texto) > MAX_CHARS:
        return  # nada selecionado (ex.: clique duplo num ícone) ou texto grande demais

    eventos.put(("abrir", req_id, x, y))

    if n == 2 and len(texto.split()) <= 3:
        pedido = f'Explique em no máximo 2 frases o que significa: "{texto}"'
    else:
        pedido = (
            "Se o trecho abaixo for uma pergunta, responda. Se não for, explique ou "
            f"resuma o essencial.\n\n{texto}"
        )

    try:
        if client is not None:
            with client.messages.stream(
                model=MODELO,
                max_tokens=MAX_TOKENS,
                system=SISTEMA,
                messages=[{"role": "user", "content": pedido}],
            ) as stream:
                for pedaco in stream.text_stream:
                    eventos.put(("texto", req_id, pedaco))
        else:
            # Sem chave de API: usa o Claude Code CLI já logado (`claude -p`)
            r = subprocess.run(
                [CLAUDE_BIN, "-p", "--model", MODELO,
                 "--append-system-prompt", SISTEMA, pedido],
                capture_output=True, text=True, timeout=90,
                stdin=subprocess.DEVNULL,
            )
            saida = (r.stdout or r.stderr).strip() or "(sem resposta)"
            eventos.put(("texto" if r.returncode == 0 else "erro", req_id, saida))
    except Exception as e:
        eventos.put(("erro", req_id, f"Erro: {e}"))


# ---------------- macOS: janela sobreposta, sem roubar foco nem trocar de tela ----------------
def mac_modo_overlay():
    """App sem ícone no Dock (não ativa/troca de Space) e janelas em todos os Spaces."""
    if not MAC:
        return
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception as e:
        print("aviso: modo overlay indisponível:", e)


def mac_ajustar_janela():
    if not MAC:
        return
    try:
        from AppKit import NSApplication
        # canJoinAllSpaces | stationary | fullScreenAuxiliary | ignoresCycle
        comportamento = (1 << 0) | (1 << 4) | (1 << 8) | (1 << 6)
        for nswin in NSApplication.sharedApplication().windows():
            nswin.setCollectionBehavior_(comportamento)
            nswin.setLevel_(101)  # acima de apps em tela cheia
            nswin.setHidesOnDeactivate_(False)
            nswin.setAlphaValue_(OPACIDADE)  # o -alpha do Tk é ignorado neste modo
    except Exception:
        pass


# ---------------- janelinha ----------------
class Popup:
    BG, FG, FG_FRACO = "#1e1e2e", "#cdd6f4", "#7f849c"

    def __init__(self, root):
        self.root = root
        self.win = None
        self.lbl = None
        self.req = 0
        self.texto = ""
        self.ancora = (0, 0)

    def dentro(self, x, y) -> bool:
        if not self.win:
            return False
        wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
        return wx <= x <= wx + self.win.winfo_width() and wy <= y <= wy + self.win.winfo_height()

    def abrir(self, req, x, y):
        self.fechar()
        self.req, self.texto, self.ancora = req, "", (x, y)
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", OPACIDADE)
        if MAC:  # janela que não ativa o app (não puxa o foco nem muda de Space)
            try:
                w.tk.call("::tk::unsupported::MacWindowStyle", "style", w, "help", "noActivates")
            except tk.TclError:
                pass
        w.configure(bg=self.BG, highlightthickness=1, highlightbackground="#45475a")
        self.lbl = tk.Label(
            w, text="pensando…", bg=self.BG, fg=self.FG_FRACO,
            font=FONTE, wraplength=LARGURA, justify="left", padx=6, pady=4,
        )
        self.lbl.pack()
        for widget in (w, self.lbl):
            widget.bind("<Button-1>", lambda e: self.fechar())
        self.win = w
        self._posicionar()
        w.update_idletasks()
        mac_ajustar_janela()

    def adicionar(self, req, pedaco, erro=False):
        if not self.win or req != self.req:
            return  # resposta de uma seleção antiga
        self.texto += pedaco
        self.lbl.configure(text=self.texto, fg="#f38ba8" if erro else self.FG)
        self._posicionar()

    def _posicionar(self):
        self.win.update_idletasks()
        x, y = self.ancora
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        px, py = x + 18, y + 18
        if px + w > sw:
            px = max(0, x - w - 18)
        if py + h > sh:
            py = max(0, y - h - 18)
        self.win.geometry(f"+{int(px)}+{int(py)}")

    def fechar(self):
        if self.win:
            self.win.destroy()
        self.win = None
        self.req = -1


def main():
    root = tk.Tk()
    root.withdraw()
    mac_modo_overlay()
    popup = Popup(root)
    contador = {"req": 0}
    ignorar_ate = {"t": 0.0}

    def processar():
        try:
            while True:
                ev = eventos.get_nowait()
                tipo = ev[0]
                if tipo == "clique":
                    _, x, y = ev
                    if popup.dentro(x, y):
                        ignorar_ate["t"] = time.monotonic() + 1.0  # cliques na janela não disparam nada
                    elif popup.win:
                        popup.fechar()
                elif tipo == "capturar":
                    if time.monotonic() < ignorar_ate["t"]:
                        continue
                    _, n, x, y = ev
                    contador["req"] += 1
                    threading.Thread(
                        target=trabalhar, args=(contador["req"], n, x, y), daemon=True
                    ).start()
                elif tipo == "abrir":
                    _, req, x, y = ev
                    if req == contador["req"]:
                        popup.abrir(req, x, y)
                elif tipo == "texto":
                    popup.adicionar(ev[1], ev[2])
                elif tipo == "erro":
                    popup.adicionar(ev[1], ev[2], erro=True)
                elif tipo == "fechar":
                    popup.fechar()
        except queue.Empty:
            pass
        root.after(25, processar)

    def alternar_pausa():
        if pausado.is_set():
            pausado.clear()
            print("▶ ativo")
        else:
            pausado.set()
            eventos.put(("fechar",))
            print("⏸ pausado")

    def enviar_selecao_atual():
        """Atalho: envia a seleção que já existe (ex.: feita arrastando o mouse)."""
        if pausado.is_set():
            return
        limite = time.monotonic() + 2.0
        while mods_apertados and time.monotonic() < limite:
            time.sleep(0.02)  # espera soltar Option/Shift antes de simular Cmd+C
        time.sleep(0.05)
        x, y = mouse_ctl.position
        eventos.put(("capturar", 3, int(x), int(y)))

    detector = DetectorCliques()
    mouse.Listener(on_click=detector.on_click, daemon=True).start()
    keyboard.Listener(
        on_press=lambda k: mods_apertados.add(k) if k in MODS_ATRAPALHAM else None,
        on_release=lambda k: mods_apertados.discard(k),
        daemon=True,
    ).start()
    keyboard.GlobalHotKeys({
        ATALHO_PAUSA: alternar_pausa,
        ATALHO_SELECAO: lambda: threading.Thread(target=enviar_selecao_atual, daemon=True).start(),
        "<esc>": lambda: eventos.put(("fechar",)),
    }).start()

    print("selecao_ia rodando. Clique duplo = palavra, triplo = parágrafo, "
          "Cmd+Option+Enter (Mac) ou Ctrl+Alt+Enter = envia a seleção atual (arrastada). "
          "Cmd+Option+P (Mac) ou Ctrl+Alt+P pausa.")
    root.after(25, processar)
    root.mainloop()


if __name__ == "__main__":
    main()
