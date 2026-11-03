# Order tracking with identity check and a late delivery

> Recorded automatically by `scripts/record_examples.py` · model `anthropic/claude-sonnet-5-5` · simulated store time `2026-03-23T10:00` (America/Campo_Grande)
>
> **What this shows:** Privacy flow: no order data before verification. Order 8 is shipped and 23 days past its estimate (store time pinned to 2026-03-23); the agent must apologise, give the tracking code and offer a human follow-up.

**Cliente:** Oi, cadê meu pedido 8? Já passou do prazo!

**Dó:** Oi! Eu sou o Dó, assistente virtual do Empório da Música. Vou olhar isso pra você.

Pra consultar o pedido, preciso confirmar sua identidade. Me passa os *4 últimos dígitos do telefone* cadastrado ou o *e-mail* do cadastro, por favor?

**Cliente:** Os 4 últimos dígitos do meu telefone são 7821.

**Dó:** Obrigado, Ana! Achei seu pedido e você tem razão: ele está atrasado. Desculpa pela demora.

O pedido 8 é o *Kala KA-C Concert Mogno*, feito em 15/02/2026. O status é *Enviado: a caminho do cliente*. A previsão de entrega era 28/02/2026, então já passou há 23 dias.

O código de rastreio é *BRJL5544332BR*. Você pode consultar no site dos Correios ou da Jadlog.

Posso abrir um chamado pra nossa equipe investigar a entrega com a transportadora. Se quiser, me confirme um telefone ou e-mail pra retorno.

<details><summary>Ferramentas chamadas (1)</summary>

- `lookup_order({"order_id": 8, "verification": "7821"})` → `{"ok": true, "order_id": 8, "customer_first_name": "Ana", "order_date": "15/02/2026", "status_code": "shipped", "status": "Enviado: a caminho do cliente", "items": [{"product": "Kala KA-C Concert Mogno", "quantity": 1}], "total": "R$ 349,90", "payment": "Cartão de débito", "tracking_code": "BRJL5544332BR", "tracking_hi …`

</details>
