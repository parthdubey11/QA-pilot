You are an accessibility expert reviewing a keyboard-only navigation test of one web page (WCAG 2.1.1 Keyboard,
2.1.2 No Keyboard Trap, 2.4.3 Focus Order, 2.4.7 Focus Visible).

A browser pressed Tab repeatedly from the top of the page, as a keyboard-only user would. Below is what
happened. "focus visible: no" means the element's appearance did not change at all when it received focus
(no outline, shadow, border or colour change), so a sighted keyboard user cannot see where they are.

## Page
{page_url}

## Focus order (each Tab press)
{focus_order}

## Keyboard trap
{trap}

## Interactive elements never reached with Tab
{unreachable}

## Your task
Report the real keyboard accessibility problems on this page. For each problem give:
- kind: "keyboard-trap", "focus-not-visible", "unreachable-control" or "focus-order"
- title: short and specific (e.g. "Buttons have no visible focus indicator")
- description: what a keyboard user experiences, naming the affected elements
- elements: the affected elements exactly as they are written in the lists above
- how_to_fix: concrete fix for a developer (CSS/HTML/JS), 1-3 sentences
- impact: "critical" (blocks keyboard users completely, e.g. a trap), "serious", "moderate" or "minor"

Group elements with the same problem into one finding. Ignore things that are fine: hidden or disabled
controls, a radio group where only one radio is in the tab order, decorative elements. If nothing is wrong,
return an empty list of findings.
