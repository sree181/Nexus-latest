# Nexus Coordinate: 3840x2160 Wall Redesign Spec

Source of truth for the command-wall frontend. Implement tokens (section 2), shared chrome (section 3), Operations Desk (section 4), and Decision Lineage (section 5) exactly. Acceptance is section 8.

The wall has one job: a person walking up to it learns in five seconds what is wrong, what it threatens, and who has to decide.

1. One hero per screen. The single most consequential fact gets about 20 percent of the pixels.
2. Nothing scrolls. Overflow is aggregated, paged, or expanded on touch.
3. Nothing under 28 px at 3840 wide.

Gold means a human decision is needed. Red means consequence. Card borders, gradients, and shadows are removed.
