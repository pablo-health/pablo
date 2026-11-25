# Mutation testing

Coverage says a line ran. Mutation testing asks whether any test would notice
if that line were wrong: it makes one small change to the code (a mutant),
runs the tests, and counts the change as caught if a test fails. The ones that
survive are the behaviour no test pins.

## Running it

From `backend/`:

```bash
# Every mutant of the calendar preset's modules, eight workers.
python -m scripts.mutation.run --preset calendar --out /tmp/mutation/calendar

# Scores per module; --survivors lists what survived, by function.
python -m scripts.mutation.score /tmp/mutation/calendar --survivors

# Compare with an earlier run: list them oldest first.
python -m scripts.mutation.score /tmp/mutation/before /tmp/mutation/calendar
```

`--modules` and `--tests` name your own sets instead of a preset.
`--baseline` only checks that the unmutated modules pass their tests (every
run checks this first, and stops if they don't). A run is resumable: it skips
mutants already in `results.jsonl`.

The calendar preset is about 1,500 mutants and takes about half an hour on
eight workers. Each worker runs in its own copy of `backend/` under `--out`,
so the checkout is never modified.

## Tests that need a database

Some code can only be judged against Postgres: what a function does to a
schema is not visible anywhere else. A preset's `integration_tests` (or
`--integration-tests`) name files under `tests_integration/` for that.

```bash
# Needs Docker: each integration run starts its own Postgres container.
python -m scripts.mutation.run --preset rls-apply --out /tmp/mutation/rls-apply
```

They run as a second pytest invocation, and only for a mutant the unit tests
let through:

- The two suites cannot be collected together. Each has its own `conftest.py`,
  and the integration one refuses the placeholder database the unit one plants.
- The integration run stops at the first failing test. One is enough to call
  a mutant caught, and the run is the expensive half. So `results.jsonl` names
  one killing test for these, not every one.
- The default is four workers, not eight, because each has a container to
  itself while its run lasts.

The `rls-apply` preset is 445 mutants and takes about 50 minutes on four
workers. Most of that is the mutants only the integration tests can catch.

A run that is killed from outside leaves its container behind, since nothing
reaps them. After stopping a run, check `docker ps` for a stray
`postgres:16-alpine`.

## What it changes

One change per mutant, made to the syntax tree: comparisons flipped (`<` to
`<=`, `==` to `!=`, `in` to `not in`), `and` and `or` swapped, a `not`
removed, arithmetic swapped, constants nudged (`True` to `False`, `n` to
`n + 1` and `n - 1`, a string changed), a return replaced with `return None`,
a condition forced true or false, a statement deleted, `return None` inserted
at the top of a function, a comprehension filter dropped, `min`/`max` and
`any`/`all` swapped, `startswith`/`endswith` and `lower`/`upper` swapped, and a
constant keyword argument dropped.

Imports, asserts, `TYPE_CHECKING` blocks, `__all__`, docstrings and type
annotations are left alone.

## Reading the score

A mutant is caught when a test fails, or when the run never finishes: a
change that makes a loop endless leaves the tests failing on their own time
limit until the run's runs out, and no run of the suite passes with it in.

A surviving mutant is not automatically a gap: some changes cannot alter
behaviour, and some code has no caller. Those judgements are recorded in
`classified.json`, under a key made of what the mutant is (file, function,
operator, description, source text, and which occurrence of that combination
it is). They are matched by that key, not by line number, so they keep
applying after the module is edited.

| Class | Meaning | Counted |
|---|---|---|
| `A` | A real gap: the change models a bug, and no test notices. | surviving |
| `D` | Untested: these tests never run the function or path. | surviving |
| `B` | Equivalent: the change cannot alter behaviour. | left out |
| `C` | Harmless: log or message text, counts only logged, performance only. | left out |
| `E` | Dead code: nothing calls it. | left out |
| `F` | Hidden by an in-memory repository sharing objects with its caller. | left out |
| `?` | Not judged yet. | surviving |

The **raw** score is caught mutants over all of them. The **adjusted** score
leaves out `B`, `C`, `E` and `F`, which no test could or should catch.

When a run reports `?` survivors, judge each one and add it to
`classified.json`: the key fields come from the run's `mutants.json`, and
`occurrence` counts earlier mutants with the same file, function, operator,
description and source text. A survivor judged `A` or `D` is a test worth
writing.
