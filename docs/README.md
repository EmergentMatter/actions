# docs/

Reference material and decision records that support this repo but do not
belong in the root [`README.md`](../README.md), [`CLAUDE.md`](../CLAUDE.md), or
[`CONTRACT.md`](../CONTRACT.md).

There is no index of the files here, deliberately. The directory listing is the
index, and it cannot fall behind the way a hand-kept list does. What each
location means:

| Location | What lives there | How it ages |
|---|---|---|
| `*.md` at this level | Procedures a person follows: onboarding a repo, running the maintenance scripts | With the procedure. Correct it when the steps change |
| [`adr/`](adr/) | One decision per file, `NNNN-kebab-title.md`, with `Status` / `Context` / `Decision` / `Consequences` per [STYLE.md](https://github.com/EmergentMatter/actions/blob/main/templates/STYLE.md) | Not at all. A record states what was true when it was accepted, and is superseded by a later one rather than rewritten |

## Adding to this directory

- Recording a **decision** and the reasoning behind it: write an ADR.
- Describing **how the system behaves**, as a contract a consumer can rely on:
  that is [`CONTRACT.md`](../CONTRACT.md), not here.
- Telling someone **how to carry out a procedure**: a reference page at this
  level.
- Anything that is a plan, a known issue, or current work: it belongs in a
  GitHub issue, not in this repo.

Name a file so the listing reads as its own index. An ADR states its decision
in its filename, not just its number.

A document that reads as a proposal is a sign the decision has not been
recorded yet. Convert it once the decision is made.
