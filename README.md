# docs-maintainer

Plugin/skill do [Claude Code](https://claude.com/claude-code) que ajuda a
criar e manter o ambiente de documentação de qualquer projeto: `docs/*.md` +
`mkdocs` (opcional) + um índice RAG vetorial local em [Chroma](https://www.trychroma.com/),
100% offline (sem chave de API — usa o modelo de embedding padrão do Chroma).

Também sabe importar conteúdo de fontes externas para dentro do padrão
docs/RAG do projeto:

- Vault [Obsidian](https://obsidian.md/)
- Documentação armazenada em banco de dados (SQLite, MySQL, Postgres)
- Markdown de outro repositório/pasta

## O que ela faz

- **Bootstrap**: cria `docs/` (+ `docs/changelog/`), `mkdocs.yml` (opcional)
  e o pipeline RAG (`scripts/rag_chunker.py`, `rag_ingest.py`, `rag_query.py`,
  `rag_eval.py`) num projeto que ainda não tem nada disso — os scripts ficam
  versionados no repositório do projeto, sem depender deste plugin depois.
- **Sync**: depois de mudanças de código, atualiza os docs afetados, cria uma
  entrada de changelog atômica e re-indexa no RAG.
- **Importação**: converte conteúdo de Obsidian/banco de dados/outro
  repositório markdown para dentro de `docs/`.
- **Perguntas**: responde perguntas sobre o projeto consultando o RAG,
  tratando "não encontrei" como sinal fraco (revalida no código antes de
  reportar como não documentado).
- **Auditoria**: cruza o que os docs afirmam contra o código real e aponta
  divergências.

A skill sempre detecta o que já existe no projeto antes de agir — nunca
sobrescreve scripts ou documentação já existentes.

**Convenção central: changelog nunca cresce como um bloco único.** O ledger
de status (`docs/REG.md`) guarda só o estado atual; cada mudança vira um
arquivo novo em `docs/changelog/`. Um "Última atualização" que acumula toda
a história do projeto numa única blockquote vira, ao mesmo tempo, um chunk
de RAG grande demais pra qualquer modelo de embedding representar bem e um
bloco difícil de buscar com precisão — problema real encontrado em produção,
não hipotético (ver `rag_eval.py` abaixo, criado por causa disso).

`rag_eval.py` roda uma lista de perguntas com fonte esperada conhecida
("golden queries") contra o índice já populado, sem chamar o Claude CLI —
rápido, determinístico, sem custo. Bugs de indexação (encoding corrompido,
chunk que sumiu, mudança no chunker) não geram erro no `rag_ingest.py`, que
sempre reporta sucesso mesmo tendo corrompido o índice inteiro; sem esse
teste, esses bugs só aparecem por acidente.

## Instalação

```
/plugin marketplace add juliomarquesjr/docs-maintainer-skill
/plugin install docs-maintainer@docs-maintainer
```

## Uso

```
/docs-maintainer:docs-maintainer
```

Ou deixe o Claude invocar automaticamente — a descrição da skill cobre os
gatilhos mais comuns (terminar uma tarefa de código, pedir para
importar/sincronizar documentação, perguntar como o sistema funciona).

## Testar localmente antes de publicar

```
claude plugin validate "<caminho desta pasta>" --strict
claude --plugin-dir "<caminho desta pasta>"
```

## Requisitos

- Python 3.10+ com `chromadb` instalado (`pip install -r scripts/requirements.txt` —
  a skill instala automaticamente se faltar)
- `claude` CLI no PATH, para o workflow de perguntas (`rag_query.py`)
- Drivers opcionais (`pymysql`, `psycopg2-binary`) só se for importar de
  MySQL/Postgres

## Licença

MIT — ver [LICENSE](LICENSE).
