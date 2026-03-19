with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    # construir_query - ? -> %s in dynamic queries
    (' AND data_ocorrencia >= ?"', ' AND data_ocorrencia >= %s"'),
    (' AND data_ocorrencia <= ?"', ' AND data_ocorrencia <= %s"'),
    (' AND cliente LIKE ?"', ' AND cliente LIKE %s"'),
    (' AND motorista LIKE ?"', ' AND motorista LIKE %s"'),
    (' AND motivo LIKE ?"', ' AND motivo LIKE %s"'),
    (' AND cidade LIKE ?"', ' AND cidade LIKE %s"'),
    (' AND modalidade = ?"', ' AND modalidade = %s"'),
    (' AND operacao = ?"', ' AND operacao = %s"'),
    (' AND {col} LIKE ?"', ' AND {col} LIKE %s"'),
]

for old, new in replacements:
    content = content.replace(old, new)

# Now handle all remaining conn.execute(...) patterns
# We'll rewrite the file using a line-by-line approach for inline ones
# and targeted block replacements for multi-line ones

blocks = [
    (
        'conn.execute("INSERT INTO sistema_patches (titulo, itens, data_lancamento) VALUES (?, ?, ?)",\n                 (titulo, json.dumps(lista_itens), agora))\n    conn.commit()\n    conn.close()',
        'cur = conn.cursor()\n    cur.execute("INSERT INTO sistema_patches (titulo, itens, data_lancamento) VALUES (%s, %s, %s)",\n                 (titulo, json.dumps(lista_itens), agora))\n    conn.commit()\n    conn.close()'
    ),
    (
        '        ocorrencia = conn.execute("SELECT fotos FROM ocorrencias WHERE id = ?", (id,)).fetchone()\n\n        if ocorrencia and ocorrencia[\'fotos\']:\n            fotos = json.loads(ocorrencia[\'fotos\'])\n            for foto in fotos:\n                caminho_arquivo = os.path.join(app.config[\'UPLOAD_FOLDER\'], foto)\n                if os.path.exists(caminho_arquivo):\n                    os.remove(caminho_arquivo)\n\n        conn.execute("DELETE FROM ocorrencias WHERE id = ?", (id,))',
        '        cur = conn.cursor()\n        cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))\n        ocorrencia = cur.fetchone()\n\n        if ocorrencia and ocorrencia[\'fotos\']:\n            fotos = json.loads(ocorrencia[\'fotos\'])\n            for foto in fotos:\n                caminho_arquivo = os.path.join(app.config[\'UPLOAD_FOLDER\'], foto)\n                if os.path.exists(caminho_arquivo):\n                    os.remove(caminho_arquivo)\n\n        cur.execute("DELETE FROM ocorrencias WHERE id = %s", (id,))'
    ),
    (
        '        ocorrencia = conn.execute("SELECT fotos FROM ocorrencias WHERE id = ?", (id,)).fetchone()\n        fotos_atuais = json.loads(ocorrencia[\'fotos\']) if (ocorrencia and ocorrencia[\'fotos\']) else []\n\n        if len(fotos_atuais) >= 5:\n            return jsonify({\'erro\': \'Limite de 5 fotos atingido\'}), 400\n\n        ext = file.filename.rsplit(\'.\', 1)[1].lower()\n        nome_arquivo = f"img_{id}_{len(fotos_atuais) + 1}_{datetime.now().strftime(\'%M%S\')}.{ext}"\n        caminho_salvar = os.path.join(app.config[\'UPLOAD_FOLDER\'], nome_arquivo)\n        file.save(caminho_salvar)\n\n        fotos_atuais.append(nome_arquivo)\n        conn.execute("UPDATE ocorrencias SET fotos = ? WHERE id = ?", (json.dumps(fotos_atuais), id))',
        '        cur = conn.cursor()\n        cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))\n        ocorrencia = cur.fetchone()\n        fotos_atuais = json.loads(ocorrencia[\'fotos\']) if (ocorrencia and ocorrencia[\'fotos\']) else []\n\n        if len(fotos_atuais) >= 5:\n            return jsonify({\'erro\': \'Limite de 5 fotos atingido\'}), 400\n\n        ext = file.filename.rsplit(\'.\', 1)[1].lower()\n        nome_arquivo = f"img_{id}_{len(fotos_atuais) + 1}_{datetime.now().strftime(\'%M%S\')}.{ext}"\n        caminho_salvar = os.path.join(app.config[\'UPLOAD_FOLDER\'], nome_arquivo)\n        file.save(caminho_salvar)\n\n        fotos_atuais.append(nome_arquivo)\n        cur.execute("UPDATE ocorrencias SET fotos = %s WHERE id = %s", (json.dumps(fotos_atuais), id))'
    ),
    (
        '        ocorrencia = conn.execute("SELECT fotos FROM ocorrencias WHERE id = ?", (id,)).fetchone()\n\n        if ocorrencia and ocorrencia[\'fotos\']:\n            fotos = json.loads(ocorrencia[\'fotos\'])\n\n            # Verifica se a foto realmente pertence a esta ocorrência\n            if nome_foto in fotos:\n                fotos.remove(nome_foto) # Remove da lista\n\n                # Atualiza o banco de dados\n                conn.execute("UPDATE ocorrencias SET fotos = ? WHERE id = ?", (json.dumps(fotos), id))',
        '        cur = conn.cursor()\n        cur.execute("SELECT fotos FROM ocorrencias WHERE id = %s", (id,))\n        ocorrencia = cur.fetchone()\n\n        if ocorrencia and ocorrencia[\'fotos\']:\n            fotos = json.loads(ocorrencia[\'fotos\'])\n\n            # Verifica se a foto realmente pertence a esta ocorrência\n            if nome_foto in fotos:\n                fotos.remove(nome_foto) # Remove da lista\n\n                # Atualiza o banco de dados\n                cur.execute("UPDATE ocorrencias SET fotos = %s WHERE id = %s", (json.dumps(fotos), id))'
    ),
    (
        "    conn.execute(\"UPDATE ocorrencias SET status_edicao = 'SOLICITADO', motivo_edicao =? WHERE id = ?\", (motivo, id,))",
        "    cur = conn.cursor()\n    cur.execute(\"UPDATE ocorrencias SET status_edicao = 'SOLICITADO', motivo_edicao = %s WHERE id = %s\", (motivo, id,))"
    ),
    (
        "    conn.execute(\"UPDATE ocorrencias SET status_edicao = 'BLOQUEADO', motivo_edicao = NULL WHERE id = ?\", (id,))",
        "    cur = conn.cursor()\n    cur.execute(\"UPDATE ocorrencias SET status_edicao = 'BLOQUEADO', motivo_edicao = NULL WHERE id = %s\", (id,))"
    ),
    (
        "    conn.execute(\"UPDATE ocorrencias SET status_edicao = 'AUTORIZADO' WHERE id = ?\", (id,))",
        "    cur = conn.cursor()\n    cur.execute(\"UPDATE ocorrencias SET status_edicao = 'AUTORIZADO' WHERE id = %s\", (id,))"
    ),
    (
        "    ocorrencias = conn.execute(sql, params).fetchall(); conn.close()\n\n    agora = datetime.now() - timedelta(hours=3); dados_processados = []\n    metricas = {'total': 0, 'andamento': 0, 'resolvidas': 0, 'atrasadas': 0}",
        "    cur = conn.cursor()\n    cur.execute(sql, params)\n    ocorrencias = cur.fetchall(); conn.close()\n\n    agora = datetime.now() - timedelta(hours=3); dados_processados = []\n    metricas = {'total': 0, 'andamento': 0, 'resolvidas': 0, 'atrasadas': 0}"
    ),
    (
        "        ultimo_patch = conn.execute(\"SELECT * FROM sistema_patches ORDER BY id DESC LIMIT 1\").fetchone()\n\n        if ultimo_patch:\n            view = conn.execute(\"SELECT contagem FROM sistema_views WHERE usuario_id = ? AND patch_id = ?\",\n                                (current_user.id, ultimo_patch['id'])).fetchone()\n\n            contagem = view[0] if view else 0\n\n            if contagem < 3:\n                aviso_patch = dict(ultimo_patch)\n                aviso_patch['lista_itens'] = json.loads(ultimo_patch['itens'])\n\n                if view:\n                    conn.execute(\"UPDATE sistema_views SET contagem = contagem + 1 WHERE usuario_id = ? AND patch_id = ?\",\n                                 (current_user.id, ultimo_patch['id']))\n                else:\n                    conn.execute(\"INSERT INTO sistema_views (usuario_id, patch_id, contagem) VALUES (?, ?, 1)\",\n                                 (current_user.id, ultimo_patch['id']))",
        "        cur2 = conn.cursor()\n        cur2.execute(\"SELECT * FROM sistema_patches ORDER BY id DESC LIMIT 1\")\n        ultimo_patch = cur2.fetchone()\n\n        if ultimo_patch:\n            cur2.execute(\"SELECT contagem FROM sistema_views WHERE usuario_id = %s AND patch_id = %s\",\n                                (current_user.id, ultimo_patch['id']))\n            view = cur2.fetchone()\n\n            contagem = view['contagem'] if view else 0\n\n            if contagem < 3:\n                aviso_patch = dict(ultimo_patch)\n                aviso_patch['lista_itens'] = json.loads(ultimo_patch['itens'])\n\n                if view:\n                    cur2.execute(\"UPDATE sistema_views SET contagem = contagem + 1 WHERE usuario_id = %s AND patch_id = %s\",\n                                 (current_user.id, ultimo_patch['id']))\n                else:\n                    cur2.execute(\"INSERT INTO sistema_views (usuario_id, patch_id, contagem) VALUES (%s, %s, 1)\",\n                                 (current_user.id, ultimo_patch['id']))"
    ),
    (
        "    motoristas = [r['nome'] for r in conn.execute(\"SELECT nome FROM motoristas ORDER BY nome\").fetchall()]\n    clientes = [r['nome'] for r in conn.execute(\"SELECT nome FROM clientes ORDER BY nome\").fetchall()]\n    conn.close()\n    return render_template('cadastros.html', motoristas=motoristas, clientes=clientes)",
        "    cur = conn.cursor()\n    cur.execute(\"SELECT nome FROM motoristas ORDER BY nome\")\n    motoristas = [r['nome'] for r in cur.fetchall()]\n    cur.execute(\"SELECT nome FROM clientes ORDER BY nome\")\n    clientes = [r['nome'] for r in cur.fetchall()]\n    conn.close()\n    return render_template('cadastros.html', motoristas=motoristas, clientes=clientes)"
    ),
    (
        '        conn.execute(f"INSERT OR IGNORE INTO {tabela} (nome) VALUES (?)", (nome,))',
        '        cur = conn.cursor()\n        cur.execute(f"INSERT INTO {tabela} (nome) VALUES (%s) ON CONFLICT DO NOTHING", (nome,))'
    ),
    (
        '        conn.execute(f"DELETE FROM {tabela} WHERE nome = ?", (nome,))',
        '        cur = conn.cursor()\n        cur.execute(f"DELETE FROM {tabela} WHERE nome = %s", (nome,))'
    ),
    (
        "        conn.execute(\"UPDATE ocorrencias SET arquivado = 1 WHERE situacao = 'RESOLVIDO'\")",
        "        cur = conn.cursor()\n        cur.execute(\"UPDATE ocorrencias SET arquivado = 1 WHERE situacao = 'RESOLVIDO'\")"
    ),
    (
        "    ocorrencias_db = conn.execute(query, params).fetchall()\n    conn.close()",
        "    cur = conn.cursor()\n    cur.execute(query, params)\n    ocorrencias_db = cur.fetchall()\n    conn.close()"
    ),
    (
        "        user_data = conn.execute(\"SELECT * FROM usuarios WHERE username = ?\", (username,)).fetchone()",
        "        cur = conn.cursor()\n        cur.execute(\"SELECT * FROM usuarios WHERE username = %s\", (username,))\n        user_data = cur.fetchone()"
    ),
    (
        "        conn.execute(\"UPDATE usuarios SET email = ? WHERE username = ?\", (email, current_user.id))",
        "        cur = conn.cursor()\n        cur.execute(\"UPDATE usuarios SET email = %s WHERE username = %s\", (email, current_user.id))"
    ),
    (
        "        user = conn.execute(\"SELECT * FROM usuarios WHERE username = ? AND email = ?\", (username, email)).fetchone()",
        "        cur = conn.cursor()\n        cur.execute(\"SELECT * FROM usuarios WHERE username = %s AND email = %s\", (username, email))\n        user = cur.fetchone()"
    ),
    (
        "            conn.execute(\"UPDATE usuarios SET token_reset = ?, token_expiracao = ? WHERE username = ?\",\n                         (token, expiracao, username))",
        "            cur.execute(\"UPDATE usuarios SET token_reset = %s, token_expiracao = %s WHERE username = %s\",\n                         (token, expiracao, username))"
    ),
    (
        "    user = conn.execute(\"SELECT * FROM usuarios WHERE token_reset = ?\", (token,)).fetchone()",
        "    cur = conn.cursor()\n    cur.execute(\"SELECT * FROM usuarios WHERE token_reset = %s\", (token,))\n    user = cur.fetchone()"
    ),
    (
        "            conn.execute(\"UPDATE usuarios SET senha = ?, token_reset = NULL, token_expiracao = NULL WHERE username = ?\",\n                         (nova_senha, user['username']))",
        "            cur.execute(\"UPDATE usuarios SET senha = %s, token_reset = NULL, token_expiracao = NULL WHERE username = %s\",\n                         (nova_senha, user['username']))"
    ),
    (
        "    motoristas = [r['nome'] for r in conn.execute(\"SELECT nome FROM motoristas ORDER BY nome\").fetchall()]\n    clientes = [r['nome'] for r in conn.execute(\"SELECT nome FROM clientes ORDER BY nome\").fetchall()]\n    motivos = [r['motivo'] for r in conn.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\").fetchall()]\n    conn.close()\n    return render_template('cadastro.html', motoristas=motoristas, clientes=clientes, motivos=motivos)",
        "    cur = conn.cursor()\n    cur.execute(\"SELECT nome FROM motoristas ORDER BY nome\")\n    motoristas = [r['nome'] for r in cur.fetchall()]\n    cur.execute(\"SELECT nome FROM clientes ORDER BY nome\")\n    clientes = [r['nome'] for r in cur.fetchall()]\n    cur.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\")\n    motivos = [r['motivo'] for r in cur.fetchall()]\n    conn.close()\n    return render_template('cadastro.html', motoristas=motoristas, clientes=clientes, motivos=motivos)"
    ),
    (
        "        conn.execute('INSERT INTO ocorrencias (data_ocorrencia, hora_ocorrencia, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (data, hora, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email))",
        "        cur = conn.cursor()\n        cur.execute('INSERT INTO ocorrencias (data_ocorrencia, hora_ocorrencia, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', (data, hora, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, situacao, responsavel, link_email))"
    ),
    (
        '    conn.execute(\'UPDATE ocorrencias SET situacao = ?, data_conclusao = ?, hora_conclusao = ? WHERE id = ?\', ("RESOLVIDO", agora.strftime("%Y-%m-%d"), agora.strftime("%H:%M"), id))',
        '    cur = conn.cursor()\n    cur.execute(\'UPDATE ocorrencias SET situacao = %s, data_conclusao = %s, hora_conclusao = %s WHERE id = %s\', ("RESOLVIDO", agora.strftime("%Y-%m-%d"), agora.strftime("%H:%M"), id))'
    ),
    (
        '    dado = conn.execute("SELECT * FROM ocorrencias WHERE id = ?", (id,)).fetchone()\n\n    permissao = False',
        '    cur = conn.cursor()\n    cur.execute("SELECT * FROM ocorrencias WHERE id = %s", (id,))\n    dado = cur.fetchone()\n\n    permissao = False'
    ),
    (
        "    motoristas = [r['nome'] for r in conn.execute(\"SELECT nome FROM motoristas ORDER BY nome\").fetchall()]\n    clientes = [r['nome'] for r in conn.execute(\"SELECT nome FROM clientes ORDER BY nome\").fetchall()]\n    motivos = [r['motivo'] for r in conn.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\").fetchall()]\n    conn.close()\n    return render_template('editar.html', dado=dado, motoristas=motoristas, clientes=clientes, motivos=motivos)",
        "    cur2 = conn.cursor()\n    cur2.execute(\"SELECT nome FROM motoristas ORDER BY nome\")\n    motoristas = [r['nome'] for r in cur2.fetchall()]\n    cur2.execute(\"SELECT nome FROM clientes ORDER BY nome\")\n    clientes = [r['nome'] for r in cur2.fetchall()]\n    cur2.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\")\n    motivos = [r['motivo'] for r in cur2.fetchall()]\n    conn.close()\n    return render_template('editar.html', dado=dado, motoristas=motoristas, clientes=clientes, motivos=motivos)"
    ),
    (
        '    checar = conn.execute("SELECT status_edicao FROM ocorrencias WHERE id = ?", (id,)).fetchone()',
        '    cur = conn.cursor()\n    cur.execute("SELECT status_edicao FROM ocorrencias WHERE id = %s", (id,))\n    checar = cur.fetchone()'
    ),
    (
        "        conn.execute('UPDATE ocorrencias SET data_ocorrencia=?, motorista=?, modalidade=?, cte=?, operacao=?, nfs=?, cliente=?, cidade=?, motivo=?, status_edicao=?, link_email=? WHERE id=?', (data, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, 'BLOQUEADO', link_email, id))",
        "        cur.execute('UPDATE ocorrencias SET data_ocorrencia=%s, motorista=%s, modalidade=%s, cte=%s, operacao=%s, nfs=%s, cliente=%s, cidade=%s, motivo=%s, status_edicao=%s, link_email=%s WHERE id=%s', (data, motorista, modalidade, cte, operacao, nfs, cliente, cidade, motivo, 'BLOQUEADO', link_email, id))"
    ),
    (
        "    df = pd.read_sql_query(query, conn, params=params)\n    cidade_db = conn.execute(\"SELECT DISTINCT cidade FROM ocorrencias ORDER BY cidade\").fetchall()\n    lista_cidades = [c['cidade'] for c in cidade_db if c['cidade']]\n    motivo_db = conn.execute(\"SELECT DISTINCT motivo FROM ocorrencias ORDER BY motivo\").fetchall()\n    lista_motivos = [m['motivo'] for m in motivo_db if m['motivo']]\n    conn.close()",
        "    cur = conn.cursor()\n    cur.execute(query, params)\n    rows = cur.fetchall()\n    df = pd.DataFrame(rows) if rows else pd.DataFrame()\n    cur.execute(\"SELECT DISTINCT cidade FROM ocorrencias ORDER BY cidade\")\n    cidade_db = cur.fetchall()\n    lista_cidades = [c['cidade'] for c in cidade_db if c['cidade']]\n    cur.execute(\"SELECT DISTINCT motivo FROM ocorrencias ORDER BY motivo\")\n    motivo_db = cur.fetchall()\n    lista_motivos = [m['motivo'] for m in motivo_db if m['motivo']]\n    conn.close()"
    ),
    (
        "    conn = get_db_connection(); total_logs = conn.execute('SELECT COUNT(*) FROM logs').fetchone()[0]\n    total_paginas = math.ceil(total_logs / itens_por_pagina)\n    logs_db = conn.execute('SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?', (itens_por_pagina, offset)).fetchall(); conn.close()",
        "    conn = get_db_connection()\n    cur = conn.cursor()\n    cur.execute('SELECT COUNT(*) FROM logs')\n    total_logs = cur.fetchone()['count']\n    total_paginas = math.ceil(total_logs / itens_por_pagina)\n    cur.execute('SELECT * FROM logs ORDER BY id DESC LIMIT %s OFFSET %s', (itens_por_pagina, offset))\n    logs_db = cur.fetchall(); conn.close()"
    ),
    (
        "    conn = get_db_connection(); sql = 'SELECT * FROM ocorrencias WHERE (arquivado = 0 OR arquivado IS NULL)'; params = []\n    if tipo_filtro and termo_busca:\n        mapa = {'MOTORISTA':'motorista','MODALIDADE':'modalidade','CLIENTE':'cliente','CIDADE':'cidade','MOTIVO':'motivo'}\n        col = mapa.get(tipo_filtro)\n        if col: sql += f\" AND {col} LIKE ?\"; params.append(f'%{termo_busca}%')\n    sql += ' ORDER BY id DESC'\n    ocorrencias = conn.execute(sql, params).fetchall(); conn.close()",
        "    conn = get_db_connection()\n    cur = conn.cursor()\n    sql = 'SELECT * FROM ocorrencias WHERE (arquivado = 0 OR arquivado IS NULL)'; params = []\n    if tipo_filtro and termo_busca:\n        mapa = {'MOTORISTA':'motorista','MODALIDADE':'modalidade','CLIENTE':'cliente','CIDADE':'cidade','MOTIVO':'motivo'}\n        col = mapa.get(tipo_filtro)\n        if col: sql += f\" AND {col} LIKE %s\"; params.append(f'%{termo_busca}%')\n    sql += ' ORDER BY id DESC'\n    cur.execute(sql, params)\n    ocorrencias = cur.fetchall(); conn.close()"
    ),
    (
        "    motoristas = [r['nome'] for r in conn.execute(\"SELECT nome FROM motoristas ORDER BY nome\").fetchall()]\n    clientes = [r['nome'] for r in conn.execute(\"SELECT nome FROM clientes ORDER BY nome\").fetchall()]\n    motivos = [r['motivo'] for r in conn.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\").fetchall()]\n    conn.close()\n    return jsonify({'motoristas': motoristas, 'clientes': clientes, 'motivos': motivos})",
        "    cur = conn.cursor()\n    cur.execute(\"SELECT nome FROM motoristas ORDER BY nome\")\n    motoristas = [r['nome'] for r in cur.fetchall()]\n    cur.execute(\"SELECT nome FROM clientes ORDER BY nome\")\n    clientes = [r['nome'] for r in cur.fetchall()]\n    cur.execute(\"SELECT DISTINCT motivo FROM ocorrencias WHERE motivo IS NOT NULL ORDER BY motivo\")\n    motivos = [r['motivo'] for r in cur.fetchall()]\n    conn.close()\n    return jsonify({'motoristas': motoristas, 'clientes': clientes, 'motivos': motivos})"
    ),
]

not_found = []
for old, new in blocks:
    if old in content:
        content = content.replace(old, new)
        print(f"OK: {old[:60].strip()!r}")
    else:
        not_found.append(old[:80].strip())
        print(f"NAO ENCONTRADO: {old[:80].strip()!r}")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

remaining = [(i+1, l) for i, l in enumerate(content.split('\n')) if 'conn.execute(' in l]
print(f"\nconn.execute restantes: {len(remaining)}")
for ln, l in remaining:
    print(f"  L{ln}: {l.rstrip()}")

if not_found:
    print(f"\nNao encontrados ({len(not_found)}):")
    for x in not_found:
        print(f"  - {x!r}")
