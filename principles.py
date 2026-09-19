"""Review Kotlin files against the functional principles of
"From Objects to Functions" (Uberto Barbini, Pragmatic Bookshelf) using Jev.

Every principle is a 0-4 score (a gradient, not a yes/no) and comes back with Jev's confidence.
The `jev` decorator discards confidence, so this calls Jev through `typesafe_sdk` directly.

Run:  uv run python principles.py [DIR]                 # asks for DIR if not given
      uv run python principles.py [DIR] --all           # no MAX_FILES limit
      uv run python principles.py [DIR] --dry           # prints the state for the first file, no API call
      uv run python principles.py --from results.json   # re-aggregate saved results, no API calls
Results are saved to results-<dir name>.json; the summary only counts answers with confidence >= MIN_CONFIDENCE.
Needs TYPESAFE_API_KEY in .env.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

load_dotenv()

MODEL = "jev-latest"
MAX_CHARS = 20_000  # longer files are truncated before review
MAX_FILES = 100  # stop after this many files (testing limit)
MIN_CONFIDENCE = 0.7  # answers below this are left out of the summary
SKIPPED_DIRS = {"build", "bin"}  # build/IDE output (generated or copied code), not hand-written source
TEST_DIRS = {"test", "testFixtures"}  # plus any "*Test" source set, e.g. integrationTest
PARALLEL_CALLS = 4  # files reviewed at the same time, still one file per call

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

# A principle only counts for a file when it applies there: a file with no failure paths
# has nothing to say about errors-as-values, and would otherwise score "Not followed at all".
APPLIES = {
    "pure_functions": "Does this file contain functions with real logic (computations, transformations, "
    "decisions), not only type declarations, constants or trivial delegation?",
    "immutable_data": "Does this file declare data or state: classes with properties, variables or collections?",
    "effects_at_the_edges": "Does this file contain logic that performs side effects (I/O, database, network, "
    "clock, randomness, logging) or domain logic that could be mixed with them?",
    "errors_as_values": "Does this file contain code that can fail or handles failure: parsing, validation, "
    "I/O, lookups that may find nothing, or explicit error handling?",
    "types_model_the_domain": "Does this file define or handle data that represents domain concepts "
    "(entities, values, states, messages), as opposed to pure infrastructure or plumbing?",
    "functions_as_values": "Does this file have dependencies, callbacks, strategies or reusable "
    "transformations that could be expressed as function values?",
    "no_shared_mutable_state": "Does this file hold any state: variables, properties, objects, caches or singletons?",
}
APPLIES_SUFFIX = "__applies"

QUESTIONS: dict[str, Score | Choice | Noul] = {
    name: Score(instructions=question, criteria=LEVELS) for name, question in PRINCIPLES.items()
}
QUESTIONS |= {name + APPLIES_SUFFIX: Noul(instructions=question) for name, question in APPLIES.items()}
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


def ask_directory(args: list[str]) -> Path:
    raw = args[0] if args else input("Directory to review: ").strip()
    directory = Path(raw).expanduser().resolve()
    if not directory.is_dir():
        sys.exit(f"Not a directory: {directory}")
    return directory


def is_test_dir(part: str) -> bool:
    return part in TEST_DIRS or part.endswith("Test")


def kotlin_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*.kt")
        if p.is_file()
        and not SKIPPED_DIRS.intersection(parts := p.relative_to(directory).parts[:-1])
        and not any(is_test_dir(part) for part in parts)
    )


def review_file(client: TypeSafeClient, directory: Path, path: Path) -> dict:
    """One Jev call for one file, reduced to plain data: {file, scores, verdict} or {file, error}."""
    name = str(path.relative_to(directory))
    try:
        response = client.system_one(state=file_state(path), questions=QUESTIONS, model=MODEL)
    except Exception as error:  # one failing file should not stop the run
        return {"file": name, "error": str(error)}
    verdict = response.choices["verdict"]
    return {
        "file": name,
        "scores": {
            field: {
                "score": answer.score,
                "confidence": answer.confidence,
                # p(yes) that the principle applies to this file; always 1 for the overall score
                "applies": response.nouls[field + APPLIES_SUFFIX].noul if field in APPLIES else 1.0,
            }
            for field, answer in response.scores.items()
        },
        "verdict": {"choice": verdict.choice, "confidence": verdict.confidence},
    }


def one_line(result: dict) -> str:
    if "error" in result:
        return f"⚠️  review failed: {result['error']}"
    style = result["scores"]["functional_style"]
    verdict = result["verdict"]
    return (
        f"style {style['score']:.1f}/4 ({style['confidence']:.0%})  "
        f"{verdict['choice']} ({verdict['confidence']:.0%})"
    )


def bar(score: float, top: int = 4) -> str:
    filled = round(score / top * 10)
    return "█" * filled + "░" * (10 - filled)


def print_summary(results: list[dict]) -> None:
    reviewed = [r for r in results if "error" not in r]
    failed = len(results) - len(reviewed)
    print(f"\n==== Summary: {len(reviewed)} file(s) reviewed, {failed} failed, "
          f"a principle counts for a file when it applies and its confidence is >= {MIN_CONFIDENCE:.0%} ====\n")

    print(f"  {'principle':<24} {'':10} {'mean':>8}  {'applies':>8}  {'kept':>5}")
    for field in [*PRINCIPLES, "functional_style"]:
        applicable = [r["scores"][field] for r in reviewed
                      if r["scores"][field].get("applies", 1.0) >= MIN_CONFIDENCE]
        kept = [a["score"] for a in applicable if a["confidence"] >= MIN_CONFIDENCE]
        mean_text = f"{bar(mean(kept))} {mean(kept):4.1f}/4" if kept else f"{'':10} {'-':>6}"
        print(f"  {field:<24} {mean_text}  {len(applicable):>4}/{len(reviewed):<4} {len(kept):>4}")

    confident = [r["verdict"]["choice"] for r in reviewed if r["verdict"]["confidence"] >= MIN_CONFIDENCE]
    print(f"\n  verdicts ({len(confident)}/{len(reviewed)} confident):")
    for choice in QUESTIONS["verdict"].criteria:
        count = confident.count(choice)
        print(f"    {choice:<20} {count:>4}  {count / len(confident):4.0%}" if confident else f"    {choice:<20}    0")

    ranked = sorted(
        (r for r in reviewed if r["scores"]["functional_style"]["confidence"] >= MIN_CONFIDENCE
         and r["verdict"]["choice"] != "not_applicable"),
        key=lambda r: r["scores"]["functional_style"]["score"],
    )
    print("\n  least functional files (confident answers only):")
    for r in ranked[:5]:
        print(f"    {r['scores']['functional_style']['score']:.1f}/4  {r['file']}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--from" in sys.argv:
        print_summary(json.loads(Path(args[0]).read_text()))
        return

    directory = ask_directory(args)
    files = kotlin_files(directory)
    if not files:
        sys.exit(f"No .kt files found in {directory}")
    print(f"Found {len(files)} Kotlin file(s) in {directory}")
    if len(files) > MAX_FILES and "--all" not in sys.argv:
        print(f"Stopping after the first {MAX_FILES} (testing limit, use --all for every file)")
        files = files[:MAX_FILES]

    if "--dry" in sys.argv:
        print(file_state(files[0]))
        return

    client = TypeSafeClient()
    results = []
    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        reviews = pool.map(lambda path: review_file(client, directory, path), files)
        for number, result in enumerate(reviews, start=1):
            print(f"[{number}/{len(files)}] {result['file']}  {one_line(result)}", flush=True)
            results.append(result)

    output = Path(f"results-{directory.name}.json")
    output.write_text(json.dumps(results, indent=2))
    print(f"\nSaved {len(results)} result(s) to {output}")
    print_summary(results)


if __name__ == "__main__":
    main()
