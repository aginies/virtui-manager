/* Sparkline rendering with fixed (non-relative) scales.
 *
 * JS port of src/vmanager/absolute_sparkline.py so the web dashboard
 * charts match the TUI exactly:
 *  - scalePercent: linear 0-100 scale, for percentage data (CPU, memory).
 *  - scaleLog: logarithmic scale from 1 MB/s to 1 GB/s, for rate data
 *    (disk, network) in KB/s.
 *
 * Like the TUI widget, values are NOT normalized to the window's own
 * min/max: an idle VM renders as a flat baseline, not a full-height bar.
 */
"use strict";

const SPARK_POINTS = 40;

function scalePercent(value) {
  return Math.max(0, Math.min(1, value / 100));
}

const LOG_FLOOR = 1024.0;          // KB/s (1 MB/s)
const LOG_CAP = 1024.0 * 1024.0;   // KB/s (1 GB/s)
const LOG_SPAN = Math.log1p(LOG_CAP / LOG_FLOOR);

function scaleLog(value) {
  if (!(value > 0)) return 0;
  return Math.max(0, Math.min(1, Math.log1p(value / LOG_FLOOR) / LOG_SPAN));
}

/* Human-readable rate for labels; input is KB/s. */
function formatRate(kbps) {
  if (!(kbps > 0)) return "0 KB/s";
  if (kbps < 1024) return kbps.toFixed(0) + " KB/s";
  const mbps = kbps / 1024;
  if (mbps < 1024) return mbps.toFixed(1) + " MB/s";
  return (mbps / 1024).toFixed(2) + " GB/s";
}

/* Blend two [r,g,b] colors, ratio 0..1 (mirrors _blend_colors). */
function blendColors(c1, c2, ratio) {
  const mix = (a, b) => Math.round(a + (b - a) * ratio);
  return `rgb(${mix(c1[0], c2[0])},${mix(c1[1], c2[1])},${mix(c1[2], c2[2])})`;
}

class Sparkline {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {function(number): number} scale  value -> 0..1 bar ratio
   * @param {Array<number>} lowColor   [r,g,b] for low values
   * @param {Array<number>} highColor  [r,g,b] for high values
   */
  constructor(canvas, scale, lowColor, highColor) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.scale = scale;
    this.lowColor = lowColor;
    this.highColor = highColor;
    this.data = [];
    this._resize();
    this._onResize = () => { this._resize(); this.draw(); };
    window.addEventListener("resize", this._onResize);
    // Cards are built off-DOM (getBoundingClientRect is 0x0 at that point)
    // and grid columns reflow without a window resize: observe the real
    // layout size so the buffer is corrected as soon as it changes.
    this._ro = null;
    if (typeof ResizeObserver !== "undefined") {
      this._ro = new ResizeObserver(this._onResize);
      this._ro.observe(canvas);
    }
  }

  destroy() {
    window.removeEventListener("resize", this._onResize);
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    // Setting width/height clears the canvas; only do it when it changed.
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.dpr = dpr;
  }

  push(value) {
    this.data.push(value === null || value === undefined ? null : value);
    if (this.data.length > SPARK_POINTS) this.data.shift();
  }

  restore(other) {
    if (other && Array.isArray(other.data)) this.data = other.data.slice(-SPARK_POINTS);
  }

  draw() {
    // The buffer size depends on layout, and the first draws can happen
    // before/while the card is attached: re-check every draw (idempotent).
    this._resize();
    const { ctx, canvas, data } = this;
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const bw = w / SPARK_POINTS;
    const gap = Math.max(1, bw * 0.2);
    const baseline = Math.max(1, Math.round(this.dpr));

    // Faint reference gridlines at 25/50/75% so heights are readable.
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    const gl = Math.max(1, Math.round(this.dpr * 0.5));
    for (const gy of [0.25, 0.5, 0.75]) {
      ctx.fillRect(0, Math.round(h * gy), w, gl);
    }

    if (data.length === 0) {
      // Idle: faint baseline, like the TUI's empty sparkline.
      ctx.fillStyle = "rgba(255,255,255,0.08)";
      ctx.fillRect(0, h - baseline, w, baseline);
      return;
    }

    for (let i = 0; i < data.length; i++) {
      const v = data[i];
      const ratio = v === null ? 0 : this.scale(v);
      const x = i * bw + gap / 2;
      if (ratio <= 0) {
        ctx.fillStyle = "rgba(255,255,255,0.08)";
        ctx.fillRect(x, h - baseline, bw - gap, baseline);
      } else {
        const bh = Math.max(baseline, ratio * h);
        ctx.fillStyle = blendColors(this.lowColor, this.highColor, ratio);
        ctx.fillRect(x, h - bh, bw - gap, bh);
      }
    }
  }
}
