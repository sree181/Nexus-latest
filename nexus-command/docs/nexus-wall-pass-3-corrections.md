# Nexus Coordinate Wall: Pass 3 Corrections

Overrides pass 2 where they differ. Root cause: the right stack is too narrow. Fix proportions, then tiles, then the frame.

A. Body columns 2000 / 1600. Stack rows 160 px / 1fr.
B. Impact strip: one line, unit word in every cell, zero styling, no title.
C. Agents 2×3, three fixed lines, nowrap+ellipsis, shortened roles.
D. Diagnose frame, then `#app { height: 100dvh; grid-template-rows: 9rem 30rem minmax(0, 1fr) 19rem; }`.
E. Top bar five items; attribution on the map.
F. Rail segments and Clear share surface-2 and one height token.
G. Centre the hero in its row.
