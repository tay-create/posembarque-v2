import math
import logging
import subprocess
import imghdr
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import psycopg2
import psycopg2.extras
import psycopg2.pool
import pandas as pd
from datetime import datetime, timedelta
import shutil
import os
import json
import secrets
import bcrypt
from flask_mail import Mail, Message
from dotenv import load_dotenv

BRT = ZoneInfo('America/Sao_Paulo')

def agora_brt():
    """Retorna datetime atual no fuso horário de Brasília."""
    return datetime.now(BRT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
PG_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'dbname': os.environ.get('DB_NAME', 'posembarque-transnet'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', '')
}
app.secret_key = os.environ.get('SECRET_KEY', '')
app.permanent_session_lifetime = timedelta(hours=8)
app.config['APPLICATION_ROOT'] = '/posembarque'
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['SESSION_COOKIE_NAME'] = 'posembarque_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['DEBUG'] = False
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB limite de upload

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'transnet.cadastro@tnetlog.com.br'
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = ('Suporte Transnet', app.config['MAIL_USERNAME'])

mail = Mail(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_MIMETYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.before_request
def verificar_timeout_sessao():
    """Renova a sessão em cada request. TV=30 dias, outros=8h.

    Nota: app.permanent_session_lifetime é mutado por request. Seguro com
    gunicorn sync workers (single-threaded, requests sequenciais). Não usar
    com workers threaded (gevent/eventlet/gthread).
    """
    if session.get('_user_id'):
        nivel = session.get('user_cache', {}).get('nivel')
        if nivel == 'tv':
            app.permanent_session_lifetime = timedelta(days=30)
        else:
            app.permanent_session_lifetime = timedelta(hours=8)
        session.modified = True

@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class User(UserMixin):
    def __init__(self, id, nome, nivel, email=None):
        self.id = id; self.nome = nome; self.nivel = nivel; self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = %s", (user_id,))
        u = cur.fetchone()
        cur.close()
        if u:
            session['user_cache'] = {
                'id': u['username'], 'nome': u['nome'],
                'nivel': u['nivel'], 'email': u['email']
            }
            return User(u['username'], u['nome'], u['nivel'], u['email'])
        logger.warning(f"load_user: utilizador '{user_id}' não encontrado no banco.")
        # Utilizador não encontrado no DB — não usar fallback de cache (diferente de falha de DB)
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

# --- FUNÇÕES DE BANCO DE DADOS ---

# --- CONNECTION POOL ---
db_pool = psycopg2.pool.SimpleConnectionPool(
    minconn=1, maxconn=10,
    **PG_CONFIG
)

def _recriar_pool():
    """Recria o pool quando as conexões estiverem mortas."""
    global db_pool
    try:
        db_pool.closeall()
    except Exception:
        pass
    logger.warning("Pool de conexões recriado após falha de conexão idle.")
    db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, **PG_CONFIG)

def get_db_connection():
    """Retorna conexão válida do pool, reconectando automaticamente se necessário."""
    global db_pool
    try:
        conn = db_pool.getconn()
        if conn.closed:
            raise psycopg2.OperationalError("Conexão fechada.")
        # Ping leve para detectar conexões idle expiradas pelo PostgreSQL
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
        except Exception as e2:
            raise psycopg2.OperationalError(f"Pool recriado mas conexão ainda inválida: {e2}")
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

def release_db_connection(conn):
    """Devolve a conexão ao pool, garantindo que está limpa."""
    if conn:
        try:
            if not conn.closed:
                conn.rollback()  # Limpa transações pendentes
            db_pool.putconn(conn)
        except Exception:
            pass

def init_cadastros_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS motoristas (nome TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS clientes (nome TEXT PRIMARY KEY)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            senha TEXT,
            nivel TEXT,
            nome TEXT,
            email TEXT,
            token_reset TEXT,
            token_expiracao TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sistema_patches (
            id SERIAL PRIMARY KEY,
            titulo TEXT,
            itens TEXT,
            data_lancamento TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sistema_views (
            usuario_id TEXT,
            patch_id INTEGER,
            contagem INTEGER DEFAULT 0,
            PRIMARY KEY (usuario_id, patch_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chamados (
            id SERIAL PRIMARY KEY,
            usuario TEXT,
            titulo TEXT,
            mensagem TEXT,
            status TEXT DEFAULT 'ABERTO',
            data_abertura TEXT,
            data_resolucao TEXT,
            resposta_dev TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            data_hora TEXT,
            acao TEXT,
            detalhes TEXT
        )
    """)

    cur.execute("""
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
        )
    """)

    conn.commit()
    release_db_connection(conn)
    logger.info("Banco de dados verificando e atualizado!")

with app.app_context():
    init_cadastros_db()

def registrar_log(acao, detalhe):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        agora = agora_brt().strftime("%d/%m/%Y %H:%M:%S")
        u = current_user.id if current_user.is_authenticated else "Sistema"
        cur.execute("INSERT INTO logs (data_hora, acao, detalhes) VALUES (%s, %s, %s)", (agora, acao, f"{detalhe} (User: {u})"))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao registrar log: {e}")
    finally:
        release_db_connection(conn)

@app.route('/abrir_chamado', methods=['POST'])
@login_required
def abrir_chamado():
    titulo = request.form.get('titulo')
    mensagem = request.form.get('mensagem')
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chamados (usuario, titulo, mensagem, data_abertura)
        VALUES (%s, %s, %s, %s)
    """, (current_user.nome, titulo, mensagem, agora))
    conn.commit()
    release_db_connection(conn)

    registrar_log("SUPORTE", f"Novo chamado aberto por {current_user.nome}: {titulo}")

    flash("Chamado aberto! O desenvolvedor foi notificado.", 'suporte')
    return redirect(url_for('dashboard'))

@app.route('/central_chamados')
@login_required
def central_chamados():
    if current_user.nivel != 'desenvolvedor': return "Acesso Negado"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM chamados ORDER BY status ASC, id DESC")
    chamados = cur.fetchall()
    release_db_connection(conn)

    return render_template('central_chamados.html', chamados=chamados)

@app.route('/resolver_chamado/<int:id>', methods=['POST'])
@login_required
def resolver_chamado(id):
    if current_user.nivel != 'desenvolvedor': return "Acesso negado"

    resposta = request.form.get('resposta')
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE chamados
        SET status = 'RESOLVIDO', resposta_dev = %s, data_resolucao = %s
        WHERE id = %s
    """, (resposta, agora, id))
    conn.commit()
    release_db_connection(conn)

    registrar_log("SUPORTE", f"Chamado #{id} resolvido.")
    return redirect(url_for('central_chamados'))

@app.route('/publicar_patch', methods=['POST'])
@login_required
def publicar_patch():
    if current_user.nivel != 'desenvolvedor': return "Acesso negado"

    titulo = request.form.get('titulo')
    itens_texto = request.form.get('itens')
    lista_itens = [i.strip() for i in itens_texto.split('\n') if i.strip()]

    conn = get_db_connection()
    agora = datetime.now().strftime("%d/%m/%Y")
    cur = conn.cursor()
    cur.execute("INSERT INTO sistema_patches (titulo, itens, data_lancamento) VALUES (%s, %s, %s)",
                 (titulo, json.dumps(lista_itens), agora))
    conn.commit()
    release_db_connection(conn)

    registrar_log("SISTEMA", F"Novo Patch lançado: {titulo}")
    return redirect(url_for('dashboard'))

@app.route('/excluir_ocorrencia/<int:id>', methods=['POST'])
@login_required
def excluir_ocorrencia(id):
    if current_user.nivel not in ['gerencial', 'desenvolvedor']:
        return jsonify({'sucesso': False, 'erro': 'Usuário sem permissão para esta ação.'})

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))
        ocorrencia = cur.fetchone()

        if ocorrencia and ocorrencia['fotos']:
            fotos = json.loads(ocorrencia['fotos'])
            for foto in fotos:
                caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], foto)
                if os.path.exists(caminho_arquivo):
                    os.remove(caminho_arquivo)

        cur.execute("DELETE FROM ocorrencias WHERE id = %s", (id,))
        conn.commit()
        return jsonify({'sucesso': True})

    except Exception as e:
        logger.error(f"Erro ao excluir ocorrencia: {e}")
        return jsonify({'sucesso': False, 'erro': 'Erro interno no servidor.'})
    finally:
        release_db_connection(conn)

@app.route('/upload_foto/<int:id>', methods=['POST'])
@login_required
def upload_foto(id):
    if 'foto' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    file = request.files['foto']
    if file.filename == '':
        return jsonify({'erro': 'Arquivo vazio'}), 400

    if not (file and allowed_file(file.filename)):
        return jsonify({'erro': 'Tipo de arquivo não permitido'}), 400

    mime_type = file.content_type or ''
    if mime_type not in ALLOWED_MIMETYPES:
        return jsonify({'erro': 'Tipo de arquivo não permitido'}), 400

    # Validação por magic bytes (conteúdo real do arquivo)
    header = file.read(32)
    file.seek(0)
    tipo_real = imghdr.what(None, h=header)
    if tipo_real not in ('png', 'jpeg', 'gif', 'webp'):
        return jsonify({'erro': 'Conteúdo do arquivo não é uma imagem válida'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))
    ocorrencia = cur.fetchone()
    fotos_atuais = json.loads(ocorrencia['fotos']) if (ocorrencia and ocorrencia['fotos']) else []

    if len(fotos_atuais) >= 5:
        return jsonify({'erro': 'Limite de 5 fotos atingido'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    nome_arquivo = f"img_{id}_{len(fotos_atuais) + 1}_{datetime.now().strftime('%M%S')}.{ext}"
    caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
    file.save(caminho_salvar)

    fotos_atuais.append(nome_arquivo)
    cur.execute("UPDATE ocorrencias SET fotos = %s WHERE id = %s", (json.dumps(fotos_atuais), id))
    conn.commit()
    release_db_connection(conn)

    registrar_log("UPLOAD", f"Foto adicionada à ocorrência {id}")
    return jsonify({'sucesso': True, 'foto': nome_arquivo})

@app.route('/apagar_foto/<int:id>', methods=['POST'])
@login_required
def apagar_foto(id):
    dados = request.get_json()
    nome_foto = dados.get('foto')

    if not nome_foto:
        return jsonify({'sucesso': False, 'erro': 'Nome da foto não enviado.'})

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))
        ocorrencia = cur.fetchone()

        if ocorrencia and ocorrencia['fotos']:
            fotos = json.loads(ocorrencia['fotos'])

            # Verifica se a foto realmente pertence a esta ocorrência
            if nome_foto in fotos:
                fotos.remove(nome_foto) # Remove da lista

                # Atualiza o banco de dados
                cur.execute("UPDATE ocorrencias SET fotos = %s WHERE id = %s", (json.dumps(fotos), id))
                conn.commit()

                # Apaga o arquivo físico da pasta do servidor
                caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], nome_foto)
                if os.path.exists(caminho_arquivo):
                    os.remove(caminho_arquivo)

                return jsonify({'sucesso': True})

        return jsonify({'sucesso': False, 'erro': 'Foto não encontrada.'})
    except Exception as e:
        logger.error(f"Erro ao apagar foto: {e}")
        return jsonify({'sucesso': False, 'erro': 'Erro interno no servidor.'})
    finally:
        release_db_connection(conn)

COLUNAS_PERMITIDAS = {
    'cliente': 'cliente', 'motorista': 'motorista', 'motivo': 'motivo',
    'cidade': 'cidade', 'modalidade': 'modalidade', 'operacao': 'operacao'
}

def construir_query(args):

    tipo_dash = args.get('tipo')
    busca_dash = args.get('busca')

    params_busca = dict(args)
    params_busca.pop('page', None)

    if tipo_dash and busca_dash:
        col_nome = tipo_dash.lower()
        if col_nome in COLUNAS_PERMITIDAS:
            params_busca[col_nome] = busca_dash

    data_ini = params_busca.get('inicio'); data_fim = params_busca.get('fim'); cliente = params_busca.get('cliente')
    motorista = params_busca.get('motorista'); motivo = params_busca.get('motivo'); cidade = params_busca.get('cidade')
    modalidade = params_busca.get('modalidade'); operacao = params_busca.get("operacao")
    query = "SELECT * FROM ocorrencias WHERE 1=1"; params = []
    if data_ini: query += " AND data_ocorrencia >= %s"; params.append(data_ini)
    if data_fim: query += " AND data_ocorrencia <= %s"; params.append(data_fim)
    if cliente: query += " AND cliente LIKE %s"; params.append(f'%{cliente}%')
    if motorista: query += " AND motorista LIKE %s"; params.append(f'%{motorista}%')
    if motivo: query += " AND motivo LIKE %s"; params.append(f'%{motivo}%')
    if cidade: query += " AND cidade LIKE %s"; params.append(f'%{cidade}%')
    if modalidade: query += " AND modalidade = %s"; params.append(modalidade)
    if operacao: query += " AND operacao = %s"; params.append(operacao)
    return query, params

def verificar_atraso(row):
    """Verifica se a ocorrência passou de 24h.
    Se já concluída, compara início com fim.
    Se ainda aberta, compara início com agora."""
    try:
        dt_str = row['data_ocorrencia']; hr_str = row['hora_ocorrencia']
        if '-' in dt_str: inicio = datetime.strptime(f"{dt_str} {hr_str}", "%Y-%m-%d %H:%M")
        else: inicio = datetime.strptime(f"{dt_str} {hr_str}", "%d/%m/%Y %H:%M")

        if row.get('data_conclusao') and row.get('hora_conclusao'):
            dt_fim_str = row['data_conclusao']; hr_fim_str = row['hora_conclusao']
            if '-' in dt_fim_str: fim = datetime.strptime(f"{dt_fim_str} {hr_fim_str}", "%Y-%m-%d %H:%M")
            else: fim = datetime.strptime(f"{dt_fim_str} {hr_fim_str}", "%d/%m/%Y %H:%M")
            return (fim - inicio) > timedelta(hours=24)
        else:
            # Ocorrência aberta: compara com agora
            return (agora_brt().replace(tzinfo=None) - inicio) > timedelta(hours=24)
    except Exception as e:
        logger.warning(f"Erro ao verificar atraso: {e}")
        return False

@app.route('/solicitar_edicao/<int:id>', methods=['POST'])
@login_required
def solicitar_edicao(id):
    motivo = request.form.get('motivo_texto')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE ocorrencias SET status_edicao = 'SOLICITADO', motivo_edicao = %s WHERE id = %s", (motivo, id,))
    conn.commit()
    release_db_connection(conn)
    registrar_log("SOLICITAÇÃO", f"{current_user.nome} solicitou edição da ocorência {id}. Motivo: {motivo}")
    return redirect(url_for('dashboard'))

@app.route('/recusar_edicao/<int:id>')
@login_required
def recusar_edicao(id):
    if current_user.nivel not in ['gerencial', 'desenvolvedor']: return "Acesso negado"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE ocorrencias SET status_edicao = 'BLOQUEADO', motivo_edicao = NULL WHERE id = %s", (id,))
    conn.commit()
    release_db_connection(conn)
    registrar_log("RECUSA", f"Gerente {current_user.nome} recusou edição da ocorrência {id}")
    return redirect(url_for('dashboard'))

@app.route('/liberar_edicao/<int:id>')
@login_required
def liberar_edicao(id):
    if current_user.nivel not in ['gerencial', 'desenvolvedor']: return "Acesso negado"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE ocorrencias SET status_edicao = 'AUTORIZADO' WHERE id = %s", (id,))
    conn.commit()
    release_db_connection(conn)
    registrar_log("AUTORIZAÇÃO", f"Gerente {current_user.nome} liberou edição para ocorrência {id}")
    return redirect(url_for('dashboard'))

@app.route('/')
@login_required
def dashboard():
    tipo_filtro = request.args.get('tipo'); termo_busca = request.args.get('busca')
    conn = get_db_connection()
    sql = 'SELECT * FROM ocorrencias WHERE (arquivado = 0 OR arquivado IS NULL)'; params = []
    if tipo_filtro and termo_busca:
        mapa = {'MOTORISTA':'motorista','MODALIDADE':'modalidade','CLIENTE':'cliente','CIDADE':'cidade','MOTIVO':'motivo'}
        col = mapa.get(tipo_filtro)
        if col: sql += f" AND {col} LIKE %s"; params.append(f'%{termo_busca}%')
    sql += ' ORDER BY id DESC'
    cur = conn.cursor()
    cur.execute(sql, params)
    ocorrencias = cur.fetchall(); release_db_connection(conn)

    agora = agora_brt().replace(tzinfo=None); dados_processados = []
    metricas = {'total': 0, 'andamento': 0, 'resolvidas': 0, 'atrasadas': 0}

    for row in ocorrencias:
        r = dict(row); metricas['total'] += 1; status = r['situacao'].upper()
        r['fotos_count'] = len(json.loads(r['fotos'])) if r.get('fotos') else 0
        cor = "orange"; texto = r['situacao']; peso = 2
        if status == "RESOLVIDO": cor = "green"; metricas['resolvidas'] += 1; peso = 3
        else:
            e_atrasado = False
            try:
                dt_str = r['data_ocorrencia']; hr_str = r['hora_ocorrencia']
                if '-' in dt_str: d = datetime.strptime(dt_str, "%Y-%m-%d")
                else: d = datetime.strptime(dt_str, "%d/%m/%Y")
                if hr_str and ':' in hr_str: h,m = map(int, hr_str.split(':')); d = d.replace(hour=h, minute=m)
                if (agora - d) > timedelta(hours=24): e_atrasado = True
            except Exception as e: logger.warning(f"Erro ao parsear data ocorrência: {e}")
            if e_atrasado: cor = "red"; texto = "JÁ PASSOU 24H"; metricas['atrasadas'] += 1; peso = 1
            else: metricas['andamento'] += 1; peso = 2
        r['cor_card'] = cor; r['status_display'] = texto; r['peso'] = peso; dados_processados.append(r)

    if request.args.get('filtro_atraso') == 'sim': dados_processados = [d for d in dados_processados if d['cor_card'] == 'red']
    dados_processados.sort(key=lambda x: x['peso'])

    aviso_patch = None
    if current_user.nivel != 'tv' and session.get('acabou_de_logar'):
        session.pop('acabou_de_logar', None)
        conn = get_db_connection()
        cur2 = conn.cursor()
        cur2.execute("SELECT * FROM sistema_patches ORDER BY id DESC LIMIT 1")
        ultimo_patch = cur2.fetchone()

        if ultimo_patch:
            cur2.execute("SELECT contagem FROM sistema_views WHERE usuario_id = %s AND patch_id = %s",
                                (current_user.id, ultimo_patch['id']))
            view = cur2.fetchone()

            contagem = view['contagem'] if view else 0

            if contagem < 3:
                aviso_patch = dict(ultimo_patch)
                aviso_patch['lista_itens'] = json.loads(ultimo_patch['itens'])

                if view:
                    cur2.execute("UPDATE sistema_views SET contagem = contagem + 1 WHERE usuario_id = %s AND patch_id = %s",
                                 (current_user.id, ultimo_patch['id']))
                else:
                    cur2.execute("INSERT INTO sistema_views (usuario_id, patch_id, contagem) VALUES (%s, %s, 1)",
                                 (current_user.id, ultimo_patch['id']))
                conn.commit()
        release_db_connection(conn)

    return render_template('dashboard.html', dados=dados_processados, metricas=metricas, aviso_patch=aviso_patch)

@app.route('/gerenciar_cadastros')
@login_required
def gerenciar_cadastros():
    if current_user.nivel not in ['operacional', 'gerencial', 'desenvolvedor']: return "<script>alert('Acesso Negado'); window.history.back();</script>"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome FROM motoristas ORDER BY nome")
    motoristas = [r['nome'] for r in cur.fetchall()]
    cur.execute("SELECT nome FROM clientes ORDER BY nome")
    clientes = [r['nome'] for r in cur.fetchall()]
    release_db_connection(conn)
    return render_template('cadastros.html', motoristas=motoristas, clientes=clientes)

@app.route('/adicionar_item', methods=['POST'])
@login_required
def adicionar_item():
    tipo = request.form.get('tipo'); nome = request.form.get('nome').strip().upper()
    if not nome: return redirect(url_for('gerenciar_cadastros'))
    try:
        conn = get_db_connection()
        tabela = "motoristas" if tipo == "motorista" else "clientes"
        cur = conn.cursor()
        cur.execute(f"INSERT INTO {tabela} (nome) VALUES (%s) ON CONFLICT DO NOTHING", (nome,))
        conn.commit(); release_db_connection(conn)
        registrar_log("CADASTRO", f"Adicionado novo {tipo}: {nome}")
    except Exception as e: logger.error(f"Erro add: {e}")
    return redirect(url_for('gerenciar_cadastros'))

@app.route('/remover_item/<tipo>/<nome>')
@login_required
def remover_item(tipo, nome):
    if current_user.nivel not in ['gerencial', 'desenvolvedor']:
        return "<script>alert('Acesso Negado'); window.history.back();</script>"
    try:
        conn = get_db_connection()
        tabela = "motoristas" if tipo == "motorista" else "clientes"
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {tabela} WHERE nome = %s", (nome,))
        conn.commit()
        release_db_connection(conn)
        registrar_log("CADASTRO", f"Removido {tipo}: {nome}")
    except Exception as e: logger.error(f"Erro ao remover: {e}")
    return redirect(url_for('gerenciar_cadastros'))

@app.route('/backup_banco')
@login_required
def backup_banco():
    if current_user.nivel not in ['gerencial', 'desenvolvedor']: return "Acesso negado"

    try:
        import tempfile
        nome = f"backup_{datetime.now().strftime('%d-%m-%Y_%H%M')}.dump"
        destino = os.path.join(tempfile.gettempdir(), nome)
        env_backup = os.environ.copy()
        env_backup['PGPASSWORD'] = PG_CONFIG['password']
        resultado = subprocess.run([
            'pg_dump',
            '-U', PG_CONFIG["user"],
            '-h', PG_CONFIG["host"],
            '-p', str(PG_CONFIG["port"]),
            '-F', 'c',
            '-f', destino,
            PG_CONFIG["dbname"]
        ], capture_output=True, text=True, env=env_backup)
        if resultado.returncode != 0:
            logger.error(f"pg_dump falhou: {resultado.stderr}")
            return "Erro ao gerar backup com pg_dump"
        return send_file(destino, as_attachment=True, download_name=nome)
    except Exception as e: return f"Erro: {e}"

@app.route('/arquivar_resolvidos')
@login_required
def arquivar_resolvidos():
    if current_user.nivel not in ['gerencial', 'desenvolvedor']:
        return "Acesso negado"
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE ocorrencias SET arquivado = 1 WHERE situacao = 'RESOLVIDO'")
        conn.commit(); release_db_connection(conn)
        registrar_log("ARQUIVAMENTO", "Limpeza de tela executada.")
        return redirect(url_for('dashboard'))
    except Exception as e: return f"Erro: {e}"

@app.route('/exportar_pdf')
@login_required
def exportar_pdf():
    import pdfkit
    import urllib.parse
    import urllib.request
    import base64
    import json
    from flask import make_response

    # 1. Preparação da Query
    query, params = construir_query(request.args)
    query += " ORDER BY data_ocorrencia DESC"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    ocorrencias_db = cur.fetchall()
    release_db_connection(conn)

    dados_processados = []

    # Métricas para os Gráficos (apenas Resolvido e Atrasado)
    ops_dict = {}
    motivos_dict = {}
    total_atrasadas = 0
    total_resolvidas = 0

    # 2. Processamento dos dados
    for row in ocorrencias_db:
        r = dict(row)
        status = r['situacao'].upper()
        is_atrasado = verificar_atraso(r)

        # Trata nulos ANTES de adicionar à lista
        r['data_conclusao'] = r.get('data_conclusao') or '--'
        r['hora_conclusao'] = r.get('hora_conclusao') or '--'
        r['cte'] = r.get('cte') or '--'
        r['nfs'] = r.get('nfs') or '--'
        r['modalidade'] = r.get('modalidade') or '--'

        # REGRA DE EXIBIÇÃO NA TABELA DO PDF (Oculta o "Em Andamento")
        if status == "RESOLVIDO":
            if is_atrasado:
                r['status_display'] = "RESOLVIDO (>24H)"
            else:
                r['status_display'] = "RESOLVIDO"
            total_resolvidas += 1
            dados_processados.append(r)

            # Contagem para gráficos (apenas Resolvido/Atrasado)
            op = r.get('operacao') or 'N/A'
            ops_dict[op] = ops_dict.get(op, 0) + 1
            mot = r.get('motivo') or 'N/A'
            if mot != 'N/A':
                motivos_dict[mot] = motivos_dict.get(mot, 0) + 1

        elif is_atrasado:
            r['status_display'] = "ATRASADO (>24H)"
            total_atrasadas += 1
            dados_processados.append(r)

            # Contagem para gráficos (apenas Resolvido/Atrasado)
            op = r.get('operacao') or 'N/A'
            ops_dict[op] = ops_dict.get(op, 0) + 1
            mot = r.get('motivo') or 'N/A'
            if mot != 'N/A':
                motivos_dict[mot] = motivos_dict.get(mot, 0) + 1

    # Top 5 Motivos (Ordenado do maior pro menor)
    top_motivos = sorted(motivos_dict.items(), key=lambda x: x[1], reverse=True)[:5]

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io

    # Total real para gráficos = apenas resolvidas + atrasadas
    total_operacoes = total_resolvidas + total_atrasadas

    def fig_to_base64(fig):
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=120)
        buf.seek(0)
        img = "data:image/png;base64," + base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        return img

    # Gráfico 1 — Volume por Operação (rosca)
    img_ops = ""
    if ops_dict:
        fig, ax = plt.subplots(figsize=(7, 4), facecolor='white')
        cores = ['#f97316','#3b82f6','#10b981','#8b5cf6','#ef4444','#eab308']
        wedges, texts, autotexts = ax.pie(
            list(ops_dict.values()),
            labels=list(ops_dict.keys()),
            autopct=lambda p: f'{int(round(p*sum(ops_dict.values())/100))}',
            colors=cores[:len(ops_dict)],
            pctdistance=0.7,
            wedgeprops=dict(width=0.6)
        )
        for at in autotexts:
            at.set_fontsize(10)
            at.set_fontweight('bold')
            at.set_color('white')
        ax.set_title('VOLUME POR OPERAÇÃO', fontsize=13, fontweight='bold', pad=15)
        img_ops = fig_to_base64(fig)

    # Gráfico 2 — SLA (pizza resolvidas x atrasadas)
    img_sla = ""
    if total_operacoes > 0:
        fig, ax = plt.subplots(figsize=(6, 4), facecolor='white')
        valores = [total_resolvidas, total_atrasadas]
        rotulos = ['Resolvidas', 'Atrasadas (>24h)']
        cores_sla = ['#10b981', '#ef4444']
        wedges, texts, autotexts = ax.pie(
            valores,
            labels=rotulos,
            autopct=lambda p: f'{int(round(p*total_operacoes/100))}\n({p:.1f}%)',
            colors=cores_sla,
            pctdistance=0.65,
            startangle=90
        )
        for at in autotexts:
            at.set_fontsize(11)
            at.set_fontweight('bold')
            at.set_color('white')
        ax.set_title('RESOLVIDAS X ATRASADAS (>24H)', fontsize=13, fontweight='bold', pad=15)
        img_sla = fig_to_base64(fig)

    # Gráfico 3 — Top 5 Motivos (barra horizontal)
    img_mot = ""
    if top_motivos:
        fig, ax = plt.subplots(figsize=(8, 4), facecolor='white')
        motivos_labels = [m[0][:35] for m in top_motivos]
        motivos_vals = [m[1] for m in top_motivos]
        bars = ax.barh(motivos_labels, motivos_vals, color='#3b82f6')
        total_s = total_operacoes if total_operacoes > 0 else 1
        for bar, val in zip(bars, motivos_vals):
            ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2,
                    f'{val} ({val/total_s*100:.1f}%)', va='center', fontsize=9, fontweight='bold')
        ax.set_xlim(0, max(motivos_vals) * 1.3)
        ax.invert_yaxis()
        ax.set_title('TOP 5 MOTIVOS DE OCORRÊNCIAS', fontsize=13, fontweight='bold', pad=15)
        ax.set_xlabel('Quantidade')
        fig.tight_layout()
        img_mot = fig_to_base64(fig)

    # 4. Textos de Cabeçalho
    agora = agora_brt().replace(tzinfo=None)
    filtros = []
    if request.args.get('inicio') or request.args.get('fim'): filtros.append(f"Período: {request.args.get('inicio', '')} até {request.args.get('fim', '')}")
    if request.args.get('operacao'): filtros.append(f"Operação: {request.args.get('operacao')}")
    if request.args.get('motorista'): filtros.append(f"Motorista: {request.args.get('motorista')}")
    if request.args.get('cliente'): filtros.append(f"Cliente: {request.args.get('cliente')}")
    if request.args.get('cidade'): filtros.append(f"Cidade: {request.args.get('cidade')}")
    if request.args.get('motivo'): filtros.append(f"Motivo: {request.args.get('motivo')}")
    if request.args.get('modalidade'): filtros.append(f"Modalidade: {request.args.get('modalidade')}")
    filtros_texto = " | ".join(filtros) if filtros else "Visualizando todos os finalizados/atrasados."

    # 5. Renderização do PDF — divide dados em páginas de 30 linhas
    LINHAS_POR_PAGINA = 22
    chunks = [dados_processados[i:i+LINHAS_POR_PAGINA] for i in range(0, len(dados_processados), LINHAS_POR_PAGINA)] or [[]]

    html_renderizado = render_template('relatorio_pdf.html',
                                       chunks=chunks,
                                       filtros_texto=filtros_texto,
                                       data_geracao=agora.strftime("%d/%m/%Y às %H:%M"),
                                       img_ops=img_ops, img_sla=img_sla, img_mot=img_mot,
                                       total_ops=total_operacoes, total_atrasadas=total_atrasadas,
                                       total_resolvidas=total_resolvidas)

    options = {
        'page-size': 'A4',
        'orientation': 'Landscape',
        'margin-top': '0.5in',
        'margin-right': '0.4in',
        'margin-bottom': '0.4in',
        'margin-left': '0.4in',
        'encoding': "UTF-8",
        'no-outline': None,
        'enable-local-file-access': None,
        'disable-smart-shrinking': None,
    }

    try:
        config = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')
        pdf = pdfkit.from_string(html_renderizado, False, options=options, configuration=config)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=Relatorio_Transnet_{agora.strftime("%d-%m_%H%M")}.pdf'
        return response
    except Exception as e:
        return f"Erro PDF: {e}"

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
        user_data = cur.fetchone()
        release_db_connection(conn)

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
        else: flash('Usuário ou Senha incorretos.', 'login')
    return render_template('login.html')

@app.route('/salvar_email_usuario', methods=['POST'])
@login_required
def salvar_email_usuario():
    email = request.form.get('email')
    if email:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET email = %s WHERE username = %s", (email, current_user.id))
        conn.commit()
        release_db_connection(conn)
        flash("E-mail cadastrado com sucesso! Agora você pode recuperar sua senha.")
    return redirect(url_for('dashboard'))

@app.route('/esqueci_senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username = %s AND email = %s", (username, email))
        user = cur.fetchone()

        if user:
            token = secrets.token_urlsafe(32)
            expiracao = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

            cur.execute("UPDATE usuarios SET token_reset = %s, token_expiracao = %s WHERE username = %s",
                         (token, expiracao, username))
            conn.commit()
            release_db_connection(conn)

            link_reset = url_for('resetar_senha', token=token, _external=True)

            try:
                msg = Message("Recuperação de Acesso | Transnet", recipients=[email])
                msg.html = render_template('email_recuperacao.html',
                                        nome=user['nome'],
                                        link=link_reset,
                                        ano=datetime.now().year)

                mail.send(msg)
                flash("E-mail enviado! Verifique sua caixa de entrada.")

            except Exception as e:
                logger.error("Erro ao enviar email de recuperação")
                registrar_log("ERRO_EMAIL", f"Falha ao enviar para {email}: {e}")
                flash("Erro ao enviar e-mail. Contate o suporte.")

            return redirect(url_for('login'))

        release_db_connection(conn)
        flash("Dados não conferem.")
    return render_template('esqueci_senha.html')

@app.route('/resetar_senha/<token>', methods=['GET', 'POST'])
def resetar_senha(token):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE token_reset = %s", (token,))
    user = cur.fetchone()

    if not user:
        release_db_connection(conn)
        return "Link inválido ou já utilizado.", 404

    limite = datetime.strptime(user['token_expiracao'], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > limite:
        release_db_connection(conn)
        return "Este link Expirou. Solicite um novo.", 400

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirmacao = request.form.get('confirmacao')

        if nova_senha == confirmacao:
            hashed = bcrypt.hashpw(nova_senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cur.execute("UPDATE usuarios SET senha = %s, token_reset = NULL, token_expiracao = NULL WHERE username = %s",
                         (hashed, user['username']))
            conn.commit()
            release_db_connection(conn)
            flash("Senha alterada com sucesso! Faça login novamente.")
            return redirect(url_for('login'))
        else:
            flash("As senhas não coincidem.")

    release_db_connection(conn)
    return render_template('resetar_senha.html', token=token)

@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('login'))

@app.route('/cadastro')
@login_required
def cadastro():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome FROM motoristas ORDER BY nome")
    motoristas = [r['nome'] for r in cur.fetchall()]
    cur.execute("SELECT nome FROM clientes ORDER BY nome")
    clientes = [r['nome'] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo")
    motivos = [r['motivo'] for r in cur.fetchall()]
    release_db_connection(conn)
    return render_template('cadastro.html', motoristas=motoristas, clientes=clientes, motivos=motivos)

@app.route('/salvar', methods=['POST'])
@login_required
def salvar():
    try:
        responsavel = request.form.get('responsavel') or current_user.nome
        data = request.form.get('data'); hora = agora_brt().strftime("%H:%M")
        motorista = request.form.get('motorista'); modalidade = request.form.get('modalidade')
        cte = request.form.get('cte'); operacao = request.form.get('operacao')
        nfs = request.form.get('nfs'); cliente = request.form.get('cliente')
        cidade = request.form.get('cidade'); motivo = request.form.get('motivo'); situacao = request.form.get('situacao'); link_email = request.form.get('link_email')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO ocorrencias (data_ocorrencia, hora_ocorrencia, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', (data, hora, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email))
        conn.commit(); release_db_connection(conn)
        return redirect(url_for('cadastro', sucesso=True))
    except Exception as e: return f"Erro: {e}"

@app.route('/concluir/<int:id>')
@login_required
def concluir(id):
    agora = agora_brt().replace(tzinfo=None); conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE ocorrencias SET situacao = %s, data_conclusao = %s, hora_conclusao = %s WHERE id = %s', ("RESOLVIDO", agora.strftime("%Y-%m-%d"), agora.strftime("%H:%M"), id))
    conn.commit(); release_db_connection(conn)
    registrar_log("CONCLUSAO", f"Ocorrência {id} concluída.")
    return redirect(url_for('dashboard'))

@app.route('/editar/<int:id>')
@login_required
def editar(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ocorrencias WHERE id = %s", (id,))
    dado = cur.fetchone()

    permissao = False
    if current_user.nivel in ['gerencial', 'desenvolvedor']:
        permissao = True
    elif current_user.nivel == 'operacional' and dado['status_edicao'] == 'AUTORIZADO':
        permissao = True

    if not permissao:
        release_db_connection(conn)
        return "<script>alert('Acesso negado ou autorização pendente.'); window.history.back();</script>"

    cur2 = conn.cursor()
    cur2.execute("SELECT nome FROM motoristas ORDER BY nome")
    motoristas = [r['nome'] for r in cur2.fetchall()]
    cur2.execute("SELECT nome FROM clientes ORDER BY nome")
    clientes = [r['nome'] for r in cur2.fetchall()]
    cur2.execute("SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo")
    motivos = [r['motivo'] for r in cur2.fetchall()]
    release_db_connection(conn)
    return render_template('editar.html', dado=dado, motoristas=motoristas, clientes=clientes, motivos=motivos)

@app.route('/atualizar/<int:id>', methods=['POST'])
@login_required
def atualizar(id):
    conn = get_db_connection()

    cur = conn.cursor()
    cur.execute("SELECT status_edicao FROM ocorrencias WHERE id = %s", (id,))
    checar = cur.fetchone()
    permissao = False

    if current_user.nivel in ['gerencial', 'desenvolvedor']:
        permissao = True
    elif current_user.nivel == 'operacional' and checar['status_edicao'] == 'AUTORIZADO':
        permissao = True

    if not permissao:
        release_db_connection(conn)
        return "Acesso negado para salvar esta edição."

    try:
        data = request.form.get('data'); motorista = request.form.get('motorista')
        modalidade = request.form.get('modalidade'); cte = request.form.get('cte')
        operacao = request.form.get('operacao'); nfs = request.form.get('nfs')
        cliente = request.form.get('cliente'); cidade = request.form.get('cidade')
        motivo = request.form.get('motivo');
        link_email = request.form.get('link_email')



        cur.execute('UPDATE ocorrencias SET data_ocorrencia=%s, motorista=%s, modalidade=%s, cte=%s, operacao=%s, nfs=%s, cliente=%s, cidade=%s, motivo=%s, status_edicao=%s, link_email=%s WHERE id=%s', (data, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, 'BLOQUEADO', link_email, id))

        conn.commit(); release_db_connection(conn)
        registrar_log("EDIÇÃO", f"Ocorrência {id} atualizada.")
        return redirect(url_for('dashboard'))
    except Exception as e: return f"Erro: {e}"

@app.route('/relatorios')
@login_required
def relatorios():
    query, params = construir_query(request.args)
    query += " ORDER BY data_ocorrencia DESC"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    cur.execute("SELECT DISTINCT cidade FROM ocorrencias ORDER BY cidade")
    cidade_db = cur.fetchall()
    lista_cidades = [c['cidade'] for c in cidade_db if c['cidade']]
    cur.execute("SELECT DISTINCT motivo FROM ocorrencias ORDER BY motivo")
    motivo_db = cur.fetchall()
    lista_motivos = [m['motivo'] for m in motivo_db if m['motivo']]
    release_db_connection(conn)

    dados_graficos = {'ops': {}, 'sla': {'resolvido':0, 'atrasado':0}, 'motivos': {}}
    if not df.empty:
        if 'operacao' in df.columns:
            dados_graficos['ops'] = df['operacao'].value_counts().to_dict()
        resolvidos = df[df['situacao'] == 'RESOLVIDO']
        dados_graficos['sla']['resolvido'] = len(resolvidos)
        atrasados_count = 0
        for index, row in df.iterrows():
             if verificar_atraso(row): atrasados_count += 1
        dados_graficos['sla']['atrasado'] = atrasados_count
        if 'motivo' in df.columns:
            dados_graficos['motivos'] = df['motivo'].value_counts().head(5).to_dict()

    if request.args.get('sla') == 'atrasado':
        if not df.empty: df = df[df.apply(verificar_atraso, axis=1)]

    pagina_atual = request.args.get('page', 1, type=int)
    itens_por_pagina = 20
    total_registros = len(df)
    total_paginas = max(1, math.ceil(total_registros / itens_por_pagina))

    inicio = (pagina_atual - 1) * itens_por_pagina
    fim = inicio + itens_por_pagina

    df_pagina = df.iloc[inicio:fim]

    tabela_html = df_pagina.to_html(classes='w-full text-sm text-left text-slate-300', header="true", index=False, border=0)
    tabela_html = tabela_html.replace('class="dataframe ', 'class="')

    return render_template('relatorios.html', tabela=tabela_html, graficos=dados_graficos, cidades=lista_cidades, motivos=lista_motivos, pagina_atual=pagina_atual, total_paginas=total_paginas, filtros=request.args)

@app.route('/logs')
@login_required
def logs():
    if current_user.nivel not in ['gerencial', 'desenvolvedor']: return "Acesso negado"

    pagina = request.args.get('page', 1, type=int); itens_por_pagina = 20; offset = (pagina - 1) * itens_por_pagina
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM logs')
    total_logs = cur.fetchone()['count']
    total_paginas = math.ceil(total_logs / itens_por_pagina)
    cur.execute('SELECT * FROM logs ORDER BY id DESC LIMIT %s OFFSET %s', (itens_por_pagina, offset))
    logs_db = cur.fetchall(); release_db_connection(conn)

    logs_filtrados = []
    for log in logs_db:
        if current_user.nivel != 'desenvolvedor' and 'Link de reset' in log['detalhes']:
            continue
        logs_filtrados.append(log)

    return render_template('logs.html', logs=logs_filtrados, pagina=pagina, total_paginas=total_paginas)

@app.route('/atualizar_cards')
@login_required
def atualizar_cards():
    tipo_filtro = request.args.get('tipo'); termo_busca = request.args.get('busca'); filtro_atraso = request.args.get('filtro_atraso')
    conn = get_db_connection()
    cur = conn.cursor()
    sql = 'SELECT * FROM ocorrencias WHERE (arquivado = 0 OR arquivado IS NULL)'; params = []
    if tipo_filtro and termo_busca:
        mapa = {'MOTORISTA':'motorista','MODALIDADE':'modalidade','CLIENTE':'cliente','CIDADE':'cidade','MOTIVO':'motivo'}
        col = mapa.get(tipo_filtro)
        if col: sql += f" AND {col} LIKE %s"; params.append(f'%{termo_busca}%')
    sql += ' ORDER BY id DESC'
    cur.execute(sql, params)
    ocorrencias = cur.fetchall(); release_db_connection(conn)
    agora = agora_brt().replace(tzinfo=None); dados_processados = []
    metricas = {'total': 0, 'andamento': 0, 'resolvidas': 0, 'atrasadas': 0}

    for row in ocorrencias:
        r = dict(row); metricas['total'] += 1; status = r['situacao'].upper()
        r['fotos_count'] = len(json.loads(r['fotos'])) if r.get('fotos') else 0
        cor = "orange"; texto = r['situacao']; peso = 2
        if status == "RESOLVIDO": cor = "green"; metricas['resolvidas'] += 1; peso = 3
        else:
            atrasado = False
            try:
                dt_str = r['data_ocorrencia']; hr_str = r['hora_ocorrencia']
                if '-' in dt_str: d = datetime.strptime(dt_str, "%Y-%m-%d")
                else: d = datetime.strptime(dt_str, "%d/%m/%Y")
                if hr_str and ':' in hr_str: h,m = map(int, hr_str.split(':')); d = d.replace(hour=h, minute=m)
                if (agora - d) > timedelta(hours=24): atrasado = True
            except Exception as e: logger.warning(f"Erro ao parsear data: {e}")
            if atrasado: cor = "red"; texto = "JÁ PASSOU 24H"; metricas['atrasadas'] += 1; peso = 1
            else: metricas['andamento'] += 1; peso = 2
        r['cor_card'] = cor; r['status_display'] = texto; r['peso'] = peso; dados_processados.append(r)

    if filtro_atraso == 'sim': dados_processados = [d for d in dados_processados if d['cor_card'] == 'red']
    dados_processados.sort(key=lambda x: x['peso'])
    html_cards = render_template('cards_partial.html', dados=dados_processados)
    return jsonify({'html': html_cards, 'metricas': metricas, 'ocorrencias': dados_processados})

@app.route('/api/listas_cadastro')
@login_required
def api_listas_cadastro():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nome FROM motoristas ORDER BY nome")
    motoristas = [r['nome'] for r in cur.fetchall()]
    cur.execute("SELECT nome FROM clientes ORDER BY nome")
    clientes = [r['nome'] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo")
    motivos = [r['motivo'] for r in cur.fetchall()]
    release_db_connection(conn)
    return jsonify({'motoristas': motoristas, 'clientes': clientes, 'motivos': motivos})

if __name__ == '__main__':
    app.run(debug=False)