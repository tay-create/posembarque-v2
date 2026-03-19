"""
Script de migração: SQLite (painel.db) -> PostgreSQL (posembarque-transnet)
"""
import sqlite3
import psycopg2
import psycopg2.extras
import json

SQLITE_PATH = 'c:/posembarque/painel.db'
PG_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'dbname': 'posembarque-transnet',
    'user': 'postgres',
    'password': '124578595'
}

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS motoristas (
    nome TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS clientes (
    nome TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS usuarios (
    username TEXT PRIMARY KEY,
    senha TEXT,
    nivel TEXT,
    nome TEXT,
    email TEXT,
    token_reset TEXT,
    token_expiracao TEXT
);

CREATE TABLE IF NOT EXISTS sistema_patches (
    id SERIAL PRIMARY KEY,
    titulo TEXT,
    itens TEXT,
    data_lancamento TEXT
);

CREATE TABLE IF NOT EXISTS sistema_views (
    usuario_id TEXT,
    patch_id INTEGER,
    contagem INTEGER DEFAULT 0,
    PRIMARY KEY (usuario_id, patch_id)
);

CREATE TABLE IF NOT EXISTS chamados (
    id SERIAL PRIMARY KEY,
    usuario TEXT,
    titulo TEXT,
    mensagem TEXT,
    status TEXT DEFAULT 'ABERTO',
    data_abertura TEXT,
    data_resolucao TEXT,
    resposta_dev TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    data_hora TEXT,
    acao TEXT,
    detalhes TEXT
);

CREATE TABLE IF NOT EXISTS ocorrencias (
    id SERIAL PRIMARY KEY,
    data_ocorrencia TEXT,
    hora_ocorrencia TEXT,
    motorista TEXT,
    modalidade TEXT,
    cte TEXT,
    operacao TEXT,
    nfs TEXT,
    cliente TEXT,
    cidade TEXT,
    motivo TEXT,
    situacao TEXT,
    data_conclusao TEXT,
    hora_conclusao TEXT,
    responsavel TEXT,
    arquivado INTEGER DEFAULT 0,
    fotos TEXT,
    status_edicao TEXT DEFAULT 'BLOQUEADO',
    link_email TEXT,
    motivo_edicao TEXT
);
"""

def migrate():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg2.connect(**PG_CONFIG)
    pg_cur = pg_conn.cursor()

    print("Criando tabelas no PostgreSQL...")
    pg_cur.execute(CREATE_TABLES_SQL)
    pg_conn.commit()
    print("Tabelas criadas com sucesso.\n")

    # Tabelas simples (sem SERIAL)
    simple_tables = ['motoristas', 'clientes', 'usuarios']
    for table in simple_tables:
        sqlite_cur.execute(f'SELECT * FROM "{table}"')
        rows = sqlite_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 linhas (pulando)")
            continue
        cols = [desc[0] for desc in sqlite_cur.description]
        placeholders = ','.join(['%s'] * len(cols))
        col_names = ','.join(cols)
        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        for row in rows:
            pg_cur.execute(insert_sql, list(row))
        pg_conn.commit()
        print(f"  {table}: {len(rows)} linhas migradas")

    # Tabelas com SERIAL (id manual)
    serial_tables = {
        'sistema_patches': ['id', 'titulo', 'itens', 'data_lancamento'],
        'chamados': ['id', 'usuario', 'titulo', 'mensagem', 'status', 'data_abertura', 'data_resolucao', 'resposta_dev'],
        'logs': ['id', 'data_hora', 'acao', 'detalhes'],
        'ocorrencias': ['id', 'data_ocorrencia', 'hora_ocorrencia', 'motorista', 'modalidade', 'cte', 'operacao',
                        'nfs', 'cliente', 'cidade', 'motivo', 'situacao', 'data_conclusao', 'hora_conclusao',
                        'responsavel', 'arquivado', 'fotos', 'status_edicao', 'link_email', 'motivo_edicao'],
    }

    for table, cols in serial_tables.items():
        sqlite_cur.execute(f'SELECT * FROM "{table}"')
        rows = sqlite_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 linhas (pulando)")
            continue
        sqlite_cols = [desc[0] for desc in sqlite_cur.description]
        placeholders = ','.join(['%s'] * len(sqlite_cols))
        col_names = ','.join(sqlite_cols)
        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        for row in rows:
            pg_cur.execute(insert_sql, list(row))
        pg_conn.commit()
        # Atualizar sequence do SERIAL para o max id
        pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table}")
        pg_conn.commit()
        print(f"  {table}: {len(rows)} linhas migradas")

    # sistema_views (chave composta, sem serial)
    sqlite_cur.execute('SELECT * FROM sistema_views')
    rows = sqlite_cur.fetchall()
    if rows:
        for row in rows:
            pg_cur.execute(
                "INSERT INTO sistema_views (usuario_id, patch_id, contagem) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (row['usuario_id'], row['patch_id'], row['contagem'])
            )
        pg_conn.commit()
        print(f"  sistema_views: {len(rows)} linhas migradas")
    else:
        print("  sistema_views: 0 linhas (pulando)")

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigração concluída com sucesso!")

if __name__ == '__main__':
    migrate()
