# Conversation Tree

The conversation tree lets you define **scripted flows** in plain Markdown that
run before the AI answer: forms (free-text questions and option menus),
closed/rule-based answers, and redirects to a human agent on another WhatsApp
number. The flows live in `tree.md` (next to the compose file), so you can edit
them without touching code or rebuilding the image.

> After editing `tree.md`, restart the chatbot so it reloads the flows:
>
>     docker compose restart chatbot

## How a conversation is decided

For every incoming message the bot checks, in order:

1. The customer's phone is **already inside a flow** -> the tree keeps driving
   the conversation (it does not matter what they type).
2. Nothing active, but the message matches a flow **keyword** (or is a
   `menu`/`start`/`help` command) -> the flow starts.
3. Otherwise the normal AI/RAG answer is used.

Redirects configured in `config/config.yaml` (`redirects:` rules, e.g. for
"human"/"sales") still take priority over the tree. Prefer moving those intents
into a tree flow and removing the duplicate keyword from `redirects.rules` if
you want the flow to handle them.

## Where flows are defined

`tree.md`, mounted into the container as a live file. Configuration for the
tree lives in `config/config.yaml` under `tree:`:

| Key                | Default | Meaning                                          |
| ------------------ | ------- | ------------------------------------------------ |
| `enabled`          | `true`  | Master switch for the whole tree                 |
| `path`             | `tree.md` | File to load the flows from                    |
| `menu_keywords`    | `menu, start, help` | Commands that show the flow list    |
| `redirect_message` | `Continue with a human agent here:` | Button text when a redirect step has no `- message:` |
| `max_steps`        | `30`    | Safety limit on executed steps per turn          |

## File format

### Flow header

A flow starts with a heading and optional metadata. Everything between two
`##` headings belongs to the same flow.

```
## <flow-id>

Menu: <label shown in the menu command>
Keywords: <comma, separated, keywords>
Description: <one-line description (optional)>
```

- `<flow-id>` must be unique. It is only used internally.
- `Menu:` labels the flow in the `menu` command. Omit it to hide the flow from
  the menu (it still triggers by keyword).
- `Keywords:` control automatic triggering: a flow starts when any keyword
  appears anywhere in the customer's message (lowercase match). A keyword that
  only appears inside another word can be a problem (e.g. `pay` inside
  `payment`) - keep keywords short but specific.
- Lines starting with `#` are comments and are ignored.

### Steps

Every step is a bullet line starting with `-`. Steps run top to bottom.

| Syntax | Meaning |
| ------ | ------- |
| `- question: <text> -> field=<name>` | Ask a free-text question and save the reply into the field `<name>`. |
| `- option: <text> -> @<label>` | Add a choice to the preceding question. The customer can reply with the number or the text. |
| `- branch: <keyword> -> @<label>` | Route the preceding question's reply: if the reply contains `<keyword>`, jump to `@<label>`. `*` is the default route. |
| `- answer: <text>` | Send a fixed (closed) answer. You can interpolate collected values with `{field}`. |
| `- message: <text>` | Text that is attached to the next redirect button (must be followed by `- redirect:`). |
| `- redirect: <number>` | Send a WhatsApp button that opens a chat with `<number>` (international, digits only). This ends the flow. |
| `- @<label>` | A jump target marker used by `option:`/`branch:`. |

Notes:

- `- answer @<label>: <text>` is shorthand for a jump target marker followed by
  an answer on the same line.
- An `option:`/`branch:` must follow a `- question:` line directly (no other
  steps in between).
- If the customer picks an option that does not exist and there is no `*`
  fallback, the question is asked again.
- After the last step (or a `- redirect:`) the flow ends and the customer's
  phone is freed again.

## Example

```
## returns

Menu: Returns & refunds
Keywords: return, refund, devolucion
Description: Guide a customer through a return request.

- question: I can help you with a return. What is your order number? -> field=order
- branch: * -> @found
- answer @found: Thanks! We found order {order}.
- question: What is the reason for the return? -> field=reason
- branch: damaged -> @damaged
- branch: * -> @return_steps
- answer @damaged: We're sorry it arrived damaged. Please email photos of the item to returns@acme.example.com and we will send a replacement.
- answer @return_steps: To return your item, ship it in its original packaging within 30 days of purchase. Refunds are issued within 10 business days.
- message: If you need more help, an agent is one message away:
- redirect: 15551234567
```

What happens when a customer writes *"I want a refund"*:

1. The `returns` flow starts (keyword "return").
2. The bot asks for the order number and waits.
3. The customer replies `ABC-123`; the flow routes to `@found` (default route).
4. The bot sends *"Thanks! We found order ABC-123."* and asks for the reason.
5. If the reply contains "damaged" the bot jumps to `@damaged`; otherwise to
   `@return_steps`.
6. Either way it ends with the return instructions plus a button that opens a
   chat with the human agent (15551234567).

## Troubleshooting

- **A flow is not triggering**: check the keyword spelling and that the flow is
  before `##` sections in the same file. Remember `redirects:` rules in
  `config/config.yaml` run first.
- **Unknown label warnings in the logs**: an `option:`/`branch:` points to an
  `@<label>` that does not exist. Fix the target or add the marker line.
- **The menu does not list a flow**: the flow has no `Menu:` line.
- **The tree is completely ignored**: check `tree.enabled` in `config/config.yaml`.
