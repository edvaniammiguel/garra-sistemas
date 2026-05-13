#!/usr/bin/env python3
"""
Gestao de Pneus - Garra Terraplenagem v4.0
Render-ready: porta via variavel de ambiente PORT
"""
import sqlite3, json, os, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pneus.db')

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def now():
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS almoxarifados (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, local TEXT, ativo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS equipamentos (
        id TEXT PRIMARY KEY, nome TEXT, tipo TEXT, org TEXT, cat TEXT,
        posicoes_json TEXT, ativo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS pneus (
        id TEXT PRIMARY KEY, marca TEXT, medida TEXT,
        sulco_ini INTEGER DEFAULT 16, sulco_atual INTEGER DEFAULT 16,
        km INTEGER DEFAULT 0, cond TEXT DEFAULT "novo",
        obs TEXT, status TEXT DEFAULT "cadastrado",
        equip_id TEXT, pos_id TEXT, almo_id TEXT,
        valor_unit REAL DEFAULT 0, nf_ref TEXT, dt_entrada TEXT, ativo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dt TEXT, pneu_id TEXT, equip_id TEXT,
        pos_label TEXT, tipo TEXT, obs TEXT
    );
    CREATE TABLE IF NOT EXISTS nf_compras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_nf TEXT NOT NULL, fornecedor TEXT,
        dt_emissao TEXT, dt_entrada TEXT,
        valor_total REAL DEFAULT 0, obs TEXT,
        almo_id TEXT, ativo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS itens_nf (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nf_id INTEGER NOT NULL, pneu_id TEXT,
        descricao TEXT, marca TEXT, medida TEXT,
        quantidade INTEGER DEFAULT 1,
        valor_unit REAL DEFAULT 0, valor_total REAL DEFAULT 0
    );
    ''')
    migrations = [
        ("pneus","valor_unit","REAL DEFAULT 0"),
        ("pneus","nf_ref","TEXT DEFAULT ''"),
        ("pneus","ativo","INTEGER DEFAULT 1"),
        ("pneus","almo_id","TEXT"),
        ("almoxarifados","ativo","INTEGER DEFAULT 1"),
        ("equipamentos","ativo","INTEGER DEFAULT 1"),
        ("nf_compras","ativo","INTEGER DEFAULT 1"),
    ]
    for tbl, col, definition in migrations:
        try:
            c.execute("ALTER TABLE {} ADD COLUMN {} {}".format(tbl, col, definition))
        except: pass
    if not c.execute("SELECT COUNT(*) FROM almoxarifados").fetchone()[0]:
        c.executemany("INSERT INTO almoxarifados VALUES(?,?,?,1)", [
            ('ALM-01','Almoxarifado Central','Sede - Galpao A'),
            ('ALM-02','Almoxarifado Campo','Obra - Externo'),
        ])
    pos_bruck = json.dumps([
        {"id":"DIAN-DIR","label":"Dianteiro Dir","eixo":"Eixo Dianteiro"},
        {"id":"DIAN-ESQ","label":"Dianteiro Esq","eixo":"Eixo Dianteiro"},
        {"id":"TR-DIR-EXT","label":"Traseiro Dir Ext","eixo":"Eixo Traseiro"},
        {"id":"TR-DIR-INT","label":"Traseiro Dir Int","eixo":"Eixo Traseiro"},
        {"id":"TR-ESQ-INT","label":"Traseiro Esq Int","eixo":"Eixo Traseiro"},
        {"id":"TR-ESQ-EXT","label":"Traseiro Esq Ext","eixo":"Eixo Traseiro"},
    ])
    if not c.execute("SELECT COUNT(*) FROM equipamentos").fetchone()[0]:
        c.executemany("INSERT INTO equipamentos VALUES(?,?,?,?,?,?,1)", [
            ("CPO-0022","Caminhao Bruck CPO-022","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CPO-0026","Caminhao Bruck CPO-026","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CPO-0036","Caminhao Bruck CPO-036","caminhao_bruck","01 - GERAL & INFRA G","Caminhao Bruck",pos_bruck),
            ("CP-0019","Caminhao Pipa CP-019","caminhao_pipa","01 - GERAL & INFRA G","Caminhao Pipa",pos_bruck),
        ])
    conn.commit()
    conn.close()
    print("  DB pronto:", DB)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
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

        conn = get_db(); c = conn.cursor()
        try:
            if p == '/api/pneus':
                rows = c.execute(
                    "SELECT p.*, e.nome equip_nome FROM pneus p "
                    "LEFT JOIN equipamentos e ON e.id=p.equip_id "
                    "WHERE p.ativo=1 ORDER BY p.id").fetchall()
                self.send_json([dict(r) for r in rows])

            elif p == '/api/equipamentos':
                rows = c.execute("SELECT * FROM equipamentos WHERE ativo=1 ORDER BY nome").fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try: d['posicoes'] = json.loads(d.get('posicoes_json') or '[]')
                    except: d['posicoes'] = []
                    pneus = c.execute(
                        "SELECT * FROM pneus WHERE equip_id=? AND status='montado' AND ativo=1",
                        (d['id'],)).fetchall()
                    d['pneus_montados'] = [dict(p) for p in pneus]
                    result.append(d)
                self.send_json(result)

            elif p == '/api/almoxarifados':
                rows = c.execute(
                    "SELECT a.*, COUNT(p.id) qtd_pneus FROM almoxarifados a "
                    "LEFT JOIN pneus p ON p.almo_id=a.id AND p.status='estoque' AND p.ativo=1 "
                    "WHERE a.ativo=1 GROUP BY a.id ORDER BY a.nome").fetchall()
                self.send_json([dict(r) for r in rows])

            elif p == '/api/historico':
                rows = c.execute(
                    "SELECT h.*, e.nome equip_nome FROM historico h "
                    "LEFT JOIN equipamentos e ON e.id=h.equip_id "
                    "ORDER BY h.id DESC LIMIT 200").fetchall()
                self.send_json([dict(r) for r in rows])

            elif p == '/api/nf_compras':
                rows = c.execute(
                    "SELECT n.*, COUNT(i.id) total_itens FROM nf_compras n "
                    "LEFT JOIN itens_nf i ON i.nf_id=n.id "
                    "WHERE n.ativo=1 GROUP BY n.id ORDER BY n.id DESC").fetchall()
                self.send_json([dict(r) for r in rows])

            elif p.startswith('/api/nf_compras/'):
                nf_id = int(p.split('/')[-1])
                nf = c.execute("SELECT * FROM nf_compras WHERE id=?",(nf_id,)).fetchone()
                itens = c.execute("SELECT * FROM itens_nf WHERE nf_id=?",(nf_id,)).fetchall()
                result = dict(nf) if nf else {}
                result['itens'] = [dict(i) for i in itens]
                self.send_json(result)

            elif p == '/api/dashboard':
                self.send_json({
                    'total':    c.execute("SELECT COUNT(*) FROM pneus WHERE ativo=1").fetchone()[0],
                    'montados': c.execute("SELECT COUNT(*) FROM pneus WHERE status='montado' AND ativo=1").fetchone()[0],
                    'estoque':  c.execute("SELECT COUNT(*) FROM pneus WHERE status='estoque' AND ativo=1").fetchone()[0],
                    'criticos': c.execute("SELECT COUNT(*) FROM pneus WHERE sulco_atual<=4 AND ativo=1").fetchone()[0],
                    'atencao':  c.execute("SELECT COUNT(*) FROM pneus WHERE sulco_atual>4 AND sulco_atual<=6 AND ativo=1").fetchone()[0],
                })
            else:
                self.send_json({'error':'not found'},404)
        except Exception as e:
            self.send_json({'error':str(e)},500)
        finally:
            conn.close()

    def do_POST(self):
        p = urlparse(self.path).path
        data = self.read_body()
        conn = get_db(); c = conn.cursor()
        try:
            if p == '/api/pneus':
                c.execute(
                    "INSERT INTO pneus(id,marca,medida,sulco_ini,sulco_atual,km,cond,obs,status,almo_id,valor_unit,nf_ref,dt_entrada,ativo) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (data['id'],data.get('marca',''),data.get('medida',''),
                     data.get('sulco_ini',16),data.get('sulco_atual',16),
                     data.get('km',0),data.get('cond','novo'),
                     data.get('obs',''),data.get('status','estoque'),
                     data.get('almo_id','ALM-01'),data.get('valor_unit',0),
                     data.get('nf_ref',''),now()))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                    (now(),data['id'],'-','-','Cadastro','Pneu cadastrado'))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/montar':
                pid=data.get('pneu_id'); eid=data.get('equip_id'); pos=data.get('pos_id')
                old=c.execute("SELECT id FROM pneus WHERE equip_id=? AND pos_id=? AND status='montado' AND ativo=1",(eid,pos)).fetchone()
                if old:
                    c.execute("UPDATE pneus SET status='estoque',equip_id=NULL,pos_id=NULL WHERE id=?",(old['id'],))
                    c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                        (now(),old['id'],eid,pos,'Desmontagem','Substituido por '+pid))
                c.execute("UPDATE pneus SET status='montado',equip_id=?,pos_id=? WHERE id=?",(eid,pos,pid))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                    (now(),pid,eid,pos,'Montagem',data.get('obs','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/desmontar':
                pid=data.get('pneu_id')
                row=c.execute("SELECT equip_id,pos_id FROM pneus WHERE id=?",(pid,)).fetchone()
                c.execute("UPDATE pneus SET status='estoque',equip_id=NULL,pos_id=NULL WHERE id=?",(pid,))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                    (now(),pid,row['equip_id'] if row else '-',row['pos_id'] if row else '-','Desmontagem',data.get('obs','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/sulco':
                pid=data.get('pneu_id'); novo=data.get('sulco_atual'); km=data.get('km')
                c.execute("UPDATE pneus SET sulco_atual=?,km=? WHERE id=?",(novo,km,pid))
                c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                    (now(),pid,'-','-','Medicao','Sulco: '+str(novo)+'mm | KM: '+str(km)))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/pneus/inativar':
                pid=data.get('id')
                row=c.execute("SELECT status,equip_id FROM pneus WHERE id=?",(pid,)).fetchone()
                if not row: self.send_json({'error':'Pneu nao encontrado'})
                elif row['status']=='montado': self.send_json({'error':'Pneu montado. Desmonte antes.'})
                else:
                    c.execute("UPDATE pneus SET ativo=0,status='inativo' WHERE id=?",(pid,))
                    c.execute("INSERT INTO historico(dt,pneu_id,equip_id,pos_label,tipo,obs) VALUES(?,?,?,?,?,?)",
                        (now(),pid,'-','-','Inativacao',data.get('motivo','Inativado')))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos':
                c.execute("INSERT INTO equipamentos(id,nome,tipo,org,cat,posicoes_json) VALUES(?,?,?,?,?,?)",
                    (data['id'],data['nome'],data.get('tipo',''),data.get('org',''),data.get('cat',''),json.dumps(data.get('posicoes',[]))))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos/atualizar':
                c.execute("UPDATE equipamentos SET nome=?,org=?,cat=? WHERE id=?",
                    (data.get('nome'),data.get('org'),data.get('cat'),data.get('id')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/equipamentos/inativar':
                eid=data.get('id')
                mt=c.execute("SELECT COUNT(*) FROM pneus WHERE equip_id=? AND status='montado'",(eid,)).fetchone()[0]
                if mt>0: self.send_json({'error':str(mt)+' pneu(s) montados. Desmonte antes.'})
                else:
                    c.execute("UPDATE equipamentos SET ativo=0 WHERE id=?",(eid,))
                    conn.commit(); self.send_json({'ok':True})

            elif p == '/api/almoxarifados':
                c.execute("INSERT INTO almoxarifados(id,nome,local) VALUES(?,?,?)",
                    (data['id'],data['nome'],data.get('local','')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/almoxarifados/editar':
                c.execute("UPDATE almoxarifados SET nome=?,local=? WHERE id=?",
                    (data.get('nome'),data.get('local',''),data.get('id')))
                conn.commit(); self.send_json({'ok':True})

            elif p == '/api/nf_compras':
                c.execute(
                    "INSERT INTO nf_compras(numero_nf,fornecedor,dt_emissao,dt_entrada,valor_total,obs,almo_id) VALUES(?,?,?,?,?,?,?)",
                    (data['numero_nf'],data.get('fornecedor',''),data.get('dt_emissao',''),
                     data.get('dt_entrada',''),data.get('valor_total',0),data.get('obs',''),data.get('almo_id','ALM-01')))
                nf_id=c.lastrowid
                for item in data.get('itens',[]):
                    vt=round((item.get('quantidade',1) or 1)*(item.get('valor_unit',0) or 0),2)
                    c.execute("INSERT INTO itens_nf(nf_id,pneu_id,descricao,marca,medida,quantidade,valor_unit,valor_total) VALUES(?,?,?,?,?,?,?,?)",
                        (nf_id,item.get('pneu_id',''),item.get('descricao',''),item.get('marca',''),item.get('medida',''),item.get('quantidade',1),item.get('valor_unit',0),vt))
                conn.commit(); self.send_json({'ok':True,'nf_id':nf_id})

            elif p == '/api/nf/inativar':
                nf_id=data.get('id')
                c.execute("UPDATE nf_compras SET ativo=0 WHERE id=?",(nf_id,))
                conn.commit(); self.send_json({'ok':True})

            else:
                self.send_json({'error':'endpoint nao encontrado'},404)

        except Exception as e:
            conn.rollback(); self.send_json({'error':str(e)},500)
        finally:
            conn.close()


if __name__ == '__main__':
    init_db()
    # ⚠️ Render injeta PORT via variável de ambiente — NÃO usar porta fixa
    port = int(os.environ.get('PORT', 8080))
    print("\n" + "="*52)
    print("  Garra Terraplenagem - Gestao de Pneus v4.0")
    print("="*52)
    print("  Porta:", port)
    print("="*52 + "\n")
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
