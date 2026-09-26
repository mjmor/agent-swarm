from pathlib import Path

import altair as alt
import vl_convert as vlc

from agent_swarm.paths import FIGURES_DIR

CATEGORICAL = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
SEQUENTIAL = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def theme() -> dict:
    return {
        "config": {
            "background": SURFACE,
            "font": FONT,
            "view": {"stroke": None, "continuousWidth": 640, "continuousHeight": 260},
            "title": {
                "color": INK,
                "subtitleColor": INK_SECONDARY,
                "anchor": "start",
                "fontSize": 14,
                "fontWeight": 600,
                "subtitleFontSize": 12,
            },
            "axis": {
                "gridColor": GRID,
                "domainColor": BASELINE,
                "tickColor": BASELINE,
                "labelColor": INK_MUTED,
                "titleColor": INK_SECONDARY,
                "titleFontWeight": "normal",
                "labelFontSize": 11,
                "titleFontSize": 12,
            },
            "axisX": {"grid": False, "labelAngle": 0},
            "mark": {"color": CATEGORICAL[0]},
            "legend": {
                "orient": "top",
                "labelColor": INK_SECONDARY,
                "titleColor": INK_SECONDARY,
                "titleFontWeight": "normal",
            },
            "range": {"category": list(CATEGORICAL), "ramp": list(SEQUENTIAL)},
            "bar": {"cornerRadiusEnd": 4, "stroke": SURFACE, "strokeWidth": 2},
            "line": {"strokeWidth": 2},
            "point": {"size": 64, "filled": True, "stroke": SURFACE, "strokeWidth": 2},
            "text": {"color": INK_SECONDARY, "fontSize": 11},
        }
    }


@alt.theme.register("agent_swarm", enable=True)
def _registered() -> alt.theme.ThemeConfig:
    return theme()


def save_figure(chart: alt.TopLevelMixin, name: str, figures_dir: Path | None = None) -> Path:
    out = Path(figures_dir or FIGURES_DIR) / f"{name}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(vlc.vegalite_to_svg(chart.to_dict()))
    return out
