import altair as alt
import polars as pl

from agent_swarm import viz


def test_theme_uses_palette_in_fixed_order():
    config = viz.theme()["config"]
    assert config["range"]["category"] == list(viz.CATEGORICAL)
    assert config["background"] == viz.SURFACE


def test_save_figure_writes_svg(tmp_path):
    chart = alt.Chart(pl.DataFrame({"x": ["a", "b"], "y": [1, 2]})).mark_bar().encode(x="x", y="y")
    path = viz.save_figure(chart, "demo", figures_dir=tmp_path)
    assert path == tmp_path / "demo.svg"
    assert path.read_text().lstrip().startswith("<svg")


def test_single_series_marks_default_to_first_palette_slot():
    config = viz.theme()["config"]
    assert config["mark"]["color"] == viz.CATEGORICAL[0]
    assert config["axisX"]["labelAngle"] == 0
