# Conversation Tree

# Define your conversational flows here. This file is read on chatbot startup;
# after editing it, restart the chatbot:
#
#     docker compose restart chatbot
#
# Full format reference and editing guide: docs/CONVERSATION_TREE.md

# A flow starts with a "## <id>" heading and optional metadata lines
# (Menu:, Keywords:, Description:). The lines that follow are the steps of
# the flow. Lines starting with "#" are comments and are ignored.

------------------------------------------------------------------

## returns

Menu: Devoluciones & reembolsos
Keywords: devolucion, reembolso
Description: Guia al usuario atraves del proceso de devolucion.

- question: Con gusto te ayudare con el proceso de devolucion. Cual es el numero de tu orden? -> field=order
- branch: * -> @found
- answer @found: Muchas Gracias! encontramos tu orden No: {order}.
- question: Cual es la razon de la devolcion? -> field=reason
- branch: damaged -> @damaged
- branch: * -> @return_steps
- answer @damaged: Lamentamos que tu producto halla llegado en mal estado. Por favor envia fotos del paquete al siguiente email ctorres@fertrac.com y te enviaremos el reemplazo.
- answer @return_steps: Para la devolucion de tu producto, envialo  con su empaque original dentro de los siguientes 30 dias de tu compra. El reembolso se efectura en los siguientes 10 dias.
- message: Si necesitas más ayuda, un agente está a un mensaje de distancia:
- redirect: 15551234567

## orderstatus

Menu: Estado de orden
Keywords: order, status, tracking, shipping, where is my order
Description: Revisa el estado de la orden.

- question: Puedo ayudarte revisando el estado de la orden. Cual es el numero de tu orden? -> field=order
- branch: * -> @status_result
- answer @status_result: Orden {order} esta siendo procesada y estara siendo enviada dentro de las siguientes 24 horas. Recibiras mas detalles via email con el track number.
- message:  ¿Tienes preguntas sobre este pedido? Un agente puede ayudar:
- redirect: 15551234567

## payment

Menu: Payment methods
Keywords: pay, payment, credit card, paypal
Description: Explain the payment methods we accept.

- question: Which payment method would you like to know about? -> field=method
- option: card -> @card
- option: paypal -> @paypal
- option: * -> @other
- answer @card: We accept all major credit and debit cards (Visa, Mastercard, Amex).
- answer @paypal: Yes, we accept PayPal.
- answer @other: We accept major cards, PayPal and bank transfer. Invoices are available in the customer portal.

## contact

Menu: Talk to a human
Keywords: agent, human, person, sales, asesor
Description: Send the customer straight to a human agent.

- answer: Sure, I'll put you in touch with a human agent.
- message: Tap below to continue the conversation with an agent:
- redirect: 15551234567
