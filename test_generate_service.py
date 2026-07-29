import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_SUBJECTS = ROOT / "class_subjects.json"
DEFAULT_PLACES = ROOT / "places.json"
DEFAULT_SEMESTER_START = "2026-08-03"
DEFAULT_SEMESTER_END = "2026-12-05"
PHP_PAGE_SEMESTER_END = "2026-12-05"


def load_json(path):
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def subject_key(subject):
    return tuple(subject.get("id_list", []))


def subject_label(subject):
    data = subject.get("data", {})
    return (
        f'{data.get("code", "UNKNOWN")} '
        f'({data.get("group", "UNKNOWN")}) - '
        f'Vagas: {subject.get("vacancies_int", 0)}'
    )


def place_label(place):
    data = place.get("data", {})
    desc = data.get("desc") or data.get("number") or place.get("id", "UNKNOWN")
    capacity = safe_int(data.get("capacity"))
    return f"{desc} (capacidade {capacity})"


def summarize_place_types(places):
    counts = Counter()
    for place in places:
        types = place.get("data", {}).get("object_sub_type") or []
        counts[types[0] if types else "<missing>"] += 1
    return dict(sorted(counts.items()))


def filter_places(places, place_types=None):
    if not place_types:
        return places

    allowed = set(place_types)
    return [
        place
        for place in places
        if (place.get("data", {}).get("object_sub_type") or [None])[0] in allowed
    ]


def analyze_subjects(generate_service, subjects):
    return generate_service._filter_subjects(subjects)


