---
name: docs-maintainer
description: Mantém a documentação de um projeto (docs/*.md + mkdocs opcional + RAG vetorial local em Chroma) sincronizada com o código. Use esta skill quando o usuário pedir para criar, atualizar, sincronizar ou auditar a documentação de um projeto; ao terminar uma tarefa de código que mudou comportamento documentado, para lembrar de manter docs + ledger de status + RAG em dia; quando o usuário pedir para importar ou sincronizar documentação vinda de um vault Obsidian, de um banco de dados, ou de outro repositório/pasta markdown; ou quando o usuário fizer uma pergunta sobre como um sistema/projeto funciona que deveria ser respondida consultando a documentação já indexada em vez de reler o código do zero.
---

# Docs Maintainer

Mantém documentação markdown + índice RAG (Chroma local) sincronizados com o
código de **qualquer** projeto — não assume a estrutura de nenhum projeto
específico. Também importa conteúdo de fontes externas (vault Obsidian,
banco de dados, outro repositório markdown) para dentro do padrão docs/RAG
do projeto atual.

**Princípio central: detectar antes de agir.** Nunca presuma que o projeto
já tem (ou não tem) docs/mkdocs/RAG — sempre confira primeiro. Nunca
sobrescreva scripts ou documentação que já existam no projeto.

## 0. Detectar o ambiente (sempre primeiro passo)

Rode estas checagens no projeto atual (`${CLAUDE_PROJECT_DIR}` ou o diretório
de trabalho atual) antes de qualquer workflow:

| O que checar | Como | Significado se existir |
|---|---|---|
| Pasta de docs | `Glob docs/**/*.md` | Projeto já tem documentação em markdown |
| Scripts RAG do próprio projeto | `Glob scripts/rag_ingest.py` (ou nome parecido — procure também `rag_*.py`) | Projeto já tem pipeline RAG próprio — **use esses scripts, não os desta skill** |
| Índice Chroma | verificar se existe a pasta apontada por esses scripts (padrão `chroma_db/`) | RAG já populado |
| `mkdocs.yml` | `Glob mkdocs.yml` | Site de docs já configurado |
| Ledger de status | `Glob docs/REG.md` ou nomes parecidos (`STATUS.md`, `ROADMAP.md`, `CHANGELOG` não conta) | Projeto já rastreia status de implementação por módulo |

Resultado da detecção decide o caminho:

- **Tudo já existe** (docs/ + scripts/rag_*.py do projeto + índice Chroma) →
  pule direto para os workflows 2–5, sempre invocando os scripts que já estão
  em `<projeto>/scripts/`, nunca os desta skill.
- **Nada existe** → Workflow 1 (Bootstrap).
- **Parcial** (ex.: tem `docs/` mas não tem pipeline RAG) → pergunte ao
  usuário se quer completar o que falta, reaproveitando o que já existe.
- **Ledger de status com changelog crescendo numa blockquote única no topo**
  (`grep -c` no arquivo mostra uma "Última atualização"/parágrafo enorme
  concatenando várias datas) → é o padrão antigo desta skill, já superado.
  Pergunte ao usuário se quer migrar: extrair cada entrada histórica pra um
  arquivo em `docs/changelog/`, deixando só a tabela de estado + "Mudanças
  Recentes" (últimas 3–5, uma linha cada) no ledger. Não faça essa migração
  sem perguntar — é uma reestruturação grande de um arquivo que o time já lê
  num formato conhecido.

## Scripts desta skill

Ficam em `${CLAUDE_PLUGIN_ROOT}/scripts/`. Os quatro primeiros são copiados
para dentro do projeto no Bootstrap (workflow 1) e passam a ser usados a
partir da cópia do projeto — não da skill. Os importadores **não** são
copiados: rode-os direto daqui, apontando `--docs-dir` para o projeto atual.

| Script | Função |
|---|---|
| `rag_chunker.py` | Divide `docs/*.md` em chunks por seção `##` (usado internamente pelos três abaixo) |
| `rag_ingest.py` | Indexa/atualiza os chunks no Chroma local (`--reset` recria do zero) |
| `rag_query.py` | Consulta o Chroma e responde via `claude --print` |
| `rag_eval.py` | Testa o retrieval contra golden queries (`rag_eval.json`) — roda depois de todo `rag_ingest.py`, sem chamar o Claude CLI (rápido, sem custo) |
| `import_obsidian.py` | Converte notas de um vault Obsidian para `docs/_imported/obsidian/` |
| `import_markdown_folder.py` | Copia markdown de outro repositório/pasta para `docs/_imported/<nome>/` |
| `import_db_docs.py` | Converte linhas de uma tabela/consulta SQL em `docs/_imported/db/` |

**Por que `rag_eval.py` existe:** bugs de indexação (encoding corrompido,
chunk que sumiu, mudança no chunker que quebrou uma seção) não geram erro —
`rag_ingest.py` sempre reporta sucesso mesmo tendo corrompido o índice
inteiro. Sem um teste de retrieval, isso só é descoberto por acidente,
perguntando algo e recebendo "não encontrei" pra informação que está
literalmente no doc. `rag_eval.py` transforma esse acidente em CI.

Antes de rodar qualquer script, garanta a dependência:

```bash
python -c "import chromadb" 2>NUL || pip install -r "${CLAUDE_PLUGIN_ROOT}/scripts/requirements.txt"
```

(No Windows use `2>NUL`; em bash/Linux/Mac use `2>/dev/null`.)

---

## Workflow 1 — Bootstrap (só quando docs/RAG não existem no projeto)

1. Pergunte ao usuário (via AskUserQuestion), com defaults sensatos, só o
   necessário:
   - Nome e descrição curta do projeto (para `mkdocs.yml`/prompt do RAG)
   - Idioma principal da documentação (padrão: o idioma em que o usuário está
     escrevendo)
   - Se quer `mkdocs.yml` também, ou só o pipeline RAG
   - Se quer um ledger de status (`docs/REG.md`) para rastrear implementação
     por módulo
2. Crie `docs/` (se não existir) e `docs/changelog/` junto — é onde toda
   entrada de changelog vai morar, uma por sessão/mudança, nunca acrescentada
   a um arquivo único (ver por quê no topo do `status-ledger.md.template`).
   Copie + preencha os templates de `${CLAUDE_PLUGIN_ROOT}/assets/`
   substituindo `{{site_name}}`, `{{site_description}}`, `{{lang}}`,
   `{{project_name}}`, `{{date}}`:
   - `docs-index.md.template` → `docs/index.md`
   - `status-ledger.md.template` → `docs/REG.md` (se o usuário quiser) — é
     só referência de estado atual + link pra `docs/changelog/`, não um
     changelog em si
   - `changelog-entry.md.template` → não copiar ainda; é o molde usado a
     cada nova entrada (workflow 2), mantenha só em `${CLAUDE_PLUGIN_ROOT}/assets/`
   - `mkdocs.yml.template` → `mkdocs.yml` na raiz (se o usuário quiser)
3. Copie os 4 scripts RAG (não os importadores) para dentro do projeto,
   versionados no git dele:
   ```bash
   mkdir -p scripts
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/rag_chunker.py" scripts/
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/rag_ingest.py" scripts/
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/rag_query.py" scripts/
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/rag_eval.py" scripts/
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/requirements.txt" scripts/
   ```
   A partir daqui, **sempre** use `scripts/rag_*.py` do projeto — essa cópia
   não depende mais desta skill estar instalada.
4. Rode a ingestão inicial a partir da raiz do projeto:
   ```bash
   pip install -r scripts/requirements.txt
   python scripts/rag_ingest.py --reset
   ```
   A primeira execução baixa o modelo de embedding padrão do Chroma (precisa
   de internet na primeira vez).
5. Confirme com uma consulta de teste: `python scripts/rag_query.py "do que trata este projeto?"`.
6. Crie um `rag_eval.json` inicial na raiz do projeto com 3–5 perguntas
   óbvias sobre os docs recém-criados (ex.: "do que trata este projeto?" →
   `docs/index.md`) e rode `python scripts/rag_eval.py` pra confirmar que
   passa. Cresça esse arquivo ao longo do projeto — cada bug de retrieval
   real que aparecer vira uma pergunta nova aqui, não só um "ah, RAG errou
   dessa vez".

## Workflow 2 — Sync após mudança de código (reforça a regra de ouro)

Use isso proativamente ao terminar qualquer tarefa de código que mudou
comportamento já documentado (nova rota, campo, variável de ambiente,
arquivo renomeado/removido, etc.) — não espere o usuário pedir.

1. Descubra o que mudou: `git status` / `git diff` (ou `git diff <base>...HEAD`
   se for revisar um branch inteiro).
2. Localize docs afetados (grep pelo nome do arquivo/símbolo mudado dentro de
   `docs/*.md`, e olhe o ledger de status se existir).
3. Atualize o conteúdo dos docs afetados e, se existir, a linha do ledger de
   status (status ✅/🔨/⬜ + observações) — isso é estado atual, edite in
   loco. Se a mudança introduziu um comportamento não óbvio (bug sutil,
   limite de tipo, ordem de chamadas), registre isso na seção "Armadilhas
   Conhecidas" do ledger.
4. Crie **um arquivo novo** `docs/changelog/{{date}}-slug-curto.md` (molde em
   `${CLAUDE_PLUGIN_ROOT}/assets/changelog-entry.md.template`) descrevendo a
   mudança — o quê, por quê, como, armadilhas, validação. **Nunca** acrescente
   essa narrativa ao topo do ledger de status nem a um changelog de arquivo
   único: cada entrada precisa ser seu próprio chunk RAG, senão vira
   informação presente mas praticamente invisível pra busca (ver a explicação
   no topo do `status-ledger.md.template`). Adicione um bullet de uma linha
   em "Mudanças Recentes" do ledger, removendo o mais antigo se passar de ~5.
5. Re-rode a ingestão (incremental, sem `--reset` — é upsert por id de
   chunk):
   ```bash
   python scripts/rag_ingest.py
   ```
6. Se existir `rag_eval.json`, rode `python scripts/rag_eval.py` — e se a
   mudança de hoje é algo que valeria a pena nunca mais regredir
   silenciosamente, adicione uma pergunta nova ao arquivo antes de seguir.
7. Avise o usuário em 1 frase o que foi atualizado.

## Workflow 3 — Importar fonte externa

Pergunte qual fonte (se não estiver claro pelo pedido do usuário) e os
parâmetros que faltarem:

**Vault Obsidian:**
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/import_obsidian.py" --vault "<caminho do vault>" --docs-dir docs
```
Peça o caminho do vault se não foi informado. Use `--filter` para importar só
uma subpasta/coleção de notas.

**Banco de dados:**
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/import_db_docs.py" --conn "<DSN ou arquivo .sqlite>" --table <tabela> --title-col <coluna> --body-col <coluna> --docs-dir docs
```
Peça a string de conexão, e se é uma tabela simples (`--table`) ou precisa de
`--query` customizado. **Nunca** grave a credencial em nenhum arquivo — passe
só na linha de comando ou peça pro usuário exportar numa variável de
ambiente antes.

**Outro repositório/pasta markdown:**
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/import_markdown_folder.py" --source "<pasta de origem>" --docs-dir docs
```

Depois de qualquer importação:
1. Leia os avisos do script (arquivos sem título `#`/sem heading `##` —
   ajuste manualmente os mais importantes se fizer sentido, já que o
   chunker depende desses headings).
2. Rode `python scripts/rag_ingest.py` (scripts do projeto, não os desta
   skill) para indexar o conteúdo novo.
3. Se o projeto tem `mkdocs.yml`, adicione o conteúdo relevante ao `nav`.

## Workflow 4 — Responder perguntas sobre o projeto

```bash
python scripts/rag_query.py "<pergunta>" --show-sources
```

Relate a resposta e as fontes. **"Não encontrei essa informação na
documentação" é sinal fraco, não resposta definitiva** — o RAG local usa um
modelo de embedding leve e `top-k` baixo por padrão; uma pergunta genérica
ou parafraseada pode falhar mesmo quando a informação está documentada
(caso real: perguntar sobre um recurso reaproveitando outro nome de
componente voltou "não encontrei" na primeira tentativa e encontrou de
primeira quando a pergunta citou o nome exato do arquivo/símbolo). Antes de
reportar como "não documentado" ao usuário:

1. Se você (agente) tem acesso a `grep`/leitura de arquivos no projeto,
   **valide direto no código-fonte** antes de aceitar o "não encontrei" —
   é mais rápido e mais confiável que insistir em reformular a query.
2. Se o grep também não achar nada, aí sim é seguro dizer "não está
   documentado" — e ofereça investigar mais a fundo ou atualizar a
   documentação depois.
3. Se a informação *estava* no código/doc e o RAG não achou, isso é uma
   falha de retrieval a registrar: considere adicionar a pergunta que falhou
   em `rag_eval.json` (se existir) pra virar teste de regressão, em vez de
   só seguir em frente.

## Workflow 5 — Auditoria código↔docs

Documentação tende a ficar defasada quando o código muda em várias sessões
sem journaling. Para auditar:

1. Use o Agent tool (general-purpose ou Explore) para cruzar as afirmações
   de cada doc (caminhos de arquivo, nomes de função/flag, variáveis de
   ambiente, contagem de tabelas/rotas) contra o código real do projeto —
   peça ao agente uma lista de discrepâncias concretas, não impressões
   gerais.
2. Reporte as divergências encontradas ao usuário.
3. Ofereça corrigir os docs (e o ledger de status, se existir) e rodar
   `python scripts/rag_ingest.py` em seguida.

## Sempre lembrar

Todo workflow (exceto o 4, que é só leitura) termina com: rodar a ingestão
(`rag_ingest.py`), rodar `rag_eval.py` se existir fixtures, e — se existir
um ledger de status no projeto — atualizá-lo (tabela de estado, nunca o
changelog acrescentado nele). Isso é o que mantém a "regra de ouro" — código
e documentação nunca divergem por mais que uma sessão, e o índice RAG nunca
fica quebrado silenciosamente por mais que uma sessão também.

**Nunca deixe o changelog crescer como um único bloco de texto no topo de um
arquivo.** É o erro estrutural mais caro desta skill: um "Última
atualização" que acumula toda a história do projeto numa única blockquote
vira, ao mesmo tempo, um chunk de RAG grande demais pra qualquer modelo de
embedding representar bem (perguntas específicas deixam de achar informação
que está literalmente ali) e um bloco difícil de `grep` com precisão. Uma
entrada por arquivo em `docs/changelog/` resolve os dois problemas com a
mesma mudança — e é mais barato de manter do que parece, porque cada
entrada é curta e isolada, em vez de exigir reler/editar um parágrafo gigante
toda vez.
