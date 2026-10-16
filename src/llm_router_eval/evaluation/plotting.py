"""Headless figures from safe aggregates, with no inference or private payload access."""

import json
import math
import os
from decimal import Decimal
from functools import cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

LABELS = {
    "always_cheap": "Always cheap",
    "always_frontier": "Always frontier",
    "rules": "Rules",
    "classifier": "Classifier",
    "jev_gate": "Jev gate",
    "cascade_jev": "Jev cascade",
    "cascade_llm": "LLM cascade",
    "random_uniform": "Random uniform (reference)",
    "random_cheap_heavy": "Random cheap-heavy (reference)",
    "random_frontier_heavy": "Random frontier-heavy (reference)",
    "oracle_cheapest_passing": "Oracle (reference)",
}
_CONFIG = None


@cache
def _backend():
    global _CONFIG
    _CONFIG = TemporaryDirectory(prefix="llm-router-render-")
    previous = os.environ.get("MPLCONFIGDIR")
    os.environ["MPLCONFIGDIR"] = _CONFIG.name
    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        if (
            not Path(matplotlib.get_cachedir())
            .resolve()
            .is_relative_to(Path(_CONFIG.name).resolve())
        ):
            raise ValueError("plot_backend_cache_already_initialized")
        return matplotlib, Figure, FigureCanvasAgg
    finally:
        if previous is None:
            os.environ.pop("MPLCONFIGDIR", None)
        else:
            os.environ["MPLCONFIGDIR"] = previous


def figure_data(report):
    """Omit unavailable points instead of plotting an invented zero; expose omissions."""
    cohort = report["cohorts"]["deterministic"]
    costs = {r["method"]: r for r in cohort["source_costs"]}
    if any(r["method"] not in LABELS or r["method"] not in costs for r in cohort["metrics"]):
        raise ValueError("unknown_or_missing_plot_method")
    candidates = [(r["method"], r, costs[r["method"]]) for r in cohort["metrics"]]
    candidates += [
        (r["reference"], r["metric"], r["source_cost"]) for r in report["references"]["rows"]
    ]
    points, omitted = [], []
    for name, metric, cost in candidates:
        if name not in LABELS:
            raise ValueError("unknown_plot_method")
        reason = (
            "no_eligible_cases"
            if not cohort["case_N"]
            else (
                "not_run"
                if metric.get("null_reason") == "not_run"
                else "unknown_source_cost"
                if cost["source_point_usd"] is None
                else "unavailable_score"
                if metric["value"] is None
                else None
            )
        )
        if reason:
            omitted.append(
                dict(
                    method=name,
                    reason=reason,
                    source_cost_lower_usd=cost["source_lower_usd"],
                    source_cost_upper_usd=cost["source_upper_usd"],
                )
            )
            continue
        x = float(Decimal(cost["source_point_usd"]) / Decimal(cohort["case_N"]))
        y = metric["value"]
        if not math.isfinite(x) or x < 0 or not math.isfinite(y) or not 0 <= y <= 1:
            raise ValueError("invalid_plot_point")
        interval = report["sampling"]["methods"].get(name, {}).get("interval")
        points.append(
            dict(
                method=name,
                source_usd_per_case=x,
                pass_fraction=y,
                interval=interval,
                source_cost_total_usd=cost["source_point_usd"],
                case_N=cohort["case_N"],
            )
        )
    timing = []
    for name, summary in report["local_policy_by_method_timing"].items():
        if name not in LABELS:
            raise ValueError("unknown_plot_method")
        if summary["n"] and summary["p50_ms"] is not None:
            timing.append(dict(method=name, **summary))
    return dict(
        version=1,
        mode="authored_synthetic_only",
        eligible_N=cohort["case_N"],
        family_N=cohort["family_N"],
        points=points,
        omitted=omitted,
        local_timing=timing,
        cost_scope="model_source_only",
        host_resource_cost_usd=None,
        model_latency_ms=None,
        reference_latency_ms=None,
        sampling=report["sampling"],
        source_manifest_sha256=report["manifest_sha256"],
        source_book_sha256=report["source_book_sha256"],
    )


