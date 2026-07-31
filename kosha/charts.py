"""Plotly figure builders for the dashboard.

Each builder turns analytics rows into a ``plotly.graph_objects.Figure``. The
dashboard renders them to self-contained HTML for a QWebEngineView; tests can
inspect the figures directly without a browser. Charts share one dark-friendly
template and a stable category colour map so a category keeps its colour across
every chart.
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

from .format import format_inr

# Brand-neutral qualitative palette (accessible, distinct in light and dark).
_PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]

def _layout(title: str = "", template: str = "plotly_white", **extra) -> dict:
    # Title is pinned to the very top and the legend sits *below* the plot, so
    # neither overlaps the plotting area (the header used to sit on the chart).
    base = dict(
        template=template,
        title=dict(text=title, x=0.02, xanchor="left", y=0.97, yanchor="top"),
        margin=dict(l=64, r=24, t=64, b=72),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0),
        font=dict(size=12),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    base.update(extra)
    return base


def _inr_hover(y_values, name: str = "") -> dict:
    """Trace kwargs giving an Indian-formatted amount in the hover tooltip."""
    tail = f"<extra>{name}</extra>" if name else "<extra></extra>"
    return dict(
        customdata=[format_inr(v) for v in y_values],
        hovertemplate="%{x}<br>₹%{customdata}" + tail,
    )


def color_map(categories) -> dict[str, str]:
    """Assign each category a stable colour from the palette."""
    return {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(sorted(set(categories)))}


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def prettify_period(period: str) -> str:
    """Human axis label for a period key: '2026-07' -> 'Jul-2026'.

    Quarter ('2026-Q2') and year ('2026') keys are already readable and pass
    through unchanged.
    """
    s = str(period)
    if len(s) == 7 and s[4] == "-" and s[5:].isdigit():
        month = int(s[5:7])
        if 1 <= month <= 12:
            return f"{_MONTHS[month - 1]}-{s[:4]}"
    return s


def snapshot_labels(isodates) -> list[str]:
    """Distinct axis labels for snapshot dates, one per date.

    Normally each snapshot is a different month, so a month label reads best. But
    two snapshots can fall in the same month, and a bar chart SUMS traces that
    share an x category — which would silently double that bar. So when a month
    repeats, every date in it is disambiguated with its day (e.g. 'Jul-2026 (15)'
    and 'Jul-2026 (31)'), keeping every snapshot its own bar.
    """
    months = [prettify_period(str(d)[:7]) for d in isodates]
    counts: dict[str, int] = {}
    for m in months:
        counts[m] = counts.get(m, 0) + 1
    labels = []
    for iso, month in zip(isodates, months):
        if counts[month] > 1:
            labels.append(f"{month} ({str(iso)[8:10]})")   # add the day to break ties
        else:
            labels.append(month)
    return labels


def _frame(fig: go.Figure) -> go.Figure:
    """Draw a light border around the plotting area (axis lines, mirrored)."""
    line = "rgba(128,128,128,0.45)"
    fig.update_xaxes(showline=True, linewidth=1, linecolor=line, mirror=True)
    fig.update_yaxes(showline=True, linewidth=1, linecolor=line, mirror=True)
    return fig


def spend_stacked_bar(rows, granularity: str = "month", template: str = "plotly_white",
                      label: str = "sub-category") -> go.Figure:
    """Stacked bar of expense per period, one trace per ``label`` (sub-category/tag)."""
    periods = sorted({r[0] for r in rows})
    labels = [prettify_period(p) for p in periods]
    subs = sorted({r[1] for r in rows})
    cmap = color_map(subs)
    by_sub = {s: {p: 0.0 for p in periods} for s in subs}
    for period, sub, total in rows:
        by_sub[sub][period] = total

    fig = go.Figure()
    for sub in subs:
        y = [by_sub[sub][p] for p in periods]
        fig.add_bar(name=sub, x=labels, y=y, marker_color=cmap[sub], **_inr_hover(y, sub))
    fig.update_layout(**_layout(
        f"Expense by {label} ({granularity})", template, barmode="stack",
        xaxis=dict(categoryorder="array", categoryarray=labels),
    ))
    fig.update_yaxes(title="Amount (₹)")
    return _frame(fig)


def income_expense_line(rows, template: str = "plotly_white") -> go.Figure:
    """Grouped Income / Expense / Savings bars per period."""
    labels = [prettify_period(r[0]) for r in rows]
    income = [r[1] for r in rows]
    expense = [r[2] for r in rows]
    savings = [r[3] for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Income", x=labels, y=income, marker_color="#54A24B", **_inr_hover(income, "Income"))
    fig.add_bar(name="Expense", x=labels, y=expense, marker_color="#E45756", **_inr_hover(expense, "Expense"))
    fig.add_bar(name="Savings / Investments", x=labels, y=savings, marker_color="#4C78A8",
                **_inr_hover(savings, "Savings / Investments"))
    fig.update_layout(**_layout(
        "Income vs expense vs savings", template,
        barmode="group",
        xaxis=dict(categoryorder="array", categoryarray=labels),
        yaxis=dict(title="Amount (₹)"),
    ))
    return _frame(fig)


def networth_trend(points, template: str = "plotly_white") -> go.Figure:
    """Assets / liabilities bars with the net-worth line over snapshot dates.

    ``points`` are :class:`kosha.wealth.NetWorthPoint`. Growth % between snapshots
    is annotated on the net-worth line so progress is readable at a glance.
    """
    labels = snapshot_labels([p.as_of for p in points])
    assets = [p.assets for p in points]
    liabs = [p.liabilities for p in points]
    net = [p.net_worth for p in points]
    growth_text = ["" if p.growth_pct is None else f"{p.growth_pct:+.0f}%" for p in points]

    fig = go.Figure()
    fig.add_bar(name="Assets", x=labels, y=assets, marker_color="#54A24B",
                **_inr_hover(assets, "Assets"))
    if any(liabs):
        fig.add_bar(name="Liabilities", x=labels, y=liabs, marker_color="#E45756",
                    **_inr_hover(liabs, "Liabilities"))
    fig.add_scatter(
        name="Net worth", x=labels, y=net, mode="lines+markers+text",
        line=dict(color="#4C78A8", width=3), marker=dict(size=8),
        text=growth_text, textposition="top center",
        customdata=[format_inr(v) for v in net],
        hovertemplate="%{x}<br>₹%{customdata}<extra>Net worth</extra>",
    )
    fig.update_layout(**_layout(
        "Net worth over time", template, barmode="group",
        xaxis=dict(categoryorder="array", categoryarray=labels),
        yaxis=dict(title="Amount (₹)"),
    ))
    return _frame(fig)


def gain_over_time(rows, template: str = "plotly_white") -> go.Figure:
    """Contributions vs market value over time, with the gain between them.

    ``rows`` are (date, invested, value, gain). Plotting both lines separates the
    money you put in from the growth on top of it.
    """
    labels = snapshot_labels([r[0] for r in rows])
    invested = [r[1] for r in rows]
    value = [r[2] for r in rows]
    gain = [r[3] for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Gain", x=labels, y=gain, marker_color="#54A24B",
                **_inr_hover(gain, "Gain"))
    fig.add_scatter(name="Invested", x=labels, y=invested, mode="lines+markers",
                    line=dict(color="#BAB0AC", width=2, dash="dot"),
                    customdata=[format_inr(v) for v in invested],
                    hovertemplate="%{x}<br>₹%{customdata}<extra>Invested</extra>")
    fig.add_scatter(name="Current value", x=labels, y=value, mode="lines+markers",
                    line=dict(color="#4C78A8", width=3),
                    customdata=[format_inr(v) for v in value],
                    hovertemplate="%{x}<br>₹%{customdata}<extra>Current value</extra>")
    fig.update_layout(**_layout(
        "Invested vs value over time", template,
        xaxis=dict(categoryorder="array", categoryarray=labels),
        yaxis=dict(title="Amount (₹)"),
    ))
    return _frame(fig)


def invested_vs_current_bar(rows, template: str = "plotly_white") -> go.Figure:
    """Grouped invested-vs-current bars per holding, largest first."""
    names = [r[0] for r in rows]
    invested = [r[1] for r in rows]
    current = [r[2] for r in rows]
    fig = go.Figure()
    fig.add_bar(name="Invested", x=names, y=invested, marker_color="#BAB0AC",
                **_inr_hover(invested, "Invested"))
    fig.add_bar(name="Current", x=names, y=current, marker_color="#4C78A8",
                **_inr_hover(current, "Current"))
    fig.update_layout(**_layout("Invested vs current value", template, barmode="group"))
    fig.update_yaxes(title="Amount (₹)")
    return _frame(fig)


def category_pie(rows, title: str = "Expense share by sub-category", template: str = "plotly_white") -> go.Figure:
    """Donut of share by label (sub-category or category)."""
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    cmap = color_map(labels)
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.5,
        marker=dict(colors=[cmap[l] for l in labels]),
        customdata=[format_inr(v) for v in values],
        hovertemplate="%{label}<br>₹%{customdata} (%{percent})<extra></extra>",
    ))
    fig.update_layout(**_layout(title, template))
    return fig


def top_merchants_bar(rows, template: str = "plotly_white") -> go.Figure:
    """Horizontal bar of top merchants by spend."""
    merchants = [r[0] for r in rows][::-1]   # largest on top
    totals = [r[1] for r in rows][::-1]
    fig = go.Figure(go.Bar(
        x=totals, y=merchants, orientation="h", marker_color="#4C78A8",
        customdata=[format_inr(v) for v in totals],
        hovertemplate="%{y}<br>₹%{customdata}<extra></extra>",
    ))
    fig.update_layout(**_layout("Top merchants by spend", template))
    fig.update_xaxes(title="Amount (₹)")
    return _frame(fig)


def figure_to_html(fig: go.Figure) -> str:
    """Self-contained HTML for one figure (Plotly.js inlined, responsive)."""
    return pio.to_html(fig, include_plotlyjs="inline", full_html=True, config={"responsive": True})


def write_plotlyjs(directory) -> str:
    """Write plotly.min.js into ``directory`` once; return its filename.

    Referencing the library from a cached local file makes each dashboard
    refresh generate a tiny HTML page (~tens of KB) instead of re-inlining the
    ~3.5 MB library every time, which is what made refreshes slow.
    """
    from plotly.offline import get_plotlyjs
    p = Path(directory) / "plotly.min.js"
    if not p.exists():
        p.write_text(get_plotlyjs(), encoding="utf-8")
    return p.name


def dashboard_html(figures, dark: bool = False, plotlyjs: str = "inline") -> str:
    """Combine multiple figures into one scrollable, responsive page.

    ``plotlyjs`` controls how the library is loaded: ``"inline"`` embeds it (self
    contained, used by tests); a filename/URL references it from there instead —
    far cheaper to regenerate. ``dark`` matches the page chrome to the app theme.
    """
    external = plotlyjs != "inline"
    blocks = []
    for i, fig in enumerate(figures):
        include = False if external else ("inline" if i == 0 else False)
        blocks.append(pio.to_html(
            fig,
            include_plotlyjs=include,
            full_html=False,
            config={"responsive": True},
            default_height="360px",
        ))
    body = "\n".join(f'<div class="chart">{b}</div>' for b in blocks)
    bg = "#1e1e1e" if dark else "#ffffff"
    fg = "#e0e0e0" if dark else "#202020"
    border = "#3a3a3a" if dark else "#d0d0d0"
    card = "#252525" if dark else "#fafafa"
    head_js = f'<script src="{plotlyjs}" charset="utf-8"></script>' if external else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">{head_js}
<style>
  body {{ margin:0; padding:8px; font-family: system-ui, sans-serif;
          background:{bg}; color:{fg}; }}
  .chart {{ margin-bottom: 12px; border:1px solid {border}; border-radius:8px;
            padding:8px; background:{card}; }}
</style></head><body>{body}</body></html>"""
