# docs-maintainer

Plugin/skill do [Claude Code](https://claude.com/claude-code) que ajuda a
criar e manter o ambiente de documentação de qualquer projeto: `docs/*.md` +
`mkdocs` (opcional) + um índice RAG vetorial local em [Chroma](https://www.trychroma.com/),
100% offline (sem chave de API — o modelo de embedding roda na própria máquina).

Também sabe importar conteúdo de fontes externas para dentro do padrão
docs/RAG do projeto:

- Vault [Obsidian](https://obsidian.md/)
- Documentação armazenada em banco de dados (SQLite, MySQL, Postgres)
- Markdown de outro repositório/pasta

## O que ela faz

- **Bootstrap**: cria `docs/` (+ `docs/changelog/`), `mkdocs.yml` (opcional)
  e o pipeline RAG num projeto que ainda não tem nada disso — os scripts ficam
  versionados no repositório do projeto, sem depender deste plugin depois.
- **Verificação**: `rag_verify.py` responde "meu índice está em dia?" comparando
  o Chroma local contra o `rag.lock.json` versionado.
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

## Busca em português

O modelo de embedding padrão é `paraphrase-multilingual-MiniLM-L12-v2`
(sentence-transformers): 50+ idiomas, 384 dimensões, forte em **português**.

Isso importa mais do que parece. O modelo padrão do Chroma é treinado em
inglês; usado sobre documentação em português ele **não dá erro** — apenas
devolve o trecho menos certo, e a perda passa despercebida porque a busca
"funciona". Trocar o modelo exige re-ingerir com `--reset`, porque vetores de
modelos diferentes não são comparáveis. Use `RAG_EMBED_MODEL` para escolher
outro.

## O `rag.lock.json`

O índice Chroma é binário, regenerável e **não** vai para o git. Quem clona o
projeto — ou dá `git pull` num branch com docs novos — fica com um índice
defasado, e nada avisa: a busca continua respondendo, com o conteúdo de ontem.

O lock é a prova versionada do que a indexação deveria conter: hash de cada
documento, contagem de chunks, modelo de embedding, parâmetros de chunking e
versões da toolchain. Ele é commitado junto com os `docs/*.md`, e
`rag_verify.py` compara o índice local contra ele.

Por isso as versões em `scripts/requirements.txt` são **pinadas**, não faixas
abertas: são elas que determinam os vetores, e o lock só tem valor se a equipe
inteira produzir a mesma indexação a partir dos mesmos documentos.

**A regra que faz isso valer:** editou `docs/*.md` → re-indexe e commite o
`rag.lock.json` na mesma tarefa; depois de um `git pull` → rode `rag_verify.py`.

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

- Python 3.10+ com `chromadb` e `sentence-transformers` instalados
  (`pip install -r scripts/requirements.txt` — a skill instala automaticamente se
  faltar). A primeira execução baixa o modelo de embedding (~470 MB); depois
  disso funciona offline.
- `claude` CLI no PATH, para o workflow de perguntas (`rag_query.py`)
- Drivers opcionais (`pymysql`, `psycopg2-binary`) só se for importar de
  MySQL/Postgres

## Licença

MIT — ver [LICENSE](LICENSE).
