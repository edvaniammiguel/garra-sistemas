# Bateria de fluxos — Garra Sistemas (v2, 21/11/2026)

Testes automáticos dos fluxos principais, em linguagem de negócio. Regra do projeto: **nenhuma entrega sem rodar a bateria**.

## Como rodar (quem roda é o chat que está entregando, no laboratório)
```
python3 testes/rodar_bateria.py            # tudo
python3 testes/rodar_bateria.py OT RH      # só alguns módulos
```
Precisa do servidor de teste no ar e do banco de teste. Padrões = laboratório do chat; para outro laboratório, definir:
`BAT_URL` (servidor), `BAT_DSN` (banco), `BAT_JWT` (segredo do JWT do laboratório), `BAT_ADMIN`, `BAT_OP1`, `BAT_OP2`, `BAT_MEC` (logins de teste: um administrador, dois operadores, um mecânico/gestor de manutenção).

**Nunca roda em produção:** a bateria grava e apaga dados e se recusa a rodar se o endereço ou o banco parecer de produção.

## O que cada arquivo cobre (um arquivo por assunto)
| Arquivo | Fluxo |
|---|---|
| `bat_estatica.py` | estrutura do código, sem servidor: Python compila; rota ou função declarada duas vezes; sintaxe do JavaScript das telas; botão que chama função inexistente |
| `bat_fumaca.py` | abre todas as rotas de consulta do sistema como administração e acusa qualquer erro interno; avisa quais módulos o laboratório não comporta |
| `bat_acesso.py` | sem login, token falso ou vencido são recusados; operador não abre o RH; varredura de todas as rotas sem login |
| `bat_os.py` | só quem lançou altera/exclui o registro; operador só vê os próprios; OS só é excluída por quem criou, vazia |
| `bat_ot.py` | pedido → OT; antes de emitir não registra nem encerra; emitir, registrar, sinalizar; motivos obrigatórios; encerrar; reabrir |
| `bat_materiais.py` | cadastro e busca; entrada que não fecha é recusada; custo médio com rateio; saída; transferência; estorno |
| `bat_abastecimento.py` | nota move o contador; cupom repetido; leitura fora da sequência; litros dos itens fecham com a nota |
| `bat_compras.py` | OC: rascunho → solicitar → aprovar → enviar → receber; saldo só sobe na conferência do almoxarifado; cotação com dois fornecedores |
| `bat_rh.py` | colaborador fictício no controle de férias; limites de férias e abono; vencimento de NR pelo catálogo; só o RH acessa |
| `bat_base.py` | base comum: conexão, tokens, verificações, trava contra produção |
| `rodar_bateria.py` | executor e relatório |

## Leitura do resultado
`✔ PASSOU` · `✖ FALHOU/ERRO` (não entregar) · `– NÃO RODOU` (o laboratório não comporta aquele teste; dizer qual e por quê na entrega).

## Como acrescentar um teste
No arquivo do assunto: `@teste("Módulo", "frase que a gestão entende")` sobre uma função que usa `chamar(papel, método, rota, corpo)` e `status/igual/perto`. Dados sempre fictícios, com a marca **BAT** no nome, e o próprio teste limpa o que criou. Assunto novo = arquivo novo `bat_<assunto>.py`, importado em `rodar_bateria.py`.

## Ainda não coberto
- **Operacional (criar OS, Controle Mensal, Excel), Checklist, Jardinagem e Logística:** as tabelas desses módulos não são criadas pelo código — existem só no banco de produção. Um laboratório montado a partir do repositório não as tem, e a bateria avisa ("NÃO RODOU"). Para cobrir, é preciso a ESTRUTURA do banco de produção (sem dados), exportada uma vez.
- Encerramento de cotação gerando OC; devolução; alçadas de aprovação.
- Telas clique a clique (navegador): hoje a bateria testa as regras no servidor e a estrutura das telas, não o clique.

`python3 testes/rodar_bateria.py Estrutura` roda só a parte que não precisa de servidor nem de banco (qualquer chat consegue).
