import pytest
import psycopg2
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
                from flask import session
                assert session.get('user_cache') == {
                    'id': 'tv1', 'nome': 'TV Sala', 'nivel': 'tv', 'email': None
                }


def test_load_user_usa_cache_quando_db_falha():
    """load_user usa cache da sessão quando DB lança excepção."""
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


# --- Task 2: lifetime dinâmico por nível ---

def test_sessao_tv_tem_lifetime_30_dias():
    """Sessão TV deve ter permanent_session_lifetime de 30 dias."""
    from datetime import timedelta

    with flask_app.app.test_request_context():
        from flask import session as s
        s['_user_id'] = 'tv1'
        s['user_cache'] = {'id': 'tv1', 'nome': 'TV', 'nivel': 'tv', 'email': None}
        flask_app.verificar_timeout_sessao()
        assert flask_app.app.permanent_session_lifetime == timedelta(days=30)


def test_sessao_outros_niveis_tem_lifetime_8h():
    """Sessão de utilizadores normais deve manter lifetime de 8 horas."""
    from datetime import timedelta

    with flask_app.app.test_request_context():
        from flask import session as s
        s['_user_id'] = 'user1'
        s['user_cache'] = {'id': 'user1', 'nome': 'Ops', 'nivel': 'operacional', 'email': None}
        flask_app.verificar_timeout_sessao()
        assert flask_app.app.permanent_session_lifetime == timedelta(hours=8)


# --- Task 3: pool robusto após reconexão ---

def test_get_db_connection_reconecta_e_verifica_nova_conexao():
    """get_db_connection verifica a nova conexão após recriar o pool."""
    mock_bad_conn = MagicMock()
    mock_bad_conn.closed = False
    mock_bad_conn.cursor.return_value.execute.side_effect = psycopg2.OperationalError("idle expired")

    mock_good_conn = MagicMock()
    mock_good_conn.closed = False
    mock_good_conn.cursor.return_value.execute.return_value = None  # SELECT 1 ok

    with patch.object(flask_app.db_pool, 'getconn', side_effect=[mock_bad_conn, mock_good_conn]):
        with patch.object(flask_app.db_pool, 'putconn'):
            with patch.object(flask_app, '_recriar_pool'):
                conn = flask_app.get_db_connection()
                assert conn == mock_good_conn


def test_get_db_connection_lanca_excecao_se_pool_recriado_e_conn_invalida():
    """get_db_connection lança excepção se nova conexão após pool recriar também falha."""
    mock_bad_conn = MagicMock()
    mock_bad_conn.closed = False
    mock_bad_conn.cursor.return_value.execute.side_effect = psycopg2.OperationalError("idle")

    mock_bad_conn2 = MagicMock()
    mock_bad_conn2.closed = False
    mock_bad_conn2.cursor.return_value.execute.side_effect = Exception("ainda offline")

    with patch.object(flask_app.db_pool, 'getconn', side_effect=[mock_bad_conn, mock_bad_conn2]):
        with patch.object(flask_app.db_pool, 'putconn'):
            with patch.object(flask_app, '_recriar_pool'):
                with pytest.raises(psycopg2.OperationalError):
                    flask_app.get_db_connection()
