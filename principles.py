"""Review Kotlin files against the functional principles of
"From Objects to Functions" (Uberto Barbini, Pragmatic Bookshelf) using Jev.

Every principle is a 0-4 score (a gradient, not a yes/no) and comes back with Jev's confidence.
The `jev` decorator discards confidence, so this calls Jev through `typesafe_sdk` directly.

Run:  uv run python principles.py [DIR]          # asks for DIR if not given
      uv run python principles.py [DIR] --dry    # prints the state for the first file, no API call
Needs TYPESAFE_API_KEY in .env.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from typesafe_sdk import Choice, Score, TypeSafeClient

load_dotenv()

MODEL = "jev-latest"
MAX_CHARS = 20_000  # longer files are truncated before review
MAX_FILES = 100  # stop after this many files (testing limit)
LOW_CONFIDENCE = 0.6  # answers below this are flagged for a human look

LEVELS = [
    "Not followed at all",
    "Followed in a few places only",
    "Partly followed",
    "Mostly followed",
    "Fully followed",
]

PRINCIPLES = {
    "pure_functions": "Is the logic written as pure functions: same output for the same input, "
    "no hidden side effects, referentially transparent?",
    "immutable_data": "Is data immutable: `val` instead of `var`, data classes, read-only collections, "
    "`copy` instead of mutation?",
    "effects_at_the_edges": "Are side effects (I/O, database, clock, randomness, logging) kept at the "
    "boundaries, leaving the domain logic free of them?",
    "errors_as_values": "Are expected failures returned as values (an Outcome/Result or sealed type) "
    "instead of thrown as exceptions or signalled with null?",
    "types_model_the_domain": "Is the domain modelled with precise types (data classes, sealed hierarchies, "
    "value classes) that make illegal states unrepresentable, instead of raw strings and primitives?",
    "functions_as_values": "Are functions used as values: higher-order functions, composition, dependencies "
    "passed as function types, rather than deep class hierarchies and mockable interfaces?",
    "no_shared_mutable_state": "Is the code free of shared mutable state, stateful singletons and global variables?",
}

QUESTIONS: dict[str, Score | Choice] = {
    name: Score(instructions=question, criteria=LEVELS) for name, question in PRINCIPLES.items()
}
QUESTIONS["functional_style"] = Score(
    instructions="Overall, how functional is this code?",
    criteria=[
        "Fully imperative / object-oriented",
        "Mostly imperative with a few functional touches",
        "A mix of imperative and functional",
        "Mostly functional",
        "Idiomatic functional Kotlin",
    ],
)
QUESTIONS["verdict"] = Choice(
    instructions="Overall verdict on how well the file follows the book's functional principles.",
    criteria={
        "functional": "Follows the principles well",
        "mostly_functional": "Follows them with a few lapses",
        "needs_refactoring": "Breaks several principles",
        "not_applicable": "No real logic to judge (build script, empty file, pure configuration)",
    },
)


def file_state(path: Path) -> str:
    code = path.read_text(errors="replace")
    if len(code) > MAX_CHARS:
        code = code[:MAX_CHARS] + "\n// ... (truncated)"
    return (
        "A Kotlin source file reviewed against the principles of functional programming described in "
        '"From Objects to Functions" by Uberto Barbini: pure functions, immutable data, side effects '
        "pushed to the edges, errors as values, types that model the domain, and functions as values.\n\n"
        f"File: {path.name}\n\n```kotlin\n{code}\n```"
    )


def ask_directory() -> Path:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = args[0] if args else input("Directory to review: ").strip()
    directory = Path(raw).expanduser().resolve()
    if not directory.is_dir():
        sys.exit(f"Not a directory: {directory}")
    return directory


def kotlin_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.kt") if p.is_file())


def bar(score: float, top: int) -> str:
    filled = round(score / top * 10)
    return "█" * filled + "░" * (10 - filled)


def confidence_text(confidence: float) -> str:
    flag = "  ⚠️ low" if confidence < LOW_CONFIDENCE else ""
    return f"conf {confidence:4.0%}{flag}"


def print_report(name: str, response) -> None:
    print(f"\n== {name} ==")
    for field, answer in response.scores.items():
        top = len(QUESTIONS[field].criteria) - 1
        print(f"  {field:<24} {bar(answer.score, top)} {answer.score:.1f}/{top}  {confidence_text(answer.confidence)}")
    verdict = response.choices["verdict"]
    print(f"  {'verdict':<24} {verdict.choice:<21} {confidence_text(verdict.confidence)}")


def main() -> None:
    directory = ask_directory()
    files = kotlin_files(directory)
    if not files:
        sys.exit(f"No .kt files found in {directory}")
    print(f"Found {len(files)} Kotlin file(s) in {directory}")
    if len(files) > MAX_FILES:
        print(f"Stopping after the first {MAX_FILES} (testing limit)")
        files = files[:MAX_FILES]

    if "--dry" in sys.argv:
        print(file_state(files[0]))
        return

    client = TypeSafeClient()
    for number, path in enumerate(files, start=1):
        name = f"[{number}/{len(files)}] {path.relative_to(directory)}"
        try:
            response = client.system_one(state=file_state(path), questions=QUESTIONS, model=MODEL)
        except Exception as error:  # one failing file should not stop the run
            print(f"\n== {name} ==\n  ⚠️  review failed: {error}")
            continue
        print_report(name, response)


if __name__ == "__main__":
    main()
