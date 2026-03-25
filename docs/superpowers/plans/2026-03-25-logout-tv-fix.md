# Correcção do Logout Automático da Conta TV — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar o logout automático da conta TV causado por falhas temporárias de DB, fuga de conexões e expiração de sessão após 8h de inactividade.

**Architecture:** Três alterações cirúrgicas em `app.py`: (1) `load_user` com `try/finally` e fallback para cache de sessão; (2) `verificar_timeout_sessao` com lifetime dinâmico por nível; (3) `get_db_connection` com verificação da conexão após recriar o pool. Nenhuma alteração ao schema de DB ou ao frontend.

**Tech Stack:** Python 3.12, Flask 3.x, Flask-Login, psycopg2, pytest

---

## Ficheiros Afectados

| Ficheiro | Operação | Responsabilidade |
|---|---|---|
| `app.py` | Modify (linhas 85-89, 109-124, 144-167, 922-934) | Todas as 3 correcções |
| `tests/test_auth_resilience.py` | Create | Testes unitários das correcções |

---

### Task 1: Cache de sessão no login + `load_user` robusto

**Ficheiros:**
- Modify: `app.py:109-124` (`load_user`)
- Modify: `app.py:922-934` (rota `login`)
- Create: `tests/test_auth_resilience.py`

- [ ] **Step 1: Instalar pytest se necessário**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
pip show pytest || pip install pytest
```

- [ ] **Step 2: Criar o ficheiro de testes com testes que falham**

Criar `tests/test_auth_resilience.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Patch DB antes de importar app
with patch('psycopg2.pool.SimpleConnectionPool'):
    import app as flask_app

@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    flask_app.app.config['SECRET_KEY'] = 'test-secret'
    flask_app.app.config['WTF_CSRF_ENABLED'] = False
    with flask_app.app.test_client() as c:
        yield c

# --- Task 1: load_user com fallback de sessão ---

def test_load_user_retorna_user_do_db_quando_db_ok():
    """load_user retorna User quando DB responde correctamente."""
    mock_row = {'username': 'tv1', 'nome': 'TV Sala', 'nivel': 'tv', 'email': None}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.fetchone.return_value = mock_row

    with flask_app.app.test_request_context():
        with patch.object(flask_app, 'get_db_connection', return_value=mock_conn):
            with patch.object(flask_app, 'release_db_connection'):
                user = flask_app.load_user('tv1')
                assert user is not None
                assert user.id == 'tv1'
                assert user.nivel == 'tv'


def test_load_user_usa_cache_quando_db_falha():
    """load_user usa cache da sessão quando DB lança excepção."""
    with flask_app.app.test_request_context():
        with flask_app.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['user_cache'] = {
                    'id': 'tv1', 'nome': 'TV Sala',
                    'nivel': 'tv', 'email': None
                }
            with flask_app.app.test_request_context():
                from flask import session
                session['user_cache'] = {
                    'id': 'tv1', 'nome': 'TV Sala',
                    'nivel': 'tv', 'email': None
                }
                with patch.object(flask_app, 'get_db_connection',
                                  side_effect=Exception("DB offline")):
                    user = flask_app.load_user('tv1')
                    assert user is not None
                    assert user.id == 'tv1'


def test_load_user_retorna_none_sem_cache_quando_db_falha():
    """load_user retorna None quando DB falha E não há cache de sessão."""
    with flask_app.app.test_request_context():
        with patch.object(flask_app, 'get_db_connection',
                          side_effect=Exception("DB offline")):
            user = flask_app.load_user('utilizador_sem_cache')
            assert user is None


