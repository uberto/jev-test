# jev-test: does this code follow functional principles?

An experiment in checking whether a codebase follows a set of design principles, using a model that
**answers questions instead of writing text**.

The principles come from *From Objects to Functions* (Uberto Barbini, Pragmatic Bookshelf):
pure functions, immutable data, side effects kept at the edges, errors as values, types that model
the domain, and functions as values. The script reads every Kotlin file in a project, asks
[Jev](https://typesafe.ai) (TypeSafe's System One model) how well each file follows each principle,
and adds up the answers into a picture of the whole codebase.

## The idea

Asking a chat model to "review this code" gives you prose: hard to compare between files, hard to
add up, and just as fluent whether the model is sure or guessing.

Jev works differently. It generates no text. You give it a *state* (here, the source of one file)
and a set of typed questions, and it answers them all in one call:

| Question type | Answer |
|---|---|
| **Score** (ordered levels, e.g. 0–4) | expected score, probability of each level, **confidence** |
| **Choice** (named options) | selected option, probability of each option, **confidence** |
| **Noul** (yes/no) | probability of *yes* |

This makes each principle **a measurement, not an opinion**:

- **Gradients, not yes/no.** Every principle is scored from 0 ("Not followed at all") to 4 ("Fully
  followed"). The result is an expected value, so a file can land on 2.6.
- **Confidence comes with every answer.** When the model can't tell, you see it, and the summary can
  leave that answer out rather than average a guess.
- **Answers only count where a principle applies.** A file with no failure paths has nothing to say
  about *errors as values*, but a 0–4 scale will still put it at "Not followed at all". So each
  principle also gets a yes/no question, "does this principle apply to this file?". A score only
  counts where the answer is yes.
- **Results add up.** Every answer is a number with a confidence, so a whole project becomes a small
  table: the average per principle, how many files it applied to, and how many answers were
  confident enough to keep.

## What it asks

For each file, in one API call:

| Principle | Scored question (0–4) |
|---|---|
| `pure_functions` | Is the logic written as pure functions: same output for the same input, no hidden side effects? |
| `immutable_data` | Is data immutable: `val`, data classes, read-only collections, `copy` instead of mutation? |
| `effects_at_the_edges` | Are side effects (I/O, database, clock, randomness, logging) kept at the boundaries? |
| `errors_as_values` | Are expected failures returned as values (Outcome/Result, sealed types) instead of thrown or signalled with null? |
| `types_model_the_domain` | Is the domain modelled with precise types that make illegal states unrepresentable? |
| `functions_as_values` | Are functions used as values: higher-order functions, composition, dependencies as function types? |
| `no_shared_mutable_state` | Is the code free of shared mutable state, stateful singletons and global variables? |

Each principle also gets a yes/no *applies* question. On top of those, the file gets an overall
`functional_style` score (0–4) and a `verdict`: `functional`, `mostly_functional`,
`needs_refactoring` or `not_applicable`.

The questions live in plain dictionaries at the top of [`principles.py`](principles.py). Editing
them changes the review, and there are no prompt templates to maintain.

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/). Get an API key from
[console.typesafe.ai](https://console.typesafe.ai) and put it in a `.env` file (git-ignored):

```sh
echo 'TYPESAFE_API_KEY=...' > .env
uv sync
```

## Usage

```sh
uv run python principles.py path/to/project           # asks for the directory if you leave it out
uv run python principles.py path/to/project --all     # lift the 100-file testing limit
uv run python principles.py path/to/project --dry     # show what would be sent for the first file, no API call
uv run python principles.py --from results-project.json   # recompute the summary from saved results, no API calls
```

- **Which files:** every `*.kt` file under the directory, including subdirectories. It skips build
  and IDE output (`build/`, `bin/`) and test source sets (`test`, `testFixtures`, `*Test`).
- **One call per file:** each file is reviewed on its own, four at a time.
- **Saved results:** full per-file answers go to `results-<dir>.json`, so you can recompute the
  summary with a different threshold without paying for the calls again.

Settings are constants at the top of `principles.py`: `MIN_CONFIDENCE` (0.7), `MAX_FILES` (100),
`MAX_CHARS` (files longer than this are truncated), `PARALLEL_CALLS` and `MODEL`.

## Example: kondor-json

A full run on the 70 main-source files of [kondor-json](https://github.com/uberto/kondor-json):

```
  principle                               mean   applies   kept
  pure_functions           █████████░  3.5/4    47/70     10
  immutable_data           █████████░  3.7/4    54/70     19
  effects_at_the_edges                     -    17/70      0
  errors_as_values         ████░░░░░░  1.7/4    52/70     23
  types_model_the_domain   █████████░  3.4/4    26/70      6
  functions_as_values      ████████░░  3.1/4    49/70     13
  no_shared_mutable_state  █████████░  3.8/4    34/70     12
  functional_style         ███████░░░  2.6/4    70/70     21
```

- **`applies`:** the number of files where Jev said the principle applies, with p(yes) ≥ 70%.
- **`kept`:** of those, the number whose score had confidence ≥ 70%.
- **`mean`:** the average of the kept scores only.

The summary also lists the verdict counts and the five least functional files.

## What we learned

- **Without the *applies* question, the averages were misleading.** Before it was added,
  `types_model_the_domain` averaged 2.2 and `functions_as_values` 1.9. Many JSON converter files
  just have no domain model or dependencies to judge, and the 0–4 scale scored them "Not followed at
  all". With the *applies* question, those averages rose to 3.4 and 3.1.
- **The model reviews one file at a time and misses the framework around it.** kondor-json handles
  errors with `Outcome` almost everywhere, yet `errors_as_values` scores 1.7. The low-scoring files
  pass functions that can throw, such as `LocalDateTime::parse`, to base classes like
  `JStringRepresentable`, and the base class wraps those calls in `Outcome.tryOrFail`. Seen alone,
  such a file looks like unguarded parsing. The files that use `Outcome` directly score 3.6–4.0.
- **The confidence filter drops most answers.** At 70%, most principles keep only a tenth to a third of the
  files. The averages are more reliable, but they lean towards clear-cut files.

## Ideas for next steps

- **Project notes:** a short text on the project's conventions (for example "converters extend base
  classes that turn exceptions into `JsonOutcome`"), added to every file review. This addresses the
  missing framework context.
- **Automatic context:** add the source of each file's base classes, found in the same repo, to what
  is sent with the file.
- **Other languages and principle sets:** only the file glob and the question dictionaries are
  Kotlin- or book-specific.

## License

[MIT](LICENSE)
