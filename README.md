# Posembarque — Painel Operacional Pós-Embarque

Sistema web de gestão de ocorrências logísticas em tempo real, desenvolvido para a operação de pós-embarque da Tramontina. Permite o registo, acompanhamento, edição e reporte de incidentes de transporte com controlo de SLA de 24 horas.

---

## Tecnologias

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python 3 · Flask · Flask-Login · Flask-Mail · Flask-Limiter |
| Base de Dados | PostgreSQL (psycopg2) |
| Frontend | Tailwind CSS · Lucide 0.263.0 |
| Servidor | Gunicorn · Nginx (reverse proxy) |
| PDF | pdfkit + wkhtmltopdf · ReportLab |
| Gráficos | Matplotlib |
| Email | Gmail SMTP (Flask-Mail) |

---

## Funcionalidades

### Painel Principal
- Cards de ocorrências em tempo real com atualização automática a cada 3 segundos
- Código de cores por estado: 🟢 Resolvido · 🟠 Em andamento · 🔴 Atrasado (>24h)
- Filtros por motorista, cliente, cidade, modalidade e motivo
- Modo escuro / claro
- Métricas ao vivo: total, em andamento, resolvidas, atrasadas

### Gestão de Ocorrências
- Registo de ocorrências com: data/hora, motorista, cliente, cidade, modalidade, CTE, NFs, operação, motivo e link de email
- Upload de até 5 fotos por ocorrência (PNG, JPG, JPEG, GIF, WEBP — máx. 5 MB)
- Conclusão e arquivamento de registos
- Fluxo de permissão de edição para utilizadores operacionais

### Relatórios e Exportação
- Filtros avançados: período, motorista, cliente, cidade, modalidade, operação, motivo
- Exportação para PDF com 3 gráficos automáticos:
  - Volume por Operação (donut)
  - Resolvidos vs Atrasados (pizza)
  - Top 5 Motivos de Incidentes (barras)
- Paginação de 22 linhas por página

### Administração
- Gestão de motoristas e clientes cadastrados
- Logs de auditoria de todas as ações por utilizador
- Backup manual da base de dados
- Central de chamados de suporte (nível desenvolvedor)
- Publicação de patches/atualizações do sistema

### Segurança
- Autenticação com bcrypt
- Proteção CSRF (Flask-WTF)
- Cookies HTTPOnly + Secure + SameSite=Lax
- Headers de segurança: HSTS, X-Frame-Options, X-Content-Type-Options, XSS-Protection
- Timeout de sessão por inatividade (2 horas)

---

## Níveis de Acesso

| Nível | Permissões |
|-------|-----------|
| `operacional` | Ver painel, criar/concluir ocorrências, fazer upload de fotos, solicitar permissão de edição, ver relatórios |
| `gerencial` | Tudo do operacional + gerir motoristas/clientes, aprovar edições, ver logs, criar backups |
| `desenvolvedor` | Acesso total + central de chamados, publicar patches, acesso a logs completos |
| `tv` | Modo leitura para painéis de TV — sem timeout de sessão |

---

## Estrutura do Projeto

```
posembarque/
├── app.py                  # Aplicação Flask principal (~1200 linhas)
├── requirements.txt        # Dependências Python
├── run_backup.py           # Script de backup automático (cron)
├── gunicorn.ctl            # Socket de controlo do Gunicorn
├── .env                    # Variáveis de ambiente (não versionado)
├── templates/
│   ├── dashboard.html      # Painel principal
│   ├── cards_partial.html  # Componente de cards (renderizado via AJAX)
│   ├── cadastro.html       # Formulário de nova ocorrência
│   ├── editar.html         # Formulário de edição
│   ├── relatorios.html     # Página de relatórios
│   ├── relatorio_pdf.html  # Template para exportação PDF
│   ├── central_chamados.html # Gestão de suporte
│   ├── cadastros.html      # Gestão de motoristas e clientes
│   ├── logs.html           # Visualizador de logs
│   ├── login.html          # Página de autenticação
│   ├── esqueci_senha.html  # Recuperação de password
│   └── resetar_senha.html  # Redefinição de password
├── static/
│   ├── tailwind.css
│   ├── lucide-0.263.0.min.js
│   └── favicon.png
├── uploads/                # Fotos das ocorrências
└── backups_auto/           # Backups locais da base de dados
```

