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
