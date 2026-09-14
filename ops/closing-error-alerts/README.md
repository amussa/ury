# Alertas de erro no fecho de caixa

Destinatário configurado: **akilmussa@gmail.com**. Site: `gelati.app.co.mz`.

O timer systemd do servidor verifica a cada minuto os `POS Closing Entry`
submetidos (`docstatus=1`) em estado `Failed`. Cria uma mensagem na `Email
Queue` do Frappe, usando a conta de saída configurada. O email contém fecho,
POS/loja, operador, data da falha, empresa, valor em MT, erro e ligação ao fecho.

A verificação demora até cerca de um minuto; o envio aguarda a fila normal
do ERPNext, pelo que a recepção pode demorar alguns minutos. Não depende de
o operador manter o POS aberto. Não cria uma regra no formulário Notification:
é um monitor externo ao ciclo de gravação/rollback do fecho. Uma notificação
simples na mudança de estado poderia executar antes de a mensagem de erro
estar gravada, ou ser suprimida pelos eventos já executados no mesmo documento.

## Instalação e operação

Copiar esta pasta para `/opt/gelatiamo-erpnext/ops/closing-error-alerts/` no
servidor e executar como root:

```sh
bash /opt/gelatiamo-erpnext/ops/closing-error-alerts/install.sh
systemctl list-timers gelatiamo-closing-error-alerts.timer --no-pager
journalctl -u gelatiamo-closing-error-alerts.service -n 30 --no-pager
```

O serviço executa o Python do backend pela configuração Compose existente.
Não altera a imagem, código Frappe/ERPNext, schema, fechos, vendas, pagamentos,
stock ou lançamentos. O timer é persistente e arranca após reinício do servidor.
Não há credenciais SMTP no script: o envio usa a Email Account do site.

Consulta sem criar mensagens:

```sh
bash /opt/gelatiamo-erpnext/ops/closing-error-alerts/run.sh --dry-run
```

Teste explícito com uma falha fictícia apenas em memória, sem criar ou alterar
um fecho real; **este comando envia um email ao destinatário configurado**:

```sh
bash /opt/gelatiamo-erpnext/ops/closing-error-alerts/run.sh --test-token activation-20260915-0010
```

Repetir o mesmo token não gera outra mensagem. Um token diferente cria outro
teste. O assunto e corpo identificam TESTE e não contêm ligação a um documento
fictício. Os testes não desactivam o monitor dos fechos reais.

## Controlo de duplicados e falhas de envio

O identificador inclui número do fecho, `modified`, mensagem de erro e
destinatário. Verificações sucessivas da mesma versão da falha não repetem o
email. Nova tentativa falhada, ou outra alteração do documento ainda em falha,
produz uma nova versão e pode gerar outro alerta.

O histórico fica em `sites/gelati.app.co.mz/private/closing-error-alerts.json`,
no volume persistente dos sites, com permissões 0600. Um lock do sistema impede
execuções concorrentes, e a gravação usa substituição atómica. Se houver uma
interrupção entre o commit da Email Queue e a gravação do histórico, a pesquisa
pelo Message-ID recupera a mensagem existente sem criar outra. Não apagar o
histórico para reiniciar o serviço; incluí-lo nos backups de ficheiros privados.

`queued` e `already_queued` significam que existe uma mensagem, não que chegou
ao destinatário. Consultar Email Queue e os destinatários para verificar `Sent`
ou `Error`. O Frappe executa as suas tentativas normais em caso de falha SMTP.
Depois de corrigir uma falha de envio, reutilizar a mensagem existente pelo
fluxo normal da Email Queue. Não apagar o histórico para forçar novo envio.

O monitor cobre falhas **persistidas no estado Failed**. Não cobre validações
rejeitadas antes de existir um fecho, um processo morto que fique em Queued,
nem uma falha resolvida entre duas verificações. Se o servidor estiver
indisponível, a verificação regressa quando o serviço recuperar. Estes casos
exigem monitorização distinta e não foram apresentados como cobertos.

## Desactivar / repor

```sh
systemctl disable --now gelatiamo-closing-error-alerts.timer
systemctl stop gelatiamo-closing-error-alerts.service
```

Conservar o histórico. Emails já enfileirados continuam a seguir pela fila
normal. Para repor: `systemctl enable --now gelatiamo-closing-error-alerts.timer`.
Não é necessário restaurar a base de dados para desactivar este alerta.

## Validação

```sh
python3 -m unittest discover -s ops/closing-error-alerts -p 'test_*.py' -v
bash -n ops/closing-error-alerts/run.sh ops/closing-error-alerts/install.sh
```

Os testes verificam deduplicação entre execuções/reinícios, recuperação após
interrupção, nova falha do mesmo fecho, exclusão de estados normais, falha de
criação do email, dry-run, isolamento da simulação e escape de HTML do erro.

### Activação em produção

Activado em 15/09/2026. Os nove testes passaram e foram observadas execuções
automáticas bem-sucedidas do timer. O teste com token
`activation-20260915-0010` criou a fila `pijt81eif9`; a repetição recuperou a
mesma fila. Às 00:12:39, hora de Maputo, o email e o destinatário estavam em
`Sent`, sem erros, com exactamente uma mensagem e sem referência a um fecho
real. A entrega à caixa de entrada deste teste não foi confirmada pelo utilizador.

Antes da activação foi validado o backup completo
`20260915_000851-gelati_app_co_mz`, com cópias no servidor e fora dele.
O estado de deduplicação foi criado posteriormente e integra os próximos
backups dos ficheiros privados.
