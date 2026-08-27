# Nexus Command Center — Visual Verification

Verified at **1920×1080** on 26 August 2026.

The command center renders as a single coherent operational workspace with a persistent review/live status rail, named command owner, source-health summary, plain-language incident brief, geospatial incident context, evidence-bound decision queue, approval authority status, and large touch actions. The layout fits the primary incident and decision workflow within one viewport. Text hierarchy, source freshness, constraints, limitations, and the distinction between review data and live agency control are legible.

The revised flex allocation displays the source-freshness strip, agency commitment rail, and four primary decision actions within the same 1920×1080 viewport. The situation and evidence columns scroll independently when their detailed content exceeds the central work area, preserving the map, human decision controls, source-health strip, and accountable action rail at all times.

No simulation controls, timesteps, rewards, episodes, exploration indicators, animated agent coordination windows, or scenario-start controls are present in the operational interface.

## Human-Approval Workflow

The interactive review flow was verified end to end in local review mode. Selecting **Review & approve** opens a modal containing the exact recommendation version, immutable evidence-snapshot hash, approval expiry, reason code, operator comment, and an explicit consequence attestation. The approval action remains disabled until the operator completes the attestation.

Submitting the approved decision removes the recommendation from the operator’s queue and creates two durable, named agency commitments in `requested` state. The interface explicitly shows that approval creates accountable work and does not directly operate traffic signals, signage, transit vehicles, or another agency system.
