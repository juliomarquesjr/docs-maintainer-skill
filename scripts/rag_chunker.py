"""
RAG Chunker — divide docs Markdown em chunks pequenos e auto-contidos para RAG.

Lê todos os .md de um diretório de docs e divide em duas etapas:
  1. por seção (heading `##`);
  2. dentro de cada seção, em SUB-CHUNKS de ~TARGET_CHARS com sobreposição
     (OVERLAP_CHARS) entre blocos vizinhos.

Cada sub-chunk é prefixado com "{título do doc} › {seção}", para que o embedding
tenha o contexto temático mesmo num pedaço do meio da seção. Isso importa porque
o modelo de embedding tem janela curta (o multilíngue MiniLM trunca ~128 tokens)
e só "vê" o início de cada chunk — se o chunk for a seção inteira (centenas de
tokens), o embedding ignora quase tudo. Sub-chunks curtos + prefixo de contexto
resolvem isso. Ver scripts/rag_embedding.py.

Uso:
    python rag_chunker.py                         # imprime JSON no stdout (docs/ na raiz do projeto)
    python rag_chunker.py --docs-dir docs --out chunks.json
    python rag_chunker.py --min-chars 100         # ignora seções muito curtas
    python rag_chunker.py --target-chars 450 --overlap-chars 90

ATENÇÃO: mudar o esquema de chunking muda os ids dos chunks. Re-indexe do zero
(`rag_ingest.py --reset`) para não deixar chunks órfãos no Chroma.

Parte do plugin docs-maintainer (adaptado). rag_ingest.py e rag_query.py importam
as funções deste módulo — os três arquivos devem ficar sempre na mesma pasta.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"migrations", "node_modules", ".git", "_site", "site"}
H2_PATTERN = re.compile(r"^## .+", re.MULTILINE)

# Versão do ESQUEMA de chunking. Faça bump (+1) sempre que mudar o algoritmo de
# fatiamento ou o formato dos ids/prefixos (qualquer coisa que altere os chunks
# gerados para um mesmo doc). O rag.lock.json grava este número; rag_verify.py
# acusa "fora de sync" quando o lock foi gerado por um esquema diferente do atual,
# forçando um re-ingest (`rag_ingest.py --reset`) em vez de comparar chunks
# incompatíveis. Ver scripts/rag_manifest.py.
SCHEMA_VERSION = 2
# v2: id pelo caminho relativo (nao pelo nome do arquivo), chunk da introducao
# antes do primeiro `##`, e os .md da raiz do projeto entram no indice.

# Tamanho-alvo do CORPO de cada sub-chunk (sem o prefixo de contexto). ~450 chars
# ≈ ~120 tokens em PT, para caber na janela do modelo de embedding multilíngue.
TARGET_CHARS = 450
# Sobreposição textual (em chars) reaproveitada no início do bloco seguinte, para
# não cortar uma ideia exatamente na fronteira entre dois chunks.
OVERLAP_CHARS = 90
# Cauda residual menor que isto é descartada (evita chunk-lixo no fim da seção).
MIN_BLOCK_CHARS = 40


def resolve_path(value: str, base: Path = ROOT_DIR) -> Path:
    """Resolve um caminho relativo à raiz do projeto (ou absoluto, se já for)."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text)


