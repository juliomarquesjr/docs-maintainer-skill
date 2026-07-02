"""
Import Markdown Folder — copia documentação markdown de outro repositório/pasta
para dentro de docs/*.md do projeto atual, para ser indexada pelo plugin
docs-maintainer.

Uso:
    python import_markdown_folder.py --source "C:/outro-repo/docs" --docs-dir docs
    python import_markdown_folder.py --source "../wiki" --pattern "**/*.md" --dest _imported/wiki

Copia os arquivos preservando a estrutura relativa de pastas. Não reescreve o
conteúdo — apenas avisa quando um arquivo não começa com um título "# ...",
já que o rag_chunker.py usa esse H1 como título do documento.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import resolve_path

H1_PATTERN = re.compile(r"^# .+", re.MULTILINE)


def main():
    parser = argparse.ArgumentParser(description="Importa markdown de outro repositório/pasta para docs/*.md")
    parser.add_argument("--source", required=True, help="Pasta de origem com arquivos markdown")
    parser.add_argument("--pattern", default="**/*.md", help="Glob relativo à origem (padrão: **/*.md)")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs do projeto, relativa à raiz ou absoluta (padrão: docs)")
    parser.add_argument("--dest", default=None,
                        help="Subpasta dentro de docs-dir onde gravar (padrão: _imported/<nome-da-origem>)")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        sys.exit(f"Pasta de origem não encontrada: {source}")

    docs_dir = resolve_path(args.docs_dir)
    dest_subdir = args.dest or f"_imported/{source.name}"
    dest_dir = docs_dir / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(f for f in source.glob(args.pattern) if f.is_file())
    if not files:
        sys.exit(f"Nenhum arquivo encontrado em {source} com o padrão '{args.pattern}'.")

    imported = 0
    flagged: list[str] = []
    for f in files:
        rel = f.relative_to(source)
        out_path = dest_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, out_path)
        imported += 1

        content = f.read_text(encoding="utf-8", errors="replace")
        if not H1_PATTERN.search(content):
            flagged.append(str(rel))
        print(f"  {rel} -> {out_path.relative_to(docs_dir)}")

    print(f"\n{imported} arquivo(s) importado(s) para {dest_dir}")
    if flagged:
        print(f"\nAviso: {len(flagged)} arquivo(s) sem título '# ...' no topo:")
        for r in flagged:
            print(f"  - {r}")
    print("\nPróximo passo: python rag_ingest.py")


if __name__ == "__main__":
    main()
