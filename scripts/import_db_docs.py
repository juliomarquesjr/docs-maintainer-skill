"""
Import DB Docs — converte linhas de uma tabela/consulta de banco de dados em
docs/*.md para serem indexadas pelo plugin docs-maintainer.

Uso:
    # SQLite
    python import_db_docs.py --conn "meubanco.sqlite" --table artigos \\
        --title-col titulo --body-col conteudo

    # MySQL (requer: pip install pymysql)
    python import_db_docs.py --conn "mysql://usuario:senha@host:3306/banco" \\
        --query "SELECT titulo AS title, corpo AS body FROM artigos WHERE ativo = 1"

    # Postgres (requer: pip install psycopg2-binary)
    python import_db_docs.py --conn "postgres://usuario:senha@host:5432/banco" \\
        --table artigos --title-col titulo --body-col corpo

Ao usar --query customizado, dê um "AS" nas colunas para bater com
--title-col/--body-col (padrão: title/body), já que a busca das colunas é
feita pelo nome retornado pela consulta, não pela posição.

Nunca passe credenciais de produção direto na linha de comando de um terminal
compartilhado/logado; prefira variável de ambiente (--conn "$DB_DSN") ou um
arquivo de config ignorado pelo git. Este script nunca persiste --conn em
lugar nenhum.
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_chunker import resolve_path, slugify


def fetch_rows(conn_str: str, query: str) -> tuple[list[str], list[tuple]]:
    scheme = urlparse(conn_str).scheme

    if not scheme or conn_str.endswith((".db", ".sqlite", ".sqlite3")):
        import sqlite3
        conn = sqlite3.connect(conn_str)
        try:
            cur = conn.execute(query)
            columns = [d[0] for d in cur.description]
            return columns, cur.fetchall()
        finally:
            conn.close()

    if scheme in ("mysql", "mysql+pymysql"):
        try:
            import pymysql
        except ImportError:
            sys.exit("Instale o driver MySQL: pip install pymysql")
        parsed = urlparse(conn_str)
        conn = pymysql.connect(
            host=parsed.hostname, port=parsed.port or 3306,
            user=parsed.username, password=parsed.password or "",
            database=parsed.path.lstrip("/"),
        )
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                columns = [d[0] for d in cur.description]
                return columns, cur.fetchall()
        finally:
            conn.close()

    if scheme in ("postgres", "postgresql"):
        try:
            import psycopg2
        except ImportError:
            sys.exit("Instale o driver Postgres: pip install psycopg2-binary")
        conn = psycopg2.connect(conn_str)
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                columns = [d[0] for d in cur.description]
                return columns, cur.fetchall()
        finally:
            conn.close()

    sys.exit(f"Esquema de conexão não suportado: '{scheme}'. Use sqlite, mysql ou postgres.")


def main():
    parser = argparse.ArgumentParser(description="Importa documentação armazenada em banco de dados para docs/*.md")
    parser.add_argument("--conn", required=True,
                        help="Caminho do arquivo SQLite ou DSN (mysql://... / postgres://...)")
    parser.add_argument("--query", default=None,
                        help="SQL customizado (SELECT título, corpo, ...). Se omitido, usa --table")
    parser.add_argument("--table", default=None, help="Tabela a consultar")
    parser.add_argument("--title-col", default="title", help="Nome da coluna de título (padrão: title)")
    parser.add_argument("--body-col", default="body", help="Nome da coluna de corpo (padrão: body)")
    parser.add_argument("--docs-dir", default="docs",
                        help="Pasta de docs do projeto, relativa à raiz ou absoluta (padrão: docs)")
    parser.add_argument("--dest", default="_imported/db",
                        help="Subpasta dentro de docs-dir onde gravar (padrão: _imported/db)")
    args = parser.parse_args()

    if not args.query and not args.table:
        sys.exit("Informe --query ou --table.")

    query = args.query or f"SELECT {args.title_col}, {args.body_col} FROM {args.table}"
    columns, rows = fetch_rows(args.conn, query)
    if not rows:
        sys.exit("A consulta não retornou nenhuma linha.")

    try:
        title_idx = columns.index(args.title_col)
    except ValueError:
        title_idx = 0
    try:
        body_idx = columns.index(args.body_col)
    except ValueError:
        body_idx = 1 if len(columns) > 1 else 0

    docs_dir = resolve_path(args.docs_dir)
    dest_dir = docs_dir / args.dest
    dest_dir.mkdir(parents=True, exist_ok=True)

    imported = 0
    for row in rows:
        title = str(row[title_idx]).strip()
        body = str(row[body_idx]).strip() if len(row) > body_idx else ""
        filename = f"{slugify(title) or f'doc-{imported + 1}'}.md"
        out_path = dest_dir / filename
        out_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        imported += 1
        print(f"  {title} -> {out_path.relative_to(docs_dir)}")

    print(f"\n{imported} documento(s) importado(s) para {dest_dir}")
    print("\nPróximo passo: python rag_ingest.py")


if __name__ == "__main__":
    main()
