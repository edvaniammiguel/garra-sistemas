#!/usr/bin/env python3
"""
Gestao de Pneus - Garra Terraplenagem v4.0
PostgreSQL + Render-ready
"""
import json, os, datetime, psycopg2, psycopg2.extras
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DATABASE_URL = os.environ.get('DATABASE_URL', '')

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def now():
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS almoxarifados (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, local TEXT, ativo INTEGER DEFAULT 1
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS equipamentos (
        id TEXT PRIMARY KEY, nome TEXT, tipo TEXT, org TEXT, cat TEXT,
        posicoes_json TEXT, ativo INTEGER DEFAULT 1
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS pneus (
        id TEXT PRIMARY KEY, marca TEXT, medida TEXT,
        sulco_ini INTEGER DEFAULT 16, sulco_atual INTEGER DEFAULT 16,
        km INTEGER DEFAULT 0, cond TEXT DEFAULT 'novo',
        obs TEXT, status TEXT DEFAULT 'cadastrado',
        equip_id TEXT, pos_id TEXT, almo_id TEXT,
        valor_unit REAL DEFAULT 0, nf_ref TEXT, dt_entrada TEXT, ativo INTEGER DEFAULT 1
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS historico (
        id SERIAL PRIMARY KEY,
        dt TEXT, pneu_id TEXT, equip_id TEXT,
        pos_label TEXT, tipo TEXT, obs TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS nf_compras (
        id SERIAL PRIMARY KEY,
        numero_nf TEXT NOT NULL, fornecedor TEXT,
        dt_emissao TEXT, dt_entrada TEXT,
        valor_total REAL DEFAULT 0, obs TEXT,
        almo_id TEXT, ativo INTEGER DEFAULT 1
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS itens_nf (
        id SERIAL PRIMARY KEY,
        nf_id INTEGER NOT NULL, pneu_id TEXT,
        descricao TEXT, marca TEXT, medida TEXT,
        quantidade INTEGER DEFAULT 1,
        valor_unit REAL DEFAULT 0, valor_total REAL DEFAULT 0
    )""")

    # Seed almoxarifados
    c.execute("SELECT COUNT(*) FROM almoxarifados")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO almoxarifados VALUES(%s,%s,%s,1)", [
            ('ALM-01','Almoxarifado Central','Sede - Galpao A'),
            ('ALM-02','Almoxarifado Campo','Obra - Externo'),
        ])

    # Seed equipamentos
    pos_bruck = json.dumps([
        {"id":"DIAN-DIR","label":"Dianteiro Dir","eixo":"Eixo Dianteiro"},
        {"id":"DIAN-ESQ","label":"Dianteiro Esq","eixo":"Eixo Dianteiro"},
        {"id":"TR-DIR-EXT","label":"Traseiro Dir Ext","eixo":"Eixo Traseiro"},
        {"id":"TR-DIR-INT","label":"Traseiro Dir Int","eixo":"Eixo Traseiro"},
        {"id":"TR-ESQ-INT","label":"Traseiro Esq Int","eixo":"Eixo Traseiro"},
        {"id":"TR-ESQ-EXT","label":"Traseiro Esq Ext","eixo":"Eixo Traseiro"},
    ])
    c.execute("SELECT COUNT(*) FROM equipamentos")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO equipamentos VALUES(%s,%s,%s,%s,%s,%s,1)", [
            ("CPO-0022","Caminhao Bruck CPO-022","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CPO-0026","Caminhao Bruck CPO-026","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CPO-0036","Caminhao Bruck CPO-036","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CP-0019","Caminhao Pipa CP-019","caminhao_pipa","01 - GERAL & INFRA G","Caminhao Pipa",pos_bruck),
        ])

    # Seed pneus demo
    c.execute("SELECT COUNT(*) FROM pneus")
    if c.fetchone()[0] == 0:
        pneus_seed = [
            ("PNEU-0032","Bridgestone R150","295/80 R22.5",16,14,45000,"usado","","montado","CPO-0022","TR-DIR-INT","ALM-01",0,"",now(),1),
            ("PNEU-0068","Bridgestone R150","295/80 R22.5",16,12,72000,"usado","","montado","CPO-0022","TR-DIR-EXT","ALM-01",0,"",now(),1),
            ("PNEU-0069","Firestone FS400","295/80 R22.5",16,5,95000,"usado","","montado","CPO-0022","TR-ESQ-INT","ALM-01",0,"",now(),1),
            ("PNEU-0114","Michelin XDE2","295/80 R22.5",15,11,60000,"usado","","montado","CPO-0022","DIAN-DIR","ALM-01",0,"",now(),1),
            ("PNEU-0115","Michelin XDE2","295/80 R22.5",15,9,80000,"usado","","montado","CPO-0022","DIAN-ESQ","ALM-01",0,"",now(),1),
            ("PNEU-A001","Bridgestone R150","295/80 R22.5",16,16,0,"novo","","estoque",None,None,"ALM-01",0,"",now(),1),
            ("PNEU-A002","Michelin XDE2","295/80 R22.5",15,15,0,"novo","","estoque",None,None,"ALM-01",0,"",now(),1),
        ]
        c.executemany(
            "INSERT INTO pneus VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            pneus_seed)

    conn.commit()
    conn.close()
    print("  DB PostgreSQL pronto!")


def row_to_dict(cursor, row):
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))

def rows_to_list(cursor, rows):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        with open(path,'rb') as f: body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control','no-cache')
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get('Content-Length',0))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        base = os.path.dirname(os.path.abspath(__file__))

        if p in ('/', '/index.html'):
            self.send_file(os.path.join(base,'index.html'),'text/html; charset=utf-8')
            return

        conn = get_db()
        c = conn.cursor()
        try:
            if p == '/api/pneus':
                c.execute("""
                    SELECT p.*, e.nome as equip_nome FROM pneus p
                    LEFT JOIN equipamentos e ON e.id=p.equip_id
                    WHERE p.ativo=1 ORDER BY p.id""")
                self.send_json(rows_to_list(c, c.fetchall()))

            elif p == '/api/equipamentos':
                c.execute("SELECT * FROM equipamentos WHERE ativo=1 ORDER BY nome")
                equips = rows_to_list(c, c.fetchall())
                for d in equips:
                    try: d['posicoes'] = json.loads(d.get('posicoes_json') or '[]')
                    except: d['posicoes'] = []
                    c.execute("SELECT * FROM pneus WHERE equip_id=%s AND status='montado' AND ativo=1", (d['id'],))
                    d['pneus_montados'] = rows_to_list(c, c.fetchall())
                self.send_json(equips)

            elif p == '/api/almoxarifados':
                c.execute("""
                    SELECT a.*, COUNT(p.id) as qtd_pneus FROM almoxarifados a
                    LEFT JOIN pneus p ON p.almo_id=a.id AND p.status='estoque' AND p.ativo=1
                    WHERE a.ativo=1 GROUP BY a.id ORDER BY a.nome""")
                self.send_json(rows_to_list(c, c.fetchall()))

            elif p == '/api/historico':
                c.execute("""
                    SELECT h.*, e.nome as equip_nome FROM historico h
                    LEFT JOIN equipamentos e ON e.id=h.equip_id
                    ORDER BY h.id DESC LIMIT 200""")
                self.send_json(rows_to_list(c, c.fetchall()))

            elif p == '/api/nf_compras':
                c.execute("""
                    SELECT n.*, COUNT(i.id) as total_itens FROM nf_compras n
                    LEFT JOIN itens_nf i ON i.nf_id=n.id
                    WHERE n.ativo=1 GROUP BY n.id ORDER BY n.id DESC""")
                self.send_json(rows_to_list(c, c.fetchall()))

            elif p.startswith('/api/nf_compras/'):
                nf_id = int(p.split('/')[-1])
                c.execute("SELECT * FROM nf_compras WHERE id=%s", (nf_id,))
                nf = c.fetchone()
                c.execute("SELECT * FROM itens_nf WHERE nf_id=%s", (nf_id,))
                itens = rows_to_list(c, c.fetchall())
                result = row_to_dict(c, nf) if nf else {}
                result['itens'] = itens
                self.send_json(result)

            elif p == '/api/dashboard':
                c.execute("SELECT COUNT(*) FROM pneus WHERE ativo=1"); total = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM pneus WHERE status='montado' AND ativo=1"); montados = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM pneus WHERE status='estoque' AND ativo=1"); estoque = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM pneus WHERE sulco_atual<=4 AND ativo=1"); criticos = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM pneus WHERE sulco_atual>4 AND sulco_atual<=6 AND ativo=1"); atencao = c.fetchone()[0]
                self.send_json({'total':total,'montados':montados,'estoque':estoque,'criticos':criticos,'atencao':atencao})

            else:
                self.send_json({'error':'not found'},404)

        except Exception as e:
            self.send_json({'error':str(e)},500)
        finally:
            conn.close()

    def do_POST(self):
        p = urlparse(self.path).path
        data = self.read_body()
        conn = get_db()
        c = conn.cursor()
        try:
            if p == '/api/pneus':
                c.execute("""
                    INSERT INTO pneus(id,marca,medida,sulco_ini,sulco_atual,km,cond,obs,
                    status,almo_id,valor_unit,nf_ref,dt_entrada,ativo)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                    (data['id'],data.get('marca',''),data.get('medida',''),
                     data.get('sulco_ini',16),data.get('sulco_atual',16),
                     data.get('km',0),data.get('cond','novo'),data.get('obs',''),
                     data.get('status','estoque'),data.get('almo_id','ALM-01'),
                     data.get('valor_unit',0),data.get('nf_ref',''),now()))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                    (now(),data['id'],'-','-','Cadastro','Pneu cadastrado'))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/montar':
                pid=data.get('pneu_id'); eid=data.get('equip_id'); pos=data.get('pos_id')
                c.execute("SELECT id FROM pneus WHERE equip_id=%s AND pos_id=%s AND status='montado' AND ativo=1",(eid,pos))
                old = c.fetchone()
                if old:
                    c.execute("UPDATE pneus SET status='estoque',equip_id=NULL,pos_id=NULL WHERE id=%s",(old[0],))
                    c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                        (now(),old[0],eid,pos,'Desmontagem','Substituido por '+pid))
                c.execute("UPDATE pneus SET status='montado',equip_id=%s,pos_id=%s WHERE id=%s",(eid,pos,pid))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                    (now(),pid,eid,pos,'Montagem',data.get('obs','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/desmontar':
                pid=data.get('pneu_id')
                c.execute("SELECT equip_id,pos_id FROM pneus WHERE id=%s",(pid,))
                row = c.fetchone()
                c.execute("UPDATE pneus SET status='estoque',equip_id=NULL,pos_id=NULL WHERE id=%s",(pid,))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                    (now(),pid,row[0] if row else '-',row[1] if row else '-','Desmontagem',data.get('obs','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/sulco':
                pid=data.get('pneu_id'); novo=data.get('sulco_atual'); km=data.get('km')
                c.execute("UPDATE pneus SET sulco_atual=%s,km=%s WHERE id=%s",(novo,km,pid))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                    (now(),pid,'-','-','Medicao','Sulco: '+str(novo)+'mm | KM: '+str(km)))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/inativar':
                pid=data.get('id')
                c.execute("SELECT status,equip_id FROM pneus WHERE id=%s",(pid,))
                row = c.fetchone()
                if not row: self.send_json({'error':'Pneu nao encontrado'})
                elif row[0]=='montado': self.send_json({'error':'Pneu montado. Desmonte antes.'})
                else:
                    c.execute("UPDATE pneus SET ativo=0,status='inativo' WHERE id=%s",(pid,))
                    c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                        (now(),pid,'-','-','Inativacao',data.get('motivo','Inativado')))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/almo':
                pid=data.get('id'); novo_almo=data.get('almo_id')
                if not pid or not novo_almo: self.send_json({'error':'id e almo_id obrigatorios'})
                else:
                    c.execute("UPDATE pneus SET almo_id=%s WHERE id=%s",(novo_almo,pid))
                    c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(%s,%s,%s,%s,%s,%s)",
                        (now(),pid,'-','-','Transferencia','Almoxarifado alterado para '+str(novo_almo)))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos':
                c.execute("INSERT INTO equipamentos(id,nome,tipo,org,cat,posicoes_json) VALUES(%s,%s,%s,%s,%s,%s)",
                    (data['id'],data['nome'],data.get('tipo',''),data.get('org',''),data.get('cat',''),json.dumps(data.get('posicoes',[]))))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos/atualizar':
                c.execute("UPDATE equipamentos SET nome=%s,org=%s,cat=%s WHERE id=%s",
                    (data.get('nome'),data.get('org'),data.get('cat'),data.get('id')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos/inativar':
                eid=data.get('id')
                c.execute("SELECT COUNT(*) FROM pneus WHERE equip_id=%s AND status='montado'",(eid,))
                mt=c.fetchone()[0]
                if mt>0: self.send_json({'error':str(mt)+' pneu(s) montados. Desmonte antes.'})
                else:
                    c.execute("UPDATE equipamentos SET ativo=0 WHERE id=%s",(eid,))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/almoxarifados':
                c.execute("INSERT INTO almoxarifados(id,nome,local) VALUES(%s,%s,%s)",
                    (data['id'],data['nome'],data.get('local','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/almoxarifados/editar':
                c.execute("UPDATE almoxarifados SET nome=%s,local=%s WHERE id=%s",
                    (data.get('nome'),data.get('local',''),data.get('id')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/almoxarifados/remover':
                aid=data.get('id')
                c.execute("SELECT COUNT(*) FROM pneus WHERE almo_id=%s AND status!='inativo'",(aid,))
                qtd=c.fetchone()[0]
                if qtd>0: self.send_json({'error':str(qtd)+' pneus ativos. Transfira antes.'})
                else:
                    c.execute("UPDATE almoxarifados SET ativo=0 WHERE id=%s",(aid,))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/nf_compras':
                c.execute("""INSERT INTO nf_compras(numero_nf,fornecedor,dt_emissao,dt_entrada,valor_total,obs,almo_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (data['numero_nf'],data.get('fornecedor',''),data.get('dt_emissao',''),
                     data.get('dt_entrada',''),data.get('valor_total',0),data.get('obs',''),data.get('almo_id','ALM-01')))
                nf_id=c.fetchone()[0]
                for item in data.get('itens',[]):
                    vt=round((item.get('quantidade',1) or 1)*(item.get('valor_unit',0) or 0),2)
                    c.execute("""INSERT INTO itens_nf(nf_id,pneu_id,descricao,marca,medida,quantidade,valor_unit,valor_total)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (nf_id,item.get('pneu_id',''),item.get('descricao',''),item.get('marca',''),
                         item.get('medida',''),item.get('quantidade',1),item.get('valor_unit',0),vt))
                conn.commit(); self.send_json({'ok':True,'nf_id':nf_id})

            elif p == '/api/nf/inativar':
                nf_id=data.get('id')
                c.execute("UPDATE nf_compras SET ativo=0 WHERE id=%s",(nf_id,))
                conn.commit(); self.send_json({'ok':True})

            else:
                self.send_json({'error':'endpoint nao encontrado'},404)

        except Exception as e:
            conn.rollback(); self.send_json({'error':str(e)},500)
        finally:
            conn.close()


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*52)
    print("  Garra Terraplenagem - Gestao de Pneus v4.0")
    print("  PostgreSQL Edition")
    print("="*52)
    print("  Porta:", port)
    print("="*52 + "\n")
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
