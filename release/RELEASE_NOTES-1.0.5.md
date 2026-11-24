# EOS 1.0.5 - Windows x64

Patch release. Unsigned.

## Fixed
- One approved file write marked a whole multi-step request as closed, with no next action, so an interrupted session had nothing to resume. A numbered request is now a durable objective (items, files, next action) that stays open until `objective done` or `objective abandon --reason`. It locks no other session out.
- Activation shows the open objective to every later session and names the valid reasons to stop. A different request, a stop instruction, a risk gate and a human approval still come first.
- A request longer than 4000 characters received no EOS context at all.

## Limit
- EOS cannot force a model to continue. It keeps the work open and shows it; it does not prompt the model by itself.