def build_unassigned_reasons(generate_service, filtered_subjects, places, reservations):

    reservation_places = {}
    for reservation in reservations:
        class_subject_ids = tuple(reservation.get("data", {}).get("class_subject", []))
        place_ids = reservation.get("data", {}).get("place", [])
        if class_subject_ids and place_ids and class_subject_ids not in reservation_places:
            reservation_places[class_subject_ids] = place_ids[0]

    assigned_subjects = []
    unassigned_subjects = []
    for subject in filtered_subjects:
        if subject_key(subject) in reservation_places:
            assigned_subjects.append(subject)
        else:
            unassigned_subjects.append(subject)

    assigned_by_place = defaultdict(list)
    for subject in assigned_subjects:
        assigned_by_place[reservation_places[subject_key(subject)]].append(subject)

    reasons = []
    for subject in unassigned_subjects:
        vacancies = subject.get("vacancies_int", 0)
        candidate_places = [
            place
            for place in places
            if safe_int(place.get("data", {}).get("capacity")) >= vacancies
        ]

        if not candidate_places:
            reasons.append({"subject": subject_label(subject), "reason": "no_room_with_enough_capacity"})
            continue

        conflict_details = []
        for place in candidate_places:
            conflicts = [
                other
                for other in assigned_by_place.get(place.get("id"), [])
                if generate_service.check_time_conflict(
                    subject.get("parsed_slots", []),
                    other.get("parsed_slots", []),
                )
            ]
            if conflicts:
                conflict_details.append((place, conflicts))

        if len(conflict_details) == len(candidate_places):
            reasons.append(
                {
                    "subject": subject_label(subject),
                    "reason": "all_candidate_rooms_conflict_directly",
                }
            )
            continue

        reasons.append(
            {
                "subject": subject_label(subject),
                "reason": "left_out_by_global_optimization",
            }
        )

    return reasons


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Run GenerateService against class_subjects.json and places.json."
        )
    )
    parser.add_argument(
        "--subjects",
        type=Path,
        default=DEFAULT_SUBJECTS,
        help=f"Subjects JSON file. Default: {DEFAULT_SUBJECTS}",
    )
    parser.add_argument(
        "--places",
        type=Path,
        default=DEFAULT_PLACES,
        help=f"Places JSON file. Default: {DEFAULT_PLACES}",
    )
    parser.add_argument(
        "--semester-start",
        default=DEFAULT_SEMESTER_START,
        help=(
            "Semester start date in YYYY-MM-DD format. "
            f"Default: {DEFAULT_SEMESTER_START}"
        ),
    )
    parser.add_argument(
        "--semester-end",
        default=DEFAULT_SEMESTER_END,
        help=(
            "Semester end date in YYYY-MM-DD format. "
            f"Default: {DEFAULT_SEMESTER_END}"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write generated reservations JSON.",
    )
    parser.add_argument(
        "--show-sample",
        type=int,
        default=3,
        help="How many generated reservations to print. Default: 3",
    )
    parser.add_argument(
        "--place-type",
        action="append",
        dest="place_types",
        help=(
            "Filter places by object_sub_type. Can be repeated, for example "
            "--place-type classroom --place-type computer_lab."
        ),
    )
    parser.add_argument(
        "--php-page-mode",
        action="store_true",
        help=(
            "Match page-gerar-reservas.php by using only classroom places and "
            f"defaulting semester_end to {PHP_PAGE_SEMESTER_END}."
        ),
    )
    return parser


def main():
    try:
        from services.generate_service import GenerateService
    except ModuleNotFoundError as exc:
        missing_module = getattr(exc, "name", "unknown")
        raise SystemExit(
            "Unable to run GenerateService because a required dependency is "
            f"missing: {missing_module}. Install project requirements first."
        ) from exc

    parser = build_parser()
    args = parser.parse_args()

    subjects_path = args.subjects.resolve()
    places_path = args.places.resolve()

    subjects = load_json(subjects_path)
    places = load_json(places_path)
    place_types = args.place_types or []
    semester_end = args.semester_end

    if args.php_page_mode:
        if not place_types:
            place_types = ["classroom"]
        if semester_end == DEFAULT_SEMESTER_END:
            semester_end = PHP_PAGE_SEMESTER_END

    filtered_places = filter_places(places, place_types)
    service_eligible_places = GenerateService._filter_places(filtered_places)
    all_place_type_counts = summarize_place_types(places)
    used_place_type_counts = summarize_place_types(filtered_places)
    eligible_place_type_counts = summarize_place_types(service_eligible_places)
    filtered_subjects, _, bad_format_subjects = analyze_subjects(
        GenerateService,
        subjects,
    )

    result = GenerateService.generate(
        subjects=subjects,
        places=filtered_places,
        semester_start=args.semester_start,
        semester_end=semester_end,
    )

    reservations = result["reservations"]
    stats = result["stats"]
    bad_format_subjects = stats.get("bad_format_subjects", [])
    unassigned_reasons = build_unassigned_reasons(
        GenerateService,
        filtered_subjects,
        service_eligible_places,
        reservations,
    )

    print("GenerateService test run completed")
    print(f"Subjects file: {subjects_path}")
    print(f"Places file: {places_path}")
    print(f"Subjects loaded: {len(subjects)}")
    print(f"Places loaded: {len(places)}")
    print(f"Places passed to service: {len(filtered_places)}")
    print(f"Places eligible inside service: {len(service_eligible_places)}")
    print(f"Semester start: {args.semester_start}")
    print(f"Semester end: {semester_end}")
    print(
        "All place types: "
        + json.dumps(all_place_type_counts, ensure_ascii=False, indent=2)
    )
    print(
        "Passed place types: "
        + json.dumps(used_place_type_counts, ensure_ascii=False, indent=2)
    )
    print(
        "Eligible place types inside service: "
        + json.dumps(eligible_place_type_counts, ensure_ascii=False, indent=2)
    )
    print(f"Reservations generated: {len(reservations)}")
    print(
        "Stats: "
        + json.dumps(stats, ensure_ascii=False, indent=2)
    )
    if unassigned_reasons:
        print(
            "Unassigned reasons: "
            + json.dumps(unassigned_reasons, ensure_ascii=False, indent=2)
        )
    if bad_format_subjects:
        print(
            "Bad format subjects: "
            + json.dumps(bad_format_subjects, ensure_ascii=False, indent=2)
        )

    sample_size = max(args.show_sample, 0)
    if sample_size and reservations:
        print("\nSample reservations:")
        for index, reservation in enumerate(reservations[:sample_size], start=1):
            print(
                f"{index}. "
                + json.dumps(reservation["data"], ensure_ascii=False, indent=2)
            )

    if args.output:
        output_path = args.output.resolve()
        with output_path.open("w", encoding="utf-8") as file_obj:
            json.dump(reservations, file_obj, ensure_ascii=False, indent=2)
        print(f"\nWrote {len(reservations)} reservations to {output_path}")


if __name__ == "__main__":
    main()
