# selecao-ia

Clique duplo numa palavra (ou triplo num parágrafo) em qualquer app e uma janelinha translúcida ao lado do cursor responde na hora. macOS.

## Instalar em outro Mac

```sh
brew install python@3.14 python-tk@3.14
git clone <url-do-repo> && cd selecao-ia
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use o Python do Homebrew: o do sistema tem Tk 8.5, que é instável.

**Permissões** (Ajustes do Sistema > Privacidade e Segurança): adicione o Terminal em **Acessibilidade** e **Monitoramento de Entrada**, depois feche o Terminal (Cmd+Q) e abra de novo.

**IA** (uma das duas):
- `claude` CLI instalado e logado (padrão, sem chave, resposta de uma vez), ou
- chave da API em `.env`: `export ANTHROPIC_API_KEY=sk-ant-...` (streaming; precisa de créditos e chave ligada a um workspace).

## Rodar

```sh
./rodar.sh
```

Para textos maiores (ex.: questão de múltipla escolha), selecione arrastando o mouse e aperte **Cmd+Option+Enter**. Esc ou clique fecha a janela. Cmd+Option+P pausa/retoma. Ajustes (tamanho, opacidade, modelo) no topo de `selecao_ia.py`.
