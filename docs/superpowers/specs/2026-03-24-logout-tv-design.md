# Design: Correcção do Logout Automático da Conta TV

**Data:** 2026-03-24
**Projecto:** posembarque
**Estado:** Aprovado

---

## Problema

A conta TV (nível `tv`) é deslogada automaticamente em dois cenários:
- Após longos períodos de inactividade (browser fechado de noite ou fim de semana)
- Após reinício do servidor gunicorn

---

## Causas Identificadas

### Causa 1 — `load_user` retorna `None` em falha de DB
Quando o PostgreSQL termina conexões idle (comum após horas sem uso), `load_user` lança uma excepção e retorna `None`. O Flask-Login interpreta `None` como "utilizador não existe" e desloga imediatamente.

### Causa 2 — Fuga de conexão em `load_user`
Se a query falha após `get_db_connection()` mas antes de `release_db_connection()`, a conexão nunca é devolvida ao pool. Com o auto-refresh de 3s no dashboard, o pool de 10 conexões esgota-se rapidamente após qualquer falha de DB, agravando a Causa 1.

### Causa 3 — Sessão expira após 8h de inactividade
`permanent_session_lifetime = 8h` aplica-se a todos os utilizadores. Se o browser TV fechar à noite e reabrir de manhã (>8h), a sessão expirou. O cookie `remember_me` deveria retomar, mas depende de `load_user` funcionar (volta à Causa 1).

---

## Design da Solução (Opção C)

### Componente 1: Cache do utilizador na sessão Flask

**No login**, guardar os dados essenciais do utilizador na sessão:

```python
session['user_cache'] = {
    'id': user_data['username'],
    'nome': user_data['nome'],
    'nivel': user_data['nivel'],
    'email': user_data['email']
}
```

**Em `load_user`**, adicionar `try/finally` para garantir devolução da conexão ao pool, e fallback para a sessão em caso de falha de DB:

```python
@login_manager.user_loader
def load_user(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = %s", (user_id,))
        u = cur.fetchone()
        if u:
            session['user_cache'] = {
                'id': u['username'], 'nome': u['nome'],
                'nivel': u['nivel'], 'email': u['email']
            }
            return User(u['username'], u['nome'], u['nivel'], u['email'])
        return None
    except Exception as e:
        logger.error(f"load_user: erro DB '{user_id}': {e}")
        cache = session.get('user_cache')
        if cache and cache['id'] == user_id:
            logger.warning(f"load_user: usando cache de sessão para '{user_id}'")
            return User(cache['id'], cache['nome'], cache['nivel'], cache['email'])
        return None
    finally:
        if conn:
            release_db_connection(conn)
```

**Trade-off aceite:** Se um utilizador for desactivado no DB, permanece "logado" até a sessão expirar naturalmente. Aceitável dado o contexto interno da aplicação.

**Resolve:** Causas 1 e 2.

---

### Componente 2: Lifetime de sessão por nível de utilizador

Ajustar `permanent_session_lifetime` dinamicamente em `before_request` com base no nível:

```python
@app.before_request
def verificar_timeout_sessao():
    if session.get('_user_id'):
        nivel = session.get('user_cache', {}).get('nivel')
        if nivel == 'tv':
            app.permanent_session_lifetime = timedelta(days=30)
        else:
            app.permanent_session_lifetime = timedelta(hours=8)
        session.modified = True
```

- **Conta TV:** sessão válida por 30 dias
- **Outros utilizadores:** mantêm os 8h actuais (comportamento inalterado)

**Resolve:** Causa 3.

---

### Componente 3: Verificação da nova conexão após recriar o pool

Adicionar `SELECT 1` na conexão obtida após `_recriar_pool()`:

```python
def get_db_connection():
    global db_pool
    try:
        conn = db_pool.getconn()
        if conn.closed:
            raise psycopg2.OperationalError("Conexão fechada.")
        try:
            conn.cursor().execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            try:
                db_pool.putconn(conn)
            except Exception:
                pass
            raise psycopg2.OperationalError("Conexão idle expirada.")
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        logger.warning(f"Reconectando ao banco de dados: {e}")
        _recriar_pool()
        conn = db_pool.getconn()
        try:
            conn.cursor().execute("SELECT 1")
        except Exception:
            raise psycopg2.OperationalError("Pool recriado mas conexão ainda inválida.")
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
```

Se o DB não estiver disponível mesmo após recriar o pool, lança excepção que é capturada em `load_user` — que usa o fallback da sessão (Componente 1).

**Resolve:** Robustez do pool após falhas.

---

## Ficheiros Afectados

- `app.py` — única alteração necessária (3 blocos de código)

## Impacto em Outras Áreas

- **Outros utilizadores** (operacional, gerencial, desenvolvedor): comportamento de sessão inalterado (8h)
- **Logout explícito**: continua a funcionar normalmente (`logout_user()` limpa sessão e cookie)
- **Segurança**: o `user_cache` está na sessão Flask (assinada com SECRET_KEY), não é manipulável pelo cliente

---

## Não Está em Scope

- Alterações ao frontend/templates
- Alterações à lógica de permissões por nível
- Alterações ao schema da base de dados
