import os
import subprocess
import logging
from datetime import datetime
from dotenv import load_dotenv

# Configuração de log
logging.basicConfig(
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backup_cron.log'),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

PG_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'dbname': os.environ.get('DB_NAME', 'posembarque-transnet'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', '')
}

PASTA_BACKUP = '/mnt/d/backup-posembarque'

def rotina_backup():
    logger.info("Iniciando rotina de backup via Cron Job...")

    if not os.path.exists(PASTA_BACKUP):
        try:
            os.makedirs(PASTA_BACKUP)
        except OSError as e:
            logger.error(f"Não foi possível criar pasta de backup: {e}")
            return

    agora = datetime.now()
    nome_arquivo = f"backup_auto_{agora.strftime('%Y_%m_%d_%H%M')}.dump"
    destino = os.path.join(PASTA_BACKUP, nome_arquivo)

    try:
        env_backup = os.environ.copy()
        env_backup['PGPASSWORD'] = PG_CONFIG['password']
        
        resultado = subprocess.run([
            'pg_dump',
            '-U', PG_CONFIG["user"],
            '-h', PG_CONFIG["host"],
            '-p', str(PG_CONFIG["port"]),
            '-F', 'c', # Formato customizado do pg_dump
            '-f', destino,
            PG_CONFIG["dbname"]
        ], capture_output=True, text=True, env=env_backup)

        if resultado.returncode == 0:
            logger.info(f"BACKUP AUTO SUCESSO: Criado {nome_arquivo}")

            # Limpar backups antigos (manter os 4 mais recentes)
            arquivos = sorted(
                [os.path.join(PASTA_BACKUP, f) for f in os.listdir(PASTA_BACKUP) if f.startswith('backup_auto_')],
                key=os.path.getmtime
            )
            while len(arquivos) > 4:
                arquivo_antigo = arquivos.pop(0)
                try:
                    os.remove(arquivo_antigo)
                    logger.info(f"Removido backup antigo: {os.path.basename(arquivo_antigo)}")
                except Exception as e:
                    logger.error(f"Erro ao remover backup antigo {arquivo_antigo}: {e}")
        else:
            logger.error(f"BACKUP AUTO FALHOU (pg_dump): {resultado.stderr}")

    except Exception as e:
        logger.error(f"Erro inesperado na rotina de backup: {e}")

if __name__ == '__main__':
    rotina_backup()