def render_figures(report):
    data = figure_data(report)
    matplotlib, Figure, FigureCanvasAgg = _backend()
    settings = {
        "svg.hashsalt": "llm-router-figures-v1",
        "svg.fonttype": "none",
        "font.family": "DejaVu Sans",
        "font.size": 10,
    }
    with matplotlib.rc_context(settings):
        figure = Figure(figsize=(13, 7), dpi=240)
        FigureCanvasAgg(figure)
        quality, timing = figure.subplots(1, 2, gridspec_kw={"width_ratios": [1.25, 1]})
        for index, point in enumerate(data["points"]):
            color = matplotlib.colormaps["tab20"](index % 20)
            x, y = point["source_usd_per_case"], point["pass_fraction"]
            interval = point["interval"]
            quality.plot(
                x,
                y,
                marker="D" if point["method"].startswith(("random_", "oracle_")) else "o",
                linestyle="none",
                markersize=6,
                color=color,
                label=LABELS[point["method"]],
            )
            if interval is not None:
                quality.vlines(x, interval["low"], interval["high"], color=color)
                quality.plot(
                    [x, x],
                    [interval["low"], interval["high"]],
                    marker="_",
                    linestyle="none",
                    color=color,
                    markersize=7,
                )
        quality.set(
            xlabel="Synthetic model-source USD per eligible case",
            ylabel="Pass fraction (assigned deterministic cases)",
            ylim=(-0.03, 1.07),
            title=f"Quality / cost: {data['eligible_N']} cases, {data['family_N']} families",
        )
        quality.set_xlim(left=0)
        quality.grid(alpha=0.2)
        if not data["points"]:
            quality.text(
                0.5,
                0.5,
                "No complete cost / quality points\nUnknown costs are omitted",
                ha="center",
                va="center",
                transform=quality.transAxes,
            )
        quality.text(
            0.02,
            0.03,
            f"{len(data['omitted'])} omitted; exact rows in figure-data.json",
            transform=quality.transAxes,
            fontsize=8,
        )
        for index, row in enumerate(data["local_timing"]):
            median, p95 = row["p50_ms"], row["p95_ms"]
            timing.barh(index, median, color="#38739b", alpha=0.75)
            if p95 is not None:
                timing.plot([median, p95], [index, index], color="#263238", linewidth=1.5)
        timing.set_yticks(
            range(len(data["local_timing"])),
            [f"{LABELS[row['method']]} (n={row['n']})" for row in data["local_timing"]],
        )
        timing.invert_yaxis()
        timing.set(
            xlabel="Local policy computation ms (fixture reuse)",
            title="Local timing only: median bar / p95 line",
            xlim=(0, None),
        )
        timing.grid(axis="x", alpha=0.2)
        if not data["local_timing"]:
            timing.text(
                0.5,
                0.5,
                "No local timing observations",
                ha="center",
                va="center",
                transform=timing.transAxes,
            )
        figure.suptitle(
            "AUTHORED SYNTHETIC CONTROLS — no model-quality or price claim", fontsize=14
        )
        if data["points"]:
            handles, labels = quality.get_legend_handles_labels()
            figure.legend(
                handles,
                labels,
                loc="lower center",
                bbox_to_anchor=(0.5, 0.06),
                ncol=4,
                fontsize=8,
                frameon=False,
            )
        figure.text(
            0.5,
            0.015,
            "Unknown host cost; model latency / TTFT unmeasured. Overlapping points retained. "
            "References have no execution latency.",
            ha="center",
            fontsize=8,
        )
        figure.text(
            0.5,
            0.04,
            "95% paired-family intervals where supported; 2,000 canonical draws."
            if data["sampling"]["status"] == "computed"
            else "No sampling interval inferred.",
            ha="center",
            fontsize=8,
        )
        figure.tight_layout(rect=(0, 0.24, 1, 0.93))
        assets = {}
        for suffix in ["png", "svg"]:
            stream = BytesIO()
            metadata = {"Date": None, "Title": "Authored synthetic routing controls"}
            if suffix == "png":
                metadata = {"Software": "LLM Router Eval authored-control figure"}
            figure.savefig(stream, format=suffix, metadata=metadata)
            assets["quality-cost." + suffix] = stream.getvalue()
        figure.clear()
    assets["figure-data.json"] = (
        json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    return assets
