from pathlib import Path

import demo_day6
from bank import ReportBuilder

OUTPUT = Path("reports")


def main(output: Path = OUTPUT) -> None:
    world = demo_day6.build_world(out=lambda line: None)
    demo_day6.simulate(world)
    builder = ReportBuilder(world.bank, world.processor)

    reports = [
        builder.client_report("CL-ANNA"),
        builder.bank_report(),
        builder.risk_report(),
    ]

    for report in reports:
        print(builder.to_text(report))
        print("\n")

    print("=" * 72)
    print(f" Экспорт в {output}/")
    print("=" * 72)
    for report in reports:
        json_path = builder.export_to_json(report, output / "json" / f"{report.kind.value}.json")
        csv_paths = builder.export_to_csv(report, output / "csv")
        print(f"  {report.title}")
        print(f"    JSON: {json_path}")
        print(f"    CSV:  {', '.join(p.name for p in csv_paths)}")

    charts = builder.save_charts(output / "charts", client_ids=["CL-ANNA", "CL-SERGEY"])
    print("  Графики:")
    for path in charts:
        print(f"    {path}  ({path.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
