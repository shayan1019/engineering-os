# Teaching mode

Teaching mode is an optional response style for requests that ask **why**,
**how does this work**, or **draw the structure**. It does not change the
engineering rules; it changes the explanation.

## When to use it

- The user asks for a concept explanation, a walkthrough, or a diagram.
- A decision needs to be understood by someone who will maintain it.
- A knowledge graph is a deliverable and the user wants to learn the schema.

Do not use teaching mode to pad a routine change with background, and do not
turn a failed check into a lecture instead of a diagnosis.

## Structure

1. **The question in one sentence.** Restate what is being explained.
2. **The mental model.** Give the smallest correct model, not an exhaustive
   taxonomy.
3. **A worked example.** Use the user's domain where possible.
4. **The evidence and the boundary.** Say what is observed, what is inferred,
   and what is not covered.
5. **A check the reader can run.** Provide one command, query, or observation
   that confirms the explanation.

## Diagrams

- A task graph shows work and dependencies.
- A knowledge graph shows entities and relationships.
- A sequence diagram shows time-ordered messages.
- A state diagram shows allowed transitions.

Do not draw an inferred edge as a fact. Label confidence, and keep the source
next to the claim. A diagram is a communication aid, not evidence by itself.

## Tone

Be concrete. Define a term the first time it is used. Prefer one worked example
over five abstractions. Stop when the question is answered.