---

## Rotas Principais

| Rota | Método | Descrição |
|------|--------|-----------|
| `/` | GET | Dashboard com cards ao vivo |
| `/login` | GET, POST | Autenticação |
| `/logout` | GET | Encerrar sessão |
| `/cadastro` | GET | Formulário de nova ocorrência |
| `/salvar` | POST | Guardar nova ocorrência |
| `/editar/<id>` | GET | Formulário de edição |
| `/atualizar/<id>` | POST | Atualizar ocorrência |
| `/concluir/<id>` | GET | Marcar como resolvido |
| `/atualizar_cards` | GET | AJAX — cards + métricas em JSON |
| `/relatorios` | GET | Página de relatórios avançados |
| `/exportar_pdf` | GET | Gerar e descarregar PDF |
| `/solicitar_edicao/<id>` | POST | Solicitar permissão de edição |
| `/liberar_edicao/<id>` | GET | Aprovar pedido de edição |
| `/upload_foto/<id>` | POST | Carregar foto |
| `/gerenciar_cadastros` | GET | Gerir motoristas e clientes |
| `/central_chamados` | GET | Central de suporte (desenvolvedor) |
| `/logs` | GET | Logs de auditoria |
| `/backup_banco` | GET | Backup manual da BD |

---

## Configuração e Instalação

### Pré-requisitos
- Python 3.10+
- PostgreSQL 14+
- wkhtmltopdf (para exportação PDF)

### 1. Clonar o repositório
```bash
git clone https://github.com/tay-create/posembarque-v2.git
cd posembarque-v2
```

### 2. Criar ambiente virtual e instalar dependências
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente
Criar ficheiro `.env` na raiz do projeto:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=posembarque-transnet
DB_USER=postgres
DB_PASSWORD=sua_password_postgres
SECRET_KEY=chave_secreta_32_bytes_hex
MAIL_PASSWORD=app_password_gmail
```

> `MAIL_PASSWORD` é uma **App Password do Gmail** (não a password da conta).

### 4. Iniciar com Gunicorn
```bash
gunicorn --workers 2 --bind 127.0.0.1:8000 app:app
```

---

## Base de Dados

| Tabela | Descrição |
|--------|-----------|
| `usuarios` | Contas de utilizador (username, senha bcrypt, nível, email, token reset) |
| `ocorrencias` | Incidentes de transporte (dados completos, fotos, estado, permissão de edição) |
| `motoristas` | Registo de motoristas |
| `clientes` | Registo de clientes |
| `chamados` | Chamados de suporte entre utilizadores e desenvolvedor |
| `logs` | Trilha de auditoria de todas as ações |
| `sistema_patches` | Anúncios de atualizações do sistema |
| `sistema_views` | Registo de visualizações de patches por utilizador |

---

## Backup Automático

O script `run_backup.py` efetua backups da base de dados PostgreSQL via `pg_dump`, mantendo os 4 mais recentes. Configurado via cron job para execução periódica. Os ficheiros são guardados em `/mnt/d/backup-posembarque`.

---

## Notas de Desenvolvimento

- **Timezone**: Todos os timestamps usam o fuso horário de São Paulo (BRT)
- **Ícones**: Lucide 0.263.0 local — usar `headphones` (não `headset`) e `pencil` (não `edit-3`)
- **Sessão**: Timeout de 2h por inatividade; desativado com "Manter conectado" ou para nível `tv`
- **Fotos**: Validadas por MIME type e magic bytes (imghdr) antes do armazenamento
