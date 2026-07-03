# Figure minimalism — plot-only rasters, honest captions

The standing figure-loop doctrine (designer-owned, Iris). It governs every figure the
`rv figure` loop produces and every hand-rendered project figure. It is the figure
analogue of the manuscript audience filter: **pixels are the "body," provenance goes to
the caption and the note.**

> **The raster is PLOT-ONLY.** A figure image carries data, axes, legend, and essential
> in-plot annotations — nothing else. Descriptive text lives in the LaTeX `\caption{…}`;
> lineage (script · data · SHA · date) lives in the `figures/<id>` OKF note. **A figure
> you can't regenerate is a rumour; a figure that argues its claim in baked pixels evades
> every grounding gate.**

## 1. What NEVER goes in the image

- **No thesis title / descriptive caption.** The words that explain the figure are the
  manuscript's `\caption{…}` — where the support-matcher can check the claim against the
  data. A caption baked into the raster is a claim no gate can read.
- **No provenance.** No `results_hash`, `sha256:`, source-path, run-id, or timestamp in
  the PNG/SVG. Provenance is the `figures/<id>` note's job (it *points to* the image and
  records the lineage); the note is diffable and checkable, the pixels are not.
- **No internal figure id.** The `fig_id` (e.g. `cb-fmt-fig2-residual`) is an internal
  slug, not a human title. `rv figure render` **never** bakes it into the image. This was
  the original leak (`figure.py` baked `ax.set_title(fig_id)` into both SVG and PNG);
  SR-FIG-MINIMAL removed it.

## 2. Preset behaviour (the render default)

- **`publication` → plot-only.** No in-raster title by default. `\includegraphics` carries
  the plot; the `\caption` carries the words; the note carries the lineage.
- **`slide` / `poster` → opt-in title.** A human title (what is plotted) may help a deck.
  Pass it explicitly: `rv figure <project> render <id> --title "HFS by model and language"`.
  The title states **what is plotted**, never the paper's claim (rule 3a). Even here the
  internal `fig_id` is never the title.

`rv figure render --title …` on the `publication` preset is honored as an explicit operator
override but emits an advisory WARN steering the text into the `\caption`.

## 3. Caption-honesty rules (standing, not one-off)

Two rules promoted from the CulturalBench `cb-fmt` dogfood to standing figure doctrine.
They are caption-*grammar* defaults, not project-specific edits.

### 3a. A title states what is plotted, never the claim (de-claim)

A figure's in-image title (or the caption's lead) must **not embed the paper's claim**
("Model X is more culturally competent", "accuracy collapses"). It states **what is
plotted** ("Easy vs. Hard accuracy by model"). The claim lives in prose, where the
support-matcher can check it against the data; a claim baked into a figure title evades
every grounding gate.

- *Bad (baked claim):* title = "Cultural competence collapses under the Hard format"
- *Good (states the plot):* title/caption-lead = "Easy → Hard accuracy by model"; the
  "collapse" interpretation moves into the caption prose / body, where it is checkable.

### 3b. A reported delta is a one-directional floor, not a symmetric point estimate

A reported improvement or gap (the `+12.17` case) is captioned as a **cross-model floor**
— "at least +12.17 pp across the models tested" — **not** a bare point estimate that
implies a precision the design does not support. Two grammar consequences:

- **Cross-model vs. per-element.** A headline number must be recomputed against the
  per-element encoding it sits over. A conservative *cross-model* floor is a different
  statistic from any single per-bar net — never present one as the other. (In `cb-fmt`
  Fig-2 the +12.17 is a cross-model floor computed by discounting the **smallest** residual
  by the **largest** observed confound bound — a combination no single model exhibits; the
  smallest *per-model* worst-case net is aya at ~+15.9. Stating "+12.17" as if it were a
  per-bar value is a reader-trap.)
- **One-directional uncertainty reads as one-directional.** When a confound can only bias
  a value in one direction (e.g. shrink each bar toward zero, never flip its sign), the
  in-plot uncertainty marker must read **unambiguously as one-directional** — a downward-only
  cap / arrowhead / shaded shrink-zone with a key — **not** a symmetric error bar that
  implies a two-sided CI. A symmetric cap over a one-directional bound overstates what the
  design supports.

### 3c. Every summary number is recomputed against the per-element encoding

Any headline number shown on or beside a figure is recomputed against the exact elements
plotted, using the *same* logic as the per-element encoding. A summary that uses different
logic than the plotted elements (a cross-model floor drawn as though it were a per-bar net,
a mean that weights differently than the bars) is a reader-trap. Recompute, then show.

## 4. Where provenance actually lives

- **`figures/<id>` OKF note** — the pointer + lineage: `source_experiment`,
  `experiment_results_hash`, render script + its sha256, render date, `svg_path`/`png_path`.
  The note *points to* the image; it never embeds image bytes.
- **The LaTeX `\caption{…}`** — the descriptive text + honest framing (rules 3a/3b/3c).
- **The image** — data only.

A render script should **hash-verify the source CSV against the frozen
`experiment_results_hash` before plotting** and abort on mismatch, so the figure→results
binding is structural, not assumed.

sr: SR-FIG-MINIMAL (§5J.16.5)