def extract_doc_title(content: str) -> str:
    match = re.search(r"^# (.+)", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


# .md da raiz que nunca entram: instrucao para agente nao e documentacao.
ROOT_DOC_EXCLUDES = {"claude.md", "agents.md"}
INCLUDE_HELP = (
    "Arquivo .md avulso fora de docs/ a indexar junto (repetivel). Padrao: os .md "
    "da raiz do projeto VERSIONADOS no git, menos CLAUDE.md e AGENTS.md. "
    'Use --include "" para indexar apenas docs/.'
)


def _root_md_in_git() -> list[str] | None:
    """Os .md da raiz que o git versiona, ou None fora de um repositorio git.

    So os versionados: um rascunho solto na raiz (guia para outro time, nota
    pessoal) entraria no indice e competiria no retrieval com a documentacao de
    verdade -- caso real, medido num projeto que usa esta skill.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", "*.md"], cwd=ROOT_DIR,
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [linha for linha in out.splitlines() if linha and "/" not in linha]


def default_includes() -> list[str]:
    """Os .md da raiz do projeto, menos os excluidos, em ordem estavel."""
    nomes = _root_md_in_git()
    if nomes is None:
        nomes = [f.name for f in ROOT_DIR.glob("*.md")]
    return sorted(n for n in nomes if n.lower() not in ROOT_DOC_EXCLUDES)


def resolve_includes(values: list[str] | None) -> list[Path]:
    """Traduz os --include em caminhos absolutos, avisando o que nao existe.

    `--include ""` (string vazia) desliga o padrao sem precisar de outra flag.
    """
    names = default_includes() if values is None else [v for v in values if v.strip()]
    paths = []
    for name in names:
        path = resolve_path(name)
        if path.is_file():
            paths.append(path)
        else:
            print(f"aviso: --include {name} nao encontrado ({path}) -- ignorado", file=sys.stderr)
    return paths


def relative_source(filepath: Path, docs_dir: Path) -> str:
    """Caminho legivel do arquivo para o metadado `source` do chunk.

    O README da raiz nao e relativo a docs_dir.parent quando docs/ nao fica na
    raiz, entao cai para a raiz do projeto antes de desistir e usar so o nome.
    """
    for base in (docs_dir.parent, ROOT_DIR):
        try:
            return filepath.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return filepath.name


def discover_md_files(docs_dir: Path, extra_files: list[Path] | None = None) -> list[Path]:
    """Todos os .md de docs/ mais os arquivos avulsos pedidos em --include.

    O README da raiz costuma ser o documento principal. Copia-lo para docs/
    criaria uma segunda copia do mesmo texto, e as duas divergem em silencio --
    por isso ele e lido onde esta.
    """
    found = [
        f for f in docs_dir.rglob("*.md")
        if not any(part in SKIP_DIRS for part in f.parts)
    ]
    seen = {f.resolve() for f in found}
    for extra in (extra_files or []):
        if extra.is_file() and extra.resolve() not in seen:
            found.append(extra)
            seen.add(extra.resolve())
    return sorted(found)


def doc_id_for(source: str) -> str:
    """Prefixo do id dos chunks de um arquivo, derivado do caminho relativo.

    Pelo nome do arquivo (`stem`), `docs/a/index.md` e `docs/b/index.md`
    gerariam os mesmos ids, e o upsert de um sobrescreveria o outro sem erro.
    """
    stem_path = source[:-3] if source.endswith(".md") else source
    return slugify(stem_path.replace("/", "--"))


def _to_pieces(text: str, target: int) -> list[str]:
    """Quebra o texto em 'peças' indivisíveis <= target quando possível: por
    parágrafo; parágrafo grande vira linhas; linha gigante vira fatias fixas."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip("\n")
        if not para.strip():
            continue
        if len(para) <= target:
            pieces.append(para)
            continue
        for line in para.split("\n"):
            if len(line) <= target:
                if line.strip():
                    pieces.append(line)
            else:
                for k in range(0, len(line), target):
                    frag = line[k:k + target]
                    if frag.strip():
                        pieces.append(frag)
    return pieces


def _pack(pieces: list[str], target: int, overlap: int) -> list[str]:
    """Agrupa peças em blocos de ~target chars, repetindo a cauda (~overlap chars,
    a partir de uma fronteira de espaço) no início do bloco seguinte."""
    blocks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > target:
            blocks.append(cur)
            tail = cur[-overlap:] if overlap and len(cur) > overlap else ""
            if tail:
                sp = tail.find(" ")
                if sp != -1:
                    tail = tail[sp + 1:]
                cur = f"{tail}\n\n{piece}"
            else:
                cur = piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        blocks.append(cur)
    return blocks


def _section_body(section_text: str) -> str:
    """Remove a 1ª linha (o heading `##`) e devolve o corpo da seção."""
    nl = section_text.find("\n")
    return section_text[nl + 1:].strip() if nl != -1 else ""


def _emit(filepath: Path, source: str, doc_title: str, section_heading: str,
          body: str, min_chars: int, target: int, overlap: int,
          slug_base: str | None = None) -> list[dict]:
    """Gera os sub-chunks de uma seção (ou do documento inteiro, se sem `##`)."""
    body = body.strip()
    if len(body) < min_chars:
        return []
    prefix = f"{doc_title} › {section_heading}".strip(" ›") if section_heading else (doc_title or "")
    slug_base = slug_base or (slugify(section_heading) if section_heading else "documento")
    blocks = _pack(_to_pieces(body, target), target, overlap) or [body]

    out: list[dict] = []
    for j, block in enumerate(blocks):
        block = block.strip()
        if not block or (len(block) < MIN_BLOCK_CHARS and len(blocks) > 1):
            continue
        content = f"{prefix}\n\n{block}" if prefix else block
        out.append({
            "id": f"{doc_id_for(source)}::{slug_base}::{j}",
            "source": source,
            "doc_title": doc_title,
            "section": section_heading or doc_title,
            "content": content,
            "chars": len(content),
        })
    return out


def split_into_chunks(filepath: Path, docs_dir: Path, min_chars: int,
                      target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    content = filepath.read_text(encoding="utf-8")
    doc_title = extract_doc_title(content)
    source = relative_source(filepath, docs_dir)

    splits = list(H2_PATTERN.finditer(content))

    if not splits:
        # Documento sem `##`: trata o corpo inteiro (menos o título `#`) como uma seção.
        body = re.sub(r"^# .+\n?", "", content, count=1).strip()
        return _emit(filepath, source, doc_title, "", body, min_chars, target, overlap)

    chunks: list[dict] = []

    # O texto antes do primeiro `##` costuma ser o que diz para que o documento
    # serve -- justamente o que responde "onde esta X?". Sem este chunk ele
    # nunca entraria no indice.
    preamble = re.sub(r"^# .+\n?", "", content[:splits[0].start()], count=1).strip()
    chunks.extend(_emit(filepath, source, doc_title, "", preamble,
                        min_chars, target, overlap, slug_base="introducao"))

    for i, match in enumerate(splits):
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        section_text = content[start:end].strip()
        section_heading = match.group(0).lstrip("# ").strip()
        body = _section_body(section_text)
        chunks.extend(_emit(filepath, source, doc_title, section_heading, body,
                            min_chars, target, overlap))

    return chunks


ARCHIVE_PREFIX = "docs/changelog/"


def diversify_by_source(metadatas, top_k, max_per_source, max_archive=2):
    """Indices dos chunks a manter, limitando quantos vem do mesmo arquivo.

    Sem isto um documento longo e recente monopoliza o top-k: um changelog de
    seis secoes ocupou as posicoes 1-4 e 6-7 numa pergunta cuja resposta de
    referencia estava no README, em 5o lugar.

    `max_archive` limita o TOTAL de chunks vindos de docs/changelog/, somando
    todos os arquivos de la -- cada entrada e um arquivo diferente, entao o
    limite por fonte sozinho deixa tres entradas ocuparem o top-k inteiro.
    Changelog e narrativa historica: deve continuar alcancavel, mas nao pode
    afogar a referencia em perguntas sobre o estado atual. O acervo so cresce.

    A ordem de relevancia e preservada, o que exige buscar mais candidatos do
    que o top-k. ATENCAO: quando a diversificacao nao enche o top-k, os pulados
    voltam no fim (repesco) -- uma golden query pode passar so por isso, e
    qualquer doc novo a derruba sem o esperado ter piorado. Olhe as distancias
    antes de "consertar" texto.
    """
    escolhidos, por_fonte, do_acervo = [], {}, 0
    for i, meta in enumerate(metadatas):
        fonte = meta.get("source", "")
        if por_fonte.get(fonte, 0) >= max_per_source:
            continue
        if fonte.startswith(ARCHIVE_PREFIX):
            if do_acervo >= max_archive:
                continue
            # Dentro do acervo o limite por arquivo cai para 1: cada entrada e
            # um assunto distinto, e uma delas com as duas vagas expulsa as outras.
            if por_fonte.get(fonte, 0) >= 1:
                continue
            do_acervo += 1
        por_fonte[fonte] = por_fonte.get(fonte, 0) + 1
        escolhidos.append(i)
        if len(escolhidos) == top_k:
            break

    if len(escolhidos) < top_k:
        ja = set(escolhidos)
        restantes = [i for i in range(len(metadatas)) if i not in ja]
        escolhidos.extend(restantes[: top_k - len(escolhidos)])

    return escolhidos


def candidates_for(top_k: int, max_per_source: int) -> int:
    """Quantos candidatos buscar no Chroma para a diversificacao ter folga.

    Com sub-chunks de ~450 chars, um documento so ocupa facilmente as primeiras
    dezenas de posicoes; com lote pequeno a diversificacao fica sem outra fonte
    e o repesco devolve o mesmo arquivo seis vezes. Medido num projeto real
    (162 golden queries, 3.5 mil chunks): lote de 18 deu 143, de 30 deu 146,
    de 60 tambem 146 -- 5x o top-k basta.
    """
    return top_k * max(5, max_per_source + 1)


def chunk_docs(docs_dir: Path, min_chars: int,
               target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS,
               extra_files: list[Path] | None = None) -> list[dict]:
    all_chunks: list[dict] = []
    for filepath in discover_md_files(docs_dir, extra_files):
        all_chunks.extend(split_into_chunks(filepath, docs_dir, min_chars, target, overlap))
    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Gera chunks RAG a partir dos docs markdown de um projeto")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs, relativa à raiz do projeto ou absoluta (padrão: docs)")
    parser.add_argument("--out", help="Arquivo de saída JSON (padrão: stdout)")
    parser.add_argument("--min-chars", type=int, default=80, metavar="N",
                        help="Ignora seções com menos de N caracteres (padrão: 80)")
    parser.add_argument("--target-chars", type=int, default=TARGET_CHARS, metavar="N",
                        help=f"Tamanho-alvo do corpo de cada sub-chunk (padrão: {TARGET_CHARS})")
    parser.add_argument("--overlap-chars", type=int, default=OVERLAP_CHARS, metavar="N",
                        help=f"Sobreposição entre sub-chunks vizinhos (padrão: {OVERLAP_CHARS})")
    parser.add_argument("--include", action="append", default=None, metavar="ARQ", help=INCLUDE_HELP)
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    if not docs_dir.exists():
        sys.exit(f"Diretório de docs não encontrado: {docs_dir}")

    all_chunks = chunk_docs(docs_dir, args.min_chars, args.target_chars, args.overlap_chars,
                            resolve_includes(args.include))
    output = json.dumps(all_chunks, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"{len(all_chunks)} chunks → {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