def test_load_user_liberta_conexao_mesmo_com_excecao():
    """load_user devolve a conexão ao pool mesmo quando a query falha."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.execute.side_effect = Exception("Query falhou")

    with flask_app.app.test_request_context():
        with patch.object(flask_app, 'get_db_connection', return_value=mock_conn):
            with patch.object(flask_app, 'release_db_connection') as mock_release:
                flask_app.load_user('tv1')
                mock_release.assert_called_once_with(mock_conn)
```

- [ ] **Step 3: Executar testes — verificar que FALHAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py::test_load_user_usa_cache_quando_db_falha tests/test_auth_resilience.py::test_load_user_liberta_conexao_mesmo_com_excecao -v
```

Esperado: FAIL (comportamento actual não tem fallback nem `finally`)

- [ ] **Step 4: Modificar `load_user` em `app.py` (linhas 109-124)**

Substituir:

```python
@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = %s", (user_id,))
        u = cur.fetchone()
        release_db_connection(conn)
        if u:
            return User(u['username'], u['nome'], u['nivel'], u['email'])
        logger.warning(f"load_user: usuário '{user_id}' não encontrado no banco.")
        return None
    except Exception as e:
        logger.error(f"load_user: erro ao carregar usuário '{user_id}': {e}")
        # Retorna None sem matar a sessão — o Flask-Login vai redirecionar ao login
        return None
```

Por:

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
        logger.warning(f"load_user: utilizador '{user_id}' não encontrado no banco.")
        return None
    except Exception as e:
        logger.error(f"load_user: erro DB '{user_id}': {e}")
        cache = session.get('user_cache')
        if cache and cache.get('id') == user_id:
            logger.warning(f"load_user: usando cache de sessão para '{user_id}'")
            return User(cache['id'], cache['nome'], cache['nivel'], cache.get('email'))
        return None
    finally:
        if conn:
            release_db_connection(conn)
```

- [ ] **Step 5: Modificar a rota `login` em `app.py` (linhas 922-934) para popular o cache**

Localizar o bloco após `login_user(user, ...)` e adicionar `session['user_cache']`:

```python
        if user_data and user_data['senha'] and bcrypt.checkpw(password.encode('utf-8'), user_data['senha'].encode('utf-8')):
            user = User(user_data['username'], user_data['nome'], user_data['nivel'], user_data['email'])
            is_tv = user_data['nivel'] == 'tv'
            remember_me = request.form.get('remember') == 'on'
            login_user(user, remember=is_tv or remember_me)
            session.permanent = True
            session['user_cache'] = {
                'id': user_data['username'], 'nome': user_data['nome'],
                'nivel': user_data['nivel'], 'email': user_data['email']
            }
            if not is_tv:
                if not remember_me:
                    session['ultima_atividade'] = datetime.now(BRT).isoformat()
                else:
                    session['manter_conectado'] = True
            session['acabou_de_logar'] = True
            return redirect(url_for('dashboard'))
```

- [ ] **Step 6: Executar todos os testes da Task 1 — verificar que PASSAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py -v -k "load_user"
```

Esperado: 4 testes PASS

- [ ] **Step 7: Commit**

```bash
cd /home/transnet/projects/posembarque
git add app.py tests/test_auth_resilience.py
git commit -m "fix: load_user com cache de sessão e try/finally para evitar logout por falha de DB"
```

---

### Task 2: Lifetime de sessão dinâmico por nível de utilizador

**Ficheiros:**
- Modify: `app.py:85-89` (`verificar_timeout_sessao`)

- [ ] **Step 1: Adicionar teste para o lifetime dinâmico**

Adicionar ao ficheiro `tests/test_auth_resilience.py`:

```python
# --- Task 2: lifetime dinâmico por nível ---

def test_sessao_tv_tem_lifetime_30_dias(client):
    """Sessão TV deve ter permanent_session_lifetime de 30 dias."""
    from datetime import timedelta

    with flask_app.app.test_request_context():
        from flask import session as s
        s['_user_id'] = 'tv1'
        s['user_cache'] = {'id': 'tv1', 'nome': 'TV', 'nivel': 'tv', 'email': None}
        flask_app.verificar_timeout_sessao()
        assert flask_app.app.permanent_session_lifetime == timedelta(days=30)


def test_sessao_outros_niveis_tem_lifetime_8h(client):
    """Sessão de utilizadores normais deve manter lifetime de 8 horas."""
    from datetime import timedelta

    with flask_app.app.test_request_context():
        from flask import session as s
        s['_user_id'] = 'user1'
        s['user_cache'] = {'id': 'user1', 'nome': 'Ops', 'nivel': 'operacional', 'email': None}
        flask_app.verificar_timeout_sessao()
        assert flask_app.app.permanent_session_lifetime == timedelta(hours=8)
```

- [ ] **Step 2: Executar testes — verificar que FALHAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py -v -k "lifetime"
```

Esperado: FAIL

- [ ] **Step 3: Modificar `verificar_timeout_sessao` em `app.py` (linhas 85-89)**

Substituir:

```python
@app.before_request
def verificar_timeout_sessao():
    """Faz refresh da sessão a cada request para evitar expiração prematura."""
    if session.get('_user_id'):  # Usuário está logado
        session.modified = True  # Força o Flask a salvar/renovar o cookie
```

Por:

```python
@app.before_request
def verificar_timeout_sessao():
    """Faz refresh da sessão a cada request. TV tem lifetime de 30 dias; outros 8h."""
    if session.get('_user_id'):
        nivel = session.get('user_cache', {}).get('nivel')
        if nivel == 'tv':
            app.permanent_session_lifetime = timedelta(days=30)
        else:
            app.permanent_session_lifetime = timedelta(hours=8)
        session.modified = True
```

- [ ] **Step 4: Executar testes — verificar que PASSAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py -v -k "lifetime"
```

Esperado: 2 testes PASS

- [ ] **Step 5: Commit**

```bash
cd /home/transnet/projects/posembarque
git add app.py tests/test_auth_resilience.py
git commit -m "fix: sessão TV com lifetime de 30 dias para evitar expiração overnight"
```

---

### Task 3: Pool de DB robusto após reconexão

**Ficheiros:**
- Modify: `app.py:144-167` (`get_db_connection`)

- [ ] **Step 1: Adicionar teste para a verificação pós-pool**

Adicionar ao ficheiro `tests/test_auth_resilience.py`:

```python
# --- Task 3: pool robusto ---

def test_get_db_connection_reconecta_e_verifica_nova_conexao():
    """get_db_connection verifica a nova conexão após recriar o pool."""
    # Primeira conexão falha no ping
    mock_bad_conn = MagicMock()
    mock_bad_conn.closed = False
    mock_bad_conn.cursor.return_value.execute.side_effect = [
        psycopg2.OperationalError("idle expired"),  # ping da 1ª conn falha
    ]

    # Nova conexão após pool recriado também é testada
    mock_good_conn = MagicMock()
    mock_good_conn.closed = False
    mock_good_conn.cursor.return_value.execute.return_value = None  # SELECT 1 ok

    import psycopg2.extras

    with patch.object(flask_app.db_pool, 'getconn',
                      side_effect=[mock_bad_conn, mock_good_conn]):
        with patch.object(flask_app.db_pool, 'putconn'):
            with patch.object(flask_app, '_recriar_pool'):
                conn = flask_app.get_db_connection()
                assert conn == mock_good_conn
                # Verificar que SELECT 1 foi chamado 2x: 1 na conn má, 1 na conn nova
                assert mock_good_conn.cursor.return_value.execute.call_count >= 1


def test_get_db_connection_lanca_excecao_se_pool_recriado_e_conn_ainda_invalida():
    """get_db_connection lança excepção se nova conexão após pool recriar também falha."""
    mock_bad_conn = MagicMock()
    mock_bad_conn.closed = False
    mock_bad_conn.cursor.return_value.execute.side_effect = psycopg2.OperationalError("idle")

    mock_bad_conn2 = MagicMock()
    mock_bad_conn2.closed = False
    mock_bad_conn2.cursor.return_value.execute.side_effect = Exception("ainda offline")

    with patch.object(flask_app.db_pool, 'getconn',
                      side_effect=[mock_bad_conn, mock_bad_conn2]):
        with patch.object(flask_app.db_pool, 'putconn'):
            with patch.object(flask_app, '_recriar_pool'):
                with pytest.raises(psycopg2.OperationalError):
                    flask_app.get_db_connection()
```

Adicionar também no topo do ficheiro: `import psycopg2`

- [ ] **Step 2: Executar testes — verificar que FALHAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py -v -k "pool"
```

Esperado: FAIL

- [ ] **Step 3: Modificar `get_db_connection` em `app.py` (linhas 144-167)**

Substituir o bloco do `except` final:

```python
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        logger.warning(f"Reconectando ao banco de dados: {e}")
        _recriar_pool()
        conn = db_pool.getconn()
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
```

Por:

```python
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        logger.warning(f"Reconectando ao banco de dados: {e}")
        _recriar_pool()
        conn = db_pool.getconn()
        try:
            conn.cursor().execute("SELECT 1")
        except Exception as e2:
            raise psycopg2.OperationalError(f"Pool recriado mas conexão ainda inválida: {e2}")
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
```

- [ ] **Step 4: Executar TODOS os testes — verificar que PASSAM**

```bash
cd /home/transnet/projects/posembarque
source venv/bin/activate
python -m pytest tests/test_auth_resilience.py -v
```

Esperado: todos os testes PASS

- [ ] **Step 5: Reiniciar o servidor e verificar manualmente**

```bash
# Reiniciar o gunicorn (ajustar o comando conforme o ambiente)
sudo systemctl restart posembarque
# ou
pkill -f gunicorn && cd /home/transnet/projects/posembarque && source venv/bin/activate && gunicorn -c gunicorn.ctl app:app &
```

Verificar no browser:
1. Fazer login com conta TV
2. Confirmar que o dashboard carrega normalmente
3. Confirmar que o relógio TV está visível

- [ ] **Step 6: Commit final**

```bash
cd /home/transnet/projects/posembarque
git add app.py tests/test_auth_resilience.py
git commit -m "fix: get_db_connection verifica nova conexão após recriar pool"
git push origin master
```

---

## Verificação Final

Após todos os commits, confirmar:

```bash
python -m pytest tests/test_auth_resilience.py -v
```

Resultado esperado:
```
tests/test_auth_resilience.py::test_load_user_retorna_user_do_db_quando_db_ok PASSED
tests/test_auth_resilience.py::test_load_user_usa_cache_quando_db_falha PASSED
tests/test_auth_resilience.py::test_load_user_retorna_none_sem_cache_quando_db_falha PASSED
tests/test_auth_resilience.py::test_load_user_liberta_conexao_mesmo_com_excecao PASSED
tests/test_auth_resilience.py::test_sessao_tv_tem_lifetime_30_dias PASSED
tests/test_auth_resilience.py::test_sessao_outros_niveis_tem_lifetime_8h PASSED
tests/test_auth_resilience.py::test_get_db_connection_reconecta_e_verifica_nova_conexao PASSED
tests/test_auth_resilience.py::test_get_db_connection_lanca_excecao_se_pool_recriado_e_conn_ainda_invalida PASSED

8 passed in X.XXs
```
