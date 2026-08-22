---
name: declutter
description: Find and apply refactorings that remove code from this repository — survey for duplication, dead code and obsolete features, then implement fix by fix, measuring the lines each one actually saves and reverting the ones that do not pay. Use when asked to declutter, deduplicate, shrink or refactor the codebase for size, to hunt dead code, or to make comments more concise or to refactor comments.
---

# Decluttering

The goal is fewer code lines (excluding comments and whitespace), not fewer files.
A refactoring done with this skill earns its place by removing more than it adds — 
measured, not estimated.

## Measuring

`.claude/skills/declutter/count-code-lines.sh` counts non-blank, non-comment lines 
across tracked files.

Run it once for a baseline before touching anything, then again after each fix. Never 
report a saving you have not measured.

## Finding Refactoring Opportunities
Assume the repo has grown quite a bit and its time to declutter. Now, its time to find 
both direct and well as indirect/architectural gains through refactoring. Your task is 
to find refactoring opportunities and architectural tweaks that reduce the amount of code.

Start by reading these pages and their subpages: 
- https://refactoring.guru/refactoring/smells
- https://refactoring.guru/refactoring/techniques
- https://refactoring.guru/design-patterns/catalog
- https://refactoring.guru/refactoring/catalog
- https://refactoring.guru/refactoring/technical-debt
- https://refactoring.guru/refactoring/what-is-refactoring
- https://refactoring.guru/refactoring/how-to

Then read the code of this repository to find refactoring opportunities. Create a list 
of the 10 refactoring opportunities ordered by number of code lines a fix likely 
reduces. Include easy wins for other quality aspects if found, but the focus is reducing 
code. Exclude anything that would be bad practice, bad design or very complex regardless 
of how much it removes. Also consider removing features that may not be that useful or 
even obsolete, dead code and literal code duplication (closely matching code lines).

Don't fix anything yet, just provide a sorted table — with the applicable 
pattern/technique/smell name (see refactoring guru), a short description of what's 
proposed, any negative side effects, the benefit and an estimate of the total lines
that the implementation/fix might save. Under 500 words.

## Fixing
Ask the user which refactoring opportunities from above shall be fixed. Then, use the 
`count-code-lines.sh` script to measure how many code lines there are now in the entire 
code base (not counting comment-like or empty lines).

Make the fixes the user selected one by one. After each, re-run the counting tool. If a 
fix reduced fewer than 5 lines, revert it. Otherwise run the full test suite and either 
fix small remaining issues or revert the fix entirely if the issues cause more trouble 
than they're worth. If tests are fine, move to the next fix.

Afterwards, list the refactorings implemented/reverted/skipped and the lines each 
actually saved.

Keep a task list of all actions (including refactoring opportunities that you going
to implement) including their status.

## Reporting

List every fix as implemented, reverted or skipped, each with the lines it actually saved,
and give the before/after total. Reverted fixes are results too: say what they cost and
why the rule rejected them. 