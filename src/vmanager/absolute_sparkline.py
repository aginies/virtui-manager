"""Sparkline renderables and widgets with fixed (non-relative) scales.

Textual's built-in Sparkline normalizes bar heights to the data's own
min/max, so a constant load renders as a flat line and a single sample
or spike renders as a full-height bar.

This module vendors a trimmed copy of Textual's Sparkline renderable
(MIT license, https://github.com/Textualize/textual) with a pluggable
scale function:

- ``AbsoluteSparkline``: linear 0-100 scale, for percentage data
  (CPU, memory).
- ``LogSparkline``: logarithmic scale from 1 MB/s to 1 GB/s, for rate
  data (disk, network). A wide range of rates maps to the full bar
  height without one spike dominating the window.
"""

from __future__ import annotations

import math

from rich.color import Color
from rich.console import Console, ConsoleOptions
from rich.segment import Segment
from rich.style import Style
from textual.app import RenderResult
from textual.renderables.sparkline import Sparkline as _SparklineRenderable
from textual.widgets import Sparkline as _SparklineWidget


def _blend_colors(color1: Color, color2: Color, ratio: float) -> Color:
    """Linearly interpolate between two RGB colors (as Textual does)."""
    if color1.triplet is None or color2.triplet is None:
        return color2
    r1, g1, b1 = color1.triplet
    r2, g2, b2 = color2.triplet
    return Color.from_rgb(
        r1 + (r2 - r1) * ratio,
        g1 + (g2 - g1) * ratio,
        b1 + (b2 - b1) * ratio,
    )


class AbsoluteSparklineRenderable(_SparklineRenderable):  # pylint: disable=too-few-public-methods
    """Sparkline with a fixed linear 0-100 scale.

    Unlike the built-in, bar heights are not normalized to the data's
    own min/max: a value of 50 always renders at half height. Intended
    for percentage data (CPU, memory).
    """

    MAXIMUM = 100.0

    def _scale_value(self, value: float) -> float:
        """Map a value to a bar height ratio (0-1)."""
        return max(0.0, min(1.0, value / self.MAXIMUM))

    def __rich_console__(  # pylint: disable=too-many-locals
        self, console: Console, options: ConsoleOptions
    ) -> "RenderResult":
        # Vendored loop body: keep close to the Textual original.
        width = self.width or options.max_width
        height = self.height or 1

        data = list(self.data)
        if len(data) == 0:
            for _ in range(height - 1):
                yield Segment.line()
            yield Segment("▁" * width, self.min_color)
            return
        if len(data) == 1:
            # The bucketing below needs at least two samples; duplicating
            # the value keeps the bar at its scaled height.
            data = data + data

        bar_line_segments = len(self.BARS)
        bar_segments = bar_line_segments * height - 1

        summary_function = self.summary_function
        min_color, max_color = self.min_color.color, self.max_color.color
        assert min_color is not None
        assert max_color is not None

        buckets = tuple(self._buckets(data, num_buckets=width))

        for i in reversed(range(height)):
            current_bar_part_low = i * bar_line_segments
            current_bar_part_high = (i + 1) * bar_line_segments

            bucket_index = 0.0
            bars_rendered = 0
            step = len(buckets) / width
            while bars_rendered < width:
                partition = buckets[int(bucket_index)]
                partition_summary = summary_function(partition)
                height_ratio = self._scale_value(partition_summary)
                bar_index = int(height_ratio * bar_segments)

                if bar_index < current_bar_part_low:
                    bar_glyph = " "
                    with_color = False
                elif bar_index >= current_bar_part_high:
                    bar_glyph = "█"
                    with_color = True
                else:
                    bar_glyph = self.BARS[bar_index % bar_line_segments]
                    with_color = True

                if with_color:
                    bar_color = _blend_colors(min_color, max_color, height_ratio)
                    style = Style.from_color(bar_color)
                else:
                    style = None

                bars_rendered += 1
                bucket_index += step
                yield Segment(bar_glyph, style)

            if i > 0:
                yield Segment.line()


class LogSparklineRenderable(AbsoluteSparklineRenderable):  # pylint: disable=too-few-public-methods
    """Sparkline with a logarithmic scale, for rate data in KB/s.

    Bar height = log(1 + v/FLOOR) / log(1 + CAP/FLOOR), so a wide range
    of rates maps to the full bar height and a single spike does not
    dominate the window. Values at or below FLOOR render near the
    bottom; values at or above CAP render full.
    """

    FLOOR = 1024.0          # KB/s (1 MB/s)
    CAP = 1024.0 * 1024.0   # KB/s (1 GB/s)
    _LOG_SPAN = math.log1p(CAP / FLOOR)

    def _scale_value(self, value: float) -> float:
        if value <= 0:
            return 0.0
        ratio = math.log1p(value / self.FLOOR) / self._LOG_SPAN
        return max(0.0, min(1.0, ratio))


class _ScaledSparklineWidget(_SparklineWidget):  # pylint: disable=too-few-public-methods
    """Sparkline widget base that renders with a fixed-scale renderable."""

    renderable_cls: type[AbsoluteSparklineRenderable]

    def render(self) -> RenderResult:
        """Renders the sparkline with the fixed-scale renderable."""
        data = self.data or []
        _, base = self.background_colors
        min_color = base + (
            self.get_component_styles("sparkline--min-color").color
            if self.min_color is None
            else self.min_color
        )
        max_color = base + (
            self.get_component_styles("sparkline--max-color").color
            if self.max_color is None
            else self.max_color
        )
        return self.renderable_cls(
            data,
            width=self.size.width,
            height=self.size.height,
            min_color=min_color.rich_color,
            max_color=max_color.rich_color,
            summary_function=self.summary_function,
        )


class AbsoluteSparkline(_ScaledSparklineWidget):  # pylint: disable=too-few-public-methods
    """Sparkline widget with a fixed linear 0-100 scale.

    Bar height reflects the absolute value instead of the data's own
    min/max, so low CPU usage renders as a low bar right from the first
    sample.
    """

    renderable_cls = AbsoluteSparklineRenderable


class LogSparkline(_ScaledSparklineWidget):  # pylint: disable=too-few-public-methods
    """Sparkline widget with a logarithmic 1 MB/s - 1 GB/s scale.

    For rate data (disk, network): the level is visible at any
    magnitude and a single spike does not flatten the rest of the
    window.
    """

    renderable_cls = LogSparklineRenderable
