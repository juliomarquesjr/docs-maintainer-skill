"""
Import Obsidian — converte notas de um vault Obsidian para o padrão docs/*.md
usado pelo plugin docs-maintainer (compatível com rag_chunker.py).

Uso:
    python import_obsidian.py --vault "C:/Users/voce/ObsidianVault" --docs-dir docs
    python import_obsidian.py --vault "C:/vault" --filter "Projetos/**/*.md" --dest _imported/obsidian

O que faz:
    - Ignora as pastas de configuração/lixeira do Obsidian (.obsidian/, .trash/)
    - Remove o frontmatter YAML (--- ... ---) do início de cada nota
    - Garante um título "# Título" no topo (usa o H1 existente ou o nome do arquivo)
    - Preserva o corpo da nota como está — wikilinks [[Nota]] ficam como texto
      legível, sem precisar resolver o link
    - Grava em <docs-dir>/<dest>/, preservando a estrutura de subpastas do vault

Depois de importar, rode rag_chunker.py / rag_ingest.py normalmente para
indexar o conteúdo novo. Notas sem nenhum heading "##" viram um único chunk
grande cada — a saída deste script avisa quais notas caem nesse caso.
"""

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import resolve_path

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
H1_PATTERN = re.compile(r"^# (.+)", re.MULTILINE)
H2_PATTERN = re.compile(r"^## .+", re.MULTILINE)
SKIP_DIRS = {".obsidian", ".trash"}


def strip_frontmatter(content: str) -> str:
    return FRONTMATTER_PATTERN.sub("", content, count=1)


def ensure_title(content: str, fallback_title: str) -> str:
    body = strip_frontmatter(content).lstrip("\n")
    if H1_PATTERN.match(body):
        return body
    return f"# {fallback_title}\n\n{body}"


def convert_note(note_path: Path) -> tuple[str, bool]:
    content = note_path.read_text(encoding="utf-8")
    converted = ensure_title(content, note_path.stem)
    has_sections = bool(H2_PATTERN.search(converted))
    return converted, has_sections


def main():
    parser = argparse.ArgumentParser(description="Importa notas de um vault Obsidian para docs/*.md")
    parser.add_argument("--vault", required=True, help="Caminho do vault Obsidian")
    parser.add_argument("--filter", default="**/*.md",
                        help="Glob relativo ao vault para filtrar notas (padrão: **/*.md)")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs do projeto, relativa à raiz ou absoluta (padrão: docs)")
    parser.add_argument("--dest", default="_imported/obsidian",
                        help="Subpasta dentro de docs-dir onde gravar as notas convertidas")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        sys.exit(f"Vault não encontrado: {vault}")

    docs_dir = resolve_path(args.docs_dir)
    dest_dir = docs_dir / args.dest
    dest_dir.mkdir(parents=True, exist_ok=True)

    notes = sorted(
        f for f in vault.glob(args.filter)
        if f.is_file() and f.suffix == ".md"
        and not any(part in SKIP_DIRS for part in f.relative_to(vault).parts)
    )

    if not notes:
        sys.exit(f"Nenhuma nota .md encontrada em {vault} com o filtro '{args.filter}'.")

    imported = 0
    flagged: list[str] = []
    for note in notes:
        converted, has_sections = convert_note(note)
        rel = note.relative_to(vault)
        out_path = dest_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(converted, encoding="utf-8")
        imported += 1
        if not has_sections:
            flagged.append(str(rel))
        print(f"  {rel} -> {out_path.relative_to(docs_dir)}")

    print(f"\n{imported} nota(s) importada(s) para {dest_dir}")
    if flagged:
        print(f"\nAviso: {len(flagged)} nota(s) sem heading '##' — serão indexadas como 1 chunk único cada:")
        for f in flagged:
            print(f"  - {f}")
    print("\nPróximo passo: python rag_ingest.py")


if __name__ == "__main__":
    main()
