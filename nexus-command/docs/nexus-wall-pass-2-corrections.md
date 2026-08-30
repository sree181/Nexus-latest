# Nexus Coordinate Wall: Pass 2 Corrections

Addendum to nexus-wall-redesign-spec.md. The spec still governs; this file lists what the first build got wrong. Where this file and the spec disagree, this file wins.

A. Frame: `#app` rows `9rem 30rem 62rem 19rem` with `gap: 3rem` and `padding: 3rem`. Exact 3840×2160, no scrollbar.

B. Hero: no buttons. Situation is one `impact` sentence, clamp 2 lines. Subtitle is `title`, 1 line.

C. Type: `Figure` for digits only. Six `--fs-*` tokens only.

D. Touch rail: 304 px, two `NavCluster`s, context actions in the middle.

E. Right stack: Impact 352 px / three columns; Agents panel 592 px / six tiles.

F. Top bar: mode, feed count, four pills, attribution, clock.

G. Map: corridor above dimming; fit to corridor bounds.

H. Acceptance additions in `e2e/wall-audit.spec.ts`.
